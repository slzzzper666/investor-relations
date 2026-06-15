/* 首頁：「列表」與「行事曆」雙檢視，記住使用者選擇（localStorage）。
   列表：載入 list.json，依日期分組渲染帳冊，支援即時搜尋與月份篩選。
   行事曆：合併 list.json（已收錄場次）與 upcoming.json（MOPS 公告場次，含歷史與未來），
   月曆格狀呈現，格內依市值由大到小排列；點擊日期開啟當日完整名單，
   已收錄場次可進入詳細頁，未收錄場次以較淡樣式顯示。 */
(function () {
  "use strict";

  var WEEKDAYS = ["週日", "週一", "週二", "週三", "週四", "週五", "週六"];
  var WEEKDAY_HEADS = ["日", "一", "二", "三", "四", "五", "六"];
  /* 每格最多顯示檔數：實排比較 3 與 5 後取 3——
     5 檔時格高需 130px 以上、單月超出一個視窗高度且多數日子留白，
     3 檔可讓整月入鏡、密度均勻，超出部分以 +N 引導點開當日視窗。 */
  var MAX_PER_CELL = 3;
  var VIEW_KEY = "ir-view";

  var elTape = document.getElementById("tape-meta");
  var elLedger = document.getElementById("ledger");
  var elEmpty = document.getElementById("empty");
  var elError = document.getElementById("load-error");
  var elCount = document.getElementById("count");
  var elQ = document.getElementById("q");
  var elMonth = document.getElementById("month");
  var elFooterMeta = document.getElementById("footer-meta");
  var elControls = document.getElementById("controls");
  var elTabList = document.getElementById("tab-list");
  var elTabCalendar = document.getElementById("tab-calendar");
  var elCalendar = document.getElementById("calendar");
  var elCalMonths = document.getElementById("cal-months");
  var elCalPrev = document.getElementById("cal-prev");
  var elCalNext = document.getElementById("cal-next");
  var elModal = document.getElementById("day-modal");
  var elModalBackdrop = document.getElementById("modal-backdrop");
  var elModalTitle = document.getElementById("modal-title");
  var elModalMeta = document.getElementById("modal-meta");
  var elModalList = document.getElementById("modal-list");
  var elModalHint = document.getElementById("modal-hint");
  var elModalClose = document.getElementById("modal-close");

  var allItems = [];
  var upcomingItems = [];
  var eventsByDate = {};   // "YYYY-MM-DD" -> [{code, company, mcap, id, time}]
  var dataReady = false;
  var calBuilt = false;
  var calFirst = null;     // 已渲染的最早月份 {y, m}
  var calLast = null;      // 已渲染的最晚月份 {y, m}
  var currentView = "list";
  var currentCat = "tw";
  var lastFocus = null;

  /* 三大市場分類：台股（既有）、美股、總經。
     美股版面同台股；總經多一個「未來」檢視。資料尚未建置時顯示佔位。 */
  var CATS = {
    tw:    { list: "list.json",       upcoming: "upcoming.json",
             views: ["list", "calendar"], label: "台股" },
    us:    { list: "us_list.json",    upcoming: "us_upcoming.json",
             views: ["list", "calendar"], label: "美股" },
    macro: { future: "macro_future.json", calendar: "macro_upcoming.json",
             history: "macro_history.json", monthly: "macro_monthly.json",
             views: ["future", "calendar"], label: "總經" }
  };
  var CAT_KEY = "ir-cat";
  var dataCache = {};   // cat -> { data, up }
  var macroData = null;    // 總經未來頁資料（macro_future.json）
  var macroEvents = [];    // 總經行事曆事件（macro_upcoming.json ＋ macro_history.json）
  var macroMonthly = [];   // 總經每月回顧（macro_monthly.json）

  var elCatTw = document.getElementById("cat-tw");
  var elCatUs = document.getElementById("cat-us");
  var elCatMacro = document.getElementById("cat-macro");
  var elViewTabs = document.getElementById("view-tabs");
  var elTabFuture = document.getElementById("tab-future");
  var elFuture = document.getElementById("future");
  var elCatPending = document.getElementById("cat-pending");
  var elCatPendingMsg = document.getElementById("cat-pending-msg");

  function pad2(n) { return (n < 10 ? "0" : "") + n; }

  var todayStr = (function () {
    var d = new Date();
    return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
  })();

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /* ---------- 列表檢視（既有功能） ---------- */

  function oneLiner(summary) {
    var line = String(summary || "").split("\n")[0].trim();
    return line.replace(/^•\s*/, "");
  }

  function monthLabel(ym) {
    var p = ym.split("-");
    return p[0] + " 年 " + parseInt(p[1], 10) + " 月";
  }

  function dayMeta(dateStr, n) {
    var d = new Date(dateStr + "T00:00:00");
    var wd = isNaN(d) ? "" : WEEKDAYS[d.getDay()];
    var year = dateStr.slice(0, 4);
    return year + " · " + wd + " · " + n + " 場";
  }

  function rowHtml(it) {
    var tags = [];
    if (it.has_transcript) tags.push('<span class="tag tag-strong">逐字稿</span>');
    if (it.pdf_url) tags.push('<span class="tag">簡報</span>');
    if (it.video_url) tags.push('<span class="tag">影音</span>');
    return (
      '<a class="row" href="detail.html?id=' + encodeURIComponent(it.id) + '">' +
        '<span class="row-code">' + (esc(it.code) || "—") + "</span>" +
        '<span class="row-main">' +
          '<span class="row-company">' + esc(it.company) + "</span>" +
          '<span class="row-oneliner">' + esc(oneLiner(it.summary)) + "</span>" +
        "</span>" +
        '<span class="row-assets">' + tags.join("") + "</span>" +
      "</a>"
    );
  }

  function render(items) {
    if (!items.length) {
      elLedger.innerHTML = "";
      elEmpty.hidden = false;
      elCount.textContent = "0 / " + allItems.length + " 場";
      return;
    }
    elEmpty.hidden = true;

    // 依日期分組（資料已新→舊排序）
    var groups = [];
    var byDate = {};
    items.forEach(function (it) {
      var key = it.date || "未標日期";
      if (!byDate[key]) {
        byDate[key] = [];
        groups.push(key);
      }
      byDate[key].push(it);
    });

    var html = groups.map(function (date) {
      var rows = byDate[date];
      var mmdd = /^\d{4}-\d{2}-\d{2}$/.test(date)
        ? date.slice(5).replace("-", ".")
        : date;
      var meta = /^\d{4}-\d{2}-\d{2}$/.test(date)
        ? dayMeta(date, rows.length)
        : rows.length + " 場";
      return (
        '<section class="day">' +
          '<div class="day-date">' +
            '<span class="day-num">' + esc(mmdd) + "</span>" +
            '<span class="day-meta">' + esc(meta) + "</span>" +
          "</div>" +
          '<div class="day-rows">' + rows.map(rowHtml).join("") + "</div>" +
        "</section>"
      );
    }).join("");

    elLedger.innerHTML = html;
    elCount.textContent = items.length + " / " + allItems.length + " 場";
  }

  function applyFilters() {
    var q = elQ.value.trim().toLowerCase();
    var month = elMonth.value;
    var items = allItems.filter(function (it) {
      if (month && it.date.slice(0, 7) !== month) return false;
      if (!q) return true;
      return it.company.toLowerCase().indexOf(q) !== -1 ||
             (it.code && it.code.indexOf(q) !== -1);
    });
    render(items);
  }

  /* ---------- 行事曆資料 ---------- */

  /* 總經行事曆：把 macro_upcoming.json 的經濟數據事件放進月曆 */
  function buildMacroEvents() {
    macroEvents.forEach(function (e) {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(e.date)) return;
      if (!eventsByDate[e.date]) eventsByDate[e.date] = [];
      eventsByDate[e.date].push({
        kind: "macro", date: e.date, time: e.time || "",
        country: e.country || "", name: e.name || "",
        previous: e.previous || "", forecast: e.forecast || "",
        actual: e.actual || "", interpretation: e.interpretation || "",
        impact: e.impact || 0,
        company: (e.country ? e.country + " " : "") + (e.name || ""),
        code: "", id: "", mcap: 0
      });
    });
    // 每日依重要度高→低，再依公布時間
    Object.keys(eventsByDate).forEach(function (d) {
      eventsByDate[d].sort(function (a, b) {
        if ((b.impact || 0) !== (a.impact || 0)) return (b.impact || 0) - (a.impact || 0);
        return (a.time || "") < (b.time || "") ? -1 : 1;
      });
    });
  }

  function buildEvents() {
    eventsByDate = {};
    if (currentCat === "macro") { buildMacroEvents(); return; }
    var seen = {};

    function add(evt) {
      if (!eventsByDate[evt.date]) eventsByDate[evt.date] = [];
      eventsByDate[evt.date].push(evt);
    }

    allItems.forEach(function (it) {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(it.date)) return;
      add({
        date: it.date, code: it.code, company: it.company,
        mcap: it.market_cap || 0, id: it.id, time: ""
      });
      seen[it.code + "@" + it.date] = true;
    });
    upcomingItems.forEach(function (u) {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(u.date)) return;
      if (seen[u.code + "@" + u.date]) return;
      add({
        date: u.date, code: u.code, company: u.name,
        mcap: u.market_cap || 0, id: "", time: u.time || ""
      });
    });

    // 每日依市值由大到小（查無市值者置後），同市值依代號
    Object.keys(eventsByDate).forEach(function (d) {
      eventsByDate[d].sort(function (a, b) {
        if (b.mcap !== a.mcap) return b.mcap - a.mcap;
        return a.code < b.code ? -1 : 1;
      });
    });
  }

  /* ---------- 行事曆渲染 ---------- */

  function calUnit() { return currentCat === "macro" ? "筆" : "場"; }
  function calNoun() { return currentCat === "macro" ? "數據" : "法說會"; }

  function monthEventCount(y, m) {
    var prefix = y + "-" + pad2(m);
    var n = 0;
    Object.keys(eventsByDate).forEach(function (d) {
      if (d.slice(0, 7) === prefix) n += eventsByDate[d].length;
    });
    return n;
  }

  function monthHtml(y, m) {
    var startWd = new Date(y, m - 1, 1).getDay();
    var days = new Date(y, m, 0).getDate();
    var cells = [];
    var i, d;

    for (i = 0; i < startWd; i++) {
      cells.push('<span class="cal-cell is-blank" aria-hidden="true"></span>');
    }
    for (d = 1; d <= days; d++) {
      var dateStr = y + "-" + pad2(m) + "-" + pad2(d);
      var evts = eventsByDate[dateStr] || [];
      var cls = "cal-cell" + (dateStr === todayStr ? " is-today" : "");
      var inner = '<span class="cal-day-num">' + d + "</span>";
      evts.slice(0, MAX_PER_CELL).forEach(function (e) {
        if (e.kind === "macro") {
          inner += '<span class="cal-evt cal-macro imp' + (e.impact || 1) + '">' +
            esc(e.company) + "</span>";
        } else {
          inner += '<span class="cal-evt' + (e.id ? "" : " is-pending") + '">' +
            esc(e.company) + "</span>";
        }
      });
      if (evts.length > MAX_PER_CELL) {
        inner += '<span class="cal-overflow">+' +
          (evts.length - MAX_PER_CELL) + "</span>";
      }
      if (evts.length) {
        cells.push(
          '<button type="button" class="' + cls + '" data-date="' + dateStr +
          '" aria-label="' + m + " 月 " + d + " 日，" + evts.length + " " +
          calUnit() + calNoun() + '" aria-haspopup="dialog">' + inner + "</button>");
      } else {
        cells.push('<span class="' + cls + '">' + inner + "</span>");
      }
    }
    while (cells.length % 7 !== 0) {
      cells.push('<span class="cal-cell is-blank" aria-hidden="true"></span>');
    }

    var n = monthEventCount(y, m);
    return (
      '<section class="cal-month">' +
        '<header class="cal-month-head">' +
          "<h2>" + y + " 年 " + m + " 月</h2>" +
          '<span class="cal-month-meta mono">' +
            (n ? n + " " + calUnit() : "無" + calNoun()) + "</span>" +
        "</header>" +
        '<div class="cal-weekdays" aria-hidden="true">' +
          WEEKDAY_HEADS.map(function (w) { return "<span>" + w + "</span>"; }).join("") +
        "</div>" +
        '<div class="cal-grid">' + cells.join("") + "</div>" +
      "</section>"
    );
  }

  function prevOf(ym) { return ym.m === 1 ? { y: ym.y - 1, m: 12 } : { y: ym.y, m: ym.m - 1 }; }
  function nextOf(ym) { return ym.m === 12 ? { y: ym.y + 1, m: 1 } : { y: ym.y, m: ym.m + 1 }; }

  function buildCalendar() {
    // 初始範圍：本月起，到最後一個有場次的月份（至少含本月）
    var now = new Date();
    calFirst = { y: now.getFullYear(), m: now.getMonth() + 1 };
    var maxYm = calFirst.y + "-" + pad2(calFirst.m);
    Object.keys(eventsByDate).forEach(function (d) {
      var ym = d.slice(0, 7);
      if (ym > maxYm) maxYm = ym;
    });
    calLast = { y: parseInt(maxYm.slice(0, 4), 10), m: parseInt(maxYm.slice(5, 7), 10) };

    var html = "";
    var cur = { y: calFirst.y, m: calFirst.m };
    while (cur.y < calLast.y || (cur.y === calLast.y && cur.m <= calLast.m)) {
      html += monthHtml(cur.y, cur.m);
      cur = nextOf(cur);
    }
    elCalMonths.innerHTML = html;
    calBuilt = true;
  }

  /* ---------- 當日場次視窗 ---------- */

  function modalRowHtml(e) {
    if (e.kind === "macro") {
      var vals = [];
      if (e.actual) vals.push('<b class="m-actual">實際 ' + esc(e.actual) + "</b>");
      if (e.forecast) vals.push("預期 " + esc(e.forecast));
      if (e.previous) vals.push("前值 " + esc(e.previous));
      var interp = e.interpretation
        ? '<span class="m-interp">' + esc(e.interpretation) + "</span>" : "";
      return '<div class="m-row m-macro imp' + (e.impact || 1) +
        (e.actual ? " is-published" : "") + '">' +
        '<span class="m-country">' + esc(e.country) + "</span>" +
        '<span class="m-name">' + esc(e.name) + "</span>" +
        '<span class="m-time mono">' + esc(e.time) + "</span>" +
        '<span class="m-fc mono">' + vals.join("　") + "</span>" +
        interp +
      "</div>";
    }
    var cap = e.mcap > 0
      ? e.mcap.toLocaleString("zh-Hant-TW") + " 億"
      : "";
    var inner =
      '<span class="m-code">' + (esc(e.code) || "—") + "</span>" +
      '<span class="m-name">' + esc(e.company) + "</span>" +
      '<span class="m-time">' + esc(e.id ? "" : e.time) + "</span>" +
      '<span class="m-cap">' + cap + "</span>";
    if (e.id) {
      return '<a class="m-row" href="detail.html?id=' +
        encodeURIComponent(e.id) + '">' + inner + "</a>";
    }
    return '<div class="m-row is-pending">' + inner + "</div>";
  }

  function openModal(dateStr) {
    var evts = eventsByDate[dateStr] || [];
    if (!evts.length) return;
    var d = new Date(dateStr + "T00:00:00");
    elModalTitle.textContent = (d.getMonth() + 1) + " 月 " + d.getDate() + " 日";
    elModalMeta.textContent = dateStr + " · " + WEEKDAYS[d.getDay()] + " · " +
      evts.length + " " + calUnit();
    elModalList.innerHTML = evts.map(modalRowHtml).join("");
    elModalHint.hidden = currentCat === "macro" ||
      !evts.some(function (e) { return !e.id; });
    lastFocus = document.activeElement;
    elModal.hidden = false;
    document.body.classList.add("modal-open");
    elModalClose.focus();
  }

  function closeModal() {
    if (elModal.hidden) return;
    elModal.hidden = true;
    document.body.classList.remove("modal-open");
    if (lastFocus && lastFocus.focus) lastFocus.focus();
    lastFocus = null;
  }

  /* ---------- 檢視切換 ---------- */

  /* 縮圖：一年走勢折線；上漲紅、下跌綠（沿用台股漲跌色） */
  function sparkSvg(vals) {
    if (!vals || vals.length < 2) return "";
    var min = Math.min.apply(null, vals);
    var max = Math.max.apply(null, vals);
    var rng = (max - min) || 1;
    var W = 100, H = 30, pad = 2.5;
    var pts = vals.map(function (v, i) {
      var x = (i / (vals.length - 1)) * W;
      var y = pad + (1 - (v - min) / rng) * (H - 2 * pad);
      return x.toFixed(1) + "," + y.toFixed(1);
    }).join(" ");
    var up = vals[vals.length - 1] >= vals[0];
    return '<svg class="mx-spark ' + (up ? "up" : "down") +
      '" viewBox="0 0 100 30" preserveAspectRatio="none" aria-hidden="true">' +
      '<polyline points="' + pts + '"/></svg>';
  }

  function deltaChips(deltas) {
    return (deltas || []).map(function (d) {
      var cls = d.dir > 0 ? "up" : (d.dir < 0 ? "down" : "flat");
      return '<span class="mx-chip ' + cls + '">' +
        esc(d.label) + " " + esc(d.text) + "</span>";
    }).join("");
  }

  function mxCard(it) {
    return '<div class="mx-card">' +
      '<div class="mx-card-top">' +
        '<span class="mx-name">' + esc(it.name) + "</span>" +
        '<span class="mx-sym mono">' + esc(it.symbol) + "</span>" +
      "</div>" +
      '<div class="mx-val">' + esc(it.value) +
        (it.unit ? '<span class="mx-unit">' + esc(it.unit) + "</span>" : "") +
      "</div>" +
      sparkSvg(it.spark) +
      '<div class="mx-deltas">' + deltaChips(it.deltas) + "</div>" +
    "</div>";
  }

  function aiBlock(text) {
    if (!text) return "";
    return '<p class="mx-analysis">' + esc(text) + "</p>";
  }

  function renderFuture(d) {
    d = d || macroData;
    if (!d) return;
    var ai = d.ai || {};

    var html = '<div class="mx">';
    html += '<p class="mx-meta mono">數據更新 ' + esc(d.generated_at || "—") +
      "（台北）" + (d.ai_asof ? " · AI 觀察 " + esc(d.ai_asof) : "") + "</p>";

    if (ai.overall) {
      html += '<p class="mx-overall">' + esc(ai.overall) + "</p>";
    }

    // 股市預期（重點，醒目卡片置前）
    var expect = ai.equity && ai.equity.expectation;
    if (expect) {
      html += '<div class="mx-expect">' +
        '<div class="mx-expect-label">股市預期</div>' +
        '<p>' + esc(expect) + "</p>" +
      "</div>";
    }

    (d.groups || []).forEach(function (g) {
      html += '<section class="mx-group">' +
        '<h2 class="mx-group-title">' + esc(g.label) + "</h2>" +
        '<div class="mx-cards">' +
          (g.items || []).map(mxCard).join("") +
        "</div>" +
        aiBlock(ai[g.key] && ai[g.key].analysis) +
      "</section>";
    });

    if (d.econ && d.econ.length) {
      html += '<section class="mx-group">' +
        '<h2 class="mx-group-title">經濟數據</h2>' +
        '<div class="mx-econ">' +
          d.econ.map(function (e) {
            return '<div class="mx-econ-item">' +
              '<div class="mx-econ-name">' + esc(e.name) + "</div>" +
              '<div class="mx-econ-val">' + esc(String(e.value)) +
                esc(e.unit || "") + "</div>" +
              '<div class="mx-econ-asof mono">' + esc(e.asof || "") + "</div>" +
            "</div>";
          }).join("") +
        "</div>" +
      "</section>";
    }

    // 每月回顧（1~6 月，新到舊）
    if (macroMonthly && macroMonthly.length) {
      html += '<section class="mx-group mx-monthly">' +
        '<h2 class="mx-group-title">每月回顧</h2>';
      macroMonthly.slice().reverse().forEach(function (mo) {
        var mlabel = mo.month ? mo.month.slice(0, 4) + " 年 " +
          parseInt(mo.month.slice(5, 7), 10) + " 月" : "";
        html += '<details class="mx-mo">' +
          '<summary><span class="mx-mo-date mono">' + esc(mlabel) + "</span>" +
            '<span class="mx-mo-sum">' + esc(mo.summary || "") + "</span></summary>" +
          '<div class="mx-mo-body">' +
            (mo.equity ? '<p><b>股市</b>　' + esc(mo.equity) + "</p>" : "") +
            (mo.bond ? '<p><b>債市</b>　' + esc(mo.bond) + "</p>" : "") +
            (mo.realestate ? '<p><b>房市</b>　' + esc(mo.realestate) + "</p>" : "") +
          "</div>" +
        "</details>";
      });
      html += "</section>";
    }

    html += '<p class="mx-note">資料來源：Yahoo Finance（市場數據）' +
      (d.econ && d.econ.length ? "、美國聯準會 FRED（經濟數據）" : "") +
      "。市場數據每日自動更新，AI 觀察由 Claude 定期彙整。" +
      "本頁內容僅供研究參考，不構成投資建議。</p>";
    html += "</div>";

    elFuture.innerHTML = html;
  }

  function applyView() {
    if (!dataReady) return;   // 佔位狀態由 showCategoryPending 控制
    var isList = currentView === "list";
    var isCal = currentView === "calendar";
    var isFuture = currentView === "future";

    elControls.hidden = !isList;
    elLedger.hidden = !isList;
    elCalendar.hidden = !isCal;
    elFuture.hidden = !isFuture;
    elCatPending.hidden = true;

    if (isList) {
      applyFilters();
    } else {
      elEmpty.hidden = true;
    }

    elTabList.classList.toggle("is-active", isList);
    elTabList.setAttribute("aria-selected", String(isList));
    elTabCalendar.classList.toggle("is-active", isCal);
    elTabCalendar.setAttribute("aria-selected", String(isCal));
    elTabFuture.classList.toggle("is-active", isFuture);
    elTabFuture.setAttribute("aria-selected", String(isFuture));

    if (isCal && !calBuilt) buildCalendar();
    if (isFuture) renderFuture();
  }

  function setView(view) {
    currentView = view;
    try { localStorage.setItem(VIEW_KEY, view); } catch (e) { /* 無痕模式 */ }
    applyView();
  }

  /* ---------- 載入資料 ---------- */

  function fetchJson(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  function populateMonths() {
    while (elMonth.options.length > 1) elMonth.remove(1);  // 保留「全部月份」
    var seen = {};
    allItems.forEach(function (it) {
      var ym = (it.date || "").slice(0, 7);
      if (/^\d{4}-\d{2}$/.test(ym) && !seen[ym]) {
        seen[ym] = true;
        var opt = document.createElement("option");
        opt.value = ym;
        opt.textContent = monthLabel(ym);
        elMonth.appendChild(opt);
      }
    });
  }

  function applyCategoryData(bundle) {
    var data = bundle.data;
    var up = bundle.up;
    allItems = data.items || [];
    upcomingItems = (up && up.items) || [];

    elCatPending.hidden = true;
    elError.hidden = true;
    elViewTabs.hidden = false;

    elTape.textContent = "最後更新 " + data.generated_at +
      "（台北時間）· " + CATS[currentCat].label + " 收錄 " +
      allItems.length + " 場" +
      (upcomingItems.length ? " · 公告 " + upcomingItems.length + " 場" : "");
    elFooterMeta.textContent = "BUILD " + data.generated_at + " · " +
      allItems.length + " RECORDS";

    elQ.value = "";
    elMonth.value = "";
    populateMonths();
    buildEvents();
    dataReady = true;
    calBuilt = false;
    elCalMonths.innerHTML = "";
    render(allItems);
    applyView();
  }

  function showCategoryPending(cat) {
    dataReady = false;
    allItems = [];
    upcomingItems = [];
    elLedger.innerHTML = "";
    elLedger.hidden = true;
    elCalendar.hidden = true;
    elFuture.hidden = true;
    elControls.hidden = true;
    elEmpty.hidden = true;
    elError.hidden = true;
    elViewTabs.hidden = true;
    elCount.textContent = "";
    elCatPendingMsg.textContent = CATS[cat].label + " 資料建置中";
    elCatPending.hidden = false;
    elTape.textContent = CATS[cat].label + " · 即將上線";
  }

  function updateCatTabs() {
    [["tw", elCatTw], ["us", elCatUs], ["macro", elCatMacro]].forEach(function (p) {
      var on = currentCat === p[0];
      p[1].classList.toggle("is-active", on);
      p[1].setAttribute("aria-selected", String(on));
    });
    var views = CATS[currentCat].views;
    elTabList.hidden = views.indexOf("list") === -1;
    elTabCalendar.hidden = views.indexOf("calendar") === -1;
    elTabFuture.hidden = views.indexOf("future") === -1;
    // 僅單一檢視（總經只有未來頁）時，整條檢視切換列隱藏
    elViewTabs.hidden = views.length <= 1;
  }

  /* 總經：未來儀表板（future）＋ 數據行事曆（calendar），由 applyView 依檢視路由 */
  function showMacro() {
    allItems = [];
    upcomingItems = [];
    elError.hidden = true;
    elCatPending.hidden = true;
    elViewTabs.hidden = CATS.macro.views.length <= 1;
    dataReady = true;
    calBuilt = false;
    elCalMonths.innerHTML = "";
    buildEvents();   // currentCat==="macro" → 走 buildMacroEvents
    elTape.textContent = "總經 ・ 股市／債市／房市 ＋ 數據行事曆 · 更新 " +
      ((macroData && macroData.generated_at) || "");
    elFooterMeta.textContent = "MACRO " +
      ((macroData && macroData.generated_at) || "");
    elCount.textContent = "";
    applyView();
  }

  function loadMacro() {
    if (macroData) { showMacro(); return; }
    elTape.textContent = "總經 載入中";
    Promise.all([
      fetchJson(CATS.macro.future),
      fetchJson(CATS.macro.calendar).catch(function () { return { items: [] }; }),
      fetchJson(CATS.macro.history).catch(function () { return { items: [] }; }),
      fetchJson(CATS.macro.monthly).catch(function () { return { months: [] }; })
    ]).then(function (res) {
      macroData = res[0];
      macroMonthly = (res[3] && res[3].months) || [];
      var upc = (res[1] && res[1].items) || [];
      var hist = (res[2] && res[2].items) || [];
      // 歷史（已公布、含實際值）＋ 未來（待公布）合併；同日同名以未來版本為準
      var seen = {};
      upc.forEach(function (e) { seen[e.date + "|" + e.country + "|" + e.name] = true; });
      macroEvents = upc.concat(hist.filter(function (e) {
        return !seen[e.date + "|" + e.country + "|" + e.name];
      }));
      showMacro();
    }).catch(function () { showCategoryPending("macro"); });
  }

  function loadCategory(cat) {
    if (!CATS[cat]) cat = "tw";
    currentCat = cat;
    try { localStorage.setItem(CAT_KEY, cat); } catch (e) { /* 無痕模式 */ }
    updateCatTabs();
    // 檢視收斂到該分類支援的範圍（總經無「列表」、台美股無「未來」）
    if (CATS[cat].views.indexOf(currentView) === -1) {
      currentView = CATS[cat].views[0];
    }

    if (cat === "macro") { loadMacro(); return; }

    if (dataCache[cat]) { applyCategoryData(dataCache[cat]); return; }

    elTape.textContent = CATS[cat].label + " 載入中";
    Promise.all([
      fetchJson(CATS[cat].list),
      fetchJson(CATS[cat].upcoming).catch(function () { return null; })
    ]).then(function (results) {
      dataCache[cat] = { data: results[0], up: results[1] };
      applyCategoryData(dataCache[cat]);
    }).catch(function () {
      showCategoryPending(cat);
    });
  }

  /* ---------- 事件 ---------- */

  elQ.addEventListener("input", applyFilters);
  elMonth.addEventListener("change", applyFilters);

  elTabList.addEventListener("click", function () { setView("list"); });
  elTabCalendar.addEventListener("click", function () { setView("calendar"); });
  elTabFuture.addEventListener("click", function () { setView("future"); });

  elCatTw.addEventListener("click", function () { loadCategory("tw"); });
  elCatUs.addEventListener("click", function () { loadCategory("us"); });
  elCatMacro.addEventListener("click", function () { loadCategory("macro"); });

  elCalPrev.addEventListener("click", function () {
    if (!calBuilt) return;
    calFirst = prevOf(calFirst);
    var h0 = document.documentElement.scrollHeight;
    elCalMonths.insertAdjacentHTML("afterbegin", monthHtml(calFirst.y, calFirst.m));
    window.scrollBy(0, document.documentElement.scrollHeight - h0);
  });

  elCalNext.addEventListener("click", function () {
    if (!calBuilt) return;
    calLast = nextOf(calLast);
    elCalMonths.insertAdjacentHTML("beforeend", monthHtml(calLast.y, calLast.m));
  });

  elCalMonths.addEventListener("click", function (ev) {
    var btn = ev.target.closest ? ev.target.closest("button.cal-cell") : null;
    if (btn && btn.getAttribute("data-date")) {
      openModal(btn.getAttribute("data-date"));
    }
  });

  elModalClose.addEventListener("click", closeModal);
  elModalBackdrop.addEventListener("click", closeModal);
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") closeModal();
  });

  // 還原上次選擇的分類與檢視
  try {
    var savedCat = localStorage.getItem(CAT_KEY);
    if (savedCat && CATS[savedCat]) currentCat = savedCat;
    if (localStorage.getItem(VIEW_KEY) === "calendar") currentView = "calendar";
  } catch (e) { /* 無痕模式 */ }
  loadCategory(currentCat);
})();
