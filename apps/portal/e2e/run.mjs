#!/usr/bin/env node
/*
 * AEDI - IONITY GLOBAL | @ionity/portal | end-to-end run (Playwright + axe-core) | Policy 986 AED
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Drives the BUILT portal (vite preview) in Chromium against the simulated
 * boards (?simulate=...). Scenarios:
 *   S1 fw 3 on native USB: provision -> test -> reboot (re-enumeration) -> online -> host lists it
 *   S2 fw 2.x: mqtts:// refused on the field, mqtt:// accepted, UUID warning, factory-reset dialog
 *   S3 validation: errors on fields, focus, aria-invalid, live region, error cleared on input
 *   S4 wrong Wi-Fi password: failure on the right step and field, then fixed
 *   S5 a browser without Web Serial gets an explanation, not a broken page
 *   S6 the REAL fleet host (Python, temp DB) serving the portal at /portal/, fed real
 *      EWP/1 frames by a simulated NetObserve node: fleet card (online, RSSI, heap,
 *      uptime, fw), quick actions, flagged banner, live DNS push + filters + history,
 *      watchlist add/validate with the admin token; axe on every fleet view
 *      (needs the server's Python: PYTHON=/path/to/venv/bin/python, default python3)
 * Every scenario: no console errors, secrets never in the page text, terminal or saved log.
 * S1 also: axe-core (WCAG 2.2 AA + best practice), desktop and 390 px screenshots.
 *
 * Usage: npm run build && npm run e2e      (writes e2e/out/)
 */
import { spawn } from 'node:child_process';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createInterface } from 'node:readline';
import { chromium } from 'playwright';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');
const out = join(here, 'out');
mkdirSync(out, { recursive: true });
const require = createRequire(import.meta.url);
const axePath = require.resolve('axe-core/axe.min.js');
const PORT = 4173;
const BASE = `http://localhost:${PORT}/`;

// Optional lookups the portal makes and handles when they are missing: a firmware
// release (none is built in the test tree) and the host's provisioning defaults
// (vite preview proxies /api to a fleet host that is not running in S1-S5).
const OPTIONAL = /\/(api\/v1\/firmware\/manifest|firmware\/manifest\.json|api\/v1\/provisioning\/defaults)$/;

const results = [];
let failures = 0;

function check(cond, message) {
  if (!cond) throw new Error(`check failed: ${message}`);
}

