/* 詳細頁：依 query string 的 id 載入 detail/{id}.json，
   渲染重點摘要、AI 觀點（含展望）、可折疊逐字稿與外部連結。 */
(function () {
  "use strict";

  var elDoc = document.getElementById("doc");
  var lang = "zh";   // 美股詳情頁的中／英切換狀態

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function fail(msg, hint) {
    elDoc.innerHTML =
      '<div class="empty" style="margin-top:48px">' +
        "<p>" + esc(msg) + "</p>" +
        '<p class="empty-hint">' + esc(hint) + "</p>" +
      "</div>" +
      '<nav class="doc-foot-nav"><a href="index.html">&larr; 返回清單</a></nav>';
  }

  /* 摘要：第一行為一句話總結，其後「• 」開頭為條列重點 */
  function summaryHtml(summary) {
    var lines = String(summary || "").split("\n")
      .map(function (l) { return l.trim(); })
      .filter(Boolean);
    if (!lines.length) return "<p class=\"prose\">本場次尚無摘要。</p>";

    var lead = "";
    var bullets = [];
    lines.forEach(function (l) {
      if (/^•/.test(l)) bullets.push(l.replace(/^•\s*/, ""));
      else if (!lead) lead = l;
      else bullets.push(l);
    });

    var html = lead ? '<p class="lead">' + esc(lead) + "</p>" : "";
    if (bullets.length) {
      html += '<ul class="bullets">' + bullets.map(function (b) {
        return "<li>" + esc(b) + "</li>";
      }).join("") + "</ul>";
    }
    return html;
  }

  function paragraphs(text) {
    return String(text || "").split(/\n+/)
      .map(function (p) { return p.trim(); })
      .filter(Boolean);
  }

  /* AI 觀點：以【展望】為界，後段獨立成「公司展望」區塊 */
  function aiViewHtml(aiView) {
    var raw = String(aiView || "").trim();
    if (!raw) return "<p class=\"prose\">本場次尚無 AI 觀點。</p>";

    var parts = raw.split("【展望】");
    var viewParas = paragraphs(parts[0]);
    var html = '<div class="prose">' + viewParas.map(function (p) {
      return "<p>" + esc(p) + "</p>";
    }).join("") + "</div>";

    if (parts.length > 1) {
      var outlookParas = paragraphs(parts.slice(1).join("\n"));
      html += '<aside class="outlook">' +
        '<p class="outlook-label">公司展望</p>' +
        outlookParas.map(function (p) { return "<p>" + esc(p) + "</p>"; }).join("") +
      "</aside>";
    }
    return html;
  }

  /* 逐字稿：偵測「發言者：」前綴並標示 */
  function transcriptHtml(transcript) {
    var paras = paragraphs(transcript);
    return paras.map(function (p) {
      var m = p.match(/^([^：\n]{1,10})：([\s\S]*)$/);
      if (m && m[2].trim()) {
        return "<p><span class=\"speaker\">" + esc(m[1]) + "</span>" +
               esc(m[2].trim()) + "</p>";
      }
      return "<p>" + esc(p) + "</p>";
    }).join("");
  }

  function linksHtml(d) {
    var links = [];
    if (d.pdf_url) {
      links.push('<a class="btn" href="' + esc(d.pdf_url) +
        '" target="_blank" rel="noopener">法說會簡報 PDF <span class="arrow">&nearr;</span></a>');
    }
    if (d.video_url) {
      links.push('<a class="btn" href="' + esc(d.video_url) +
        '" target="_blank" rel="noopener">影音紀錄 <span class="arrow">&nearr;</span></a>');
    }
    return links.length
      ? '<div class="doc-links">' + links.join("") + "</div>"
      : "";
  }

  /* ── 右欄財報數據 ───────────────────────────────────── */

  var FIN_ICONS = {
    cap: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v6c0 1.7 3.1 3 7 3s7-1.3 7-3V6"/><path d="M5 12v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6"/></svg>',
    rev: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20h16"/><rect x="5" y="12" width="3.4" height="6"/><rect x="10.3" y="8" width="3.4" height="10"/><rect x="15.6" y="4" width="3.4" height="14"/></svg>',
    eps: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 16 9 11 13 14 20 6"/><polyline points="14.5 6 20 6 20 11.5"/></svg>'
  };

  function finNum(n, digits) {
    if (n == null) return "—";
    return Number(n).toLocaleString("zh-Hant-TW", {
      minimumFractionDigits: digits, maximumFractionDigits: digits
    });
  }

  function finDelta(label, v) {
    if (v == null) return "";
    var cls = v >= 0 ? "up" : "down";
    var sign = v >= 0 ? "+" : "";
    return '<span class="fin-delta ' + cls + '">' + esc(label) +
           " " + sign + v.toFixed(1) + "%</span>";
  }

  function finCard(icon, label, value, unit, deltas) {
    return '<div class="fin-card">' +
      '<span class="fin-icon">' + icon + "</span>" +
      '<div class="fin-body">' +
        '<div class="fin-label">' + esc(label) + "</div>" +
        '<div class="fin-value">' + value +
          (unit ? ' <span class="fin-unit">' + esc(unit) + "</span>" : "") +
        "</div>" +
        (deltas ? '<div class="fin-deltas">' + deltas + "</div>" : "") +
      "</div>" +
    "</div>";
  }

  function finMini(label, value) {
    if (value == null || value === "") return "";
    return '<div class="fin-mini-item">' +
      '<div class="fin-mini-label">' + esc(label) + "</div>" +
      '<div class="fin-mini-value">' + value + "</div>" +
    "</div>";
  }

  /* 美元金額 → [數字, 單位]（兆／億美元） */
  function usdParts(v) {
    if (v == null) return ["—", ""];
    var a = Math.abs(v);
    if (a >= 1e12) return [(v / 1e12).toFixed(2), "兆美元"];
    if (a >= 1e8) return [finNum(v / 1e8, 0), "億美元"];
    if (a >= 1e6) return [finNum(v / 1e6, 0), "百萬美元"];
    return [finNum(v, 0), "美元"];
  }

  function usFinancialsHtml(fin) {
    var head = '<div class="fin-head"><span class="fin-title">財報數據</span>' +
      (fin.report_date
        ? '<span class="fin-period mono">' + esc(fin.report_date) + "</span>" : "") +
      "</div>";

    var cards = "";
    if (fin.market_cap != null) {
      var mc = usdParts(fin.market_cap);
      cards += finCard(FIN_ICONS.cap, "市值", mc[0], mc[1], "");
    }
    if (fin.revenue != null) {
      var rv = usdParts(fin.revenue);
      cards += finCard(FIN_ICONS.rev, "單季營收", rv[0], rv[1],
        finDelta("年", fin.revenue_yoy));
    }
    if (fin.eps != null) {
      // EPS 旁的 delta 用 surprise%（對比市場預期）
      cards += finCard(FIN_ICONS.eps, "EPS（美元）", finNum(fin.eps, 2), "",
        finDelta("驚奇", fin.surprise_pct));
    }

    var mini =
      finMini("本益比", fin.pe != null ? finNum(fin.pe, 1) + "倍" : null) +
      finMini("EPS 預期", fin.eps_estimate != null
        ? finNum(fin.eps_estimate, 2) : null) +
      finMini("公布後反應", fin.price_reaction != null
        ? (fin.price_reaction >= 0 ? "+" : "") + finNum(fin.price_reaction, 1) + "%"
        : null);
    var miniBlock = mini ? '<div class="fin-mini">' + mini + "</div>" : "";

    if (!cards && !miniBlock) {
      return '<aside class="fin-panel">' + head +
        '<p class="fin-empty">本檔暫無財報數據。</p></aside>';
    }
    return '<aside class="fin-panel">' + head +
      '<div class="fin-cards">' + cards + "</div>" + miniBlock +
      '<p class="fin-note">資料來源：Yahoo Finance · 數據僅供參考</p>' +
    "</aside>";
  }

  function financialsHtml(fin) {
    if (fin && fin.market === "us") return usFinancialsHtml(fin);
    var head = '<div class="fin-head"><span class="fin-title">財報數據</span>' +
      (fin && fin.period
        ? '<span class="fin-period mono">' + esc(fin.period) + "</span>" : "") +
      "</div>";

    if (!fin) {
      return '<aside class="fin-panel">' + head +
        '<p class="fin-empty">本檔暫無最新一季財報數據，季報公布後將自動更新。</p>' +
      "</aside>";
    }

    var cards = "";
    if (fin.market_cap != null) {
      cards += finCard(FIN_ICONS.cap, "市值", finNum(fin.market_cap, 0), "億", "");
    }
    if (fin.revenue != null) {
      cards += finCard(FIN_ICONS.rev, "單季營收", finNum(fin.revenue, 2), "億",
        finDelta("年", fin.revenue_yoy) + finDelta("季", fin.revenue_qoq));
    }
    if (fin.eps != null) {
      cards += finCard(FIN_ICONS.eps, "單季 EPS", finNum(fin.eps, 2), "元",
        finDelta("年", fin.eps_yoy));
    }

    var mini =
      finMini("毛利率", fin.gross_margin != null
        ? finNum(fin.gross_margin, 1) + "%" : null) +
      finMini("本益比", fin.pe != null
        ? finNum(fin.pe, 1) + "倍" : null) +
      finMini("資本支出", fin.capex != null
        ? finNum(fin.capex, 1) + "億" : null);
    var miniBlock = mini ? '<div class="fin-mini">' + mini + "</div>" : "";

    if (!cards && !miniBlock) {
      return '<aside class="fin-panel">' + head +
        '<p class="fin-empty">本檔暫無最新一季財報數據。</p></aside>';
    }

    return '<aside class="fin-panel">' + head +
      '<div class="fin-cards">' + cards + "</div>" + miniBlock +
      '<p class="fin-note">單季數據（累計相減）· 來源：公開資訊觀測站</p>' +
    "</aside>";
  }

  function renderDoc(d) {
    var isUs = d.market === "us";
    var en = isUs && lang === "en";

    var company = en ? (d.company_en || d.company) : d.company;
    var summaryText = isUs ? (d["summary_" + lang] || d.summary) : d.summary;
    var aiText = isUs ? (d["ai_view_" + lang] || d.ai_view) : d.ai_view;

    var L = en
      ? { conf: "Earnings", summary: "Highlights", aiview: "AI View & Outlook",
          ainote: "AI-generated · reference only", back: "&larr; Back to list" }
      : { conf: isUs ? "財報電話會議" : "法人說明會", summary: "重點摘要",
          aiview: "AI 觀點與未來方向", ainote: "AI 彙整 · 僅供參考",
          back: "&larr; 返回清單" };

    document.title = company + " " + d.date +
      (isUs ? "" : " 法說會") + "｜法說會觀測站";

    var eyebrowBits = [];
    if (d.code) eyebrowBits.push(esc(d.code));
    if (d.date) eyebrowBits.push(esc(d.date));
    eyebrowBits.push(L.conf);

    var langToggle = isUs
      ? '<div class="lang-toggle">' +
          '<button type="button" class="lang-btn' + (lang === "zh" ? " on" : "") +
            '" data-lang="zh">中文</button>' +
          '<button type="button" class="lang-btn' + (lang === "en" ? " on" : "") +
            '" data-lang="en">EN</button>' +
        "</div>"
      : "";

    var transcriptSection = "";
    if (!isUs) {
      if (d.transcript) {
        transcriptSection =
          '<section class="doc-section">' +
            "<h2>完整逐字稿" +
              '<span class="h2-note">約 ' + d.transcript.length.toLocaleString("zh-Hant-TW") + " 字 · 語音辨識產生</span>" +
            "</h2>" +
            '<div class="transcript" id="transcript">' + transcriptHtml(d.transcript) + "</div>" +
            '<button class="transcript-toggle" id="transcript-toggle" aria-expanded="false" aria-controls="transcript">展開完整逐字稿</button>' +
          "</section>";
      } else {
        transcriptSection =
          '<section class="doc-section">' +
            "<h2>完整逐字稿</h2>" +
            '<p class="transcript-none">本場次尚未取得影音來源，暫無逐字稿。摘要與觀點改以法說會簡報內容彙整。</p>' +
          "</section>";
      }
    }

    elDoc.innerHTML =
      '<header class="doc-head">' +
        '<p class="doc-eyebrow">' + eyebrowBits.join('<span class="sep">·</span>') + "</p>" +
        "<h1>" + esc(company) + "</h1>" +
        linksHtml(d) + langToggle +
      "</header>" +
      '<div class="doc-body">' +
        '<article>' +
          '<section class="doc-section">' +
            "<h2>" + esc(L.summary) + "</h2>" + summaryHtml(summaryText) +
          "</section>" +
          '<section class="doc-section">' +
            "<h2>" + esc(L.aiview) + '<span class="h2-note">' + esc(L.ainote) + "</span></h2>" +
            aiViewHtml(aiText) +
          "</section>" +
          transcriptSection +
        "</article>" +
        financialsHtml(d.financials) +
      "</div>" +
      '<nav class="doc-foot-nav"><a href="index.html">' + L.back + "</a></nav>";

    var toggle = document.getElementById("transcript-toggle");
    if (toggle) {
      var box = document.getElementById("transcript");
      toggle.addEventListener("click", function () {
        var expanded = box.classList.toggle("expanded");
        toggle.setAttribute("aria-expanded", String(expanded));
        toggle.textContent = expanded ? "收合逐字稿" : "展開完整逐字稿";
        if (!expanded) box.scrollIntoView({ block: "start" });
      });
    }

    if (isUs) {
      var btns = elDoc.querySelectorAll(".lang-btn");
      Array.prototype.forEach.call(btns, function (b) {
        b.addEventListener("click", function () {
          if (lang === b.getAttribute("data-lang")) return;
          lang = b.getAttribute("data-lang");
          renderDoc(d);
        });
      });
    }
  }

  var id = new URLSearchParams(location.search).get("id") || "";
  if (!/^[\w.一-鿿-]{1,80}$/.test(id)) {
    fail("找不到這場法說會。", "網址缺少有效的場次編號，請從清單重新進入。");
    return;
  }

  fetch("detail/" + encodeURIComponent(id) + ".json")
    .then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    })
    .then(renderDoc)
    .catch(function () {
      fail("找不到這場法說會。", "資料可能尚未建置，或場次編號已變更。請返回清單重新查詢。");
    });
})();
