// Session detail: summary, synchronised signals with the detected gait phase, and events.

import { api, clear, eventItem, fmt, h, PHASE_LABEL, sessionDuration } from "./core.js";
import { legend, lineChart } from "./charts.js";

const sid = Number(location.pathname.split("/").pop());
const isPatient = document.body.dataset.role === "participant";
const PHASE_COLOR = { stance: "var(--phase-stance)", push_off: "var(--phase-push)", swing: "var(--phase-swing)" };

let data = null;
let participant = null;
let winLen = 10;
let winStart = 0;
let charts = [];

async function load() {
  data = await api(`/api/sessions/${sid}?max_points=20000`);
  participant = await api(`/api/participants/${data.session.participant_id}`);
  renderHeader();
  renderTiles();
  renderEvents();
  setupWindow();
  renderSignals();
}

function renderHeader() {
  const s = data.session;
  document.getElementById("title").textContent =
    `${s.kind === "calibration" ? "Calibration walk" : s.kind === "bench" ? "Bench test" : "Walk"} · ${fmt.dateTime(s.started_at)}`;
  document.getElementById("subtitle").textContent =
    `${s.participant_id} · session ${s.id}${s.notes ? ` · ${s.notes}` : ""}`;
}

function renderTiles() {
  const s = data.session;
  const tiles = [
    ["Length", sessionDuration(s)],
    ["Support level", `${s.support_level_pct ?? 0}%`, s.reference_torque_nm ? `${fmt.num(s.reference_torque_nm, 1)} N·m requested` : null],
    ["Strides", fmt.num(s.strides), s.cadence_spm ? `${fmt.num(s.cadence_spm)} steps/min` : null],
  ];
  if (s.stride_time_cv_pct !== null) tiles.push(["Stride time variation", `${fmt.num(s.stride_time_cv_pct, 1)}%`, s.stride_time_mean_s ? `mean stride ${fmt.num(s.stride_time_mean_s, 2)} s` : null]);
  if (!isPatient && s.push_off_torque_nm !== null) {
    const err = s.reference_torque_nm ? (100 * (s.push_off_torque_nm - s.reference_torque_nm)) / s.reference_torque_nm : null;
    tiles.push(["Measured push-off torque", `${fmt.num(s.push_off_torque_nm, 1)} N·m`, err === null ? null : `${err > 0 ? "+" : ""}${fmt.num(err, 1)}% vs requested`]);
  }
  const fc = data.foot_check;
  if (fc.windows) tiles.push(["Toe-walking check", `${fc.toe_walking} of ${fc.windows}`, `${fc.window_s} s windows flagged`]);
  if (s.peak_plantar_load_kg !== null) tiles.push(["Peak foot load", `${fmt.num(s.peak_plantar_load_kg, 1)} kg`, participant.weight_bearing_limit_kg ? `limit ${participant.weight_bearing_limit_kg} kg` : null]);
  if (!isPatient && s.model_confidence_mean !== null) tiles.push(["Gait model confidence", fmt.num(s.model_confidence_mean, 2), s.assist_active_pct !== null ? `${fmt.num(s.assist_active_pct)}% of strides assisted` : null]);
  clear(document.getElementById("tiles"), tiles.map(([label, value, note]) => h("div", { class: "card tile" },
    h("div", { class: "label" }, label), h("div", { class: "value" }, value), note ? h("div", { class: "delta" }, note) : null)));
}

function renderEvents() {
  const ul = document.getElementById("events");
  clear(ul, data.events.length
    ? data.events.map((e) => { const li = eventItem(e, { patient: isPatient }); li.querySelector("a[href^='/sessions/']")?.remove(); return li; })
    : h("li", { class: "empty" }, "No alerts or safety events in this session."));
}

// ------------------------------------------------------------------ window

function duration() {
  const ss = data.samples;
  return ss.length ? (ss[ss.length - 1].t_ms - ss[0].t_ms) / 1000 : 0;
}

function setupWindow() {
  const section = document.getElementById("signals-section");
  if (!data.samples.length) {
    clear(document.getElementById("signals"), h("div", { class: "card empty" },
      "No raw sensor data was uploaded for this session (summary metrics only)."));
    document.getElementById("window-ctl").hidden = true;
    return;
  }
  section.hidden = false;
  const range = document.getElementById("win-start");
  const update = () => {
    const d = duration();
    const len = winLen || d;
    range.max = String(Math.max(0, d - len));
    range.disabled = !winLen || d <= len;
    winStart = Math.min(winStart, Number(range.max));
    range.value = String(winStart);
    document.getElementById("win-label").textContent = `${fmt.num(winStart, 1)}–${fmt.num(winStart + len, 1)} s of ${fmt.num(d, 1)} s`;
  };
  range.addEventListener("input", () => { winStart = Number(range.value); update(); renderSignals(); });
  for (const b of document.querySelectorAll("#win-len button")) {
    b.addEventListener("click", () => {
      winLen = Number(b.dataset.sec);
      for (const o of document.querySelectorAll("#win-len button")) o.setAttribute("aria-pressed", String(o === b));
      update();
      renderSignals();
    });
  }
  update();
}