async function startPreview() {
  const proc = spawn(process.execPath, [join(root, '../../node_modules/vite/bin/vite.js'), 'preview', '--port', String(PORT), '--strictPort'], {
    cwd: root,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let log = '';
  proc.stdout.on('data', (d) => (log += d));
  proc.stderr.on('data', (d) => (log += d));
  const until = Date.now() + 20_000;
  while (Date.now() < until) {
    try {
      const r = await fetch(BASE);
      if (r.ok) return proc;
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  proc.kill();
  throw new Error(`vite preview did not start:\n${log}`);
}

/** New page with console-error capture. */
async function open(browser, path, { viewport = { width: 1440, height: 900 }, init } = {}) {
  const context = await browser.newContext({ viewport, acceptDownloads: true });
  const page = await context.newPage();
  const errors = [];
  page.on('console', (m) => {
    // Chromium logs every HTTP error status; the mocked host API answers 404 on purpose
    // while it "has not seen" the board yet. Any other error fails the scenario.
    if (m.type() === 'error' && !/\/api\/v1\/devices\//.test(m.location().url) && !OPTIONAL.test(m.location().url)) errors.push(`${m.text()} @ ${m.location().url}`);
  });
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  if (init) await page.addInitScript(init);
  await page.goto(BASE + path);
  return { context, page, errors };
}

async function connect(page, prov) {
  await page.getByRole('button', { name: 'Connect board' }).click();
  await page.getByTestId('board-card').waitFor({ timeout: 10_000 });
  const p = await page.getByTestId('board-card').getAttribute('data-prov');
  check(p === String(prov), `board reports prov ${p}, expected ${prov}`);
}

async function pairingOutcome(page, timeout = 30_000) {
  const el = page.getByTestId('pairing-outcome');
  await el.waitFor({ timeout });
  return { outcome: await el.getAttribute('data-outcome'), text: await el.innerText() };
}

async function stepStates(page) {
  return page.$$eval('[data-step]', (els) => Object.fromEntries(els.map((e) => [e.getAttribute('data-step'), e.getAttribute('data-state')])));
}

async function terminalText(page) {
  return page.locator('.xterm-rows').innerText();
}

async function savedLog(page) {
  const [dl] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: 'Save log' }).click()]);
  return readFileSync(await dl.path(), 'utf8');
}

async function assertNoSecret(page, secret, where) {
  // The simulator banner prints the DEMO network's password on purpose; nothing else may.
  const text = await page.evaluate(() => {
    const banner = document.querySelector('[data-testid=sim-banner]');
    return banner ? document.body.innerText.replace(banner.innerText, '') : document.body.innerText;
  });
  check(!text.includes(secret), `${where}: secret visible in page text`);
  check(!(await terminalText(page)).includes(secret), `${where}: secret visible in the terminal`);
}

/** The real fleet host + simulated node (e2e/fleet_host.py). */
async function startFleetHost() {
  const proc = spawn(process.env.PYTHON ?? 'python3', [join(here, 'fleet_host.py')], { stdio: ['pipe', 'pipe', 'pipe'] });
  let err = '';
  proc.stderr.on('data', (d) => (err += d));
  const lines = createInterface({ input: proc.stdout });
  const waiters = [];
  const seen = [];
  lines.on('line', (l) => {
    seen.push(l);
    for (const w of [...waiters]) if (w.test(l)) w.resolve(l), waiters.splice(waiters.indexOf(w), 1);
  });
  const waitLine = (re, ms = 30_000) =>
    new Promise((resolve, reject) => {
      const hit = seen.find((l) => re.test(l));
      if (hit) return resolve(hit);
      const t = setTimeout(() => reject(new Error(`fleet host: no ${re} in ${ms} ms\n${seen.join('\n')}\n${err.slice(-3000)}`)), ms);
      waiters.push({ test: (l) => re.test(l) || /^ERROR/.test(l), resolve: (l) => (clearTimeout(t), /^ERROR/.test(l) ? reject(new Error(l + '\n' + err)) : resolve(l)) });
      proc.once('exit', (code) => reject(new Error(`fleet host exited (${code})\n${err.slice(-3000)}`)));
    });
  const [, base, token] = (await waitLine(/^READY /, 60_000)).split(' ');
  return {
    base,
    token,
    async send(cmd) {
      proc.stdin.write(`${cmd}\n`);
      await waitLine(new RegExp(`^OK ${cmd}$`));
      seen.splice(0); // the next OK of the same command is a new one
    },
    async stop() {
      proc.stdin.end('quit\n');
      await new Promise((r) => (proc.exitCode !== null ? r() : proc.once('exit', r)));
    },
  };
}

async function axeCheck(page, name) {
  await page.addScriptTag({ path: axePath });
  const axe = await page.evaluate(async () => {
    const r = await globalThis.axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice'] } });
    return r.violations.map((v) => ({ id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.length, targets: v.nodes.slice(0, 3).map((n) => n.target.join(' ')) }));
  });
  writeFileSync(join(out, `${name}-axe.json`), JSON.stringify(axe, null, 2));
  check(axe.length === 0, `${name} axe violations: ${axe.map((v) => `${v.id}(${v.impact}) x${v.nodes} ${v.targets.join(' | ')}`).join('; ')}`);
}

async function scenario(name, fn) {
  const t0 = Date.now();
  try {
    const detail = await fn();
    results.push({ name, ok: true, ms: Date.now() - t0, ...(detail ?? {}) });
    console.log(`  ok   ${name} (${Date.now() - t0} ms)`);
  } catch (e) {
    failures++;
    results.push({ name, ok: false, ms: Date.now() - t0, error: String(e?.stack ?? e) });
    console.log(`  FAIL ${name}\n       ${String(e?.message ?? e)}`);
  }
}

const preview = await startPreview();
// CHROMIUM_PATH: a preinstalled browser when the pinned Playwright build is not downloaded (CI images, sandboxes)
const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
console.log(`portal e2e against ${BASE}`);

try {
  // ------------------------------------------------------------------------------------------ S1
  await scenario('S1 fw 3 native USB: provision, test, reboot through re-enumeration, online, listed', async () => {
    const { context, page, errors } = await open(browser, '?simulate=v2-usb');
    let hostCalls = 0;
    // The host's provisioning defaults pre-fill untouched, empty fields (site/group here).
    await page.route('**/api/v1/provisioning/defaults', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ server: '192.168.0.3', mdns_host: 'ionity-fleet.local', mqtt_port: 1883, http_port: 8099, site: 'lab', group: 'bench', require_token: false, admin: false, warning: null }) }),
    );
    await page.route('**/api/v1/devices/**', async (route) => {
      hostCalls++;
      if (hostCalls === 1) return route.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"unknown device"}' });
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ device: { health: 'online', transport: 'ewp1/mqtt' } }) });
    });
    // The app renders after the simulator chunk has loaded: wait for it rather than sampling once.
    await page.getByTestId('sim-banner').waitFor({ state: 'visible', timeout: 10_000 });
    await connect(page, 2);

    await page.getByLabel('Wi-Fi name (SSID)').fill('IONITY-LAB-IOT');
    await page.getByLabel('Wi-Fi password', { exact: true }).fill('lab-pass-123');
    await page.getByLabel('MCP host URL').fill('mqtt://192.168.0.3:1883');
    await page.getByRole('button', { name: 'Generate' }).click();
    const uuid = await page.getByRole('textbox', { name: /^Device UUID/ }).inputValue();
    check(/^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(uuid), `generated UUIDv7 ${uuid}`);
    await page.getByText('Advanced: fleet, security, verification').click();
    const api = page.getByLabel('Fleet host API (for the last check)');
    await api.fill(BASE);
    await api.blur();
    await page.getByRole('button', { name: 'Write to board' }).click();

    const res = await pairingOutcome(page, 40_000);
    const steps = await stepStates(page);
    check(res.outcome === 'success', `outcome ${res.outcome}: ${res.text}`);
    for (const s of ['connect', 'identify', 'write', 'wifi', 'broker', 'reboot', 'online', 'host']) check(steps[s] === 'done', `step ${s} is ${steps[s]}`);
    check(hostCalls >= 2, 'host API polled until it listed the board');

    const sim = await page.evaluate(() => {
      const b = globalThis.__IONITY_SIM__.board();
      return { pass: b.nvs.get('wifi_pass'), uuid: b.nvs.get('dev_uuid'), host: b.nvs.get('host_url'), reboots: b.rebootCount, lastV: b.received.at(-1)?.v };
    });
    check(sim.pass === 'lab-pass-123', 'password reached the board NVS');
    check(sim.uuid === uuid, 'device UUID stored');
    check(sim.host === 'mqtt://192.168.0.3:1883', `host_url stored as ${sim.host}`);
    check(sim.reboots === 1, 'board rebooted once');
    const site = await page.evaluate(() => globalThis.__IONITY_SIM__.board().nvs.get('site'));
    check(site === 'lab', `provisioning defaults pre-filled the site (${site})`);
    const status = await page.getByTestId('connection-status').innerText();
    check(/reattached 1×/.test(status), `session reattached after re-enumeration (${status})`);

    await assertNoSecret(page, 'lab-pass-123', 'S1');
    const log = await savedLog(page);
    check(log.includes('IONITY-PROV'), 'saved log has the protocol lines');
    check(!log.includes('lab-pass-123'), 'saved log has no password');
    check(log.includes('"pass":"•••"'), 'TX line in the log is redacted');
    writeFileSync(join(out, 'S1-session.log'), log);

    // Accessibility: axe-core on the finished state.
    await page.addScriptTag({ path: axePath });
    const axe = await page.evaluate(async () => {
      const r = await globalThis.axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice'] } });
      return r.violations.map((v) => ({ id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.length, targets: v.nodes.slice(0, 3).map((n) => n.target.join(' ')) }));
    });
    writeFileSync(join(out, 'S1-axe.json'), JSON.stringify(axe, null, 2));
    check(axe.length === 0, `axe violations: ${axe.map((v) => `${v.id}(${v.impact}) x${v.nodes} ${v.targets.join(' | ')}`).join('; ')}`);

    // The console column is sticky: scroll to the top so the captures show the real layout.
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: join(out, 'S1-desktop-viewport.png') });
    await page.screenshot({ path: join(out, 'S1-desktop-full.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(300);
    await page.evaluate(() => window.scrollTo(0, 0));
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    check(overflow <= 0, `no horizontal scroll at 390 px (overflow ${overflow}px)`);
    await page.screenshot({ path: join(out, 'S1-mobile-390.png'), fullPage: true });
    await page.screenshot({ path: join(out, 'S1-mobile-top.png') });
    await page.getByTestId('pairing').screenshot({ path: join(out, 'S1-mobile-pairing.png') });
    await page.getByTestId('console').screenshot({ path: join(out, 'S1-mobile-console.png') });
    check(errors.length === 0, `console errors: ${errors.join(' | ')}`);
    await context.close();
    return { steps, hostCalls, axeViolations: axe.length };
  });

  // ------------------------------------------------------------------------------------------ S2
  await scenario('S2 fw 2.x: mqtts refused on the field, mqtt accepted, UUID warning, factory reset confirm', async () => {
    const { context, page, errors } = await open(browser, '?simulate=v1');
    await connect(page, 1);
    await page.getByLabel('Wi-Fi name (SSID)').fill('Afrihost Fibre DTM');
    await page.getByLabel('Wi-Fi password', { exact: true }).fill('correct-horse');
    const host = page.getByLabel('MCP host URL');
    await host.fill('mqtts://fleet.ionity.lan');
    await page.getByRole('button', { name: 'Generate' }).click();
    await page.getByRole('button', { name: 'Write to board' }).click();

    await page.waitForFunction(() => document.activeElement?.getAttribute('aria-invalid') === 'true');
    check((await host.getAttribute('aria-invalid')) === 'true', 'host URL marked invalid');
    check(await host.evaluate((el) => el === document.activeElement), 'focus moved to the host URL');
    const errId = await host.getAttribute('aria-errormessage');
    const errText = await page.locator(`[id="${errId}"]`).innerText();
    check(/2\.x|TLS|mqtt:\/\//i.test(errText), `field error explains the firmware limit: "${errText}"`);
    check((await page.getByTestId('pairing').count()) === 0, 'nothing was written');

    await host.fill('mqtt://192.168.0.3:1883');
    check((await host.getAttribute('aria-invalid')) === 'false', 'typing clears the error');
    await page.getByRole('button', { name: 'Write to board' }).click();
    const warn = await page.getByTestId('form-warnings').innerText();
    check(/cannot store a device UUID/.test(warn), 'UUID warning for fw 2.x');
    const res = await pairingOutcome(page);
    check(res.outcome === 'success', `outcome ${res.outcome}: ${res.text}`);
    const steps = await stepStates(page);
    check(steps.host === 'skipped', `host step skipped without an API (${steps.host})`);
    const nvs = await page.evaluate(() => Object.fromEntries(globalThis.__IONITY_SIM__.board().nvs));
    check(nvs.server === '192.168.0.3' && nvs.mqtt_port === 1883 && !('dev_uuid' in nvs), `fw 2.x NVS: ${JSON.stringify({ server: nvs.server, port: nvs.mqtt_port, uuid: nvs.dev_uuid })}`);

    // Factory reset asks first; Escape cancels; confirming erases.
    await page.getByRole('button', { name: 'Factory reset…' }).click();
    const dialog = page.getByRole('dialog', { name: 'Factory reset this board?' });
    await dialog.waitFor();
    check(await page.getByRole('button', { name: 'Cancel' }).evaluate((el) => el === document.activeElement), 'Cancel has focus');
    await page.keyboard.press('Escape');
    await dialog.waitFor({ state: 'hidden' });
    check((await page.evaluate(() => globalThis.__IONITY_SIM__.board().nvs.size)) > 0, 'Escape erased nothing');
    await page.getByRole('button', { name: 'Factory reset…' }).click();
    await page.getByRole('button', { name: 'Erase settings' }).click();
    await page.waitForFunction(() => globalThis.__IONITY_SIM__.board().nvs.size === 0);

    await assertNoSecret(page, 'correct-horse', 'S2');
    check(!(await savedLog(page)).includes('correct-horse'), 'saved log has no password');
    check(errors.length === 0, `console errors: ${errors.join(' | ')}`);
    await context.close();
  });

  // ------------------------------------------------------------------------------------------ S3
  await scenario('S3 validation: field errors, focus, aria wiring, live region, cleared on input', async () => {
    const { context, page, errors } = await open(browser, '?simulate=v2');
    await connect(page, 2);
    await page.getByLabel('MCP host URL').fill('ftp://fleet');
    await page.getByText('Advanced: fleet, security, verification').click();
    await page.getByLabel('Site').fill('Bad Site');
    await page.getByRole('button', { name: 'Write to board' }).click();

    const ssid = page.getByLabel('Wi-Fi name (SSID)');
    await page.waitForFunction(() => document.activeElement?.getAttribute('aria-invalid') === 'true');
    check(await ssid.evaluate((el) => el === document.activeElement), 'first invalid field (SSID) has focus');
    for (const [label, opts] of [['Wi-Fi name (SSID)', {}], ['Wi-Fi password', { exact: true }], ['MCP host URL', {}], ['Site', {}]]) {
      const el = page.getByLabel(label, opts);
      check((await el.getAttribute('aria-invalid')) === 'true', `${label} aria-invalid`);
      const described = (await el.getAttribute('aria-describedby')) ?? '';
      const errId = await el.getAttribute('aria-errormessage');
      check(errId && described.split(' ').includes(errId), `${label} error is in aria-describedby`);
      check((await page.locator(`[id="${errId}"]`).innerText()).length > 5, `${label} has an error text`);
    }
    await page.waitForFunction(() => /fields need attention/.test(document.querySelector('[data-testid=live-assertive]')?.textContent ?? ''));
    await ssid.fill('IONITY-LAB-IOT');
    check((await ssid.getAttribute('aria-invalid')) === 'false', 'typing clears the SSID error');
    check((await page.getByTestId('pairing').count()) === 0, 'nothing was written');
    check(errors.length === 0, `console errors: ${errors.join(' | ')}`);
    await context.close();
  });

  // ------------------------------------------------------------------------------------------ S4
  await scenario('S4 wrong Wi-Fi password: fails on the Wi-Fi step and the password field, then succeeds', async () => {
    const { context, page, errors } = await open(browser, '?simulate=v2');
    await connect(page, 2);
    await page.getByLabel('Wi-Fi name (SSID)').fill('IONITY-LAB-IOT');
    const pass = page.getByLabel('Wi-Fi password', { exact: true });
    await pass.fill('not-the-password');
    await page.getByLabel('MCP host URL').fill('mqtt://192.168.0.3');
    await page.getByRole('button', { name: 'Write to board' }).click();
    const res = await pairingOutcome(page);
    check(res.outcome === 'failed', `outcome ${res.outcome}`);
    const steps = await stepStates(page);
    check(steps.write === 'done' && steps.wifi === 'failed' && steps.reboot === 'pending', `steps ${JSON.stringify(steps)}`);
    await page.waitForFunction(() => document.activeElement?.getAttribute('type') === 'password');
    check((await pass.getAttribute('aria-invalid')) === 'true', 'password field marked');
    await page.waitForFunction(() => /could not join/.test(document.querySelector('[data-testid=live-assertive]')?.textContent ?? ''));

    await pass.fill('lab-pass-123');
    await page.getByRole('button', { name: 'Write to board' }).click();
    await page.waitForFunction(() => document.querySelector('[data-testid=pairing-outcome]')?.getAttribute('data-outcome') === 'success', null, { timeout: 30_000 });
    await assertNoSecret(page, 'not-the-password', 'S4');
    await assertNoSecret(page, 'lab-pass-123', 'S4');
    check(errors.length === 0, `console errors: ${errors.join(' | ')}`);
    await context.close();
  });

  // ------------------------------------------------------------------------------------------ S5
  await scenario('S5 browser without Web Serial: explained, Connect disabled, no errors', async () => {
    const { context, page, errors } = await open(browser, '', {
      init: () => {
        delete Navigator.prototype.serial;
      },
    });
    await page.getByTestId('unsupported').waitFor();
    check(await page.getByRole('button', { name: 'Connect board' }).isDisabled(), 'Connect disabled');
    check(errors.length === 0, `console errors: ${errors.join(' | ')}`);
    await context.close();
  });
  // ------------------------------------------------------------------------------------------ S6
  await scenario('S6 real fleet host at /portal/: fleet status, actions, flagged banner, live DNS, watchlist', async () => {
    const host = await startFleetHost();
    const PORTAL = `${host.base}/portal/`;
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await context.newPage();
    const errors = [];
    page.on('console', (m) => {
      if (m.type() === 'error' && !OPTIONAL.test(m.location().url)) errors.push(`${m.text()} @ ${m.location().url}`);
    });
    page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
    try {
      const legacy = await fetch(`${host.base}/flasher/`, { redirect: 'manual' });
      check(legacy.status === 308 && legacy.headers.get('location') === '/portal/', `/flasher/ redirects to /portal/ (${legacy.status})`);

      // -- Fleet --------------------------------------------------------------------
      await page.goto(`${PORTAL}#fleet`);
      const card = page.locator('[data-testid=fleet-card][data-device="esp32-98a316e5d18c"]');
      await card.waitFor({ timeout: 15_000 });
      const facts = await card.innerText();
      for (const want of ['lounge-node', '-58 dBm', '177 kB', '1h 2m', '3.1.0', 'EWP/1', '192.168.0.50'])
        check(facts.includes(want), `fleet card shows ${want}: ${facts.replace(/\s+/g, ' ')}`);
      check((await card.getAttribute('data-health')) === 'online', 'board online');
      check(await card.getByRole('button', { name: 'Reboot…' }).isDisabled(), 'reboot needs the admin token');
      await card.getByRole('button', { name: 'Ping' }).click();
      const ping = await card.getByTestId('action-result').innerText();
      // HTTP-ingest nodes get tool calls over MQTT only: the host says so instead of hanging.
      check(/MQTT|ms/.test(ping), `ping result reported (${ping})`);

      // the flagged banner, from the stored episode + the snapshot
      const banner = page.getByTestId('flag-banner');
      await banner.waitFor({ timeout: 10_000 });
      const btext = await banner.innerText();
      check(/Kitchen TV/.test(btext) && /YouTube/.test(btext), `banner names device + category: ${btext}`);

      await page.getByTestId('host-token').fill(host.token);
      check(!(await card.getByRole('button', { name: 'Reboot…' }).isDisabled()), 'token enables reboot');
      await card.getByRole('button', { name: 'Reboot…' }).click();
      const dialog = page.getByRole('dialog', { name: /Reboot lounge-node/ });
      await dialog.waitFor();
      check(await page.getByRole('button', { name: 'Cancel' }).evaluate((el) => el === document.activeElement), 'Cancel has focus');
      await page.keyboard.press('Escape');
      await dialog.waitFor({ state: 'hidden' });
      await axeCheck(page, 'S6-fleet');
      await page.screenshot({ path: join(out, 'S6-fleet.png'), fullPage: true });

      // -- Network activity ------------------------------------------------------------
      await page.getByTestId('tab-network').click();
      check(page.url().endsWith('#network'), 'tab in the URL');
      await page.locator('[data-testid=dns-row]', { hasText: 'www.youtube.com' }).first().waitFor({ timeout: 10_000 });
      const yt = page.locator('[data-testid=dns-row]', { hasText: 'www.youtube.com' }).first();
      check((await yt.getAttribute('data-flagged')) === '1' && /Kitchen TV/.test(await yt.innerText()), 'YouTube lookup flagged and attributed');
      const before = await page.locator('[data-testid=dns-row]').count();
      await host.send('batch');
      await page.locator('[data-testid=dns-row]', { hasText: 'rr1---sn-abc.googlevideo.com' }).first().waitFor({ timeout: 10_000 });
      check((await page.locator('[data-testid=dns-row]').count()) >= before + 3, 'live batch pushed over /ws/events');
      await page.waitForFunction(() => /\b4[1-9] hits\b/.test(document.querySelector('[data-testid=flag-banner]')?.textContent ?? ''), null, { timeout: 10_000 });

      await page.getByTestId('filter-domain').fill('*.googlevideo.com');
      const g = await page.locator('[data-testid=dns-row]').allInnerTexts();
      check(g.length >= 1 && g.every((t) => t.includes('googlevideo.com')), `domain filter (${g.length} rows)`);
      await page.getByTestId('filter-domain').fill('');
      await page.getByTestId('filter-flagged').check();
      const flagged = await page.locator('[data-testid=dns-row]').evaluateAll((els) => els.map((e) => e.getAttribute('data-flagged')));
      check(flagged.length >= 3 && flagged.every((f) => f === '1'), 'flagged-only filter');
      await page.getByTestId('filter-flagged').uncheck();
      await page.getByTestId('filter-device').fill('laptop');
      const lap = await page.locator('[data-testid=dns-row]').allInnerTexts();
      check(lap.length >= 1 && lap.every((t) => /laptop|192\.168\.0\.43/.test(t)), `device filter (${lap.length})`);
      await page.getByTestId('filter-device').fill('');
      await page.getByTestId('filter-domain').fill('googlevideo');
      await page.getByRole('button', { name: 'Search history' }).click();
      await page.locator('[data-testid=dns-history] [data-testid=dns-row]').first().waitFor({ timeout: 10_000 });
      await page.getByTestId('filter-domain').fill('');
      await axeCheck(page, 'S6-network');
      await page.screenshot({ path: join(out, 'S6-network.png'), fullPage: true });

      // -- Watchlist -----------------------------------------------------------------
      await page.getByTestId('tab-watchlist').click();
      await page.locator('[data-testid=rule-row][data-pattern="*.googlevideo.com"]').waitFor({ timeout: 10_000 });
      const v0 = await page.getByTestId('watchlist-version').innerText();
      await page.getByTestId('rule-pattern').fill('com');
      await page.getByTestId('rule-add').click();
      check((await page.getByTestId('rule-pattern').getAttribute('aria-invalid')) === 'true', 'a bare TLD is refused before it reaches the host');
      await page.getByTestId('rule-pattern').fill('*.tiktokcdn.com');
      await page.getByTestId('rule-category').fill('video_streaming:tiktok');
      await page.getByTestId('rule-add').click();
      await page.locator('[data-testid=rule-row][data-pattern="*.tiktokcdn.com"]').waitFor({ timeout: 10_000 });
      await page.waitForFunction((v) => document.querySelector('[data-testid=watchlist-version]')?.textContent !== v, v0);
      const r = await fetch(`${host.base}/api/v1/watchlist`).then((x) => x.json());
      check(r.rules.some((x) => x.pattern === '*.tiktokcdn.com' && x.category === 'video_streaming:tiktok'), 'rule stored on the host');
      await page.getByRole('button', { name: 'Delete rule *.tiktokcdn.com' }).click();
      await page.locator('[data-testid=rule-row][data-pattern="*.tiktokcdn.com"]').waitFor({ state: 'detached', timeout: 10_000 });
      await axeCheck(page, 'S6-watchlist');
      await page.screenshot({ path: join(out, 'S6-watchlist.png'), fullPage: true });

      await page.setViewportSize({ width: 390, height: 844 });
      await page.getByTestId('tab-network').click();
      await page.waitForTimeout(300);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      check(overflow <= 0, `no horizontal page scroll at 390 px (overflow ${overflow}px)`);
      await page.screenshot({ path: join(out, 'S6-mobile-network.png'), fullPage: true });

      check(!(await page.evaluate(() => document.body.innerText)).includes(host.token), 'admin token never shown');
      check(errors.length === 0, `console errors: ${errors.join(' | ')}`);
    } finally {
      await context.close();
      await host.stop();
    }
  });
} finally {
  await browser.close();
  preview.kill();
  writeFileSync(join(out, 'results.json'), JSON.stringify({ at: new Date().toISOString(), failures, results }, null, 2));
}

console.log(failures === 0 ? `\nall ${results.length} scenarios passed` : `\n${failures} of ${results.length} scenarios FAILED`);
process.exit(failures === 0 ? 0 : 1);
