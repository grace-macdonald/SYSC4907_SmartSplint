// Clinician view of one participant: settings, alerts, progress, sessions, event log.

import { api, badge, clear, eventItem, fmt, h, sessionDuration, toast, watchAlerts } from "./core.js";
import { initEstop } from "./estop.js";
import { renderProgress } from "./progress.js";

const pid = document.body.dataset.pid;
const TORQUE_PER_KG = 0.61; // FR-6; mirrors domain.py for the live preview only (server is authoritative)
initEstop();

let participant = null;
let sessions = [];
let rangeDays = 0;
let logCat = "";

async function load() {
  [participant, sessions] = await Promise.all([api(`/api/participants/${pid}`), api(`/api/participants/${pid}/sessions`)]);
  renderHead();
  renderSettings();
  renderProgress(document.getElementById("progress"), sessions, participant, { mode: "clinician", sinceDays: rangeDays });
  renderSessions();
  await Promise.all([loadOpenAlerts(), loadLog()]);
}

function renderHead() {
  const p = participant;
  const walks = sessions.filter((s) => s.kind === "walk");
  const bits = [`Enrolled ${fmt.date(p.created_at)}`, `${walks.length} walking session${walks.length === 1 ? "" : "s"}`];
  if (walks.length) bits.push(`last ${fmt.ago(walks[walks.length - 1].started_at)}`);
  document.getElementById("head-sub").textContent = bits.join(" · ");
  const warn = document.getElementById("consent-warning");
  clear(warn, p.consented ? null : h("div", { class: "notice warning flash" },
    h("div", {}, h("strong", {}, "No consent recorded. "),
      "The brace cannot upload data for this participant until they sign in and give consent (FR-12).")));
}

// ------------------------------------------------------------------ settings

function numberField(name, label, value, { min, max, step = 0.1, hint, placeholder } = {}) {
  return h("label", { class: "field" }, label,
    h("input", { type: "number", name, value: value ?? "", min, max, step, placeholder, inputmode: "decimal" }),
    hint ? h("span", { class: "hint" }, hint) : null);
}

function levelSelect(name, label, value, maxAllowed = 100, hint) {
  const sel = h("select", { name });
  for (let v = 0; v <= 100; v += 5) sel.append(h("option", { value: v, disabled: v > maxAllowed, selected: v === value }, `${v}%`));
  return h("label", { class: "field" }, label, sel, hint ? h("span", { class: "hint" }, hint) : null);
}

