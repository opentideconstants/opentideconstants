// Tide Mechanics page (src/pages/tide-mechanics.html): draws the canvas figures and wires the
// controls. Colours come from the .tm custom properties in assets/tide-mechanics.css; the
// figures redraw when the OS colour scheme changes, as the site follows prefers-color-scheme.
(function(){
'use strict';
const rad = d => d*Math.PI/180;
const mod360 = x => ((x%360)+360)%360;
const scope = document.querySelector('.tm');
if (!scope) return;
const cssv = n => getComputedStyle(scope).getPropertyValue(n).trim();
const FONT = () => cssv('--f-mono') || 'monospace';
const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

// Constituent table. Speeds in deg/hour (standard values). H, g: illustrative Boston-like.
const C = {
  M2:{speed:28.9841042, doodson:'255.555', what:'Principal lunar semidiurnal', H:1.37, g:111, col:'--c-m2', p:2},
  S2:{speed:30.0000000, doodson:'273.555', what:'Principal solar semidiurnal', H:0.22, g:147, col:'--c-s2', p:2},
  N2:{speed:28.4397295, doodson:'245.655', what:'Larger lunar elliptic (perigee)', H:0.31, g:80, col:'--c-n2', p:2},
  K2:{speed:30.0821373, doodson:'275.555', what:'Lunisolar semidiurnal', H:0.06, g:145, col:'--muted', p:2},
  K1:{speed:15.0410686, doodson:'165.555', what:'Lunisolar diurnal', H:0.14, g:200, col:'--c-k1', p:1},
  O1:{speed:13.9430356, doodson:'145.555', what:'Principal lunar diurnal', H:0.11, g:215, col:'--c-o1', p:1},
  P1:{speed:14.9589314, doodson:'163.555', what:'Principal solar diurnal', H:0.05, g:198, col:'--muted', p:1},
  M4:{speed:57.9682084, doodson:'455.555', what:'Shallow-water overtide of M2', H:0.03, g:140, col:'--c-m4', p:4},
  Sa:{speed:0.0410686,  doodson:'056.554', what:'Solar annual (seasonal)', H:0.06, g:150, col:'--muted', p:0},
};
const NODE_SPEED = 0.0022064; // deg/hour, lunar node regression

// ---------- canvas plumbing ----------
const draws = [];
function prep(cv, hFn){
  const w = Math.max(200, Math.floor(cv.parentElement.clientWidth));
  const h = typeof hFn === 'function' ? hFn(w) : hFn;
  const dpr = window.devicePixelRatio || 1;
  cv.style.height = h + 'px';
  if (cv.width !== Math.round(w*dpr) || cv.height !== Math.round(h*dpr)){ cv.width = Math.round(w*dpr); cv.height = Math.round(h*dpr); }
  const ctx = cv.getContext('2d');
  ctx.setTransform(dpr,0,0,dpr,0,0);
  ctx.clearRect(0,0,w,h);
  ctx.textBaseline = 'alphabetic';
  return {ctx, w, h};
}
function reg(f){ draws.push(f); }
let pending = false;
function redrawAll(){ if (pending) return; pending = true; requestAnimationFrame(()=>{ pending = false; draws.forEach(f=>{ try{ f(); }catch(e){ console.error(e); } }); }); }
function font(ctx, px, weight){ ctx.font = (weight||'400') + ' ' + px + 'px ' + FONT(); }
function line(ctx, pts, color, width, dash){ ctx.save(); ctx.strokeStyle = color; ctx.lineWidth = width||1.5; ctx.setLineDash(dash||[]); ctx.lineJoin='round'; ctx.beginPath(); pts.forEach((p,i)=> i ? ctx.lineTo(p[0],p[1]) : ctx.moveTo(p[0],p[1])); ctx.stroke(); ctx.restore(); }
function text(ctx, s, x, y, color, px, align, weight){ font(ctx, px||11, weight); ctx.fillStyle = color; ctx.textAlign = align||'left'; ctx.fillText(s, x, y); }
function arrow(ctx, x1,y1,x2,y2,color,width){
  ctx.save(); ctx.strokeStyle=color; ctx.fillStyle=color; ctx.lineWidth=width||1.5;
  ctx.beginPath(); ctx.moveTo(x1,y1); ctx.lineTo(x2,y2); ctx.stroke();
  const a=Math.atan2(y2-y1,x2-x1), s=7;
  ctx.beginPath(); ctx.moveTo(x2,y2); ctx.lineTo(x2-s*Math.cos(a-0.4),y2-s*Math.sin(a-0.4)); ctx.lineTo(x2-s*Math.cos(a+0.4),y2-s*Math.sin(a+0.4)); ctx.closePath(); ctx.fill(); ctx.restore();
}
function gridV(ctx, x, y0, y1){ ctx.save(); ctx.strokeStyle=cssv('--grid'); ctx.lineWidth=1; ctx.beginPath(); ctx.moveTo(Math.round(x)+.5,y0); ctx.lineTo(Math.round(x)+.5,y1); ctx.stroke(); ctx.restore(); }
function gridH(ctx, y, x0, x1, color){ ctx.save(); ctx.strokeStyle=color||cssv('--grid'); ctx.lineWidth=1; ctx.beginPath(); ctx.moveTo(x0,Math.round(y)+.5); ctx.lineTo(x1,Math.round(y)+.5); ctx.stroke(); ctx.restore(); }

// ---------- Section 1 ----------
const S1K = ['M2','S2','N2','K1','O1','M4'];
const st1 = {}; S1K.forEach(k => st1[k] = {on: k !== 'M4', H: C[k].H});
(function buildS1(){
  const box = document.getElementById('s1-controls');
  S1K.forEach(k=>{
    const d = document.createElement('div'); d.className = 'ctl chk';
    d.innerHTML = `<input type="checkbox" id="s1-on-${k}" ${st1[k].on?'checked':''}>`+
      `<label for="s1-on-${k}"><span class="sw" style="background:var(${C[k].col})"></span> <span class="mono">${k}</span></label>`+
      `<input type="range" id="s1-amp-${k}" min="0" max="${k==='M2'?2:1}" step="0.01" value="${st1[k].H}" aria-label="${k} amplitude in metres">`+
      `<output id="s1-amp-${k}-o" for="s1-amp-${k}">${st1[k].H.toFixed(2)} m</output>`;
    box.appendChild(d);
    d.querySelector('input[type=checkbox]').addEventListener('change', e=>{ st1[k].on = e.target.checked; redrawAll(); });
    d.querySelector('input[type=range]').addEventListener('input', e=>{ st1[k].H = +e.target.value; document.getElementById(`s1-amp-${k}-o`).textContent = st1[k].H.toFixed(2)+' m'; redrawAll(); });
  });
})();
function wave(k, t, H){ return H*Math.cos(rad(C[k].speed*t - C[k].g)); }
function sum1(t){ let s=0; S1K.forEach(k=>{ if (st1[k].on) s += wave(k,t,st1[k].H); }); return s; }

reg(function drawLanes(){
  const cv = document.getElementById('s1-lanes');
  const laneH = 34, sumH = 120, top = 8, axis = 22;
  const {ctx,w,h} = prep(cv, S1K.length*laneH + sumH + top + axis + 10);
  const L = 44, R = w - 10, T = 72;
  const X = t => L + (R-L)*t/T;
  const maxH = Math.max(0.05, ...S1K.filter(k=>st1[k].on).map(k=>st1[k].H));
  const sc = (laneH/2 - 3)/maxH;
  for (let d=0; d<=T; d+=12) gridV(ctx, X(d), top, h-axis);
  S1K.forEach((k,i)=>{
    const yc = top + laneH*i + laneH/2;
    gridH(ctx, yc, L, R);
    const col = st1[k].on ? cssv(C[k].col) : cssv('--muted');
    text(ctx, k, 6, yc+4, col, 12, 'left', '500');
    if (!st1[k].on){ text(ctx, 'off', L+4, yc+4, cssv('--muted'), 10); return; }
    const pts=[]; for (let px=0; px<=R-L; px+=1){ const t = T*px/(R-L); pts.push([L+px, yc - sc*wave(k,t,st1[k].H)]); }
    line(ctx, pts, col, 1.6);
  });
  const ySum = top + laneH*S1K.length + sumH/2 + 4;
  const tot = Math.max(0.05, S1K.reduce((a,k)=> a + (st1[k].on?st1[k].H:0), 0));
  const ss = (sumH/2 - 8)/tot;
  gridH(ctx, ySum, L, R, cssv('--rule'));
  text(ctx, 'Sum', 6, ySum+4, cssv('--c-sum'), 12, 'left', '600');
  const pts=[]; for (let px=0; px<=R-L; px+=1){ const t=T*px/(R-L); pts.push([L+px, ySum - ss*sum1(t)]); }
  line(ctx, pts, cssv('--c-sum'), 2.2);
  // mark high waters to show inequality
  let prev = sum1(0), cur = sum1(0.05);
  for (let t=0.1; t<T; t+=0.05){ const nx = sum1(t); if (cur>prev && cur>=nx && cur>0){ const x=X(t-0.05), y=ySum-ss*cur; ctx.fillStyle=cssv('--accent'); ctx.beginPath(); ctx.arc(x,y,3,0,7); ctx.fill(); text(ctx, cur.toFixed(2), x, y-6, cssv('--ink-2'), 9, 'center'); } prev=cur; cur=nx; }
  for (let d=0; d<=T; d+=12) text(ctx, d+' h', X(d), h-6, cssv('--ink-2'), 10, d===0?'left':(d===T?'right':'center'));
});

reg(function drawMonth(){
  const cv = document.getElementById('s1-month');
  const {ctx,w,h} = prep(cv, w => Math.min(260, Math.max(190, w*0.3)));
  const L=40, R=w-10, Tp=26, B=h-24, D=30;
  const X = t => L + (R-L)*t/(D*24);
  const tot = Math.max(0.05, S1K.reduce((a,k)=> a + (st1[k].on?st1[k].H:0), 0));
  const ym = (Tp+B)/2, sc = (B-Tp)/2/tot;
  const dStep = w < 560 ? 10 : 5;
  for (let d=0; d<=D; d+=dStep){ gridV(ctx, X(d*24), Tp, B); text(ctx, 'day '+d, X(d*24), h-6, cssv('--ink-2'), 10, d===0?'left':(d===D?'right':'center')); }
  gridH(ctx, ym, L, R, cssv('--rule'));
  text(ctx, '+'+tot.toFixed(1)+' m', L-4, Tp+4, cssv('--ink-2'), 9, 'right'); text(ctx, '−'+tot.toFixed(1), L-4, B, cssv('--ink-2'), 9, 'right');
  const pts=[]; const n = Math.min(4000, Math.max(800,(R-L)*4));
  for (let i=0;i<=n;i++){ const t=D*24*i/n; pts.push([X(t), ym - sc*sum1(t)]); }
  line(ctx, pts, cssv('--c-sum'), 0.9);
  if (st1.M2.on && st1.S2.on){
    const dw = C.S2.speed - C.M2.speed, dg = C.S2.g - C.M2.g;
    const env = t => Math.hypot(st1.M2.H + st1.S2.H*Math.cos(rad(dw*t - dg)), st1.S2.H*Math.sin(rad(dw*t - dg)));
    const up=[], dn=[]; for (let i=0;i<=400;i++){ const t=D*24*i/400, e=env(t); up.push([X(t), ym-sc*e]); dn.push([X(t), ym+sc*e]); }
    line(ctx, up, cssv('--accent'), 1.4, [5,4]); line(ctx, dn, cssv('--accent'), 1.4, [5,4]);
    const per = 360/dw;
    for (let k=-1;k<4;k++){
      const ts = (dg + 360*k)/dw, tn = ts + per/2;
      if (ts>=0 && ts<=D*24) text(ctx, 'spring', X(ts), Tp-10, cssv('--accent'), 11, 'center', '500');
      if (tn>=0 && tn<=D*24) text(ctx, 'neap', X(tn), Tp-10, cssv('--ink-2'), 11, 'center', '500');
    }
  }
});

// ---------- Section 2 ----------
(function buildS2(){
  const tb = document.querySelector('#s2-table tbody');
  ['M2','S2','N2','K2','K1','O1','P1','M4','Sa'].forEach(k=>{
    const c=C[k], per = 360/c.speed;
    const perS = per > 1000 ? (per/24).toFixed(2)+' d' : per.toFixed(2);
    const tr=document.createElement('tr');
    tr.innerHTML = `<td><span class="sw" style="background:var(${c.col})"></span> ${k}</td><td>${c.speed.toFixed(4)}</td><td>${perS}</td><td>${c.doodson}</td><td style="font-family:var(--f-body);text-align:left">${c.what}</td>`;
    tb.appendChild(tr);
  });
})();
reg(function drawSpec(){
  const cv = document.getElementById('s2-spec');
  const {ctx,w,h} = prep(cv, w => w < 560 ? 420 : 330);
  const narrow = w < 560;
  const topH = narrow ? 150 : 140;
  const A = {x0:44, x1:w-12, y0:22, y1:topH};
  const maxH = 1.4;
  function panel(P, s0, s1, keys, labels, title){
    const X = s => P.x0 + (P.x1-P.x0)*(s-s0)/(s1-s0);
    const Y = a => P.y1 - (P.y1-P.y0)*a/maxH;
    gridH(ctx, P.y1, P.x0, P.x1, cssv('--rule'));
    if (title) text(ctx, title, P.x0, P.y0-8, cssv('--ink-2'), 10);
    return {X,Y};
  }
  // top
  const top = panel(A, 0, 62, null, null, 'amplitude (m) vs speed (°/h)');
  [[13,16,'diurnal'],[27.5,31,'semidiurnal'],[57,59,'quarter-diurnal']].forEach(([a,b,n])=>{
    ctx.fillStyle = cssv('--shoal'); ctx.fillRect(top.X(a), A.y0, top.X(b)-top.X(a), A.y1-A.y0);
    const cxl=(top.X(a)+top.X(b))/2; text(ctx, n, Math.min(cxl, A.x1-2), A.y0+10, cssv('--ink-2'), 10, cxl>A.x1-40?'right':'center');
  });
  for (let s=0;s<=60;s+=15){ text(ctx, s+'°/h', top.X(s), A.y1+14, cssv('--ink-2'), 10, 'center'); }
  [0.5,1.0].forEach(a=>{ text(ctx, a.toFixed(1), A.x0-6, top.Y(a)+3, cssv('--ink-2'), 9, 'right'); gridH(ctx, top.Y(a), A.x0, A.x1); });
  Object.keys(C).forEach(k=>{ const c=C[k]; line(ctx, [[top.X(c.speed), A.y1],[top.X(c.speed), top.Y(c.H)]], cssv(c.col), 2.5); });
  text(ctx, 'Sa', top.X(C.Sa.speed)+4, top.Y(C.Sa.H)-2, cssv('--ink-2'), 10);
  text(ctx, 'M4', top.X(C.M4.speed)+4, top.Y(C.M4.H)-2, cssv('--ink-2'), 10);
  // zooms
  const zy0 = topH + 52, zy1 = h - 26;
  let Z1, Z2;
  if (narrow){ const mid = (zy0+zy1)/2; Z1 = {x0:44,x1:w-12,y0:zy0,y1:mid-22}; Z2 = {x0:44,x1:w-12,y0:mid+30,y1:zy1}; }
  else { Z1 = {x0:44,x1:w/2-14,y0:zy0,y1:zy1}; Z2 = {x0:w/2+30,x1:w-12,y0:zy0,y1:zy1}; }
  function zoom(P, s0, s1, keys, title, step, dx){
    const X = s => P.x0 + (P.x1-P.x0)*(s-s0)/(s1-s0);
    const zmax = Math.max(...keys.map(k=>C[k].H))*1.15;
    const Y = a => P.y1 - (P.y1-P.y0)*a/zmax;
    gridH(ctx, P.y1, P.x0, P.x1, cssv('--rule'));
    text(ctx, title, P.x0, P.y0-10, cssv('--ink'), 11, 'left', '500');
    for (let s=Math.ceil(s0/step)*step; s<=s1; s+=step) text(ctx, s.toFixed(1), X(s), P.y1+13, cssv('--ink-2'), 9, 'center');
    keys.forEach(k=>{ const c=C[k]; const x=X(c.speed), y=Y(c.H); line(ctx, [[x,P.y1],[x,y]], cssv(c.col), 3); const d=dx[k]||0; text(ctx, k, x+d, y-5, cssv(c.col), 11, d<0?'right':(d>0?'left':'center'), '500'); });
  }
  zoom(Z1, 13.5, 15.5, ['O1','P1','K1'], 'Diurnal cluster', 0.5, {P1:-3,K1:3});
  zoom(Z2, 28.2, 30.3, ['N2','M2','S2','K2'], 'Semidiurnal cluster', 0.5, {S2:-3,K2:3});
});

// ---------- Section 3 ----------
const N_RATE = 0.0529539; // deg/day
function nodeN(date){ const d = (date.getTime() - Date.UTC(2000,0,1,12))/86400000; return mod360(125.0445 - N_RATE*d); }
const fM2 = N => 1.0004 - 0.0373*Math.cos(rad(N)) + 0.0002*Math.cos(rad(2*N));
const fK1 = N => 1.0060 + 0.1150*Math.cos(rad(N)) - 0.0088*Math.cos(rad(2*N)) + 0.0006*Math.cos(rad(3*N));
let s3term = null;
document.querySelectorAll('#s3-formula button.t').forEach(b=>{
  b.addEventListener('click', ()=>{
    const t = b.dataset.term; s3term = (s3term===t) ? null : t;
    document.querySelectorAll('#s3-formula button.t').forEach(o=> o.setAttribute('aria-pressed', String(o.dataset.term===s3term)));
    document.querySelectorAll('#s3-terms li').forEach(li=> li.classList.toggle('active', li.dataset.term===s3term || (s3term==='u' && li.dataset.term==='f' )));
    redrawAll();
  });
});
reg(function drawWave(){
  const cv = document.getElementById('s3-wave');
  const {ctx,w,h} = prep(cv, w => w < 560 ? 270 : 300);
  const L=44, R=w-12, Tp=30, B=h-28, T0=-1, T1=27;
  const X = t => L + (R-L)*(t-T0)/(T1-T0);
  const Z0 = 1.55, H = C.M2.H, V = 40, g = C.M2.g, om = C.M2.speed, f = fM2(nodeN(new Date()));
  const Y = z => B - (B-Tp)*(z+0.55)/3.85;
  const on = k => !s3term || s3term===k || (k==='u' && s3term==='v0') || (k==='v0' && s3term==='u');
  const dim = k => on(k) ? 1 : 0.18;
  for (let t=0;t<=24;t+=6){ gridV(ctx, X(t), Tp, B); text(ctx, t+' h', X(t), h-8, cssv('--ink-2'), 10, 'center'); }
  text(ctx, '0 m (datum)', R-3, Y(0)-4, cssv('--muted'), 9, 'right'); gridH(ctx, Y(0), L, R, cssv('--rule'));
  // f band
  ctx.globalAlpha = dim('f')*(s3term==='f'?1:0.6);
  const fu=[], fd=[]; for (let px=0; px<=R-L; px+=2){ const t=T0+(T1-T0)*px/(R-L); const c=Math.cos(rad(om*t+V-g)); fu.push([L+px, Y(Z0+1.037*H*c)]); fd.push([L+px, Y(Z0+0.963*H*c)]); }
  line(ctx, fu, cssv('--accent'), 1, [2,3]); line(ctx, fd, cssv('--accent'), 1, [2,3]);
  if (s3term==='f') text(ctx, 'f range 0.963–1.037 over 18.6 yr', X(16.2), Y(Z0+H)-8, cssv('--accent'), 11);
  // Z0
  ctx.globalAlpha = dim('z0');
  gridH(ctx, Y(Z0), L, R, cssv('--c-m2'));
  text(ctx, 'Z₀ = '+Z0.toFixed(2)+' m', L+4, Y(Z0)+14, cssv('--c-m2'), 11, 'left', s3term==='z0'?'600':'400');
  // equilibrium
  ctx.globalAlpha = (on('v0')||on('g')) ? 1 : 0.18;
  const ep=[]; for (let px=0; px<=R-L; px+=2){ const t=T0+(T1-T0)*px/(R-L); ep.push([L+px, Y(Z0 + H*Math.cos(rad(om*t+V)))]); }
  line(ctx, ep, cssv('--muted'), 1.4, [6,4]);
  const te = (360 - V)/om; // equilibrium crest
  ctx.globalAlpha = dim('v0');
  ctx.fillStyle = cssv('--muted'); ctx.beginPath(); ctx.arc(X(te), Y(Z0+H), 4, 0, 7); ctx.fill();
  text(ctx, 'equilibrium crest', X(te), Y(Z0+H)-10, cssv('--ink-2'), 10, 'center');
  text(ctx, 'V₀+u='+V+'°', X(0)+4, Tp+2, cssv('--ink-2'), 10, 'left', (s3term==='v0'||s3term==='u')?'600':'400');
  // actual wave
  ctx.globalAlpha = 1;
  const ap=[]; for (let px=0; px<=R-L; px+=1){ const t=T0+(T1-T0)*px/(R-L); ap.push([L+px, Y(Z0 + f*H*Math.cos(rad(om*t+V-g)))]); }
  line(ctx, ap, cssv('--c-m2'), 2.4);
  const ta = te + g/om, ta0 = ta - 360/om;
  // g arrow
  ctx.globalAlpha = dim('g');
  const ya = Y(Z0+H)-24;
  arrow(ctx, X(te), ya, X(ta), ya, cssv('--accent'), 2);
  text(ctx, 'g = '+g+'° ('+(g/om).toFixed(2)+' h)', (X(te)+X(ta))/2, ya-6, cssv('--accent'), 11, 'center', s3term==='g'?'600':'500');
  // H arrow
  ctx.globalAlpha = dim('H');
  arrow(ctx, X(ta)+14, Y(Z0), X(ta)+14, Y(Z0+f*H), cssv('--c-m2'), 1.8);
  text(ctx, 'f·H', X(ta)+20, (Y(Z0)+Y(Z0+H))/2+4, cssv('--c-m2'), 11, 'left', s3term==='H'?'600':'500');
  // omega bracket
  ctx.globalAlpha = dim('w');
  const yb = Y(Z0 - H) + 20;
  if (ta0 >= T0){ line(ctx, [[X(ta0),yb-5],[X(ta0),yb],[X(ta),yb],[X(ta),yb-5]], cssv('--ink'), 1.3);
    text(ctx, '360° ÷ 28.984°/h = 12.42 h', (X(ta0)+X(ta))/2, yb+13, cssv('--ink'), 11, 'center', s3term==='w'?'600':'400'); }
  ctx.globalAlpha = 1;
});
reg(function drawNodal(){
  const cv = document.getElementById('s3-nodal');
  const {ctx,w,h} = prep(cv, 220);
  const L=46, R=w-12, Tp=16, B=h-28;
  const now = new Date(), nowY = now.getUTCFullYear() + (now - Date.UTC(now.getUTCFullYear(),0,1))/(365.25*864e5);
  const span = 360/N_RATE/365.25; // 18.61 years
  const y0 = nowY - span/2, y1 = nowY + span/2;
  const X = y => L + (R-L)*(y-y0)/(y1-y0);
  const Y = f => B - (B-Tp)*(f-0.86)/(1.14-0.86);
  [0.9,1.0,1.1].forEach(v=>{ gridH(ctx, Y(v), L, R, v===1?cssv('--rule'):null); text(ctx, v.toFixed(1), L-6, Y(v)+3, cssv('--ink-2'), 10, 'right'); });
  for (let yy=Math.ceil(y0/5)*5; yy<=y1; yy+=5){ gridV(ctx, X(yy), Tp, B); text(ctx, String(yy), X(yy), h-8, cssv('--ink-2'), 10, 'center'); }
  const pm=[], pk=[];
  for (let i=0;i<=300;i++){ const yy=y0+(y1-y0)*i/300; const d = new Date(Date.UTC(2000,0,1,12) + (yy-2000.0)*365.25*864e5 - 0.5*864e5); const N=nodeN(d); pm.push([X(yy),Y(fM2(N))]); pk.push([X(yy),Y(fK1(N))]); }
  line(ctx, pk, cssv('--c-k1'), 2.2); line(ctx, pm, cssv('--c-m2'), 2.2);
  const Nn = nodeN(now);
  line(ctx, [[X(nowY),Tp],[X(nowY),B]], cssv('--accent'), 1.2, [3,3]);
  text(ctx, 'today', X(nowY)+4, B-6, cssv('--accent'), 10);
  text(ctx, 'K1 f today '+fK1(Nn).toFixed(3), L+6, Tp+10, cssv('--c-k1'), 11, 'left', '600');
  text(ctx, 'M2 f today '+fM2(Nn).toFixed(3), L+6, Tp+25, cssv('--c-m2'), 11, 'left', '600');
});

// ---------- Section 4 ----------
const S4K = ['M2','S2','N2','K1','O1'];
const st4 = {dt:1, lon:0};
const dtEl = document.getElementById('s4-dt'), lonEl = document.getElementById('s4-lon');
function setS4(dt, lon){ st4.dt=dt; st4.lon=lon; dtEl.value=dt; lonEl.value=lon; updS4(); }
function updS4(){
  document.getElementById('s4-dt-o').textContent = (st4.dt>=0?'+':'−')+Math.abs(st4.dt).toFixed(2)+' h';
  document.getElementById('s4-lon-o').textContent = Math.abs(st4.lon).toFixed(1)+'°'+(st4.lon>0?'E':st4.lon<0?'W':'');
  const tb = document.querySelector('#s4-table tbody'); tb.innerHTML='';
  S4K.forEach(k=>{ const c=C[k], sh=c.speed*st4.dt, pl=c.p*st4.lon;
    const tr=document.createElement('tr');
    tr.innerHTML=`<td>${k}</td><td>${c.g.toFixed(1)}</td><td>${sh.toFixed(2)}</td><td>${mod360(c.g+sh).toFixed(1)}</td><td>${pl.toFixed(1)}</td><td>${mod360(c.g+pl).toFixed(1)}</td>`;
    tb.appendChild(tr); });
  redrawAll();
}
dtEl.addEventListener('input', e=>{ st4.dt=+e.target.value; updS4(); });
lonEl.addEventListener('input', e=>{ st4.lon=+e.target.value; updS4(); });
document.getElementById('s4-p-kv').addEventListener('click', ()=>setS4(1,0));
document.getElementById('s4-p-linz').addEventListener('click', ()=>setS4(12,0));
document.getElementById('s4-p-jma').addEventListener('click', ()=>setS4(0,139.8));
document.getElementById('s4-p-0').addEventListener('click', ()=>setS4(0,0));
function dials(cvId, shiftFn, labelFn){
  const cv = document.getElementById(cvId);
  const {ctx,w} = prep(cv, w => { const cell=w/5; const r=Math.min(46,cell/2-8); return Math.round(2*r+62); });
  const cell = w/5, r = Math.min(46, cell/2-8);
  S4K.forEach((k,i)=>{
    const cx = cell*i + cell/2, cy = r + 16;
    ctx.strokeStyle = cssv('--rule'); ctx.lineWidth=1; ctx.beginPath(); ctx.arc(cx,cy,r,0,7); ctx.stroke();
    for (let a=0;a<360;a+=90){ const x=cx+Math.sin(rad(a))*r, y=cy-Math.cos(rad(a))*r; ctx.beginPath(); ctx.moveTo(x,y); ctx.lineTo(cx+Math.sin(rad(a))*(r-5), cy-Math.cos(rad(a))*(r-5)); ctx.stroke(); }
    const g0 = C[k].g, sh = shiftFn(k), g1 = mod360(g0+sh);
    // arc of shift
    if (Math.abs(sh) > 0.01){
      ctx.save(); ctx.strokeStyle = cssv('--accent'); ctx.globalAlpha=0.35; ctx.lineWidth=5;
      ctx.beginPath(); const a0 = rad(g0-90); let s = sh; if (Math.abs(s)>=360) s = s%360;
      ctx.arc(cx,cy,r-9,a0,a0+rad(s), s<0); ctx.stroke(); ctx.restore();
    }
    const nd = (a,col,wd,len)=> arrow(ctx, cx, cy, cx+Math.sin(rad(a))*len, cy-Math.cos(rad(a))*len, col, wd);
    nd(g0, cssv('--ink'), 2, r-4);
    if (Math.abs(sh)>0.01) nd(g1, cssv('--accent'), 2, r-14);
    ctx.fillStyle=cssv('--ink'); ctx.beginPath(); ctx.arc(cx,cy,2.5,0,7); ctx.fill();
    text(ctx, k, cx, cy+r+16, cssv(C[k].col), 12, 'center', '600');
    text(ctx, labelFn(k, sh), cx, cy+r+31, cssv('--ink-2'), 10, 'center');
  });
}
reg(()=> dials('s4-clock', k=> C[k].speed*st4.dt, (k,sh)=> (sh>=0?'+':'−')+Math.abs(sh).toFixed(1)+'°'));
reg(()=> dials('s4-mer', k=> C[k].p*st4.lon, (k,sh)=> 'p='+C[k].p+'  '+(sh>=0?'+':'−')+Math.abs(sh).toFixed(1)+'°'));

// ---------- Section 5 ----------
const S5K = ['M2','K1','M4','O1','S2','N2']; // priority order for inclusion
let seed = 7;
function rng(s){ return function(){ s|=0; s=s+0x6D2B79F5|0; let t=Math.imul(s^s>>>15,1|s); t=t+Math.imul(t^t>>>7,61|t)^t; return ((t^t>>>14)>>>0)/4294967296; }; }
function gauss(r){ let u=0,v=0; while(u===0) u=r(); v=r(); return Math.sqrt(-2*Math.log(u))*Math.cos(2*Math.PI*v); }
function solve(A,b){ const n=b.length; const M=A.map((r,i)=>r.concat([b[i]]));
  for (let c=0;c<n;c++){ let p=c; for (let r=c+1;r<n;r++) if (Math.abs(M[r][c])>Math.abs(M[p][c])) p=r; [M[c],M[p]]=[M[p],M[c]];
    const d=M[c][c]; if (Math.abs(d)<1e-12) return null; for (let j=c;j<=n;j++) M[c][j]/=d;
    for (let r=0;r<n;r++) if (r!==c){ const f=M[r][c]; if (f) for (let j=c;j<=n;j++) M[r][j]-=f*M[c][j]; } }
  return M.map(r=>r[n]); }
function rayleighOK(a,b,hours){ return hours >= 360/Math.abs(C[a].speed-C[b].speed); }
function fitRecord(days, sigma){
  const n = Math.round(days*24), r = rng(seed), Z0=1.55;
  const t=[], y=[];
  for (let i=0;i<n;i++){ let v=Z0; S5K.forEach(k=> v += C[k].H*Math.cos(rad(C[k].speed*i - C[k].g))); v += sigma*gauss(r); t.push(i); y.push(v); }
  const hours = n-1;
  const inc=[]; S5K.forEach(k=>{ if (inc.every(j=>rayleighOK(k,j,hours))) inc.push(k); });
  const m = 1+2*inc.length;
  const A = Array.from({length:m},()=>new Array(m).fill(0)), bb=new Array(m).fill(0);
  const row = new Array(m);
  for (let i=0;i<n;i++){ row[0]=1; inc.forEach((k,j)=>{ const a=rad(C[k].speed*t[i]); row[1+2*j]=Math.cos(a); row[2+2*j]=Math.sin(a); });
    for (let p=0;p<m;p++){ bb[p]+=row[p]*y[i]; for (let q=p;q<m;q++) A[p][q]+=row[p]*row[q]; } }
  for (let p=0;p<m;p++) for (let q=0;q<p;q++) A[p][q]=A[q][p];
  const x = solve(A,bb) || new Array(m).fill(0);
  const res = {Z0:x[0], con:{}};
  inc.forEach((k,j)=>{ const a=x[1+2*j], b=x[2+2*j]; res.con[k]={H:Math.hypot(a,b), g:mod360(Math.atan2(b,a)*180/Math.PI)}; });
  res.pred = ti => { let v=res.Z0; inc.forEach(k=>{ const c=res.con[k]; v+=c.H*Math.cos(rad(C[k].speed*ti - c.g)); }); return v; };
  return {t,y,res,inc};
}
let fit5 = null;
const st5 = {days:30, noise:0.10};
function updS5(){
  fit5 = fitRecord(st5.days, st5.noise);
  document.getElementById('s5-days-o').textContent = st5.days+' d';
  document.getElementById('s5-noise-o').textContent = st5.noise.toFixed(2)+' m';
  const tb=document.querySelector('#s5-table tbody'); tb.innerHTML='';
  tb.insertAdjacentHTML('beforeend', `<tr class="yes"><td>Z₀</td><td>1.55</td><td>${fit5.res.Z0.toFixed(3)}</td><td></td><td></td><td><span class="pill ok">fitted</span></td></tr>`);
  ['M2','S2','N2','K1','O1','M4'].forEach(k=>{ const c=fit5.res.con[k];
    tb.insertAdjacentHTML('beforeend', c ? `<tr class="yes"><td>${k}</td><td>${C[k].H.toFixed(2)}</td><td>${c.H.toFixed(3)}</td><td>${C[k].g}</td><td>${c.g.toFixed(1)}</td><td><span class="pill ok">fitted</span></td></tr>`
      : `<tr class="no"><td>${k}</td><td>${C[k].H.toFixed(2)}</td><td>–</td><td>${C[k].g}</td><td>–</td><td><span class="pill no">record too short</span></td></tr>`); });
  redrawAll();
}
document.getElementById('s5-days').addEventListener('input', e=>{ st5.days=+e.target.value; updS5(); });
document.getElementById('s5-noise').addEventListener('input', e=>{ st5.noise=+e.target.value; updS5(); });
document.getElementById('s5-regen').addEventListener('click', ()=>{ seed = (seed*7919+13)%100003; updS5(); });
reg(function drawFit(){
  if (!fit5) return;
  const cv=document.getElementById('s5-plot');
  const {ctx,w,h}=prep(cv, w => w<560 ? 300 : 320);
  const L=40, R=w-10, Tp=12, B=h-92, rT=h-76, rB=h-26;
  const n=fit5.t.length, T=n-1 || 1;
  const X = t => L+(R-L)*t/T;
  const Y = z => B-(B-Tp)*(z+0.6)/4.6;
  [0,1,2,3].forEach(v=>{ gridH(ctx, Y(v), L, R); text(ctx, v+' m', L-5, Y(v)+3, cssv('--ink-2'), 9, 'right'); });
  const stepD = (st5.days<=10?1:(st5.days<=30?5:10)) * (w<560 && st5.days>5 ? 2 : 1);
  for (let d=0; d<=st5.days; d+=stepD){ gridV(ctx, X(d*24), Tp, rB); text(ctx, 'day '+d, X(d*24), h-8, cssv('--ink-2'), 10, d===0?'left':(d+stepD>st5.days?'right':'center')); }
  ctx.fillStyle = cssv('--c-obs'); const dot = n>900 ? 1.1 : 1.6;
  for (let i=0;i<n;i++){ ctx.fillRect(X(fit5.t[i])-dot/2, Y(fit5.y[i])-dot/2, dot, dot); }
  const pts=[]; const steps=Math.min(3000,(R-L)*3); for (let i=0;i<=steps;i++){ const t=T*i/steps; pts.push([X(t), Y(fit5.res.pred(t))]); }
  line(ctx, pts, cssv('--c-fit'), 1.3);
  // residual strip
  const ym=(rT+rB)/2, rs=(rB-rT)/2/0.8;
  gridH(ctx, ym, L, R, cssv('--rule'));
  text(ctx, 'residual (observed − fit)', L+2, rT+2, cssv('--ink-2'), 9, 'left'); text(ctx, '±0.8', L-5, ym+3, cssv('--ink-2'), 9, 'right');
  ctx.fillStyle = cssv('--accent');
  let ss=0;
  for (let i=0;i<n;i++){ const r=fit5.y[i]-fit5.res.pred(fit5.t[i]); ss+=r*r; ctx.fillRect(X(fit5.t[i])-0.6, ym - Math.max(-0.8,Math.min(0.8,r))*rs - 0.6, 1.2, 1.2); }
  text(ctx, 'rms '+Math.sqrt(ss/n).toFixed(3)+' m', R, rT+2, cssv('--ink-2'), 10, 'right');
});

// ---------- Section 6 ----------
const MAXD = 19*365.25;
const PAIRS = [
  {name:'M2 / S2', a:'M2', b:'S2', why:'spring–neap'},
  {name:'K1 / O1', a:'K1', b:'O1', why:'diurnal pair'},
  {name:'N2 / M2', a:'N2', b:'M2', why:'lunar perigee'},
  {name:'S2 / K2', a:'S2', b:'K2', why:'solar declination'},
  {name:'K1 / P1', a:'K1', b:'P1', why:'solar declination'},
  {name:'Sa / mean level', short:'Sa / Z₀', dw: C.Sa.speed, why:'seasonal cycle'},
  {name:'18.6-yr nodal cycle', short:'nodal', dw: NODE_SPEED, why:'observe f, u directly'},
];
PAIRS.forEach(p=>{ if (p.dw===undefined) p.dw = Math.abs(C[p.a].speed - C[p.b].speed); p.hours = 360/p.dw; p.days = p.hours/24; });
window.__rayleigh = PAIRS.map(p=>({name:p.name, dw:p.dw, days:p.days}));
const sliderToDays = v => Math.pow(MAXD, v/1000);
const daysToSlider = d => Math.round(1000*Math.log(d)/Math.log(MAXD));
let s6days = 30;
const fmtDays = d => d < 60 ? d.toFixed(2)+' d' : (d < 730 ? d.toFixed(1)+' d' : (d/365.25).toFixed(2)+' yr');
function updS6(){
  document.getElementById('s6-len-o').textContent = fmtDays(s6days);
  const tb=document.querySelector('#s6-table tbody'); tb.innerHTML='';
  PAIRS.forEach(p=>{ const ok = s6days >= p.days;
    tb.insertAdjacentHTML('beforeend', `<tr class="${ok?'yes':'no'}"><td>${p.name}</td><td>${p.dw.toFixed(7)}</td><td>${fmtDays(p.days)}</td><td><span class="pill ${ok?'ok':'no'}">${ok?'separable':'merged'}</span></td></tr>`); });
  redrawAll();
}
const lenEl = document.getElementById('s6-len');
lenEl.value = daysToSlider(30);
lenEl.addEventListener('input', e=>{ s6days = sliderToDays(+e.target.value); updS6(); });
document.querySelectorAll('#s6 button[data-days]').forEach(b=> b.addEventListener('click', ()=>{ s6days=+b.dataset.days; lenEl.value=daysToSlider(s6days); updS6(); }));
reg(function drawTime(){
  const cv=document.getElementById('s6-time');
  const rowH = 26;
  const {ctx,w,h}=prep(cv, PAIRS.length*rowH + 56);
  const narrow = w < 560;
  const L = narrow ? 92 : 150, R=w-14, Tp=10, B=h-34;
  const X = d => L + (R-L)*Math.log(d)/Math.log(MAXD);
  [[1,'1 d'],[7,'1 wk'],[30,'1 mo'],[182.6,'6 mo'],[365.25,'1 yr'],[5*365.25,'5 yr'],[MAXD,'19 yr']].filter(t=>!narrow||!['1 wk','6 mo','5 yr'].includes(t[1])).forEach(([d,l])=>{ gridV(ctx, X(d), Tp, B); text(ctx, l, X(d), B+14, cssv('--ink-2'), 10, d===MAXD?'right':'center'); });
  ctx.fillStyle = cssv('--accent-soft'); ctx.fillRect(L, Tp, X(s6days)-L, B-Tp);
  PAIRS.forEach((p,i)=>{
    const y = Tp + rowH*i + rowH/2 + 2, ok = s6days>=p.days;
    text(ctx, narrow ? (p.short||p.name) : p.name, 6, y+4, ok?cssv('--ink'):cssv('--muted'), narrow?10:11, 'left', ok?'600':'400');
    line(ctx, [[L,y],[X(p.days),y]], cssv('--rule'), 1, [2,3]);
    ctx.fillStyle = ok ? cssv('--ok') : cssv('--paper'); ctx.strokeStyle = ok ? cssv('--ok') : cssv('--muted'); ctx.lineWidth=1.6;
    ctx.beginPath(); ctx.arc(X(p.days), y, 5, 0, 7); ctx.fill(); ctx.stroke();
    const lbl = fmtDays(p.days), lx = X(p.days);
    const right = lx < R - 70;
    text(ctx, lbl, right ? lx+9 : lx-9, y+4, cssv('--ink-2'), 10, right?'left':'right');
  });
  line(ctx, [[X(s6days),Tp-2],[X(s6days),B]], cssv('--accent'), 2);
  text(ctx, 'record: '+fmtDays(s6days), Math.min(Math.max(X(s6days), L+60), R-60), h-4, cssv('--accent'), 11, 'center', '600');
});

// ---------- Section 7 ----------
reg(function drawH(){
  const cv=document.getElementById('s7-h'); const {ctx,w,h}=prep(cv,150);
  const L=10,R=w-10,Tp=10,B=h-20,T=48; const X=t=>L+(R-L)*t/T, Y=z=>B-(B-Tp)*(z+0.6)/4.4;
  gridH(ctx, Y(1.55), L, R, cssv('--rule')); text(ctx,'Z₀',R,Y(1.55)-4,cssv('--ink-2'),10,'right');
  const f=t=>{ let v=1.55; ['M2','S2','N2','K1','O1'].forEach(k=> v+=C[k].H*Math.cos(rad(C[k].speed*t-C[k].g))); return v; };
  const p=[]; for (let px=0;px<=R-L;px++){ const t=T*px/(R-L); p.push([X(t),Y(f(t))]); } line(ctx,p,cssv('--c-m2'),2);
  for (let d=0; d<=T; d+=12) text(ctx, d+' h', X(d), h-5, cssv('--ink-2'), 9, d===0?'left':(d===T?'right':'center'));
});
reg(function drawSub(){
  const cv=document.getElementById('s7-sub'); const {ctx,w,h}=prep(cv,190);
  const L=10,R=w-10,Tp=42,B=h-12,T=26; const X=t=>L+(R-L)*t/T, Y=z=>B-(B-Tp)*(z+0.1)/3.3;
  const ref=t=>1.55+1.37*Math.cos(rad(C.M2.speed*t-111))+0.22*Math.cos(rad(30*t-147));
  const off=0.75, ratio=0.88; const sub=t=>ratio*ref(t-off);
  const a=[],b=[]; for (let px=0;px<=R-L;px++){ const t=T*px/(R-L); a.push([X(t),Y(ref(t))]); b.push([X(t),Y(sub(t))]); }
  line(ctx,a,cssv('--c-m2'),2); line(ctx,b,cssv('--accent'),2,[6,3]);
  let thw=0,best=-9; for (let t=0;t<14;t+=0.01){ const v=ref(t); if(v>best){best=v;thw=t;} }
  arrow(ctx, X(thw), Y(best)-6, X(thw+off), Y(best)-6, cssv('--ink'), 1.4);
  text(ctx,'+45 min', X(thw+off)+4, Y(best)-6, cssv('--ink'), 10);
  text(ctx,'×0.88 height', X(thw+off)+4, Y(ratio*best)+16, cssv('--accent'), 10);
  text(ctx,'reference', L, 12, cssv('--c-m2'), 10, 'left', '600'); text(ctx,'subordinate', L+70, 12, cssv('--accent'), 10, 'left', '600');
});
const BINS = [{s:1, az:62},{s:0.82, az:58},{s:0.55, az:50}];
const st7 = {phase:40, bin:0};
document.getElementById('s7-phase').addEventListener('input', e=>{ st7.phase=+e.target.value; document.getElementById('s7-phase-o').textContent = st7.phase+'°'; redrawAll(); });
document.getElementById('s7-bin').addEventListener('change', e=>{ st7.bin=+e.target.value; redrawAll(); });
reg(function drawCur(){
  const cv=document.getElementById('s7-cur'); const {ctx,w,h}=prep(cv,290);
  const cx=w/2, cy=h/2-6, sc=Math.min(w/2-50,h/2-30)/0.95;
  const bin=BINS[st7.bin], Maj=0.85*bin.s, Min=0.14*bin.s, az=rad(bin.az), mean=[0.05,0.02];
  const vec = th => { const a=Maj*Math.cos(rad(th)), b=Min*Math.sin(rad(th)); return [mean[0]+a*Math.sin(az)+b*Math.cos(az), mean[1]+a*Math.cos(az)-b*Math.sin(az)]; }; // [east,north]
  const P = v => [cx+v[0]*sc, cy-v[1]*sc];
  // compass
  ctx.strokeStyle=cssv('--grid'); ctx.lineWidth=1; [0.25,0.5,0.75].forEach(r=>{ ctx.beginPath(); ctx.arc(cx,cy,r*sc,0,7); ctx.stroke(); });
  gridH(ctx, cy, cx-sc, cx+sc); gridV(ctx, cx, cy-sc, cy+sc);
  text(ctx,'N',cx,cy-sc-4,cssv('--ink-2'),10,'center'); text(ctx,'0.5 m/s',cx+3,cy+0.5*sc+11,cssv('--muted'),9);
  const pts=[]; for (let a=0;a<=360;a+=3) pts.push(P(vec(a))); line(ctx,pts,cssv('--c-m2'),2);
  // major axis
  line(ctx,[P([mean[0]+Maj*Math.sin(az)*1.12, mean[1]+Maj*Math.cos(az)*1.12]),P([mean[0]-Maj*Math.sin(az)*1.12, mean[1]-Maj*Math.cos(az)*1.12])],cssv('--muted'),1,[4,3]);
  const fl=P(vec(0)), eb=P(vec(180)), sl=P(vec(90)), sl2=P(vec(270));
  text(ctx,'flood',fl[0]+8,fl[1],cssv('--c-m2'),11,'left','600'); text(ctx,'ebb',eb[0]-8,eb[1]+10,cssv('--c-m2'),11,'right','600');
  [sl,sl2].forEach(p=>{ ctx.fillStyle=cssv('--ink-2'); ctx.beginPath(); ctx.arc(p[0],p[1],3,0,7); ctx.fill(); });
  line(ctx,[[sl[0],sl[1]],[sl[0]-14,sl[1]-28]],cssv('--ink-2'),0.8); text(ctx,'slack',sl[0]-16,sl[1]-31,cssv('--ink-2'),10,'right');
  ctx.fillStyle=cssv('--warn'); ctx.beginPath(); ctx.arc(P(mean)[0],P(mean)[1],3,0,7); ctx.fill();
  const v=vec(st7.phase), pv=P(v); arrow(ctx,cx,cy,pv[0],pv[1],cssv('--accent'),2.4);
  const spd=Math.hypot(v[0],v[1]), dir=mod360(Math.atan2(v[0],v[1])*180/Math.PI);
  text(ctx, spd.toFixed(2)+' m/s toward '+dir.toFixed(0)+'°', 8, h-8, cssv('--accent'), 11, 'left', '500');
  text(ctx, '● mean current', w-8, h-8, cssv('--warn'), 10, 'right');
});
reg(function drawSurge(){
  const cv=document.getElementById('s7-surge'); const {ctx,w,h}=prep(cv,240);
  const L=10,R=w-10; const T=96;
  // top: surge
  const Tp=16, B=128; const X=t=>L+(R-L)*t/T, Y=z=>B-(B-Tp)*(z+0.2)/4.0;
  const pred=t=>1.55+1.37*Math.cos(rad(C.M2.speed*t-111))+0.22*Math.cos(rad(30*t-147));
  const surge=t=>0.65*Math.exp(-Math.pow((t-50)/14,2)) + 0.12*Math.exp(-Math.max(0,t-60)/10)*Math.sin(rad(360*(t-60)/4))*(t>60?1:0);
  const a=[],b=[]; for (let px=0;px<=R-L;px++){ const t=T*px/(R-L); a.push([X(t),Y(pred(t))]); b.push([X(t),Y(pred(t)+surge(t))]); }
  line(ctx,a,cssv('--muted'),1.4,[5,3]); line(ctx,b,cssv('--c-m2'),1.8);
  text(ctx,'prediction (dashed) vs observed',L,10,cssv('--ink-2'),10);
  // residual
  const rY = z => 160 - z*40;
  gridH(ctx,160,L,R,cssv('--rule'));
  const r=[]; for (let px=0;px<=R-L;px++){ const t=T*px/(R-L); r.push([X(t),rY(surge(t))]); } line(ctx,r,cssv('--accent'),1.8);
  text(ctx,'residual: surge, then seiche',L,rY(0)-6,cssv('--accent'),10,'left');
  // microtidal
  const mY0=200, mY=z=>mY0 - z*120;
  gridH(ctx,mY0,L,R,cssv('--rule'));
  const m=[]; for (let px=0;px<=R-L;px++){ const t=T*px/(R-L); m.push([X(t), mY(0.03*Math.cos(rad(C.M2.speed*t)) + 0.09*Math.sin(t/9)+0.04*Math.sin(t/3.1+1))]); }
  line(ctx,m,cssv('--c-m2'),1.6);
  const tl=[]; for (let px=0;px<=R-L;px++){ const t=T*px/(R-L); tl.push([X(t), mY(0.03*Math.cos(rad(C.M2.speed*t)))]); }
  line(ctx,tl,cssv('--muted'),1,[3,3]);
  text(ctx,'lake: 3 cm tide (dashed) inside wind set-up',L,h-6,cssv('--ink-2'),10);
  text(ctx,'microtidal',R,h-6,cssv('--warn'),10,'right','600');
});

// ---------- redraw on OS colour scheme change and on resize ----------
const mq = window.matchMedia('(prefers-color-scheme: dark)');
if (mq.addEventListener) mq.addEventListener('change', redrawAll); else if (mq.addListener) mq.addListener(redrawAll);
let lastW = 0;
new ResizeObserver(()=>{ const w = scope.clientWidth; if (w!==lastW){ lastW=w; redrawAll(); } }).observe(scope);
if (document.fonts && document.fonts.ready) document.fonts.ready.then(redrawAll);

updS4(); updS5(); updS6(); redrawAll();
})();
