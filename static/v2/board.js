





(function () {
  "use strict";
  try {
    if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      document.documentElement.setAttribute("data-motion", "reduce");
    }
  } catch (e) {   }

  function navMenus() {
    var ds = document.querySelectorAll(".board-nav details.board-more");
    if (!ds.length) return;
    Array.prototype.forEach.call(ds, function (d) {
      d.addEventListener("toggle", function () {
        if (!d.open) return;
        Array.prototype.forEach.call(ds, function (o) { if (o !== d) o.open = false; });
      });
    });
    document.addEventListener("click", function (e) {
      Array.prototype.forEach.call(ds, function (d) {
        if (d.open && !d.contains(e.target)) d.open = false;
      });
    });
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape") return;
      Array.prototype.forEach.call(ds, function (d) { if (d.open) d.open = false; });
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", navMenus);
  else navMenus();
})();
