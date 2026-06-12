/* 詳細頁：依 query string 的 id 載入 detail/{id}.json，
   渲染重點摘要、AI 觀點（含展望）、可折疊逐字稿與外部連結。 */
(function () {
  "use strict";

  var elDoc = document.getElementById("doc");

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

  function renderDoc(d) {
    document.title = d.company + " " + d.date + " 法說會｜法說會觀測站";

    var eyebrowBits = [];
    if (d.code) eyebrowBits.push(esc(d.code));
    if (d.date) eyebrowBits.push(esc(d.date));
    eyebrowBits.push("法人說明會");

    var transcriptSection;
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

    elDoc.innerHTML =
      '<header class="doc-head">' +
        '<p class="doc-eyebrow">' + eyebrowBits.join('<span class="sep">·</span>') + "</p>" +
        "<h1>" + esc(d.company) + "</h1>" +
        linksHtml(d) +
      "</header>" +
      '<article>' +
        '<section class="doc-section">' +
          "<h2>重點摘要</h2>" + summaryHtml(d.summary) +
        "</section>" +
        '<section class="doc-section">' +
          "<h2>AI 觀點與未來方向<span class=\"h2-note\">AI 彙整 · 僅供參考</span></h2>" +
          aiViewHtml(d.ai_view) +
        "</section>" +
        transcriptSection +
      "</article>" +
      '<nav class="doc-foot-nav"><a href="index.html">&larr; 返回清單</a></nav>';

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
  }

  var id = new URLSearchParams(location.search).get("id") || "";
  if (!/^[\w一-鿿-]{1,80}$/.test(id)) {
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
