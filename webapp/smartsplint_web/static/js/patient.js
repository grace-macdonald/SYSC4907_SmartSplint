// Patient view: own progress, alerts, sessions, and privacy controls.

import { api, clear, eventItem, fmt, h, sessionDuration, watchAlerts } from "./core.js";
import { initEstop } from "./estop.js";
import { renderProgress, windowTotals } from "./progress.js";

const pid = document.body.dataset.pid;
initEstop();

async function load() {
  const [p, sessions, events] = await Promise.all([
    api(`/api/participants/${pid}`),
    api(`/api/participants/${pid}/sessions`),
    api(`/api/participants/${pid}/events?category=anomaly,safety&limit=5`),
  ]);
  renderHero(p, sessions);
  renderTiles(sessions);
  renderAlerts(events);
  renderProgress(document.getElementById("progress"), sessions, p, { mode: "patient" });
  renderSessions(sessions);
  document.getElementById("consent-line").textContent =
    `You gave consent on ${fmt.date(p.consent_given_at)}. Your data is stored under ${p.id} only, and is deleted automatically after 12 months.`;
}

function renderHero(p, sessions) {
  const walks = sessions.filter((s) => s.kind === "walk");
  const first = walks[0];
  const last = walks[walks.length - 1];
  let story;
  if (!walks.length) story = "Your care team will set your support level. Your first walk will appear here.";
  else if (first.support_level_pct > p.support_level_pct) {
    story = `Down from ${first.support_level_pct}% when you started on ${fmt.date(first.started_at)}. Less support means your calf is doing more of the work.`;
  } else story = "Your care team sets your support level and lowers it as your ankle gets stronger.";

  const meter = h("div", { class: "meter", role: "img", "aria-label": `Support ${p.support_level_pct}% of maximum` });
  const fill = h("span");
  fill.style.width = `${p.support_level_pct}%`;
  meter.append(fill);

  clear(document.getElementById("hero"),
    h("div", { class: "hero" },
      h("div", {},
        h("div", { class: "secondary small" }, "Brace support"),
        h("div", { class: "hero-figure" }, String(p.support_level_pct), h("small", {}, "%"))),
      h("div", { class: "hero-text" },
        meter,
        h("p", {}, story),
        last ? h("p", { class: "muted small" }, `Last walk ${fmt.ago(last.started_at)}, ${sessionDuration(last)}.`) : null)));
}

function renderTiles(sessions) {
  const now = Date.now();
  const WEEK = 7 * 86400000;
  const cur = windowTotals(sessions, now - WEEK, now + 1);
  const prev = windowTotals(sessions, now - 2 * WEEK, now - WEEK);
  const tile = (label, value, unit, prevValue, lowerIsBetter = false) => {
    let delta = null;
    if (prevValue !== undefined) {
      const d = value - prevValue;
      const cls = d === 0 ? "" : (d < 0) === lowerIsBetter ? "good" : "";
      delta = h("div", { class: `delta ${cls}` }, d === 0 ? "same as last week" : `${d > 0 ? "+" : "−"}${fmt.num(Math.abs(d))} vs last week`);
    }
    return h("div", { class: "card tile" },
      h("div", { class: "label" }, label),
      h("div", { class: "value" }, fmt.num(value), unit ? h("small", {}, unit) : null),
      delta);
  };
  clear(document.getElementById("tiles"),
    tile("Walking sessions", cur.sessions, "", prev.sessions),
    tile("Time walking", cur.minutes, "min", prev.minutes),
    tile("Strides", cur.strides, "", prev.strides),
    tile("Walking alerts", cur.alerts, "", prev.alerts, true));
}

function renderAlerts(events) {
  const ul = document.getElementById("alerts");
  if (!events.length) {
    clear(ul, h("li", { class: "empty" }, "No alerts. Nice walking."));
    return;
  }
  clear(ul, events.map((e) => eventItem(e, { patient: true })));
}

function renderSessions(sessions) {
  const rows = sessions.slice().reverse().slice(0, 10);
  clear(document.getElementById("sessions"),
    h("thead", {}, h("tr", {}, h("th", {}, "Date"), h("th", { class: "hide-sm" }, "Type"), h("th", { class: "num" }, "Length"),
      h("th", { class: "num" }, "Support"), h("th", { class: "num hide-sm" }, "Strides"), h("th", { class: "num" }, "Alerts"))),
    h("tbody", {}, rows.map((s) => h("tr", { class: "clickable", onclick: () => (location.href = `/sessions/${s.id}`) },
      h("td", {}, h("a", { href: `/sessions/${s.id}` }, fmt.dateTime(s.started_at))),
      h("td", { class: "hide-sm" }, s.kind === "calibration" ? "Calibration walk" : "Walk"),
      h("td", { class: "num" }, sessionDuration(s)),
      h("td", { class: "num" }, `${s.support_level_pct ?? 0}%`),
      h("td", { class: "num hide-sm" }, fmt.num(s.strides)),
      h("td", { class: "num" }, s.kind === "walk" ? fmt.num(s.anomaly_count) : "—")))));
}

// ------------------------------------------------------------------ notifications

const notifyBtn = document.getElementById("notify-btn");
if ("Notification" in window && Notification.permission === "default") {
  notifyBtn.hidden = false;
  notifyBtn.addEventListener("click", async () => {
    await Notification.requestPermission();
    notifyBtn.hidden = true;
  });
}

watchAlerts(pid, { patient: true, onNew: () => load() });

// ------------------------------------------------------------------ delete my data

const dialog = document.getElementById("delete-dialog");
const confirmInput = document.getElementById("delete-confirm");
const goBtn = document.getElementById("delete-go");
document.getElementById("delete-btn").addEventListener("click", () => {
  confirmInput.value = "";
  goBtn.disabled = true;
  dialog.showModal();
});
confirmInput.addEventListener("input", () => { goBtn.disabled = confirmInput.value.trim().toUpperCase() !== pid; });
dialog.addEventListener("close", async () => {
  if (dialog.returnValue !== "confirm") return;
  await api(`/api/participants/${pid}/data`, { method: "DELETE" });
  location.href = "/consent";
});

load().catch((e) => console.error(e));
