/* Optional browser acceptance: NODE_PATH must resolve playwright.
 * Run: node tests/monitor_visual_qa.cjs. No service or hardware is contacted.
 */
const { chromium } = require("playwright");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");

(async () => {
  const output = path.resolve(".test-temp-monitor-visual");
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, ...(process.env.NHR_QA_BROWSER_CHANNEL ? { channel: process.env.NHR_QA_BROWSER_CHANNEL } : {}) });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
    const errors = [];
    page.on("pageerror", e => errors.push(String(e)));
    const snapshot = {
      generated_at_utc: new Date().toISOString(),
      instrument: { instrument_id: "sim-operator", state: 2, output_enabled: true },
      measurement: { available: true, fresh: true, age_s: 0.1,
        value: { timestamp_utc: new Date().toISOString(), voltage_v: 94, current_a: 2, power_w: 188 } },
      workflow: { active: true, workflow_id: "CCCV demonstration", state: "running",
        stage: { index: 0, count: 3, name: "charge" }, step: "03_wait",
        step_description: "CCCV active: current cutoff applies after voltage activation; timeout remains enforced",
        termination: { field: "cutoff_current", operator: "<=", value: 0.5 },
        termination_metric: { current: 2, unit: "A" },
        progress_available: true, progress: { current: 20, target: 90, percent: 22, unit: "s" },
        recording: { state: "recording", path: "C:/runs/demo/session.csv" } },
      acquisition: { active: true, health: "ok", sample_count: 200, observed_rate_hz: 10,
        evidence_path: "C:/runs/surveillance.csv" },
      interlocks: { results: [
        { name: "cell-temperature", safe: true, fresh: true, value: 39,
          unit: "degC", age_s: 0.1 },
        { name: "operator_supervision", safe: true, fresh: true, age_s: 0 },
      ] },
      external_sources: { status: "configured", results: [{ rule_id: "cell-temperature", value: 39,
        unit: "degC", rule: { comparison: "maximum", maximum: 40 }, latched: false,
        current: { value: 39, age_s: 0.1, health: "ok" } }] },
    };
    await page.route("http://nhr.test/**", async route => {
      const url = new URL(route.request().url());
      if (url.pathname === "/api/config") return route.fulfill({ json: {
        instrument_id: "sim-operator", refresh_interval_s: 60, trend_points: 600, read_only: true } });
      if (url.pathname === "/api/runtime") return route.fulfill({ json: snapshot });
      const file = url.pathname === "/" ? "index.html" : path.basename(url.pathname);
      const contentType = file.endsWith("js") ? "text/javascript" : file.endsWith("css") ? "text/css" : "text/html";
      return route.fulfill({ body: fs.readFileSync(path.join("src/nhr9300/monitor_assets", file)), contentType });
    });
    await page.goto("http://nhr.test/");
    await page.waitForFunction(() => document.getElementById("recording-state").textContent === "recording");
    await page.evaluate(() => {
      config.refresh_interval_s = 1;
      const now = Date.now();
      for (let i = 0; i < 60; i++) {
        if (i > 25 && i < 34) continue; // visible data gap
        pushTrend({ timestamp_utc: new Date(now + i * 1000).toISOString(),
          voltage_v: 94, current_a: i < 40 ? 2 : null, power_w: 188 - i });
      }
    });
    assert.match(await page.locator("#interlock-list").innerText(), /value 39.00 degC/);
    assert.match(await page.locator("#interlock-list").innerText(), /condition <= 40/);
    assert.match(await page.locator("#interlock-list").innerText(), /Operator supervision · declared YES · condition required = true · source configuration/);
    assert.doesNotMatch(await page.locator("#interlock-list .interlock").nth(1).innerText(), /age/);
    assert.doesNotMatch(await page.locator("#interlock-list").innerText(), /margin/);
    assert.match(await page.locator("#workflow-condition").innerText(), /2.00 <= 0.5 A/);
    await page.screenshot({ path: path.join(output, "running.png"), fullPage: true });
    snapshot.interlocks.results[0].safe = false;
    snapshot.interlocks.results[0].reason = "condition_violated";
    snapshot.external_sources.results[0].value = 41;
    snapshot.external_sources.results[0].latched = true;
    snapshot.external_sources.results[0].current.value = 38;
    await page.evaluate(s => renderSnapshot(s), snapshot);
    assert.match(await page.locator("#interlock-list").innerText(), /TRIGGER retained: 41.00/);
    assert.match(await page.locator("#interlock-list").innerText(), /38.00/);
    await page.screenshot({ path: path.join(output, "latched.png"), fullPage: true });
    snapshot.interlocks.results[0].fresh = false;
    snapshot.external_sources.results[0].current = { value: null, age_s: 8, health: "error" };
    snapshot.workflow = { active: false, state: "idle", last_run: { outcome: "stopped",
      recording: { state: "finalized", finalized: true, manifest_path: "C:/runs/demo/session-evidence.json" } } };
    await page.evaluate(s => renderSnapshot(s), snapshot);
    assert.match(await page.locator("#recording-state").innerText(), /finalized/);
    await page.evaluate(() => setLinkError("fixture offline"));
    assert.match(await page.locator("#freshness").innerText(), /OFFLINE/);
    await page.setViewportSize({ width: 1100, height: 900 });
    await page.screenshot({ path: path.join(output, "offline.png"), fullPage: true });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    assert.deepEqual(errors, []);
    console.log(`Visual acceptance passed: ${output}`);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
