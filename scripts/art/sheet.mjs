// Cartographic chart sheet for B2: depth contours, soundings, graticule, compass rose. Deterministic.
const f = (n) => n.toFixed(1);
let seed = 7;
const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);

function blob(cx, cy, R, wob, phase) {
  const pts = [];
  const N = 72;
  for (let i = 0; i < N; i++) {
    const a = (i / N) * Math.PI * 2;
    const r = R * (1 + wob * (0.18 * Math.sin(2 * a + phase) + 0.11 * Math.sin(3 * a + phase * 1.7) + 0.06 * Math.sin(5 * a + phase * 0.6)));
    pts.push([cx + r * Math.cos(a) * 1.25, cy + r * Math.sin(a)]);
  }
  // Catmull-Rom to cubic Bezier, closed
  let d = `M${f(pts[0][0])} ${f(pts[0][1])}`;
  for (let i = 0; i < N; i++) {
    const p0 = pts[(i - 1 + N) % N], p1 = pts[i], p2 = pts[(i + 1) % N], p3 = pts[(i + 2) % N];
    d += ` C${f(p1[0] + (p2[0] - p0[0]) / 6)} ${f(p1[1] + (p2[1] - p0[1]) / 6)} ${f(p2[0] - (p3[0] - p1[0]) / 6)} ${f(p2[1] - (p3[1] - p1[1]) / 6)} ${f(p2[0])} ${f(p2[1])}`;
  }
  return d + " Z";
}

export function chartSheet({ w = 1200, h = 660, cx = 900, cy = 320, rx = 1070, ry = 548 } = {}) {
  seed = 7;
  const levels = [
    { R: 300, cls: "dc d30", fill: "" },
    { R: 230, cls: "dc d20", fill: "" },
    { R: 170, cls: "dc d10 band-shallow", fill: "shallow" },
    { R: 120, cls: "dc d5 band-shoal", fill: "shoal" },
    { R: 82, cls: "dc d2 band-shoal2", fill: "shoal" },
  ];
  const contours = levels.map((l, i) => {
    const d = blob(cx, cy, l.R, 0.55 + i * 0.05, 0.9 + i * 0.35);
    return `${l.fill ? `<path class="fill-${l.fill}" d="${d}"/>` : ""}<path class="${l.cls}" pathLength="1" style="--i:${i}" d="${d}"/>`;
  }).join("\n");
  const island = blob(cx - 6, cy + 4, 48, 0.7, 2.2);
  const dune = blob(cx + 96, cy + 44, 14, 0.5, 4.1);
  // Soundings: depth grows with distance from the island; skip the title area and the rose.
  const snd = [];
  for (let k = 0; k < 150 && snd.length < 64; k++) {
    const x = 30 + rnd() * (w - 60), y = 30 + rnd() * (h - 60);
    const dx = (x - cx) / 1.25, dy = y - cy, dist = Math.hypot(dx, dy);
    if (dist < 120) continue;
    if (x < 600) continue; // under the title cartouche
    if (x > 820 && y < 160) continue; // under the chart note
    if (Math.hypot(x - rx, y - ry) < 125) continue; // compass rose
    if (Math.abs(y - (cy + 235)) < 30 && Math.abs(x - (cx - 70)) < 190) continue; // sea name
    if (snd.some(([a, b]) => Math.hypot(a - x, b - y) < 58)) continue;
    const depth = Math.round(2 + dist / 14 + rnd() * 3);
    const frac = rnd() < 0.35 ? `<tspan class="sub" dx="1" dy="4">${1 + Math.floor(rnd() * 9)}</tspan>` : "";
    snd.push([x, y, `<text x="${f(x)}" y="${f(y)}" style="--d:${snd.length}">${depth}${frac}</text>`]);
  }
  // Graticule: every 120 px, labelled on the neatline by the HTML frame.
  const grat = [];
  for (let x = 120; x < w; x += 120) grat.push(`M${x} 0 V${h}`);
  for (let y = 110; y < h; y += 110) grat.push(`M0 ${y} H${w}`);
  // Compass rose
  const R = 92;
  const ticks = [];
  for (let a = 0; a < 360; a += 5) {
    const len = a % 30 === 0 ? 12 : a % 10 === 0 ? 8 : 4;
    const t = ((a - 90) * Math.PI) / 180;
    ticks.push(`M${f(rx + R * Math.cos(t))} ${f(ry + R * Math.sin(t))} L${f(rx + (R - len) * Math.cos(t))} ${f(ry + (R - len) * Math.sin(t))}`);
  }
  const labels = [0, 90, 180, 270].map((a) => {
    const t = ((a - 90) * Math.PI) / 180;
    return `<text x="${f(rx + (R + 14) * Math.cos(t))}" y="${f(ry + (R + 14) * Math.sin(t) + 4)}">${String(a).padStart(3, "0")}</text>`;
  }).join("");
  const star = (r1, r2, n, off) => {
    const p = [];
    for (let i = 0; i < n * 2; i++) {
      const t = ((i * 180) / n + off - 90) * (Math.PI / 180);
      const r = i % 2 ? r2 : r1;
      p.push(`${f(rx + r * Math.cos(t))} ${f(ry + r * Math.sin(t))}`);
    }
    return "M" + p.join(" L") + " Z";
  };
  return `<svg class="sheet-map" viewBox="0 0 ${w} ${h}" preserveAspectRatio="xMaxYMid slice" aria-hidden="true">
  <rect class="water" width="${w}" height="${h}"/>
  <path class="grat" d="${grat.join(" ")}"/>
  ${contours}
  <path class="land" d="${island}"/><path class="land" d="${dune}"/>
  <g class="snd">${snd.map((s) => s[2]).join("")}</g>
  <g class="rose">
    <circle cx="${rx}" cy="${ry}" r="${R}" class="ring"/><circle cx="${rx}" cy="${ry}" r="${R - 20}" class="ring thin"/>
    <path class="rtick" d="${ticks.join(" ")}"/>
    <path class="star4" d="${star(66, 12, 4, 0)}"/>
    <path class="star8" d="${star(40, 10, 4, 45)}"/>
    <g class="rlab">${labels}</g>
  </g>
  <text class="pname" x="${cx - 6}" y="${cy + 80}" text-anchor="middle">Helgoland</text>
  <text class="pname sea" x="${cx - 70}" y="${cy + 245}" text-anchor="middle">German Bight</text>
  <g class="gauge" transform="translate(${cx + 18} ${cy - 6})"><circle r="6"/><path d="M0 -6 V-26 M-8 -19 H8"/></g>
  <text class="gauge-lab" x="${cx + 36}" y="${cy - 50}">Tide gauge</text>
  <g class="tdiamond" transform="translate(${cx + 170} ${cy - 110})"><path d="M0 -18 L18 0 L0 18 L-18 0 Z"/><text y="6" text-anchor="middle">A</text></g>
  <path class="limit" d="M${cx - 420} ${h - 10} C ${cx - 300} ${h - 70}, ${cx - 330} ${cy - 40}, ${cx - 200} 10"/>
</svg>`;
}
