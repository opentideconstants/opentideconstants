// Sticky header. Adds .is-stuck to the header once the page has scrolled, so the bottom rule
// shows only when content runs under it. An IntersectionObserver watches a 1px sentinel at
// the top of the page; there is no scroll listener. On phones the navigation is one row that
// scrolls sideways; the current page's link is brought into view in that row.
(() => {
  const header = document.querySelector(".site-header");
  const sentinel = document.querySelector(".top-sentinel");
  if (header && sentinel && "IntersectionObserver" in window) {
    document.documentElement.classList.add("js-header");
    new IntersectionObserver(([e]) => header.classList.toggle("is-stuck", !e.isIntersecting)).observe(sentinel);
  }
  const list = document.querySelector(".nav-list");
  const current = list && list.querySelector("[aria-current]");
  if (current && list.scrollWidth > list.clientWidth) {
    const li = current.parentElement;
    list.scrollLeft = li.offsetLeft - (list.clientWidth - li.offsetWidth) / 2;
  }
})();
