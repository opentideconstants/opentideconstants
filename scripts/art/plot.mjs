// Tide-curve plot for the Home page proof section. A schematic semidiurnal tide at Helgoland.
// The official curve has high water at 12:00. The TICON-4 and OTC curves are the same curve shifted by
// the proof-of-concept mean time errors at Helgoland (TICON-4 88-90 min, drawn at 89; our fit 6.7 min).
const M2 = 12.4206; // hours
const f = (n) => n.toFixed(1);

export function tidePlot({ w = 640, h = 360, padL = 16, padR = 16, padT = 64, padB = 40, t0 = 6, t1 = 18.5, id = "tide", label = "plot", fs = 18, tiAbove = false } = {}) {
  const X = (t) => padL + ((t - t0) / (t1 - t0)) * (w - padL - padR);
  const yTop = padT, yBot = h - padB;
  const Y = (v) => yBot - ((v + 1) / 2) * (yBot - yTop); // v in [-1, 1]
  const curve = (shift, amp = 1) => {
    const pts = [];
    for (let t = t0; t <= t1 + 1e-9; t += 0.125) pts.push(`${f(X(t))} ${f(Y(amp * Math.cos((2 * Math.PI * (t - 12 - shift)) / M2)))}`);
    return "M" + pts.join(" L");
  };
  const hw = 12, ti = 12 + 89 / 60, fit = 12 + 6.7 / 60;
  const grid = [];
  for (let t = Math.ceil(t0); t <= t1; t++) grid.push(`<path class="g${t % 3 === 0 ? " g3" : ""}" d="M${f(X(t))} ${yTop - 8} V${yBot}"/>`);
  const hlines = [-1, -0.5, 0, 0.5, 1].map((v) => `<path class="g${v === 0 ? " g3" : ""}" d="M${padL} ${f(Y(v))} H${w - padR}"/>`).join("");
  const ticks = [];
  for (let t = Math.ceil(t0); t <= t1; t += 3) ticks.push(`<text x="${f(X(t))}" y="${h - padB + 24}">${String(t).padStart(2, "0")}:00</text>`);
  const yb = yTop - 26; // bracket height
  return `<svg class="tide ${label}" viewBox="0 0 ${w} ${h}" role="img" aria-labelledby="${id}-t" style="--fs:${fs}px" preserveAspectRatio="xMidYMid meet">
  <title id="${id}-t">Schematic tide at Helgoland, proof of concept. The official prediction has high water at 12:00. The TICON-4 curve is shifted by its mean time error at Helgoland, 88 to 90 minutes. The curve of our fit is shifted by its mean time error, 6.7 minutes.</title>
  <g class="grid">${grid.join("")}${hlines}</g>
  <g class="ticks">${ticks.join("")}</g>
  <path class="c c-ticon" style="--dx:${f(X(hw) - X(ti))}px" d="${curve(89 / 60)}"/>
  <path class="c c-official" pathLength="1" d="${curve(0)}"/>
  <path class="c c-fit" style="--dx:${f(X(hw) - X(fit))}px" d="${curve(6.7 / 60, 0.985)}"/>
  <g class="marks">
    <path class="lead lead-ti" d="M${f(X(hw))} ${f(Y(1) - 6)} V${yb} M${f(X(ti))} ${f(Y(1) - 6)} V${yb}"/>
    <path class="span span-ti" d="M${f(X(hw))} ${yb} H${f(X(ti) - 1)}"/>
    <path class="span span-ti" d="M${f(X(ti) - 9)} ${yb - 6} L${f(X(ti) - 1)} ${yb} L${f(X(ti) - 9)} ${yb + 6}"/>
    <path class="hw hw-ti" d="M${f(X(ti))} ${f(Y(1) - 8)} l8 8 l-8 8 l-8 -8 Z"/>
    <circle class="hw hw-fit" cx="${f(X(fit))}" cy="${f(Y(0.985))}" r="6"/>
    <circle class="hw hw-official" cx="${f(X(hw))}" cy="${f(Y(1))}" r="3"/>
    ${tiAbove ? `<text class="lab lab-ti" x="${w - padR}" y="${yb - 16}" text-anchor="end">TICON-4 88–90 min</text>` : `<text class="lab lab-ti" x="${f(X(ti) + 12)}" y="${yb + 6}">TICON-4 88–90 min</text>`}
    <text class="lab lab-fit" x="${f(X(hw) - 12)}" y="${yb + 6}" text-anchor="end">Our fit 6.7 min</text>
  </g>
</svg>`;
}

