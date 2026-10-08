// Documentation pages. On phones the page list is a closed "Documentation menu" above the
// text; on wider screens it is the open left sidebar (the HTML ships it open, so it shows
// without this script). The "On this page" list marks the section being read.
(() => {
  const menu = document.querySelector(".docs-menu");
  const wide = matchMedia("(min-width: 60rem)");
  const sync = () => { if (menu) menu.open = wide.matches; };
  sync();
  wide.addEventListener("change", sync);

  const links = [...document.querySelectorAll(".page-toc a")];
  if (!links.length || !("IntersectionObserver" in window)) return;
  const byId = new Map(links.map((a) => [decodeURIComponent(a.hash.slice(1)), a]));
  const heads = [...byId.keys()].map((id) => document.getElementById(id)).filter(Boolean);
  const visible = new Set();
  const mark = () => {
    const first = heads.find((h) => visible.has(h)) ?? heads.filter((h) => h.getBoundingClientRect().top < 0).pop();
    links.forEach((a) => a.classList.toggle("is-current", first != null && a === byId.get(first.id)));
  };
  const io = new IntersectionObserver((entries) => {
    entries.forEach((e) => (e.isIntersecting ? visible.add(e.target) : visible.delete(e.target)));
    mark();
  }, { rootMargin: "-80px 0px -60% 0px" });
  heads.forEach((h) => io.observe(h));
})();
