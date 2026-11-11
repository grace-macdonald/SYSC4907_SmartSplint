// Progress charts built from per-session summary metrics. Shared by the patient and clinician views.

import { chartCard, dataTable, lineChart } from "./charts.js";
import { fmt, h } from "./core.js";

const C1 = "var(--series-1)";
const C2 = "var(--series-2)";

function walkSessions(sessions) {
  return sessions.filter((s) => s.kind === "walk");
}

const alertRate = (s) => (s.strides ? (100 * (s.anomaly_count || 0)) / s.strides : null);

/**
 * Metric definitions. `patient` text is plain language; `clinician` text is technical.
 */
function metrics(participant) {
  return [
    {
      key: "support", clinician: true, patient: true, unit: "%", digits: 0, step: true, yZero: true,
      title: { clinician: "Support level", patient: "Brace support" },
      desc: { clinician: "Requested push-off support, % of 100% torque", patient: "How much the brace helps. It goes down as you get stronger." },
      value: (s) => s.support_level_pct,
      yDomain: [0, 100],
      thresholds: (p, mode) => (mode === "clinician" && p.support_max_pct ? [{ y: p.support_max_pct, label: `Max ${p.support_max_pct}%`, status: "neutral" }] : []),
    },
    {
      key: "alerts", clinician: true, patient: true, unit: "", digits: 1, yZero: true, lowerIsBetter: true,
      title: { clinician: "Gait alerts per 100 strides", patient: "Walking alerts" },
      desc: { clinician: "Toe walking, toe dragging, stride variation, weak push-off", patient: "Alerts per 100 strides. Fewer is better." },
      value: alertRate,
    },
    {
      key: "cv", clinician: true, patient: true, unit: "%", digits: 1, yZero: true, lowerIsBetter: true,
      title: { clinician: "Stride time variation (CV)", patient: "Step rhythm" },
      desc: { clinician: "Coefficient of variation of stride time", patient: "How uneven your steps are. Lower means a steadier rhythm." },
      value: (s) => s.stride_time_cv_pct,
    },
    {
      key: "emg", clinician: true, patient: true, unit: "", digits: 2, yZero: true,
      title: { clinician: "Calf EMG peak", patient: "Calf muscle activity" },
      desc: { clinician: "Envelope peak, normalised to the calibration walk", patient: "How hard your calf works when you push off. Higher means a stronger push." },
      value: (s) => s.emg_peak_norm,
    },
    {
      key: "torque", clinician: true, patient: false, unit: "N·m", digits: 1, yZero: true,
      title: { clinician: "Push-off torque: measured vs requested" },
      desc: { clinician: "Mean measured peak hinge torque vs the reference for the level (P8: within ±10%)" },
      value: (s) => s.push_off_torque_nm,
      second: { name: "Requested", value: (s) => s.reference_torque_nm },
      firstName: "Measured",
    },
    {
      key: "load", clinician: true, patient: false, unit: "kg", digits: 1, yZero: true,
      title: { clinician: "Peak plantar load" },
      desc: { clinician: "Estimated from the insole; compared with the weight-bearing limit" },
      value: (s) => s.peak_plantar_load_kg,
      thresholds: (p) => (p.weight_bearing_limit_kg ? [{ y: p.weight_bearing_limit_kg, label: `Limit ${p.weight_bearing_limit_kg} kg`, status: "critical" }] : []),
    },
    {
      key: "rom", clinician: true, patient: true, unit: "°", digits: 0, yZero: true,
      title: { clinician: "Ankle range of motion", patient: "Ankle movement" },
      desc: { clinician: "Range used during the session (hinge potentiometer)", patient: "How far your ankle bends while you walk." },
      value: (s) => s.rom_deg,
    },
    {
      key: "cadence", clinician: true, patient: false, unit: "steps/min", digits: 0,
      title: { clinician: "Cadence" },
      desc: { clinician: "Steps per minute" },
      value: (s) => s.cadence_spm,
    },
    {
      key: "assist", clinician: true, patient: false, unit: "%", digits: 0, yDomain: [0, 100],
      title: { clinician: "Strides assisted" },
      desc: { clinician: "Share of strides where the gait model was confident enough to assist (FR-5)" },
      value: (s) => s.assist_active_pct,
    },
  ];
}

