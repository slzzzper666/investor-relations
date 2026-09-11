/* 族群打字搜尋（combobox）
   真正的值仍在隱藏的 <select id="group">，app.js 照舊從那裡讀、也照舊監聽 change。
   這支只負責：把 select 的選項變成可打字過濾的清單，選了就寫回 select 並觸發 change。
   select 的選項由 app.js 在每次載入資料時重建，這裡用 MutationObserver 跟著同步。 */
(function () {
  "use strict";

  var sel = document.getElementById("group");
  var input = document.getElementById("group-q");
  var list = document.getElementById("group-list");
  var clearBtn = document.getElementById("group-clear");
  var combo = document.getElementById("group-combo");
  if (!sel || !input || !list || !clearBtn || !combo) return;

  var items = [];        // [{value, name, count, section}]
  var visible = [];      // 目前清單顯示的 items
  var active = -1;       // 鍵盤游標位置

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  /* 從 select 讀出選項。option 文字格式：名稱（N） */
  function readOptions() {
    items = [];
    var groups = sel.querySelectorAll("optgroup");
    var nodes = groups.length ? groups : [sel];
    Array.prototype.forEach.call(nodes, function (g) {
      var section = g.tagName === "OPTGROUP" ? g.label : "";
      Array.prototype.forEach.call(g.querySelectorAll("option"), function (o) {
        if (!o.value) return;
        var m = /^(.*?)（(\d+)）$/.exec(o.textContent.trim());
        items.push({
          value: o.value,
          name: m ? m[1] : o.textContent.trim(),
          count: m ? m[2] : "",
          section: section
        });
      });
    });
  }

  function labelOf(value) {
    for (var i = 0; i < items.length; i++) if (items[i].value === value) return items[i].name;
    return "";
  }

  /* 把 select 目前的值反映到輸入框 */
  function syncFromSelect() {
    var name = labelOf(sel.value);
    input.value = name;
    clearBtn.hidden = !name;
    combo.classList.toggle("has-value", !!name);
  }

  function filter(q) {
    q = q.trim().toLowerCase();
    if (!q) return items.slice();
    return items.filter(function (it) { return it.name.toLowerCase().indexOf(q) !== -1; });
  }

  function renderList(q) {
    visible = filter(q);
    active = -1;
    if (!visible.length) {
      list.innerHTML = '<li class="combo-empty">沒有符合的族群</li>';
      return;
    }
    var html = "";
    var lastSection = null;
    visible.forEach(function (it, i) {
      if (it.section && it.section !== lastSection) {
        html += '<li class="combo-section" aria-hidden="true">' + esc(it.section) + "</li>";
        lastSection = it.section;
      }
      html += '<li class="combo-item' + (it.value === sel.value ? " is-selected" : "") +
              '" role="option" data-i="' + i + '" aria-selected="' + (it.value === sel.value) + '">' +
              '<span class="combo-name">' + esc(it.name) + "</span>" +
              (it.count ? '<span class="combo-count">' + esc(it.count) + "</span>" : "") +
              "</li>";
    });
    list.innerHTML = html;
  }

  function openList() {
    if (!items.length) return;
    renderList(input.value);
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
  }

  function closeList() {
    list.hidden = true;
    input.setAttribute("aria-expanded", "false");
    active = -1;
  }

  function commit(value) {
    sel.value = value;
    if (sel.value !== value) sel.value = "";
    sel.dispatchEvent(new Event("change", { bubbles: true }));   // 交給 app.js 重新篩選
    syncFromSelect();
    closeList();
  }

  function setActive(i) {
    var rows = list.querySelectorAll(".combo-item");
    if (!rows.length) return;
    if (i < 0) i = rows.length - 1;
    if (i >= rows.length) i = 0;
    active = i;
    Array.prototype.forEach.call(rows, function (r, k) { r.classList.toggle("is-active", k === i); });
    var el = rows[i];
    var top = el.offsetTop, bottom = top + el.offsetHeight;
    if (top < list.scrollTop) list.scrollTop = top;
    else if (bottom > list.scrollTop + list.clientHeight) list.scrollTop = bottom - list.clientHeight;
  }

  /* ---- 事件 ---- */
  input.addEventListener("focus", openList);
  input.addEventListener("input", function () {
    openList();
    combo.classList.remove("has-value");
    clearBtn.hidden = !input.value;
  });

  input.addEventListener("keydown", function (e) {
    if (list.hidden && (e.key === "ArrowDown" || e.key === "ArrowUp")) { openList(); e.preventDefault(); return; }
    if (list.hidden) return;
    var rows = list.querySelectorAll(".combo-item");
    if (e.key === "ArrowDown") { e.preventDefault(); setActive(active + 1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive(active - 1); }
    else if (e.key === "Enter") {
      e.preventDefault();
      var pick = active >= 0 ? rows[active] : (rows.length === 1 ? rows[0] : null);
      if (pick) commit(visible[+pick.getAttribute("data-i")].value);
    }
    else if (e.key === "Escape") { e.preventDefault(); syncFromSelect(); closeList(); }
  });

  list.addEventListener("mousedown", function (e) {   // mousedown 才不會先觸發 input 的 blur
    var row = e.target.closest(".combo-item");
    if (!row) return;
    e.preventDefault();
    commit(visible[+row.getAttribute("data-i")].value);
  });

  clearBtn.addEventListener("click", function () {
    commit("");
    input.focus();
  });

  document.addEventListener("mousedown", function (e) {
    if (!combo.contains(e.target)) {
      if (!list.hidden) { syncFromSelect(); closeList(); }   // 沒選就把字還原成目前的值
    }
  });

  /* app.js 換分類會整批重建選項；重建完（含它同步設好的 value）再同步一次 */
  new MutationObserver(function () {
    readOptions();
    syncFromSelect();
    if (!list.hidden) renderList(input.value);
  }).observe(sel, { childList: true, subtree: true });

  sel.addEventListener("change", function (e) {
    if (e.isTrusted) syncFromSelect();   // 其他來源改了 select（保險）
  });

  readOptions();
  syncFromSelect();
})();
