"use strict";

const byId = (id) => document.getElementById(id);
const history = { voltage: [], current: [], power: [] };
let config = { refresh_interval_s: 1, trend_points: 600, instrument_id: "—" };
let lastSuccess = null;
let lastMeasurementTimestamp = null;
let pollTimer = null;

function numeric(value, digits = 2) {
  if (value == null || value === "") return "—";
  return Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : "—";
}

function setText(id, value) { byId(id).textContent = value; }

function setTone(element, tone) {
  element.classList.remove("text-ok", "text-error", "text-warn");
  if (tone) element.classList.add(`text-${tone}`);
}

function formatOperatingState(value) {
  const names = { 0: "OFF", 1: "STANDBY", 2: "CHARGE", 3: "DISCHARGE", 4: "BATTERY EMULATION" };
  return names[value] || String(value || "unknown").toUpperCase();
}

function formatCondition(workflow) {
  const condition = workflow.termination;
  if (!condition) return "—";
  const value = condition.value ?? "—";
  return `${condition.field} ${condition.operator} ${value}`;
}

function formatProgressValue(progress) {
  if (!progress || progress.current == null || progress.target == null) return "Unavailable";
  return `${numeric(progress.current)} / ${numeric(progress.target)} ${progress.unit || ""}`.trim();
}

function renderWorkflow(workflow = {}) {
  const badge = byId("workflow-badge");
  const state = String(workflow.state || "idle").toUpperCase();
  badge.textContent = state;
  badge.className = `state-badge ${workflow.active ? "ok" : ""}`;
  setText("workflow-name", workflow.active ? (workflow.workflow_id || "Active workflow") : "No active workflow");
  const stage = workflow.stage;
  setText("workflow-stage", stage ? `${stage.index + 1} / ${stage.count} · ${stage.name}` : "—");
  setText("workflow-step", workflow.step || "—");
  setText("workflow-condition", formatCondition(workflow));

  const progress = workflow.progress;
  const percent = workflow.progress_available && Number.isFinite(Number(progress?.percent))
    ? Math.max(0, Math.min(100, Number(progress.percent))) : null;
  setText("progress-label", percent == null ? "Unavailable" : `${percent.toFixed(0)}%`);
  byId("progress-bar").style.width = percent == null ? "0" : `${percent}%`;
  setText("progress-detail", percent == null
    ? formatProgressValue(progress)
    : `${formatProgressValue(progress)} · reviewed time bound`);
}

function renderInterlocks(interlocks = []) {
  const region = byId("interlock-list");
  region.replaceChildren();
  if (!interlocks.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "No interlock data";
    region.append(empty);
    return;
  }
  interlocks.forEach((item) => {
    const row = document.createElement("div");
    row.className = "interlock";
    const name = document.createElement("span");
    name.textContent = item.name || "Unnamed interlock";
    const result = document.createElement("b");
    const healthy = item.safe && item.fresh;
    result.className = healthy ? "" : "error";
    result.textContent = healthy ? `SAFE · ${numeric(item.age_s, 1)} s` : (item.fresh ? "UNSAFE" : "STALE");
    row.append(name, result);
    region.append(row);
  });
}

function renderAlerts(alerts = []) {
  const region = byId("alert-region");
  if (!alerts.length) {
    region.classList.add("hidden");
    region.replaceChildren();
    return;
  }
  region.classList.remove("hidden");
  region.textContent = alerts.map((alert) => `${alert.code}: ${alert.message}`).join(" · ");
}

function pushTrend(measurement) {
  if (!measurement || measurement.timestamp_utc === lastMeasurementTimestamp) return;
  lastMeasurementTimestamp = measurement.timestamp_utc;
  [[history.voltage, measurement.voltage_v], [history.current, measurement.current_a], [history.power, measurement.power_w]]
    .forEach(([series, value]) => {
      series.push(Number(value));
      if (series.length > config.trend_points) series.shift();
    });
  setText("trend-window", `Last ${history.voltage.length} of ${config.trend_points} samples`);
  drawChart();
}

