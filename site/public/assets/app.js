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
  var lastFocus = null;

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

  function buildEvents() {
    eventsByDate = {};
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
        inner += '<span class="cal-evt' + (e.id ? "" : " is-pending") + '">' +
          esc(e.company) + "</span>";
      });
      if (evts.length > MAX_PER_CELL) {
        inner += '<span class="cal-overflow">+' +
          (evts.length - MAX_PER_CELL) + "</span>";
      }
      if (evts.length) {
        cells.push(
          '<button type="button" class="' + cls + '" data-date="' + dateStr +
          '" aria-label="' + m + " 月 " + d + " 日，" + evts.length +
          ' 場法說會" aria-haspopup="dialog">' + inner + "</button>");
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
          '<span class="cal-month-meta mono">' + (n ? n + " 場" : "無場次") + "</span>" +
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
      evts.length + " 場";
    elModalList.innerHTML = evts.map(modalRowHtml).join("");
    elModalHint.hidden = !evts.some(function (e) { return !e.id; });
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

  function applyView() {
    var cal = currentView === "calendar";
    elControls.hidden = cal;
    elCalendar.hidden = !cal;
    elLedger.hidden = cal;
    if (cal) {
      elEmpty.hidden = true;
    } else if (dataReady) {
      applyFilters();
    }
    elTabList.classList.toggle("is-active", !cal);
    elTabList.setAttribute("aria-selected", String(!cal));
    elTabCalendar.classList.toggle("is-active", cal);
    elTabCalendar.setAttribute("aria-selected", String(cal));
    if (cal && dataReady && !calBuilt) buildCalendar();
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

  Promise.all([
    fetchJson("list.json"),
    fetchJson("upcoming.json").catch(function () { return null; })
  ])
    .then(function (results) {
      var data = results[0];
      var up = results[1];
      allItems = data.items || [];
      upcomingItems = (up && up.items) || [];

      elTape.textContent = "最後更新 " + data.generated_at +
        "（台北時間）· 收錄 " + allItems.length + " 場法說會" +
        (upcomingItems.length ? " · MOPS 公告 " + upcomingItems.length + " 場" : "");
      elFooterMeta.textContent = "BUILD " + data.generated_at + " · " +
        allItems.length + " RECORDS";

      // 月份選單（新→舊）
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

      buildEvents();
      dataReady = true;
      calBuilt = false;
      render(allItems);
      applyView();
    })
    .catch(function () {
      elTape.textContent = "資料載入失敗";
      elError.hidden = false;
    });

  /* ---------- 事件 ---------- */

  elQ.addEventListener("input", applyFilters);
  elMonth.addEventListener("change", applyFilters);

  elTabList.addEventListener("click", function () { setView("list"); });
  elTabCalendar.addEventListener("click", function () { setView("calendar"); });

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

  // 還原上次選擇的檢視
  try {
    if (localStorage.getItem(VIEW_KEY) === "calendar") currentView = "calendar";
  } catch (e) { /* 無痕模式 */ }
  applyView();
})();
