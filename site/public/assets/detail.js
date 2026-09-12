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

  /* 結構化逐字稿：intro 段落 / topic 主題小標 / qa 問答（Q 為小標、A 為內文） */
  function segmentsHtml(segs) {
    var qaOpened = false;
    return segs.map(function (s) {
      var body = paragraphs(s.text).map(function (p) {
        return "<p>" + esc(p) + "</p>";
      }).join("");
      if (s.type === "qa") {
        var head = "";
        if (!qaOpened) {
          qaOpened = true;
          head = '<h3 class="seg-qa-head">問答 Q&amp;A</h3>';
        }
        return head + '<div class="seg-qa">' +
          (s.title ? '<p class="seg-q">' + esc(s.title) + "</p>" : "") +
          '<div class="seg-a">' + body + "</div></div>";
      }
      if (s.type === "topic") {
        return (s.title ? '<h3 class="seg-topic">' + esc(s.title) + "</h3>" : "") + body;
      }
      return body;   // intro
    }).join("");
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

  function finMini(label, value, sub) {
    if (value == null || value === "") return "";
    return '<div class="fin-mini-item">' +
      '<div class="fin-mini-label">' + esc(label) + "</div>" +
      '<div class="fin-mini-value">' + value + "</div>" +
      (sub ? '<div class="fin-mini-sub">' + sub + "</div>" : "") +
    "</div>";
  }

  /* 本益比對同業中位數的折溢價：判斷貴或便宜的關鍵，單看絕對值沒有意義 */
  function peerPeSub(fin) {
    var peer = fin.industry_pe;
    if (!peer || !peer.median) return "";
    var txt = esc(fin.industry || "同業") + "中位 " + finNum(peer.median, 1);
    if (fin.pe == null) return '<span class="fin-peer">' + txt + "</span>";
    var ratio = fin.pe / peer.median;
    var cls = ratio >= 1 ? "up" : "down";   // 高於同業＝紅、低於同業＝綠（同年增率語意）
    // 微利股的本益比會飆到幾百倍，換算成「溢價 9852%」既難讀也沒意義，
    // 差距超過一倍就改用倍數表示。
    var label = (ratio >= 2 || ratio <= 0.5)
      ? finNum(ratio, 1) + "× 同業"
      : (ratio >= 1 ? "溢價 " : "折價 ") +
        finNum(Math.abs(ratio - 1) * 100, 0) + "%";
    return '<span class="fin-peer">' + txt + "</span>" +
      '<span class="fin-delta ' + cls + '">' + label + "</span>";
  }

  /* 近 N 季趨勢表：欄＝季別（舊→新，趨勢由左往右讀），列＝指標。
     每格帶一條與同列最大值等比的底條，數字之外還能一眼看出高低。 */
  var TW_ROWS = [
    { key: "revenue", label: "營收", unit: "億", digits: 1 },
    { key: "revenue_yoy", label: "營收 YoY", unit: "%", digits: 1, signed: true },
    { key: "eps", label: "EPS", unit: "元", digits: 2 },
    { key: "gross_margin", label: "毛利率", unit: "%", digits: 1 },
    { key: "capex", label: "資本支出", unit: "億", digits: 1 }
  ];
  /* 美股：yfinance 只給 5～7 季、YoY 多數季算不出來，改放市場最在意的「EPS vs 預期」 */
  var US_ROWS = [
    { key: "revenue", label: "營收", unit: "億美元", digits: 1, scale: 1e-8 },
    { key: "revenue_qoq", label: "營收 QoQ", unit: "%", digits: 1, signed: true },
    { key: "eps", label: "EPS", unit: "美元", digits: 2 },
    { key: "eps_estimate", label: "EPS 預期", unit: "美元", digits: 2 },
    { key: "surprise_pct", label: "驚奇", unit: "%", digits: 1, signed: true },
    { key: "gross_margin", label: "毛利率", unit: "%", digits: 1 },
    { key: "capex", label: "資本支出", unit: "億美元", digits: 1, scale: 1e-8 }
  ];

  /* 期別標頭：台股 "2026 Q2" → 兩行；美股季底 "2026-07" → 年／月 */
  function periodHead(p) {
    var s = String(p || "");
    if (s.indexOf("-") !== -1) {
      var parts = s.split("-");
      return esc(parts[0]) + "<br>" + esc(parts[1]) + "月";
    }
    return esc(s).replace(" ", "<br>");
  }

  function quartersHtml(quarters, rowsSpec) {
    if (!quarters || !quarters.length) return "";
    var qs = quarters.slice().reverse();   // 傳入是新→舊
    var rows = rowsSpec || TW_ROWS;

    var head = '<tr><th scope="col">季別</th>' + qs.map(function (q, i) {
      var cls = i === qs.length - 1 ? ' class="is-latest"' : "";
      return "<th scope=\"col\"" + cls + ">" + periodHead(q.period) + "</th>";
    }).join("") + "</tr>";

    var body = rows.map(function (r) {
      var vals = qs.map(function (q) {
        var v = q[r.key];
        return (v != null && r.scale) ? v * r.scale : v;
      });
      var present = vals.filter(function (v) { return v != null; });
      if (!present.length) return "";       // 整列都沒資料（如金控無毛利率）就不顯示
      var max = Math.max.apply(null, present.map(Math.abs));
      // 大數字（台積電營收上萬億）在窄欄位放不下小數，依量級決定精度
      var digits = r.digits;
      if (r.key === "revenue" || r.key === "capex") digits = max >= 1000 ? 0 : 1;
      var tds = qs.map(function (q, i) {
        var v = vals[i];
        var latest = i === qs.length - 1 ? " is-latest" : "";
        if (v == null) return '<td class="is-na' + latest + '">—</td>';
        var pctW = max > 0 ? Math.round(Math.abs(v) / max * 100) : 0;
        var neg = v < 0 ? " is-neg" : "";
        var txt = (r.signed && v > 0 ? "+" : "") + finNum(v, digits);
        return '<td class="' + (latest ? "is-latest" : "") + neg + '">' +
          '<span class="q-bar" style="width:' + pctW + '%"></span>' +
          '<span class="q-val">' + txt + "</span></td>";
      }).join("");
      return '<tr><th scope="row">' + esc(r.label) +
        '<span class="q-unit">' + esc(r.unit) + "</span></th>" + tds + "</tr>";
    }).join("");

    if (!body) return "";
    return '<div class="fin-trend">' +
      '<div class="fin-trend-head">近 ' + qs.length + ' 季趨勢</div>' +
      '<div class="fin-table-wrap"><table class="fin-table">' +
        "<thead>" + head + "</thead><tbody>" + body + "</tbody></table></div>" +
    "</div>";
  }

  /* 業務項目：公司自己的營收結構 + 可點的族群標籤 */
  function businessHtml(biz, cat) {
    if (!biz || (!biz.segments || !biz.segments.length)) return "";
    var catQ = cat ? "cat=" + encodeURIComponent(cat) + "&" : "";
    // 比重條用絕對佔比（55% 就填 55% 寬），不做相對最大項的等比放大——
    // 這是營收結構，條的長度本身就該等於佔比。
    var items = biz.segments.map(function (s) {
      var pct = s.pct != null ? finNum(s.pct, 1) + "%" : "";
      var w = s.pct != null ? Math.max(0, Math.min(100, s.pct)) : 0;
      return '<li class="biz-item">' +
        '<span class="biz-bar" style="width:' + w + '%"></span>' +
        '<span class="biz-name">' + esc(s.name) +
          (s.note ? '<span class="biz-note">' + esc(s.note) + "</span>" : "") +
        "</span>" +
        '<span class="biz-pct mono">' + pct + "</span>" +
      "</li>";
    }).join("");

    var tags = (biz.tags || []).map(function (t) {
      return '<a class="biz-tag" href="index.html?' + catQ + 'tag=' +
        encodeURIComponent(t) + '">' + esc(t) + "</a>";
    }).join("");

    return '<aside class="biz-panel">' +
      '<div class="fin-head"><span class="fin-title">業務項目</span>' +
        (biz.as_of ? '<span class="fin-period mono">' + esc(biz.as_of) + "</span>" : "") +
      "</div>" +
      '<ul class="biz-list">' + items + "</ul>" +
      (tags ? '<div class="biz-tags"><span class="biz-tags-label">族群</span>' +
        tags + "</div>" : "") +
      '<p class="fin-note">' + (cat === "us"
        ? "業務項目由 AI 讀取公司公開描述整理 · 點族群看同類公司"
        : "營收結構由 AI 讀取法說會簡報整理 · 點族群看同類公司") + "</p>" +
    "</aside>";
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
      var indLabel = fin.industry
        ? '<span class="fin-peer">' + esc(fin.industry) +
          (fin.industry_en ? " · " + esc(fin.industry_en) : "") + "</span>"
        : "";
      cards += finCard(FIN_ICONS.cap, "市值", mc[0], mc[1], indLabel);
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

    var latest = (fin.quarters || [])[0] || {};
    var mini =
      finMini("本益比", fin.pe != null ? finNum(fin.pe, 1) + "倍" : null, peerPeSub(fin)) +
      finMini("EPS 預期", fin.eps_estimate != null
        ? finNum(fin.eps_estimate, 2) : null) +
      finMini("毛利率", latest.gross_margin != null
        ? finNum(latest.gross_margin, 1) + "%" : null) +
      finMini("公布後反應", fin.price_reaction != null
        ? (fin.price_reaction >= 0 ? "+" : "") + finNum(fin.price_reaction, 1) + "%"
        : null);
    var miniBlock = mini ? '<div class="fin-mini">' + mini + "</div>" : "";
    var trend = quartersHtml(fin.quarters, US_ROWS);

    if (!cards && !miniBlock && !trend) {
      return '<aside class="fin-panel">' + head +
        '<p class="fin-empty">本檔暫無財報數據。</p></aside>';
    }
    return '<aside class="fin-panel">' + head +
      (cards ? '<div class="fin-cards">' + cards + "</div>" : "") + miniBlock +
      trend +
      '<p class="fin-note">資料來源：Yahoo Finance · EPS 為調整後數字 · 同業中位為白名單內同產業 · 僅供參考</p>' +
    "</aside>";
  }

  function financialsHtml(fin, d) {
    if (fin && fin.market === "us") return usFinancialsHtml(fin);
    var quarters = (fin && fin.quarters) || [];
    var latest = quarters[0] || {};
    var head = '<div class="fin-head"><span class="fin-title">財報數據</span>' +
      (latest.period
        ? '<span class="fin-period mono">' + esc(latest.period) + "</span>" : "") +
      "</div>";

    if (!fin) {
      return '<aside class="fin-panel">' + head +
        '<p class="fin-empty">本檔暫無最新一季財報數據，季報公布後將自動更新。</p>' +
      "</aside>";
    }

    var cards = "";
    if (fin.market_cap != null) {
      cards += finCard(FIN_ICONS.cap, "市值", finNum(fin.market_cap, 0), "億",
        fin.industry ? '<span class="fin-peer">' + esc(fin.industry) + "</span>" : "");
    }
    if (latest.revenue != null) {
      cards += finCard(FIN_ICONS.rev, "單季營收", finNum(latest.revenue, 2), "億",
        finDelta("年", latest.revenue_yoy) + finDelta("季", latest.revenue_qoq));
    }
    if (latest.eps != null) {
      cards += finCard(FIN_ICONS.eps, "單季 EPS", finNum(latest.eps, 2), "元",
        finDelta("年", latest.eps_yoy));
    }

    var mini =
      finMini("毛利率", latest.gross_margin != null
        ? finNum(latest.gross_margin, 1) + "%" : null) +
      finMini("本益比", fin.pe != null
        ? finNum(fin.pe, 1) + "倍" : null, peerPeSub(fin)) +
      finMini("資本支出", latest.capex != null
        ? finNum(latest.capex, 1) + "億" : null);
    var miniBlock = mini ? '<div class="fin-mini">' + mini + "</div>" : "";
    var trend = quartersHtml(quarters);

    if (!cards && !miniBlock && !trend) {
      return '<aside class="fin-panel">' + head +
        '<p class="fin-empty">本檔暫無最新一季財報數據。</p></aside>';
    }

    return '<aside class="fin-panel">' + head +
      (cards ? '<div class="fin-cards">' + cards + "</div>" : "") + miniBlock +
      trend +
      finReportLinks(d) +
      '<p class="fin-note">單季數據 · 來源：公開財報（FinMind）／本益比與市值：TWSE、TPEx</p>' +
    "</aside>";
  }

  /* 財務報告書傳送門：連到公開資訊觀測站的電子書清單（各季合併／個體報表 PDF）。
     PDF 本身要走站方的 JS 二次請求，無法直接深連結，所以連清單頁。 */
  function finReportLinks(d) {
    if (!d || !/^\d{4}$/.test(d.code || "")) return "";
    var year = parseInt(String(d.date || "").slice(0, 4), 10) || new Date().getFullYear();
    var roc = year - 1911;
    var url = "https://doc.twse.com.tw/server-java/t57sb01?step=1&colorchg=1&co_id=" +
      encodeURIComponent(d.code) + "&year=" + roc + "&seamon=&mtype=A&";
    return '<div class="fin-links">' +
      '<a class="fin-link" href="' + url + '" target="_blank" rel="noopener">' +
        '財務報告書（各季三表 PDF）<span class="arrow">&nearr;</span></a>' +
      '<span class="fin-links-note">公開資訊觀測站 · ' + (roc) + ' 年度</span>' +
    "</div>";
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
        var hasSegs = d.transcript_segments && d.transcript_segments.length;
        var tHtml = hasSegs
          ? segmentsHtml(d.transcript_segments)
          : transcriptHtml(d.transcript);
        var note = (hasSegs ? "已分段 · " : "") + "約 " +
          d.transcript.length.toLocaleString("zh-Hant-TW") + " 字 · 語音辨識產生";
        transcriptSection =
          '<section class="doc-section">' +
            "<h2>完整逐字稿" +
              '<span class="h2-note">' + note + "</span>" +
            "</h2>" +
            '<div class="transcript' + (hasSegs ? " is-segmented" : "") +
              '" id="transcript">' + tHtml + "</div>" +
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
        '<div class="doc-side">' +
          businessHtml(d.business, isUs ? "us" : "") + financialsHtml(d.financials, d) +
        "</div>" +
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