function phaseBands(samples) {
  const bands = [];
  let cur = null;
  for (const s of samples) {
    const t = s.t_ms / 1000;
    if (!cur || cur.phase !== s.gait_phase) {
      if (cur) cur.x1 = t;
      cur = s.gait_phase ? { phase: s.gait_phase, x0: t, x1: t, color: PHASE_COLOR[s.gait_phase] } : null;
      if (cur) bands.push(cur);
    } else cur.x1 = t;
  }
  return bands;
}

function renderSignals() {
  if (!data.samples.length) return;
  for (const c of charts) c.destroy();
  charts = [];
  const t0 = data.samples[0].t_ms;
  const d = duration();
  const len = winLen || d;
  const xDomain = [winStart, Math.min(d, winStart + len)];
  // Keep a little margin either side so lines enter/leave the frame cleanly.
  const inWin = data.samples.filter((s) => {
    const t = (s.t_ms - t0) / 1000;
    return t >= xDomain[0] - 0.05 && t <= xDomain[1] + 0.05;
  });
  const shifted = inWin.map((s) => ({ ...s, t: (s.t_ms - t0) / 1000 }));
  // Invalid frames (valid = 0) and missing readings (null) both draw as gaps (NFR-11).
  // The gait phase rides on the first series only, so the tooltip shows it once.
  const pts = (key, withPhase) => shifted.map((s) => ({
    x: s.t, y: s.valid ? s[key] : null,
    extra: withPhase ? [s.gait_phase ? `Phase: ${PHASE_LABEL[s.gait_phase]} (confidence ${fmt.num(s.phase_confidence, 2)})`
      : "Phase: none"].concat(s.valid ? [] : ["Invalid frame"]) : null,
  }));
  const bands = phaseBands(shifted.map((s) => ({ ...s, t_ms: s.t * 1000 })));
  const s = data.session;
  const p = participant;

  const box = document.getElementById("signals");
  clear(box);
  renderInsole(box, shifted, xDomain, bands, pts);

  const defs = [
    {
      title: "Ankle angle", desc: "Hinge potentiometer; dorsiflexion positive", unit: "°", digits: 1,
      series: [{ name: "Ankle angle", key: "ankle_angle_deg", color: "var(--series-1)" }],
      thresholds: [
        { y: p.rom_dorsi_limit_deg, label: `Dorsiflexion limit ${p.rom_dorsi_limit_deg}°`, status: "critical" },
        { y: -p.rom_plantar_limit_deg, label: `Plantarflexion limit ${p.rom_plantar_limit_deg}°`, status: "critical" },
      ],
    },
    {
      title: "Hinge torque", desc: "Measured at the ankle hinge (closed-loop feedback)", unit: "N·m", digits: 1, yZero: true,
      series: [{ name: "Hinge torque", key: "hinge_torque_nm", color: "var(--series-1)" }],
      thresholds: s.reference_torque_nm ? [{ y: s.reference_torque_nm, label: `Requested ${fmt.num(s.reference_torque_nm, 1)} N·m`, status: "neutral" }] : [],
    },
    {
      title: "Calf EMG", desc: "Envelope, normalised to the calibration walk", unit: "", digits: 2, yZero: true,
      series: [{ name: "Calf EMG", key: "emg", color: "var(--series-1)" }],
    },
    {
      title: "IMU pitch", desc: "Foot and shank orientation", unit: "°", digits: 1,
      series: [
        { name: "Foot", key: "imu_foot_pitch", color: "var(--series-1)" },
        { name: "Shank", key: "imu_shank_pitch", color: "var(--series-2)" },
      ],
    },
  ];
  if (!isPatient) {
    defs.push({
      title: "Gait model confidence", desc: "Assistance only runs above the confidence threshold (FR-5)", unit: "", digits: 2,
      yDomain: [0, 1], series: [{ name: "Confidence", key: "phase_confidence", color: "var(--series-1)" }],
    });
  }

  defs.forEach((def) => {
    const body = h("div");
    const items = def.series.length > 1 ? def.series.map((se) => ({ name: se.name, color: se.color })) : [];
    box.append(h("article", { class: "card chart-card" },
      h("header", {}, h("div", {}, h("h3", {}, def.title), h("p", { class: "desc" }, def.desc))),
      body,
      items.length ? legend(items) : null));
    charts.push(lineChart(body, {
      title: def.title,
      series: def.series.map((se, j) => ({ ...se, points: pts(se.key, j === def.series.length - 1), markers: false })),
      xType: "seconds",
      xDomain,
      yDomain: def.yDomain,
      yZero: def.yZero,
      unit: def.unit,
      digits: def.digits,
      height: 150,
      thresholds: def.thresholds,
      bands,
      tooltipTitle: (pt) => `${fmt.num(pt.x, 2)} s`,
      syncGroup: "signals",
      marginRight: 74,
    }));
  });
}

