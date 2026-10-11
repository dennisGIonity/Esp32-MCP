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
import { chromium } from 'playwright';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');
const out = join(here, 'out');
mkdirSync(out, { recursive: true });
const require = createRequire(import.meta.url);
const axePath = require.resolve('axe-core/axe.min.js');
const PORT = 4173;
const BASE = `http://localhost:${PORT}/`;

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
    if (m.type() === 'error' && !/\/api\/v1\/devices\//.test(m.location().url)) errors.push(`${m.text()} @ ${m.location().url}`);
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
const browser = await chromium.launch();
console.log(`portal e2e against ${BASE}`);

try {
  // ------------------------------------------------------------------------------------------ S1
  await scenario('S1 fw 3 native USB: provision, test, reboot through re-enumeration, online, listed', async () => {
    const { context, page, errors } = await open(browser, '?simulate=v2-usb');
    let hostCalls = 0;
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
} finally {
  await browser.close();
  preview.kill();
  writeFileSync(join(out, 'results.json'), JSON.stringify({ at: new Date().toISOString(), failures, results }, null, 2));
}

console.log(failures === 0 ? `\nall ${results.length} scenarios passed` : `\n${failures} of ${results.length} scenarios FAILED`);
process.exit(failures === 0 ? 0 : 1);
