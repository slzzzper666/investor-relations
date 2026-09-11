/* 盤後籌碼快照浮層
   點首頁入口不跳頁，改開浮層以 iframe 載入同站 /chips/。
   關閉方式：右上角關閉鈕、點浮層外的暗色區域、Esc。
   入口仍是真正的連結：中鍵、Ctrl/Cmd+點擊、右鍵開新分頁都維持原本行為，
   萬一這支腳本沒載入，點擊也只會退化成一般跳頁，不會壞掉。 */
(function () {
  "use strict";

  var link = document.querySelector("a.sister-link");
  var modal = document.getElementById("chips-modal");
  var panel = modal && modal.querySelector(".chips-panel");
  var frame = document.getElementById("chips-frame");
  var closeBtn = document.getElementById("chips-close");
  var loading = document.getElementById("chips-loading");
  if (!link || !modal || !panel || !frame || !closeBtn) return;

  var ANIM_MS = 220;
  var lastFocus = null;
  var loaded = false;
  var hideTimer = null;

  function isPlainClick(e) {
    return !(e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0);
  }

  function open(e) {
    if (e) {
      if (!isPlainClick(e)) return;   // 讓使用者仍能開新分頁
      e.preventDefault();
    }
    if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
    lastFocus = document.activeElement;

    if (!loaded) {                    // 首次開啟才真的去載入，平時不佔流量
      frame.src = link.getAttribute("href");
      loaded = true;
    }

    modal.hidden = false;
    document.body.classList.add("chips-open");
    // 先讓瀏覽器套用起始狀態，下一幀再加 is-open 才有淡入
    requestAnimationFrame(function () { modal.classList.add("is-open"); });
    closeBtn.focus();
  }

  function close() {
    if (modal.hidden) return;
    modal.classList.remove("is-open");
    document.body.classList.remove("chips-open");
    hideTimer = setTimeout(function () {
      modal.hidden = true;
      hideTimer = null;
    }, ANIM_MS);
    if (lastFocus && typeof lastFocus.focus === "function") lastFocus.focus();
  }

  link.addEventListener("click", open);
  closeBtn.addEventListener("click", close);

  // 點浮層外的暗色區域關閉；點面板內部不關
  modal.addEventListener("mousedown", function (e) {
    if (e.target.hasAttribute("data-chips-close")) close();
  });

  document.addEventListener("keydown", function (e) {
    if (modal.hidden) return;
    if (e.key === "Escape" || e.key === "Esc") { close(); return; }
    // 把 Tab 留在浮層內，避免焦點跑到後面被遮住的頁面
    if (e.key === "Tab") { e.preventDefault(); closeBtn.focus(); }
  });

  frame.addEventListener("load", function () {
    if (frame.src === "about:blank" || !loading) return;
    loading.classList.add("is-done");
  });

  /* 進站自動先跳出籌碼：分享首頁網址就等於分享籌碼。
     一個分頁只自動跳一次——關掉後去逛法說會、看完再回首頁不會再跳，
     想再看就點入口。帶著 ?tag=／?industry= 進來的是刻意要看某族群清單，不打擾。 */
  var AUTO_KEY = "chips-auto-shown";
  var deepLinked = /[?&](tag|industry)=/.test(location.search);
  var shown = false;
  try { shown = sessionStorage.getItem(AUTO_KEY) === "1"; } catch (e) { /* 無痕 */ }
  if (!deepLinked && !shown) {
    try { sessionStorage.setItem(AUTO_KEY, "1"); } catch (e) { /* 無痕 */ }
    setTimeout(function () { open(); }, 350);   // 讓首頁先畫出來，再淡入浮層
  }
})();
