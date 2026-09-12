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

  /* 與上次法說會比較：逐項對照 + 方向標記 + 下次追蹤 */
  function compareHtml(c) {
    if (!c || !c.items || !c.items.length) return "";
    var DIR = { up: ["↑", "上修／轉佳"], down: ["↓", "下修／轉弱"], same: ["＝", "維持"],
                "new": ["新", "新出現"], gone: ["－", "不再提及"] };
    var rows = c.items.map(function (i) {
      var dd = DIR[i.direction] || DIR.same;
      return '<div class="cmp-row cmp-' + esc(i.direction) + '">' +
        '<span class="cmp-dir" title="' + dd[1] + '">' + dd[0] + "</span>" +
        '<span class="cmp-topic">' + esc(i.topic) + "</span>" +
        '<span class="cmp-before">' + (i.before ? esc(i.before) : '<em>上次未提</em>') + "</span>" +
        '<span class="cmp-arrow">→</span>' +
        '<span class="cmp-after">' + esc(i.after) + "</span>" +
      "</div>";
    }).join("");
    var watch = (c.watch || []).length
      ? '<p class="cmp-watch"><span class="cmp-watch-label">下次追蹤</span>' +
        c.watch.map(esc).join("　·　") + "</p>" : "";
    var prevLink = c.prev_id
      ? '<a href="detail.html?id=' + encodeURIComponent(c.prev_id) + '">' + esc(c.prev_date) + "</a>"
      : esc(c.prev_date);
    return '<section class="doc-section cmp">' +
      "<h2>與上次法說會比較" + '<span class="h2-note">上次：' + prevLink + " · AI 對照 · 僅供參考</span></h2>" +
      (c.verdict ? '<p class="lead">' + esc(c.verdict) + "</p>" : "") +
      '<div class="cmp-list">' +
        '<div class="cmp-row cmp-head"><span></span><span>議題</span><span>上次</span><span></span><span>這次</span></div>' +
        rows + "</div>" + watch +
    "</section>";
  }

  /* ── 右欄財報／業務面板：見 assets/panels.js（與公司頁、族群頁共用） ── */
  var P = window.IRPanels;
  var financialsHtml = P.financialsHtml;
  var businessHtml = P.businessHtml;

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
          (isUs ? "" : compareHtml(d.compare)) +
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
