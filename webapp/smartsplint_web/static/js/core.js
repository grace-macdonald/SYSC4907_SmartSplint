// Shared helpers: API calls, DOM building, formatting, icons, alerts.
// All untrusted text goes through textContent (never innerHTML).

export async function api(path, { method = "GET", body } = {}) {
  const opts = { method, headers: { "X-Requested-With": "smartsplint" }, credentials: "same-origin" };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (res.status === 401) {
    location.href = `/login?next=${encodeURIComponent(location.pathname)}`;
    throw new Error("Not signed in");
  }
  const data = res.headers.get("content-type")?.includes("json") ? await res.json() : null;
  if (!res.ok) throw new Error(data?.error || `Request failed (${res.status})`);
  return data;
}

/** Build an element: h("div", {class: "x", onclick: fn}, child, "text", ...) */
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (k === "dataset") Object.assign(el.dataset, v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export function clear(el, ...children) {
  el.replaceChildren();
  append(el, children);
  return el;
}

// ------------------------------------------------------------------ icons

const SVG_NS = "http://www.w3.org/2000/svg";
const ICON_PATHS = {
  critical: ["M8 1.5 15 14H1z", "M8 6v3.5", "M8 11.7v.1"],
  warning: ["M8 1.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13z", "M8 4.8v4", "M8 11v.1"],
  info: ["M8 1.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13z", "M8 7.2v4", "M8 4.8v.1"],
  good: ["M8 1.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13z", "M5 8.2l2 2 4-4"],
  config: ["M2 4.5h7", "M12 4.5h2", "M2 11.5h2", "M7 11.5h7", "M10.5 3v3", "M5.5 10v3"],
  close: ["M4 4l8 8", "M12 4l-8 8"],
  bluetooth: ["M4.5 5l7 6-3.5 3V2l3.5 3-7 6"],
};

export function icon(name) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.6");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  for (const d of ICON_PATHS[name] || ICON_PATHS.info) {
    const p = document.createElementNS(SVG_NS, "path");
    p.setAttribute("d", d);
    svg.append(p);
  }
  return svg;
}

export function badge(text, status = "info", withIcon = true) {
  return h("span", { class: `badge ${status}` }, withIcon ? icon(status) : null, text);
}

// ------------------------------------------------------------------ formatting

const dateFmt = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });
const dateTimeFmt = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
const timeFmt = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });
const rel = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

export const fmt = {
  date: (iso) => (iso ? dateFmt.format(new Date(iso)) : "—"),
  dateTime: (iso) => (iso ? dateTimeFmt.format(new Date(iso)) : "—"),
  time: (iso) => (iso ? timeFmt.format(new Date(iso)) : "—"),
  num: (v, digits = 0) => (v === null || v === undefined || Number.isNaN(v) ? "—"
    : Number(v).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })),
  ago(iso) {
    if (!iso) return "never";
    const s = (new Date(iso) - Date.now()) / 1000;
    const a = Math.abs(s);
    if (a < 60) return "just now";
    if (a < 3600) return rel.format(Math.round(s / 60), "minute");
    if (a < 86400) return rel.format(Math.round(s / 3600), "hour");
    return rel.format(Math.round(s / 86400), "day");
  },
  minutes: (a, b) => (a && b ? Math.round((new Date(b) - new Date(a)) / 60000) : null),
};

export const PHASE_LABEL = { stance: "Stance", push_off: "Push-off", swing: "Swing" };

// ------------------------------------------------------------------ events

