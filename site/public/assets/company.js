/* 公司頁：依 ?code=2330 載入 company/{code}.json；美股 ?code=ADBE 載入 company/us-ADBE.json。
   版面：抬頭（名稱／代號／產業／族群）→ 左欄「歷次法說會」時間軸，右欄業務項目＋財報面板
   （右欄直接重用 panels.js，與法說會頁一致）。 */
(function () {
  "use strict";

  var P = window.IRPanels;
  var esc = P.esc;
  var elDoc = document.getElementById("doc");

  function fail(msg, hint) {
    elDoc.innerHTML =
      '<div class="empty" style="margin-top:48px"><p>' + esc(msg) + "</p>" +
      '<p class="empty-hint">' + esc(hint) + "</p></div>" +
      '<nav class="doc-foot-nav"><a href="index.html">&larr; 返回清單</a></nav>';
  }

  function confRow(c, isUs) {
    var assets = "";
    if (c.has_transcript) {
      assets += '<span class="tag tag-on">逐字稿 ' +
        Math.round(c.transcript_chars / 1000) + "k</span>";
    }
    if (c.video_url) assets += '<span class="tag">影音</span>';
    if (c.pdf_url) assets += '<span class="tag">簡報</span>';
    if (isUs && c.beat_or_miss) {   // 「超預期：EPS…」「不如預期，…」只取前面的結論
      assets += '<span class="tag">' + esc(c.beat_or_miss.split(/[，：,:（(]/)[0].trim()) + "</span>";
    }
    var title = c.title || (esc(c.date.slice(0, 4)) + (isUs ? " 年財報" : " 年法說會"));
    return '<a class="row" href="detail.html?id=' + encodeURIComponent(c.id) + '">' +
      '<span class="row-code">' + esc(c.date.slice(5).replace("-", "/")) + "</span>" +
      '<span class="row-main">' +
        '<span class="row-company">' + esc(title) + "</span>" +
        (c.summary ? '<span class="row-oneliner">' + esc(c.summary) + "</span>" : "") +
      "</span>" +
      '<span class="row-assets">' + assets + "</span>" +
    "</a>";
  }

  function render(d) {
    var fin = d.financials || {};
    var biz = d.business || {};
    var isUs = d.market === "us";
    var cat = isUs ? "us" : "tw";
    var catQ = isUs ? "cat=us&" : "";
    document.title = d.company + "（" + d.code + "）｜法說會觀測站";

    var eyebrow = [esc(d.code)];
    if (isUs && d.company_en) eyebrow.push(esc(d.company_en));
    if (fin.industry) {
      eyebrow.push('<a href="index.html?' + catQ + 'industry=' + encodeURIComponent(fin.industry) +
        '&mode=company">' + esc(fin.industry) + "</a>");
    }
    // 族群：台股有獨立族群頁；美股走首頁「依公司」篩選（同樣是族群落地）
    var tags = (biz.tags || []).map(function (t) {
      var href = isUs ? "index.html?cat=us&tag=" + encodeURIComponent(t) + "&mode=company"
                      : "group.html?tag=" + encodeURIComponent(t);
      return '<a class="biz-tag" href="' + href + '">' + esc(t) + "</a>";
    }).join("");

    var confs = d.conferences || [];
    var withTr = confs.filter(function (c) { return c.has_transcript; }).length;
    var confsHtml = confs.length
      ? '<section class="ledger company-ledger">' +
          confs.map(function (c) { return confRow(c, isUs); }).join("") + "</section>"
      : '<p class="prose">尚無收錄的' + (isUs ? "財報" : "法說會") + "。</p>";

    // 財報面板需要 code/date 組財務報告書連結
    var finCtx = { code: d.code, date: confs.length ? confs[0].date : "" };

    elDoc.innerHTML =
      '<header class="doc-head">' +
        '<p class="doc-eyebrow">' + eyebrow.join('<span class="sep">·</span>') +
          '<span class="sep">·</span>公司頁</p>' +
        "<h1>" + esc(d.company) + "</h1>" +
        (tags ? '<div class="biz-tags company-tags"><span class="biz-tags-label">族群</span>' +
          tags + "</div>" : "") +
      "</header>" +
      '<div class="doc-body">' +
        "<article>" +
          '<section class="doc-section">' +
            "<h2>" + (isUs ? "歷次財報" : "歷次法說會") +
              '<span class="h2-note">' + confs.length + " 場" +
                (isUs ? "" : " · 逐字稿 " + withTr + " 場") + "</span>" +
            "</h2>" + confsHtml +
          "</section>" +
        "</article>" +
        '<div class="doc-side">' +
          P.businessHtml(biz, isUs ? "us" : "") + P.financialsHtml(fin, finCtx) +
        "</div>" +
      "</div>" +
      '<nav class="doc-foot-nav"><a href="index.html' + (isUs ? "?cat=us" : "") +
        '">&larr; 返回清單</a></nav>';
  }

  // 台股四位數代號 → company/2330.json；美股代號（字母，可含 . -）→ company/us-ADBE.json
  var m = /[?&]code=([A-Za-z0-9.\-]{1,10})(?=&|$)/.exec(location.search);
  if (!m) {
    fail("缺少公司代號", "網址格式：company.html?code=2330 或 ?code=ADBE");
    return;
  }
  var code = decodeURIComponent(m[1]);
  var file = /^\d{4}$/.test(code) ? code : "us-" + code.toUpperCase();
  fetch("company/" + file + ".json")
    .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then(render)
    .catch(function () { fail("找不到這家公司", "可能尚未收錄任何法說會，或代號有誤。"); });
})();
