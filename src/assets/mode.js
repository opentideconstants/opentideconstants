// Day / night palette. The page follows the OS setting (prefers-color-scheme) until the reader
// presses the header button; that choice is kept in localStorage. Without this script, the page
// still follows the OS setting and the button stays hidden.
(() => {
  const root = document.documentElement;
  const KEY = "otc-palette";
  try {
    const saved = localStorage.getItem(KEY);
    if (saved === "light" || saved === "dark") root.dataset.theme = saved;
  } catch {}
  const isNight = () => (root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches);
  document.addEventListener("DOMContentLoaded", () => {
    const btn = document.querySelector(".mode");
    if (!btn) return;
    const sync = () => { btn.textContent = isNight() ? "Day palette" : "Night palette"; };
    btn.hidden = false;
    sync();
    btn.addEventListener("click", () => {
      root.dataset.theme = isNight() ? "light" : "dark";
      try { localStorage.setItem(KEY, root.dataset.theme); } catch {}
      sync();
    });
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", sync);
  });
})();