export function eventItem(ev, { patient = false, onAck } = {}) {
  const status = ev.severity === "critical" ? "critical" : ev.severity === "warning" ? "warning" : "info";
  const ic = ev.category === "config" ? icon("config") : icon(status);
  const meta = [fmt.dateTime(ev.occurred_at)];
  if (ev.detail?.by) meta.push(`by ${ev.detail.by}`);
  if (ev.detail?.from !== undefined && ev.detail?.to !== undefined) {
    const unit = ev.type === "support_level_changed" ? "%" : ev.type === "body_mass_changed" ? " kg" : "";
    meta.push(`${ev.detail.from ?? "—"}${unit} → ${ev.detail.to}${unit}`);
  }
  if (ev.detail?.bout_strides) meta.push(`${ev.detail.bout_strides} strides`);
  if (ev.detail?.plantar_load_kg) meta.push(`${ev.detail.plantar_load_kg} kg (limit ${ev.detail.limit_kg} kg)`);
  if (ev.type === "app_estop" && ev.detail) meta.push(ev.detail.ble_delivered ? "stop delivered to brace" : "not confirmed by brace");
  if (ev.detail?.changes) meta.push(Object.keys(ev.detail.changes).map((k) => k.replace(/_/g, " ")).join(", "));

  let right = null;
  if (!patient && ev.category !== "config") {
    right = ev.acknowledged_at
      ? h("span", { class: "small muted" }, `Ack'd ${ev.acknowledged_by ?? ""}`)
      : onAck ? h("button", { type: "button", class: "small", onclick: () => onAck(ev) }, "Acknowledge") : null;
  }
  if (ev.session_id) {
    right = h("div", { class: "stack" }, right, h("a", { class: "small", href: `/sessions/${ev.session_id}` }, "View session"));
  }
  return h("li", { class: `${status} ${ev.acknowledged_at && !patient ? "acked" : ""}` },
    h("span", { class: "ev-icon" }, ic),
    h("div", {},
      h("div", { class: "ev-title" }, ev.label),
      patient && ev.patient_message ? h("div", { class: "ev-msg" }, ev.patient_message) : null,
      h("div", { class: "ev-meta" }, meta.join(" · "))),
    right);
}

// ------------------------------------------------------------------ toasts & live alerts

export function toast({ title, message, status = "info", timeout = 9000 }) {
  const box = document.getElementById("toasts");
  if (!box) return;
  const t = h("div", { class: `toast ${status}`, role: "status" },
    icon(status),
    h("div", {}, h("div", { class: "t-title" }, title), message ? h("div", { class: "t-msg" }, message) : null),
    h("button", { type: "button", class: "t-close", "aria-label": "Dismiss", onclick: () => t.remove() }, icon("close")));
  box.append(t);
  if (timeout) setTimeout(() => t.remove(), timeout);
}

/**
 * Poll for new anomaly/safety events and surface them (P9: notify within 5 s).
 * Polls every 3 s. The patient view keeps polling in the background so it can raise a
 * system notification; the clinician view pauses while hidden and catches up after.
 */
export function watchAlerts(pid, { onNew, intervalMs = 3000, patient = false } = {}) {
  let lastId = null;
  let timer = null;
  async function tick() {
    try {
      if (lastId === null) {
        const latest = await api(`/api/participants/${pid}/events?limit=1`);
        lastId = latest[0]?.id ?? 0;
      } else {
        const fresh = await api(`/api/participants/${pid}/events?after_id=${lastId}&category=anomaly,safety`);
        if (fresh.length) {
          lastId = Math.max(lastId, ...fresh.map((e) => e.id));
          for (const ev of fresh.reverse()) {
            const status = ev.severity === "critical" ? "critical" : ev.severity === "warning" ? "warning" : "info";
            toast({ title: ev.label, message: patient ? ev.patient_message : fmt.dateTime(ev.occurred_at), status });
            if (patient && "Notification" in window && Notification.permission === "granted" && document.hidden) {
              new Notification(`Smart Splint: ${ev.label}`, { body: ev.patient_message || "", tag: `ev-${ev.id}` });
            }
          }
          onNew?.(fresh);
        }
      }
    } catch (e) {
      console.warn("alert poll failed", e);
    }
  }
  function start() { if (!timer) { tick(); timer = setInterval(tick, intervalMs); } }
  function stop() { clearInterval(timer); timer = null; }
  if (!patient) document.addEventListener("visibilitychange", () => (document.hidden ? stop() : start()));
  start();
}

export function sessionDuration(s) {
  const m = fmt.minutes(s.started_at, s.ended_at);
  return m === null ? "—" : `${m} min`;
}