function renderSettings() {
  const p = participant;
  const form = document.getElementById("settings");
  const preview = h("div", { class: "notice" });
  const errBox = h("div");
  const saveBtn = h("button", { class: "primary", type: "submit" }, "Save changes");
  const resetBtn = h("button", { type: "button", onclick: () => renderSettings() }, "Discard");

  clear(form,
    h("div", { class: "form-grid" },
      levelSelect("support_level_pct", "Support level", p.support_level_pct, p.support_max_pct, "At push-off, % of 100% torque"),
      levelSelect("support_max_pct", "Maximum level", p.support_max_pct, 100, "The level can't be set above this"),
      numberField("body_mass_kg", "Body mass (kg)", p.body_mass_kg, { min: 20, max: 250, hint: "Sets 100% torque (FR-6)" }),
      numberField("weight_bearing_limit_kg", "Weight-bearing limit (kg)", p.weight_bearing_limit_kg,
        { min: 0, max: 250, placeholder: "No limit", hint: "Assistance pauses above this" }),
      numberField("rom_plantar_limit_deg", "Plantarflexion limit (°)", p.rom_plantar_limit_deg, { min: 0, max: 25, step: 1, hint: "Hard stop is 25°" }),
      numberField("rom_dorsi_limit_deg", "Dorsiflexion limit (°)", p.rom_dorsi_limit_deg, { min: 0, max: 15, step: 1, hint: "Hard stop is 15°" })),
    preview,
    p.motor_safe_torque_verified ? null : h("div", { class: "notice warning" },
      h("div", {}, h("strong", {}, "Motor safe torque not yet verified. "),
        `Torque is capped at a placeholder of ${p.motor_safe_torque_nm} N·m until the bench test (P8) sets the real value.`)),
    errBox,
    h("div", { class: "form-actions" }, saveBtn, resetBtn));

  const f = form.elements;
  const levelOpts = f.support_level_pct;

  function updatePreview() {
    const mass = parseFloat(f.body_mass_kg.value);
    const level = Number(levelOpts.value);
    const max = Number(f.support_max_pct.value);
    for (const o of levelOpts.options) o.disabled = Number(o.value) > max;
    if (!Number.isFinite(mass)) {
      clear(preview, h("div", {}, "Enter body mass to calculate the support torque."));
      return;
    }
    const raw = mass * TORQUE_PER_KG;
    const full = Math.min(raw, p.motor_safe_torque_nm);
    const capped = raw > p.motor_safe_torque_nm;
    clear(preview, h("div", {},
      h("div", {}, h("strong", {}, `${fmt.num((full * level) / 100, 1)} N·m`), ` requested at push-off at ${level}%.`),
      h("div", { class: "small" }, `100% = ${fmt.num(mass, 1)} kg × ${TORQUE_PER_KG} N·m/kg = ${fmt.num(raw, 1)} N·m`,
        capped ? `, capped at the motor's safe torque of ${p.motor_safe_torque_nm} N·m.` : ".",
        " Swing assistance uses a small fixed dorsiflexion torque.")));
  }
  form.oninput = updatePreview;
  updatePreview();

  form.onsubmit = async (e) => {
    e.preventDefault();
    const num = (v) => (v === "" ? null : Number(v));
    const next = {
      support_level_pct: Number(f.support_level_pct.value),
      support_max_pct: Number(f.support_max_pct.value),
      body_mass_kg: num(f.body_mass_kg.value),
      weight_bearing_limit_kg: num(f.weight_bearing_limit_kg.value),
      rom_plantar_limit_deg: num(f.rom_plantar_limit_deg.value),
      rom_dorsi_limit_deg: num(f.rom_dorsi_limit_deg.value),
    };
    const changes = Object.fromEntries(Object.entries(next).filter(([k, v]) => v !== p[k]));
    if (!Object.keys(changes).length) { clear(errBox); return; }
    saveBtn.disabled = true;
    try {
      participant = await api(`/api/participants/${pid}`, { method: "PATCH", body: changes });
      toast({ title: "Settings saved", message: "The brace picks them up at its next sync.", status: "good", timeout: 4000 });
      renderSettings();
      loadLog();
      renderProgress(document.getElementById("progress"), sessions, participant, { mode: "clinician", sinceDays: rangeDays });
    } catch (err) {
      clear(errBox, h("div", { class: "notice critical", role: "alert" }, err.message));
    } finally {
      saveBtn.disabled = false;
    }
  };
}

// ------------------------------------------------------------------ alerts & log

async function acknowledge(ev) {
  await api(`/api/events/${ev.id}/acknowledge`, { method: "POST" });
  await Promise.all([loadOpenAlerts(), loadLog()]);
}

async function loadOpenAlerts() {
  const open = await api(`/api/participants/${pid}/events?category=safety,anomaly&open=1&limit=50`);
  open.sort((a, b) => (a.category === "safety" ? 0 : 1) - (b.category === "safety" ? 0 : 1));
  const ul = document.getElementById("open-alerts");
  ul.parentElement.classList.toggle("card-scroll", open.length > 5);
  clear(ul, open.length ? open.map((e) => eventItem(e, { onAck: acknowledge })) : h("li", { class: "empty" }, "No open alerts."));
  const ackAll = document.getElementById("ack-all");
  ackAll.hidden = open.length < 2;
  ackAll.onclick = async () => {
    ackAll.disabled = true;
    for (const e of open) await api(`/api/events/${e.id}/acknowledge`, { method: "POST" });
    ackAll.disabled = false;
    await Promise.all([loadOpenAlerts(), loadLog()]);
  };
}

async function loadLog() {
  const q = logCat ? `&category=${logCat}` : "";
  const evs = await api(`/api/participants/${pid}/events?limit=60${q}`);
  const ul = document.getElementById("log");
  ul.parentElement.classList.add("card-scroll");
  clear(ul, evs.length ? evs.map((e) => eventItem(e, { onAck: acknowledge })) : h("li", { class: "empty" }, "Nothing logged."));
}

