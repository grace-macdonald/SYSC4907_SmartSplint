// Small dependency-free SVG line chart with crosshair + tooltip.
// Marks follow the data-viz specs: 2px lines, >=8px markers with a 2px surface ring,
// hairline solid grid, one y-axis per chart, text in ink tokens (never series colour).

import { h, clear } from "./core.js";

const NS = "http://www.w3.org/2000/svg";
function s(tag, attrs = {}) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) el.setAttribute(k, v);
  return el;
}

export function niceTicks(min, max, count = 4) {
  if (min === max) { min -= 1; max += 1; }
  const span = max - min;
  const raw = span / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((st) => span / st <= count) || 10 * mag;
  const lo = Math.floor(min / step) * step;
  const hi = Math.ceil(max / step) * step;
  const ticks = [];
  for (let v = lo; v <= hi + step / 2; v += step) ticks.push(+v.toFixed(10));
  return { lo, hi, ticks, step };
}

function bisect(points, x) {
  let lo = 0, hi = points.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (points[mid].x < x) lo = mid; else hi = mid;
  }
  return Math.abs(points[lo].x - x) <= Math.abs(points[hi].x - x) ? lo : hi;
}

const syncGroups = new Map();

/**
 * lineChart(container, {
 *   series: [{ name, color, points: [{x, y, ref?}], step?, markers? }],
 *   xType: "time" | "seconds", xDomain?, yDomain?, yZero?, height?,
 *   thresholds?: [{ y, label, status: "critical" | "warning" | "neutral" }],
 *   bands?: [{ x0, x1, color }], bandLegend?,
 *   unit?, digits?, title?, onPointClick?(point, seriesIndex), syncGroup?
 * })
 */
