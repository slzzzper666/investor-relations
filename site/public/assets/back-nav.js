/* 「返回清單」：若是從本站清單頁點進來的，就用瀏覽器上一頁回去——
   這樣會回到原本的族群篩選、模式與捲動位置，而不是重新載入一個全新的首頁
   （首頁會把篩選狀態即時寫進網址，所以退回去的狀態是對的）。
   直接用網址開進來（沒有上一頁可回）、或是從公司頁／族群頁互連過來時，維持原本的連結行為。 */
document.addEventListener("click", function (e) {
  var a = e.target.closest ? e.target.closest('a[href="index.html"]') : null;
  if (!a) return;
  if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
  var here = location.href.replace(/(detail|company|group)\.html.*$/, "");
  var ref = document.referrer || "";
  var fromList = ref.indexOf(here) === 0 && !/(detail|company|group)\.html/.test(ref);
  if (fromList && history.length > 1) {
    e.preventDefault();
    history.back();
  }
});
