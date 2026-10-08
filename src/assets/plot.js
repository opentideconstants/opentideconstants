// Plays the tide-curve animation on the Home page once, when the plot first scrolls into view.
// Without this script, or with reduced motion, the plot shows its final state.
(() => {
  const plot = document.querySelector(".plot");
  if (!plot || !("IntersectionObserver" in window) || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const io = new IntersectionObserver((entries) => {
    if (entries.some((e) => e.isIntersecting)) { plot.classList.add("play"); io.disconnect(); }
  }, { threshold: 0.4 });
  io.observe(plot);
})();