export function renderProgress(container, sessions, participant, { mode = "clinician", sinceDays = 0 } = {}) {
  container.replaceChildren();
  let walks = walkSessions(sessions);
  if (sinceDays) {
    const cutoff = Date.now() - sinceDays * 86400000;
    walks = walks.filter((s) => new Date(s.started_at).getTime() >= cutoff);
  }
  if (!walks.length) {
    container.append(h("div", { class: "card empty" }, mode === "patient"
      ? "Your progress will show here after your first walk with the brace."
      : "No walking sessions in this range yet."));
    return;
  }
  const xDomain = [new Date(walks[0].started_at).getTime(), new Date(walks[walks.length - 1].started_at).getTime()];
  if (xDomain[0] === xDomain[1]) { xDomain[0] -= 86400000; xDomain[1] += 86400000; }

  for (const m of metrics(participant).filter((m) => m[mode])) {
    const pts = walks.map((s) => ({ x: new Date(s.started_at).getTime(), y: m.value(s), session: s }));
    const valid = pts.filter((p) => p.y !== null && p.y !== undefined);
    if (!valid.length) continue;
    const series = [{ name: m.firstName || m.title[mode], color: C1, points: pts, step: m.step }];
    if (m.second) series.push({ name: m.second.name, color: C2, points: walks.map((s) => ({ x: new Date(s.started_at).getTime(), y: m.second.value(s), session: s })) });

    const first = valid[0].y, last = valid[valid.length - 1].y;
    const unit = m.unit && !["%", "°"].includes(m.unit) ? ` ${m.unit}` : m.unit;
    let sub = valid.length > 1 ? `from ${fmt.num(first, m.digits)}${unit}` : "latest";
    if (mode === "patient" && valid.length > 1 && m.lowerIsBetter !== undefined) {
      const better = m.lowerIsBetter ? last < first : last > first;
      if (Math.abs(last - first) > 1e-9) sub = `${better ? "improved" : "up"} from ${fmt.num(first, m.digits)}${unit}`;
    }

    container.append(chartCard({
      title: m.title[mode],
      desc: m.desc[mode],
      latest: `${fmt.num(last, m.digits)}${unit}`,
      latestSub: sub,
      legendItems: series.length > 1 ? series.map((se) => ({ name: se.name, color: se.color })) : null,
      table: () => dataTable(
        [{ label: "Session", value: (p) => fmt.dateTime(p.session.started_at) },
          ...series.map((se, i) => ({ label: `${se.name}${m.unit ? ` (${m.unit})` : ""}`, num: true,
            value: (p) => fmt.num(i === 0 ? p.y : m.second.value(p.session), m.digits) }))],
        [...pts].reverse()),
      build: (el) => lineChart(el, {
        title: m.title[mode],
        series,
        xType: "time",
        xDomain,
        yDomain: m.yDomain,
        yZero: m.yZero,
        unit: m.unit,
        digits: m.digits,
        stepToEnd: false,
        height: mode === "patient" ? 180 : 160,
        thresholds: m.thresholds ? m.thresholds(participant, mode) : [],
        tooltipTitle: (p) => `${fmt.dateTime(p.session.started_at)} · ${p.session.support_level_pct}% support`,
        onPointClick: (p) => { location.href = `/sessions/${p.session.id}`; },
      }),
    }));
  }
}

/** Sum metrics for sessions within [from, to). */
export function windowTotals(sessions, from, to) {
  const ws = walkSessions(sessions).filter((s) => {
    const t = new Date(s.started_at).getTime();
    return t >= from && t < to;
  });
  return {
    sessions: ws.length,
    minutes: ws.reduce((a, s) => a + (fmt.minutes(s.started_at, s.ended_at) || 0), 0),
    strides: ws.reduce((a, s) => a + (s.strides || 0), 0),
    alerts: ws.reduce((a, s) => a + (s.anomaly_count || 0), 0),
  };
}
