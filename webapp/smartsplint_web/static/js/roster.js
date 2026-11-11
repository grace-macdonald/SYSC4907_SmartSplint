// Clinician roster: every participant with their status at a glance.

import { api, badge, clear, fmt, h } from "./core.js";

async function load() {
  const list = await api("/api/participants");
  renderSummary(list);
  renderTable(list);
}

function renderSummary(list) {
  const active = list.filter((p) => p.last_session_at && Date.now() - new Date(p.last_session_at) < 7 * 86400000).length;
  const safety = list.reduce((a, p) => a + p.open_safety, 0);
  const alerts = list.reduce((a, p) => a + p.open_alerts, 0);
  const pending = list.filter((p) => !p.consented).length;
  const tile = (label, value, note) => h("div", { class: "card tile" },
    h("div", { class: "label" }, label), h("div", { class: "value" }, fmt.num(value)), note ? h("div", { class: "delta" }, note) : null);
  clear(document.getElementById("summary"),
    tile("Participants", list.length, `${active} walked in the last 7 days`),
    tile("Open safety events", safety, safety ? "Need review" : "None open"),
    tile("Open gait alerts", alerts - safety),
    tile("Awaiting consent", pending, pending ? "No data stored for these" : null));
}

function statusCell(p) {
  if (!p.consented) return badge("Awaiting consent", "info");
  if (p.open_safety) return badge(`${p.open_safety} safety`, "critical");
  if (p.open_alerts) return badge(`${p.open_alerts} alert${p.open_alerts > 1 ? "s" : ""}`, "warning");
  return badge("No open alerts", "good");
}

function renderTable(list) {
  // Safety first, then open alerts, then by ID.
  const rank = (p) => (p.open_safety ? 0 : p.open_alerts ? 1 : p.consented ? 2 : 3);
  const rows = list.slice().sort((a, b) => rank(a) - rank(b) || a.id.localeCompare(b.id));
  clear(document.getElementById("roster"),
    h("thead", {}, h("tr", {},
      h("th", {}, "Participant"), h("th", {}, "Status"), h("th", { class: "num" }, "Support"),
      h("th", { class: "num" }, "Push-off torque"), h("th", { class: "num" }, "Body mass"),
      h("th", {}, "Last session"), h("th", { class: "num" }, "Sessions"))),
    h("tbody", {}, rows.map((p) => {
      const href = `/clinician/participants/${encodeURIComponent(p.id)}`;
      return h("tr", { class: "clickable", onclick: (e) => { if (e.target.tagName !== "A") location.href = href; } },
        h("td", {}, h("a", { href }, h("b", {}, p.id))),
        h("td", {}, statusCell(p)),
        h("td", { class: "num" }, `${p.support_level_pct}%`, h("span", { class: "muted" }, ` / ${p.support_max_pct}%`)),
        h("td", { class: "num" }, p.reference_torque_nm === null ? "—" : `${fmt.num(p.reference_torque_nm, 1)} N·m`),
        h("td", { class: "num" }, p.body_mass_kg === null ? "—" : `${fmt.num(p.body_mass_kg, 1)} kg`),
        h("td", {}, p.last_session_at ? fmt.ago(p.last_session_at) : h("span", { class: "muted" }, "none")),
        h("td", { class: "num" }, fmt.num(p.session_count)));
    })));
}

// ------------------------------------------------------------------ add participant

const dialog = document.getElementById("add-dialog");
const form = document.getElementById("add-form");
const result = document.getElementById("add-result");
document.getElementById("add-btn").addEventListener("click", () => {
  form.reset();
  form.hidden = false;
  result.hidden = true;
  dialog.showModal();
});
form.addEventListener("submit", async (e) => {
  if (e.submitter?.value !== "create") return;
  e.preventDefault();
  const mass = form.elements.mass.value;
  try {
    const r = await api("/api/participants", { method: "POST", body: mass ? { body_mass_kg: Number(mass) } : {} });
    document.getElementById("new-pid").textContent = r.participant_id;
    document.getElementById("new-pid-2").textContent = r.participant_id;
    document.getElementById("new-pass").textContent = r.password;
    form.hidden = true;
    result.hidden = false;
    load();
  } catch (err) {
    alertBox(err.message);
  }
});
document.getElementById("add-done").addEventListener("click", () => dialog.close());

function alertBox(msg) {
  form.querySelector(".notice")?.remove();
  form.prepend(h("div", { class: "notice critical", role: "alert" }, msg));
}

load().catch((e) => console.error(e));
setInterval(() => { if (!document.hidden) load().catch(() => {}); }, 15000);