for (const b of document.querySelectorAll("#log-filter button")) {
  b.addEventListener("click", () => {
    logCat = b.dataset.cat;
    for (const o of document.querySelectorAll("#log-filter button")) o.setAttribute("aria-pressed", String(o === b));
    loadLog();
  });
}
for (const b of document.querySelectorAll("#range button")) {
  b.addEventListener("click", () => {
    rangeDays = Number(b.dataset.days);
    for (const o of document.querySelectorAll("#range button")) o.setAttribute("aria-pressed", String(o === b));
    const box = document.getElementById("progress");
    box.style.opacity = "0.5";
    renderProgress(box, sessions, participant, { mode: "clinician", sinceDays: rangeDays });
    box.style.opacity = "";
  });
}

// ------------------------------------------------------------------ sessions table

function renderSessions() {
  const rows = sessions.slice().reverse();
  const tbl = document.getElementById("sessions");
  tbl.parentElement.classList.add("card-scroll");
  const torqueErr = (s) => (s.push_off_torque_nm && s.reference_torque_nm
    ? (100 * (s.push_off_torque_nm - s.reference_torque_nm)) / s.reference_torque_nm : null);
  clear(tbl,
    h("thead", {}, h("tr", {},
      h("th", {}, "Started"), h("th", {}, "Type"), h("th", { class: "num" }, "Length"), h("th", { class: "num" }, "Support"),
      h("th", { class: "num" }, "Strides"), h("th", { class: "num" }, "Stride CV"), h("th", { class: "num" }, "Torque error"),
      h("th", { class: "num" }, "Peak load"), h("th", {}, "Alerts"), h("th", {}, "Data"))),
    h("tbody", {}, rows.length ? rows.map((s) => {
      const te = torqueErr(s);
      return h("tr", { class: "clickable", onclick: (e) => { if (e.target.tagName !== "A") location.href = `/sessions/${s.id}`; } },
        h("td", {}, h("a", { href: `/sessions/${s.id}` }, fmt.dateTime(s.started_at))),
        h("td", {}, s.kind),
        h("td", { class: "num" }, sessionDuration(s)),
        h("td", { class: "num" }, `${s.support_level_pct ?? 0}%`),
        h("td", { class: "num" }, fmt.num(s.strides)),
        h("td", { class: "num" }, s.stride_time_cv_pct === null ? "—" : `${fmt.num(s.stride_time_cv_pct, 1)}%`),
        h("td", { class: "num" }, te === null ? "—" : `${te > 0 ? "+" : ""}${fmt.num(te, 1)}%`),
        h("td", { class: "num" }, s.peak_plantar_load_kg === null ? "—" : `${fmt.num(s.peak_plantar_load_kg, 1)} kg`),
        h("td", {}, s.safety_count ? badge(`${s.safety_count} safety`, "critical") : null, " ",
          s.anomaly_count ? badge(String(s.anomaly_count), "warning") : s.kind === "walk" ? h("span", { class: "muted" }, "0") : null),
        h("td", {}, s.has_samples ? h("a", { href: `/api/sessions/${s.id}/export.csv` }, "CSV") : h("span", { class: "muted small" }, "summary only")));
    }) : h("tr", {}, h("td", { colspan: 10, class: "empty" }, "No sessions yet."))));
}

// ------------------------------------------------------------------ deletion

for (const b of document.querySelectorAll("[data-delete]")) {
  b.addEventListener("click", async () => {
    const whole = b.dataset.delete === "participant";
    const typed = prompt(whole
      ? `Remove ${pid} completely (login, paired braces and all data)? Type ${pid} to confirm.`
      : `Delete all sessions, recordings and events for ${pid} and withdraw consent? Type ${pid} to confirm.`);
    if (typed?.trim().toUpperCase() !== pid) return;
    await api(whole ? `/api/participants/${pid}` : `/api/participants/${pid}/data`, { method: "DELETE" });
    if (whole) location.href = "/clinician";
    else load();
  });
}

watchAlerts(pid, { onNew: () => load() });
load().catch((e) => console.error(e));
