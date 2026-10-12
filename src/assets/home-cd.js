// Plays the engine-check strips on /home-c/ and /home-d/ once, when the figure first scrolls into view.
// Without this script, or with reduced motion, the figure shows its final state.
(() => {
  const fig = document.querySelector(".eng-fig");
  if (!fig || !("IntersectionObserver" in window) || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const io = new IntersectionObserver((entries) => {
    if (entries.some((e) => e.isIntersecting)) { fig.classList.add("play"); io.disconnect(); }
  }, { threshold: 0.3 });
  io.observe(fig);
})();