export function lineChart(container, opts) {
  const state = { width: 0 };
  const root = h("div", { class: "chart" });
  clear(container, root);
  const tooltip = h("div", { class: "tooltip hidden", role: "presentation" });
  root.append(tooltip);

  const ro = new ResizeObserver(() => {
    const w = Math.round(root.clientWidth);
    if (w && w !== state.width) { state.width = w; draw(); }
  });
  ro.observe(root);

  let api = null;

  function draw() {
    root.querySelector("svg")?.remove();
    const W = state.width;
    const H = opts.height || 170;
    const bandH = opts.bands?.length ? 12 : 0;
    const multi = opts.series.length > 1;
    const endLabels = multi && opts.series.length <= 4 && opts.endLabels !== false;
    // A fixed right margin keeps stacked charts aligned on the same x positions.
    const m = { top: 8 + bandH + (bandH ? 6 : 0), right: opts.marginRight ?? (endLabels ? 74 : 14), bottom: 22, left: 40 };
    const iw = Math.max(10, W - m.left - m.right);
    const ih = H - m.top - m.bottom;

    const all = opts.series.flatMap((se) => se.points);
    const ys = all.map((p) => p.y).filter((v) => v !== null && v !== undefined && !Number.isNaN(v));
    for (const t of opts.thresholds || []) if (t.y !== null && t.y !== undefined) ys.push(t.y);
    const xs = all.map((p) => p.x);
    const [x0, x1] = opts.xDomain || [Math.min(...xs), Math.max(...xs)];
    let ymin = opts.yDomain?.[0] ?? (ys.length ? Math.min(...ys) : 0);
    let ymax = opts.yDomain?.[1] ?? (ys.length ? Math.max(...ys) : 1);
    if (opts.yZero) ymin = Math.min(0, ymin);
    const { lo, hi, ticks } = opts.yDomain ? { lo: ymin, hi: ymax, ticks: niceTicks(ymin, ymax).ticks.filter((t) => t >= ymin && t <= ymax) }
      : niceTicks(ymin, ymax, 4);
    const sx = (x) => m.left + (x1 === x0 ? iw / 2 : ((x - x0) / (x1 - x0)) * iw);
    const sy = (y) => m.top + ih - ((y - lo) / (hi - lo || 1)) * ih;

    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": opts.title || "" });

    // grid + y ticks
    for (const t of ticks) {
      svg.append(s("line", { class: t === 0 || t === lo ? "baseline" : "gridline", x1: m.left, x2: m.left + iw, y1: sy(t), y2: sy(t) }));
      const lab = s("text", { class: "tick", x: m.left - 6, y: sy(t) + 3.5, "text-anchor": "end" });
      lab.textContent = Math.abs(t) >= 10000 ? `${t / 1000}K` : t.toLocaleString();
      svg.append(lab);
    }

    // x ticks
    const nX = Math.max(2, Math.min(6, Math.floor(iw / 90)));
    if (opts.xType === "time") {
      const DAY = 86400000;
      const days = (x1 - x0) / DAY;
      const want = Math.max(1, nX);
      const stepDays = [1, 2, 3, 7, 14, 28, 56].find((d) => days / d <= want) || 91;
      const start = new Date(x0); start.setHours(0, 0, 0, 0);
      const f = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });
      for (let t = start.getTime() + DAY; t <= x1; t += stepDays * DAY) {
        if (t < x0) continue;
        const lab = s("text", { class: "tick", x: sx(t), y: H - 6, "text-anchor": "middle" });
        lab.textContent = f.format(new Date(t));
        svg.append(lab);
      }
    } else {
      const { ticks: xt } = niceTicks(x0, x1, nX);
      for (const t of xt) {
        if (t < x0 || t > x1) continue;
        const lab = s("text", { class: "tick", x: sx(t), y: H - 6, "text-anchor": "middle" });
        lab.textContent = `${+t.toFixed(1)} s`;
        svg.append(lab);
      }
    }

    // gait-phase strip (a thin band above the plot)
    if (bandH) {
      const g = s("g");
      for (const b of opts.bands) {
        const bx0 = Math.max(x0, b.x0), bx1 = Math.min(x1, b.x1);
        if (bx1 <= bx0) continue;
        const w = Math.max(0.5, sx(bx1) - sx(bx0) - 1);
        g.append(s("rect", { x: sx(bx0), y: 6, width: w, height: bandH, rx: 2, fill: b.color }));
      }
      svg.append(g);
    }

    // thresholds (limits) - solid, status-coloured, labelled
    for (const t of opts.thresholds || []) {
      if (t.y === null || t.y === undefined || t.y < lo || t.y > hi) continue;
      const color = t.status === "critical" ? "var(--critical)" : t.status === "warning" ? "var(--warning)" : "var(--ink-2)";
      svg.append(s("line", { class: "threshold", x1: m.left, x2: m.left + iw, y1: sy(t.y), y2: sy(t.y), stroke: color }));
      const lab = s("text", { class: "threshold-label", x: m.left + iw - 2, y: sy(t.y) - 4, "text-anchor": "end" });
      lab.textContent = t.label;
      svg.append(lab);
    }

    // series
    opts.series.forEach((se) => {
      const pts = se.points.filter((p) => p.x >= x0 && p.x <= x1);
      let d = "";
      let pen = false;
      let prev = null;
      for (const p of pts) {
        if (p.y === null || p.y === undefined || Number.isNaN(p.y)) { pen = false; continue; }
        const X = sx(p.x).toFixed(1), Y = sy(p.y).toFixed(1);
        if (!pen) d += `M${X},${Y}`;
        else if (se.step) d += `H${X}V${Y}`;
        else d += `L${X},${Y}`;
        pen = true;
        prev = p;
      }
      if (se.step && prev && opts.stepToEnd) d += `H${sx(x1).toFixed(1)}`;
      svg.append(s("path", { d, fill: "none", stroke: se.color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
      const showMarkers = se.markers ?? (pts.length <= 40);
      if (showMarkers) {
        for (const p of pts) {
          if (p.y === null || p.y === undefined) continue;
          svg.append(s("circle", { cx: sx(p.x), cy: sy(p.y), r: 4, fill: se.color, stroke: "var(--surface)", "stroke-width": 2 }));
        }
      } else if (prev) {
        svg.append(s("circle", { cx: sx(prev.x), cy: sy(prev.y), r: 4, fill: se.color, stroke: "var(--surface)", "stroke-width": 2 }));
      }
    });

    // end labels for <= 4 series (legend is still shown by the caller)
    if (endLabels) {
      const placed = [];
      opts.series.forEach((se) => {
        const pts = se.points.filter((p) => p.x >= x0 && p.x <= x1 && p.y !== null && p.y !== undefined);
        const last = pts[pts.length - 1];
        if (!last) return;
        let y = sy(last.y) + 3.5;
        if (placed.some((py) => Math.abs(py - y) < 12)) return; // collision: legend + tooltip carry it
        placed.push(y);
        const lab = s("text", { class: "end-label", x: m.left + iw + 8, y });
        lab.textContent = se.short || se.name;
        svg.append(lab);
      });
    }

    // interaction layer
    const cross = s("line", { class: "crosshair hidden", y1: m.top, y2: m.top + ih, visibility: "hidden" });
    const dotsG = s("g", { visibility: "hidden" });
    svg.append(cross, dotsG);
    const hit = s("rect", { x: m.left, y: 0, width: iw, height: H, fill: "transparent", tabindex: 0 });
    svg.append(hit);
    root.insertBefore(svg, tooltip);

    const xGrid = opts.series[0].points.filter((p) => p.x >= x0 && p.x <= x1);
    let focusIdx = -1;

    function showAt(xVal, fromSync = false) {
      if (!xGrid.length) return;
      const i = bisect(xGrid, xVal);
      focusIdx = i;
      const xv = xGrid[i].x;
      const X = sx(xv);
      cross.setAttribute("x1", X); cross.setAttribute("x2", X);
      cross.setAttribute("visibility", "visible");
      dotsG.replaceChildren();
      const rows = [];
      opts.series.forEach((se) => {
        const pts = se.points;
        if (!pts.length) return;
        const p = pts[bisect(pts, xv)];
        if (p.y !== null && p.y !== undefined) {
          dotsG.append(s("circle", { cx: sx(p.x), cy: sy(p.y), r: 4.5, fill: se.color, stroke: "var(--surface)", "stroke-width": 2 }));
        }
        rows.push({ se, p });
      });
      dotsG.setAttribute("visibility", "visible");
      tooltip.replaceChildren(h("div", { class: "tt-title" }, opts.tooltipTitle ? opts.tooltipTitle(xGrid[i]) : String(xv)));
      for (const { se, p } of rows) {
        const sw = h("i");
        sw.style.background = se.color;
        const val = p.y === null || p.y === undefined ? "missing" : `${fmtVal(p.y, opts.digits)}${opts.unit ? ` ${opts.unit}` : ""}`;
        tooltip.append(h("div", { class: "tt-row" }, sw, h("b", {}, val), multi || se.name ? h("span", {}, se.name) : null));
        if (p.extra) for (const line of p.extra) tooltip.append(h("div", { class: "tt-row" }, h("span", {}, line)));
      }
      tooltip.classList.remove("hidden");
      const tw = tooltip.offsetWidth;
      let left = X + 12;
      if (left + tw > W) left = X - tw - 12;
      tooltip.style.left = `${Math.max(0, left)}px`;
      tooltip.style.top = `${m.top}px`;
      if (!fromSync && opts.syncGroup) for (const c of syncGroups.get(opts.syncGroup) || []) if (c !== api) c.showAt(xv, true);
    }
    function hide(fromSync = false) {
      cross.setAttribute("visibility", "hidden");
      dotsG.setAttribute("visibility", "hidden");
      tooltip.classList.add("hidden");
      focusIdx = -1;
      if (!fromSync && opts.syncGroup) for (const c of syncGroups.get(opts.syncGroup) || []) if (c !== api) c.hide(true);
    }
    const toX = (ev) => {
      const r = svg.getBoundingClientRect();
      const px = ((ev.clientX - r.left) / r.width) * W;
      return x0 + ((px - m.left) / iw) * (x1 - x0);
    };
    hit.addEventListener("pointermove", (ev) => showAt(toX(ev)));
    hit.addEventListener("pointerleave", () => hide());
    hit.addEventListener("focus", () => showAt(xGrid[xGrid.length - 1]?.x ?? x0));
    hit.addEventListener("blur", () => hide());
    hit.addEventListener("keydown", (ev) => {
      if (focusIdx < 0) return;
      if (ev.key === "ArrowLeft" || ev.key === "ArrowRight") {
        ev.preventDefault();
        const ni = Math.min(xGrid.length - 1, Math.max(0, focusIdx + (ev.key === "ArrowRight" ? 1 : -1)));
        showAt(xGrid[ni].x);
      } else if (ev.key === "Enter" && opts.onPointClick) {
        opts.onPointClick(xGrid[focusIdx], 0);
      }
    });
    if (opts.onPointClick) {
      hit.style.cursor = "pointer";
      hit.addEventListener("click", (ev) => opts.onPointClick(xGrid[bisect(xGrid, toX(ev))], 0));
    }
    api.showAt = showAt;
    api.hide = hide;
  }

  api = { showAt() {}, hide() {}, destroy() { ro.disconnect(); if (opts.syncGroup) syncGroups.get(opts.syncGroup)?.delete(api); } };
  if (opts.syncGroup) {
    if (!syncGroups.has(opts.syncGroup)) syncGroups.set(opts.syncGroup, new Set());
    syncGroups.get(opts.syncGroup).add(api);
  }
  return api;
}

function fmtVal(v, digits) {
  const d = digits ?? (Math.abs(v) >= 100 ? 0 : Math.abs(v) >= 10 ? 1 : 2);
  return Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
}

export function legend(items) {
  return h("div", { class: "chart-legend" }, items.map(({ name, color, box }) => {
    const sw = h("i", { class: box ? "box" : "" });
    sw.style.background = color;
    return h("span", {}, sw, name);
  }));
}

/** A simple data table: the always-available alternative to the chart. */
export function dataTable(columns, rows) {
  return h("table", {},
    h("thead", {}, h("tr", {}, columns.map((c) => h("th", { class: c.num ? "num" : "" }, c.label)))),
    h("tbody", {}, rows.map((r) => h("tr", {}, columns.map((c) => h("td", { class: c.num ? "num" : "" }, c.value(r)))))));
}

/**
 * A chart card: title, description, latest value, chart, legend and a table toggle.
 */
export function chartCard({ title, desc, latest, latestSub, legendItems, table, build }) {
  const body = h("div");
  const tableBox = h("div", { class: "chart-table hidden" });
  const toggle = h("button", { type: "button", class: "linklike", "aria-expanded": "false" }, "Show table");
  toggle.addEventListener("click", () => {
    const open = tableBox.classList.toggle("hidden") === false;
    toggle.textContent = open ? "Hide table" : "Show table";
    toggle.setAttribute("aria-expanded", String(open));
    if (open && !tableBox.firstChild && table) tableBox.append(table());
  });
  const card = h("article", { class: "card chart-card" },
    h("header", {},
      h("div", {}, h("h3", {}, title), desc ? h("p", { class: "desc" }, desc) : null),
      latest !== undefined ? h("div", { class: "latest" }, h("b", {}, latest), latestSub ? h("span", {}, latestSub) : null) : null),
    body,
    legendItems ? legend(legendItems) : null,
    table ? h("div", { class: "chart-tools" }, toggle) : null,
    tableBox);
  queueMicrotask(() => build(body));
  return card;
}
