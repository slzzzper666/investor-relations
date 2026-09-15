/* Service Worker 註冊（從各頁的 inline script 搬出來，CSP 才能不放行 inline script） */
if ("serviceWorker" in navigator) {
  window.addEventListener("load", function () {
    navigator.serviceWorker.register("sw.js").catch(function () { /* 忽略 */ });
  });
}