function drawSeries(ctx, values, color, width, height) {
  if (values.length < 2) return;
  const finite = values.filter(Number.isFinite);
  if (!finite.length) return;
  let min = Math.min(...finite), max = Math.max(...finite);
  if (max === min) { max += 1; min -= 1; }
  ctx.beginPath();
  values.forEach((value, index) => {
    const x = 8 + index / Math.max(1, config.trend_points - 1) * (width - 16);
    const y = 8 + (1 - (value - min) / (max - min)) * (height - 16);
    if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.7;
  ctx.stroke();
}

function drawChart() {
  const canvas = byId("trend-chart");
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(rect.width * ratio));
  canvas.height = Math.max(1, Math.floor(rect.height * ratio));
  const ctx = canvas.getContext("2d");
  ctx.scale(ratio, ratio);
  const width = rect.width, height = rect.height;
  ctx.strokeStyle = "#1e3440";
  ctx.lineWidth = 1;
  for (let row = 1; row < 4; row += 1) {
    const y = row * height / 4;
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(width, y); ctx.stroke();
  }
  drawSeries(ctx, history.voltage, "#35d4e8", width, height);
  drawSeries(ctx, history.current, "#5794ff", width, height);
  drawSeries(ctx, history.power, "#ffb84d", width, height);
}

function renderSnapshot(snapshot) {
  const instrument = snapshot.instrument || {};
  const measurement = snapshot.measurement || {};
  const value = measurement.value;
  setText("instrument-id", instrument.instrument_id || config.instrument_id);
  setText("instrument-state", formatOperatingState(instrument.state));
  setText("output-state", instrument.output_enabled ? "ENABLED" : "DISABLED");
  setTone(byId("output-state"), instrument.output_enabled ? "warn" : "ok");
  setText("freshness", !measurement.available ? "UNAVAILABLE" : (measurement.fresh ? `FRESH · ${numeric(measurement.age_s, 1)} s` : `STALE · ${numeric(measurement.age_s, 1)} s`));
  setTone(byId("freshness"), measurement.available && measurement.fresh ? "ok" : "error");
  setText("generated-time", `Snapshot ${new Date(snapshot.generated_at_utc).toLocaleString()}`);

  setText("voltage", numeric(value?.voltage_v, 2));
  setText("current", numeric(value?.current_a, 2));
  setText("power", numeric(value?.power_w, 1));
  const timestamp = value?.timestamp_utc ? new Date(value.timestamp_utc).toLocaleTimeString() : "Awaiting measurement";
  ["voltage-caption", "current-caption", "power-caption"].forEach((id) => setText(id, timestamp));
  pushTrend(value);

  const totals = snapshot.totals || {};
  setText("charge-ah", numeric(totals.capacity_charge_ah, 3));
  setText("discharge-ah", numeric(totals.capacity_discharge_ah, 3));
  setText("charge-wh", numeric(totals.energy_charge_wh, 2));
  setText("discharge-wh", numeric(totals.energy_discharge_wh, 2));
  renderWorkflow(snapshot.workflow);
  renderInterlocks(snapshot.interlocks?.results || []);
  renderAlerts(snapshot.alerts || []);

  const limits = snapshot.effective_power_limits || {};
  setText("charge-limit", limits.configured && limits.charge_w != null ? `${numeric(limits.charge_w, 0)} W` : "Not configured");
  setText("discharge-limit", limits.configured && limits.discharge_w != null ? `${numeric(limits.discharge_w, 0)} W` : "Not configured");
  const external = snapshot.external_sources?.status || limits.external_source || "not_configured";
  setText("external-status", external === "not_configured" ? "Not configured — reserved for M5/M6" : String(external));

  const acquisition = snapshot.acquisition || {};
  const acquisitionBadge = byId("acquisition-badge");
  acquisitionBadge.textContent = String(acquisition.health || "unknown").toUpperCase();
  acquisitionBadge.className = `state-badge ${acquisition.health === "ok" ? "ok" : "error"}`;
  setText("sample-count", acquisition.sample_count ?? "—");
  setText("observed-rate", acquisition.observed_rate_hz == null ? "—" : `${numeric(acquisition.observed_rate_hz, 2)} Hz`);
  setText("evidence-path", acquisition.evidence_path || "—");
}

function setLinkHealthy() {
  lastSuccess = Date.now();
  byId("link-dot").className = "status-dot ok";
  setText("link-label", "Service link live");
  setText("link-age", "Snapshot received now");
}

function setLinkError(message) {
  byId("link-dot").className = "status-dot error";
  setText("link-label", "Service unavailable");
  setText("link-age", message);
  setText("freshness", "UNKNOWN · SERVICE OFFLINE");
  setTone(byId("freshness"), "error");
  ["voltage-caption", "current-caption", "power-caption"].forEach((id) => {
    if (byId(id).textContent !== "Awaiting measurement") {
      setText(id, "Retained last value · service offline");
    }
  });
  const region = byId("alert-region");
  region.classList.remove("hidden");
  region.textContent = `Monitor link: ${message}`;
}

async function poll() {
  try {
    const response = await fetch("/api/runtime", { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || `HTTP ${response.status}`);
    setLinkHealthy();
    renderSnapshot(payload);
  } catch (error) {
    setLinkError(error instanceof Error ? error.message : String(error));
  } finally {
    pollTimer = window.setTimeout(poll, config.refresh_interval_s * 1000);
  }
}

async function start() {
  try {
    const response = await fetch("/api/config", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    config = await response.json();
    setText("instrument-id", config.instrument_id);
    await poll();
  } catch (error) {
    setLinkError(`Monitor configuration failed: ${error}`);
  }
}

window.addEventListener("resize", drawChart);
window.addEventListener("beforeunload", () => window.clearTimeout(pollTimer));
window.setInterval(() => {
  if (lastSuccess == null) return;
  const age = (Date.now() - lastSuccess) / 1000;
  setText("link-age", `Last response ${age.toFixed(1)} s ago`);
}, 500);

drawChart();
start();
