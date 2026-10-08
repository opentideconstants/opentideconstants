// Documentation pages, with the behaviour of the aimock docs (aimock.copilotkit.dev/sidebar.js):
// - the left sidebar scrolls its current page link to the middle on load;
// - at 768px and below the header's menu button slides the sidebar in and out;
// - the "On this page" list marks the topmost heading in view (an IntersectionObserver with
//   the same margins as aimock), starting with the first, and a click scrolls smoothly to the
//   heading and puts its #id in the address bar without a jump.
(() => {
  const root = document.documentElement;
  const header = document.querySelector(".site-header");
  const setTop = () => root.style.setProperty("--docs-top", `${header ? header.offsetHeight : 64}px`);
  setTop();
  addEventListener("resize", setTop);

  const sidebar = document.getElementById("docs-sidebar");
  const active = sidebar && sidebar.querySelector("a.active");
  if (active) sidebar.scrollTop = active.offsetTop - (sidebar.clientHeight - active.offsetHeight) / 2;

  const toggle = document.querySelector(".sidebar-toggle");
  if (toggle && sidebar) {
    toggle.addEventListener("click", () => {
      const open = sidebar.classList.toggle("open");
      toggle.setAttribute("aria-expanded", String(open));
    });
  }

  const links = [...document.querySelectorAll('.page-toc a[href^="#"]')];
  const heads = links.map((a) => document.getElementById(decodeURIComponent(a.hash.slice(1)))).filter(Boolean);
  if (!links.length) return;
  const setActive = (i) => links.forEach((a, j) => a.classList.toggle("active", i === j));
  setActive(0);
  if ("IntersectionObserver" in window && heads.length) {
    const io = new IntersectionObserver((entries) => {
      let top = -1;
      for (const e of entries) {
        if (!e.isIntersecting) continue;
        const i = heads.indexOf(e.target);
        if (i !== -1 && (top === -1 || i < top)) top = i;
      }
      if (top !== -1) setActive(top);
    }, { rootMargin: "-80px 0px -60% 0px", threshold: 0 });
    heads.forEach((h) => io.observe(h));
  }
  links.forEach((a) => a.addEventListener("click", (e) => {
    const target = document.getElementById(decodeURIComponent(a.hash.slice(1)));
    if (!target) return;
    e.preventDefault();
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    target.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
    history.pushState(null, "", a.hash);
  }));
})();
