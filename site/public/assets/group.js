/* 族群落地頁：依 ?tag=AI伺服器（業務族群）或 ?industry=半導體（產業別）載入 tags.json，
   列出成員公司：最新法說會一句話、最新一季營收年增／EPS／毛利率、本益比，
   抬頭給族群本益比中位數。每一列連到公司頁。 */
(function () {
  "use strict";

  var P = window.IRPanels;
  var esc = P.esc;
  var finNum = P.finNum;
  var elDoc = document.getElementById("doc");

  function fail(msg, hint) {
    elDoc.innerHTML =
      '<div class="empty" style="margin-top:48px"><p>' + esc(msg) + "</p>" +
      '<p class="empty-hint">' + esc(hint) + "</p></div>" +
      '<nav class="doc-foot-nav"><a href="index.html">&larr; 返回清單</a></nav>';
  }

  function pct(v) {
    if (v == null) return '<span class="g-na">—</span>';
    var cls = v >= 0 ? "up" : "down";
    return '<span class="fin-delta ' + cls + '">' + (v >= 0 ? "+" : "") + finNum(v, 1) + "%</span>";
  }

  function memberRow(m, groupPe) {
    var peTxt = "—";
    if (m.pe != null) {
      peTxt = finNum(m.pe, 1) + "×";
      if (groupPe) {
        var r = m.pe / groupPe;
        // 與詳細頁同語意：高於族群＝紅（up）、低於＝綠（down）
        peTxt += ' <span class="g-sub ' + (r >= 1 ? "up" : "down") + '">' +
          (r >= 1 ? "＋" : "－") + finNum(Math.abs(r - 1) * 100, 0) + "%</span>";
      }
    }
    return '<a class="g-row" href="company.html?code=' + encodeURIComponent(m.code) + '">' +
      '<span class="g-code mono">' + esc(m.code) + "</span>" +
      '<span class="g-main">' +
        '<span class="g-company">' + esc(m.company) + "</span>" +
        (m.summary ? '<span class="g-summary">' + esc(m.summary) + "</span>" : "") +
      "</span>" +
      '<span class="g-num"><span class="g-label">營收 YoY</span>' + pct(m.rev_yoy) + "</span>" +
      '<span class="g-num"><span class="g-label">EPS</span>' +
        (m.eps != null ? finNum(m.eps, 2) : '<span class="g-na">—</span>') + "</span>" +
      '<span class="g-num"><span class="g-label">毛利率</span>' +
        (m.gross_margin != null ? finNum(m.gross_margin, 1) + "%" : '<span class="g-na">—</span>') + "</span>" +
      '<span class="g-num"><span class="g-label">本益比</span>' + peTxt + "</span>" +
    "</a>";
  }

  function render(kind, group) {
    var name = group.name;
    document.title = name + (kind === "tag" ? " 族群" : " 產業") + "｜法說會觀測站";
    var ms = group.members || [];
    var withYoy = ms.filter(function (m) { return m.rev_yoy != null; });
    var growing = withYoy.filter(function (m) { return m.rev_yoy > 0; }).length;
    var period = (ms.find(function (m) { return m.period; }) || {}).period || "";

    var stats =
      '<div class="g-stats">' +
        '<div class="g-stat"><span class="g-stat-v">' + ms.length + "</span><span class=\"g-stat-l\">家公司</span></div>" +
        '<div class="g-stat"><span class="g-stat-v">' + (group.pe_median != null ? finNum(group.pe_median, 1) + "×" : "—") +
          '</span><span class="g-stat-l">本益比中位數</span></div>' +
        (withYoy.length ? '<div class="g-stat"><span class="g-stat-v">' + growing + "/" + withYoy.length +
          '</span><span class="g-stat-l">最新季營收年增為正' + (period ? "（" + esc(period) + "）" : "") + "</span></div>" : "") +
      "</div>";

    var listHead =
      '<div class="g-row g-head">' +
        '<span class="g-code">代號</span><span class="g-main">公司 · 最新法說會一句話</span>' +
        '<span class="g-num">營收 YoY</span><span class="g-num">EPS</span>' +
        '<span class="g-num">毛利率</span><span class="g-num">本益比 vs 族群</span>' +
      "</div>";

    elDoc.innerHTML =
      '<header class="doc-head">' +
        '<p class="doc-eyebrow">' + (kind === "tag" ? "業務族群" : "產業別") +
          '<span class="sep">·</span><a href="index.html?' + kind + "=" + encodeURIComponent(name) +
          '&mode=company">在首頁篩選這個族群</a></p>' +
        "<h1>" + esc(name) + "</h1>" + stats +
      "</header>" +
      '<section class="doc-section"><h2>成員公司' +
        '<span class="h2-note">依市值排序 · 數字為最新一季單季</span></h2>' +
        '<div class="g-list">' + listHead + ms.map(function (m) { return memberRow(m, group.pe_median); }).join("") + "</div>" +
      "</section>" +
      '<p class="fin-note">族群標籤由 AI 讀取法說會簡報整理；本益比取自 TWSE／TPEx，中位數為本族群成員。</p>' +
      '<nav class="doc-foot-nav"><a href="index.html">&larr; 返回清單</a></nav>';
  }

  var mt = /[?&]tag=([^&]+)/.exec(location.search);
  var mi = /[?&]industry=([^&]+)/.exec(location.search);
  if (!mt && !mi) { fail("缺少族群名稱", "網址格式：group.html?tag=AI伺服器"); return; }
  var kind = mt ? "tag" : "industry";
  var want = decodeURIComponent((mt || mi)[1]);

  // 每個族群一個小檔（build_data 的 write_tags_index 產出）；檔名把「/」換成「_」
  var slug = want.replace(/\//g, "_");
  fetch("group/" + kind + "_" + encodeURIComponent(slug) + ".json")
    .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then(function (g) { render(kind, g); })
    .catch(function () { fail("找不到這個族群", want); });
})();