// Plantar pressure: one small chart per FSR, stacked like FootAnomaly/plot_fsr.py,
// on the raw 0-4095 ADC scale with the contact threshold and flagged windows.
const FSR_COLORS = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)", "var(--series-5)",
  "var(--ink-2)"];

function renderInsole(box, shifted, xDomain, bands, pts) {
  const ins = data.insole;
  const fc = data.foot_check;
  const sensors = ins.sensors.filter((se) => shifted.some((s) => s[se.key] !== null && s[se.key] !== undefined));
  if (!sensors.length) return;
  const spans = fc.flagged.map((w) => ({ x0: w.start_ms / 1000, x1: w.end_ms / 1000, label: "Toe walking" }));
  const heel = ins.sensors.find((se) => se.key === ins.heel_sensor);
  const verdict = fc.windows === 0
    ? "Recording is shorter than one check window."
    : fc.toe_walking
      ? `${fc.toe_walking} of ${fc.windows} windows flagged: the foot was loaded but the heel (${heel?.label}) never passed ${ins.contact_on}.`
      : `No toe walking: the heel (${heel?.label}) made contact in every loaded window (${fc.windows} windows).`;

  const stack = h("div", { class: "insole" });
  const items = [
    ...Object.entries(PHASE_COLOR).map(([k, c]) => ({ name: `${PHASE_LABEL[k]} phase`, color: c, box: true })),
  ];
  const legendRow = legend(items);
  if (fc.toe_walking) {
    const sw = h("i", { class: "box" });
    sw.style.background = "var(--critical-wash)";
    sw.style.outline = "1px solid var(--critical)";
    legendRow.append(h("span", {}, sw, `Toe-walking window (${fc.window_s} s check)`));
  }
  box.append(h("article", { class: "card chart-card" },
    h("header", {}, h("div", {},
      h("h3", {}, "Plantar pressure"),
      h("p", { class: "desc" }, `${sensors.length} FSRs, raw ADC (0–${ins.adc_max}). Line = contact threshold (on above ${ins.contact_on}, off below ${ins.contact_off}).`))),
    h("div", { class: `notice ${fc.toe_walking ? "warning" : ""}` }, verdict),
    stack,
    legendRow));

  sensors.forEach((se, i) => {
    const sw = h("i");
    sw.style.background = FSR_COLORS[ins.sensors.indexOf(se)] || "var(--ink-2)";
    stack.append(h("div", { class: "sensor-label" }, sw, h("b", {}, se.label), se.location));
    const body = h("div");
    stack.append(body);
    const last = i === sensors.length - 1;
    charts.push(lineChart(body, {
      title: `${se.label} ${se.location}`,
      series: [{ name: `${se.label} ${se.location}`, color: FSR_COLORS[ins.sensors.indexOf(se)], points: pts(se.key, last), markers: false }],
      xType: "seconds",
      xDomain,
      yDomain: [0, ins.adc_max],
      unit: "ADC",
      digits: 0,
      height: (last ? 92 : 70) + (i === 0 ? 18 : 0),   // the first chart also carries the phase strip
      hideXTicks: !last,
      thresholds: [{ y: ins.contact_on, label: `Contact ${ins.contact_on}`, status: "neutral" }],
      thresholdLabelsOutside: true,
      bands: i === 0 ? bands : undefined,
      spans: spans.map((sp) => ({ ...sp, label: i === 0 ? sp.label : "" })),
      tooltipTitle: (pt) => `${fmt.num(pt.x, 2)} s`,
      syncGroup: "signals",
      marginRight: 74,
    }));
  });
}

load().catch((e) => {
  console.error(e);
  clear(document.getElementById("signals"), h("div", { class: "notice critical" }, e.message));
});
