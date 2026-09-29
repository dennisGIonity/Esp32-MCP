// ===========================================================================
// AEDI - IONITY GLOBAL | Ionity ESP32 Flasher
// Doc ID: DOC-2026-09-ESP32MCP-FLASH | Version 2.0.0 | Policy 986 AED
// (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd - All Rights Reserved - TM2
// ---------------------------------------------------------------------------
// 1. Board     pick the USB port; esptool-js identifies the chip
// 2. Firmware  from the fleet host, the images bundled with this page, or a .bin
// 3. Network   WiFi + MCP host (fleet server) + tokens, written to NVS over USB
// 4. Install   flash -> reboot -> ionity-prov/1 set -> test WiFi + host
// 5. Verify    the host sees the board online and its own MCP tools answer
// ===========================================================================
import { useEffect, useMemo, useRef, useState } from "react";
import { Flasher, sha256Hex, type ChipInfo } from "./lib/flash";
import { LineSerial, type LogFn } from "./lib/serial";
import { compactFields, provisionWarnings, randomToken, validateFields, type BoardInfo, type ProvisionFields } from "./lib/prov";
import { HostClient, defaultHostUrl, fetchBytes, mixedContentBlocked, store, type DeviceView, type ProvDefaults } from "./lib/host";
import { bridgeName, chipFamily, pickBuild, type BuildInfo, type Manifest } from "./lib/variants";

type Phase = "idle" | "connecting" | "downloading" | "erasing" | "flashing" | "booting" | "provisioning" | "testing" | "verifying" | "done" | "error";
type Source = "host" | "bundled" | "file";
interface LogLine { t: number; text: string; kind: "rx" | "tx" | "info" | "error" }

const PHASE_LABEL: Record<Phase, string> = {
  idle: "Ready", connecting: "Connecting to the bootloader", downloading: "Downloading firmware",
  erasing: "Erasing flash", flashing: "Writing firmware", booting: "Waiting for the new firmware to boot",
  provisioning: "Writing WiFi + MCP host", testing: "Board is joining WiFi", verifying: "Checking the fleet host",
  done: "Done", error: "Stopped",
};

const serialSupported = typeof navigator !== "undefined" && "serial" in navigator;

export default function App() {
  // ---- host --------------------------------------------------------------
  const [hostUrl, setHostUrl] = useState(() => store.get("host") ?? defaultHostUrl());
  const [adminToken, setAdminToken] = useState("");
  const [hostState, setHostState] = useState<{ ok: boolean; msg: string; defaults?: ProvDefaults } | null>(null);
  const host = useMemo(() => (hostUrl ? new HostClient(hostUrl, adminToken) : null), [hostUrl, adminToken]);

  // ---- firmware ----------------------------------------------------------
  const [source, setSource] = useState<Source>("host");
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [manifestErr, setManifestErr] = useState("");
  const [variant, setVariant] = useState("auto");
  const [file, setFile] = useState<File | null>(null);
  const [eraseAll, setEraseAll] = useState(true);
  const [skipFlash, setSkipFlash] = useState(false);
  const [keepSettings, setKeepSettings] = useState(false);

  // ---- board -------------------------------------------------------------
  const [port, setPort] = useState<SerialPort | null>(null);
  const [chip, setChip] = useState<ChipInfo | null>(null);
  const [board, setBoard] = useState<BoardInfo | null>(null);
  const [networks, setNetworks] = useState<BoardInfo["networks"]>([]);

  // ---- provisioning fields ----------------------------------------------
  const [f, setF] = useState<ProvisionFields & { showPass?: boolean }>(() => ({
    ssid: store.get("ssid") ?? "", pass: "", server: store.get("server") ?? "",
    mqtt_port: 1883, http_port: 8099, fleet_token: "", mcp_token: "", ota_pass: "",
    role: "node", site: store.get("site") ?? "lab", group: store.get("group") ?? "bench", label: "",
    mqtt_user: "", mqtt_pass: "",
  }));
  const set = <K extends keyof typeof f>(k: K, v: (typeof f)[K]) => setF((p) => ({ ...p, [k]: v }));

  // ---- run ---------------------------------------------------------------
  const [phase, setPhase] = useState<Phase>("idle");
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState("");
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [verify, setVerify] = useState<{ device?: DeviceView; tools?: string[]; note?: string } | null>(null);
  const serialRef = useRef<LineSerial | null>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const busy = !["idle", "done", "error"].includes(phase);

  const log: LogFn = (text, kind = "info") =>
    setLogs((l) => (l.length > 1500 ? l.slice(-1200) : l).concat({ t: Date.now(), text, kind }));

  useEffect(() => { logRef.current?.scrollTo({ top: logRef.current.scrollHeight }); }, [logs]);

  // ---- load host defaults + manifest -----------------------------------
  async function probeHost() {
    setHostState(null);
    if (!host) { setHostState({ ok: false, msg: "No fleet host set - using the images bundled with this page." }); return; }
    if (mixedContentBlocked(host.base)) {
      setHostState({ ok: false, msg: `This page is https, the host is http: the browser blocks that. Open ${host.base}/flasher/ instead, or use bundled images and enter the host by hand.` });
      return;
    }
    try {
      const h = await host.health();
      const d = await host.defaults();
      setHostState({ ok: true, msg: `${h.fleet} · MCP ${h.mcp.server} · MQTT ${h.mqtt.connected ? "up" : "DOWN"}`, defaults: d });
      setF((p) => ({
        ...p,
        server: p.server || d.server,
        mqtt_port: d.mqtt_port, http_port: d.http_port,
        site: p.site || d.site, group: p.group || d.group,
        fleet_token: d.fleet_token ?? p.fleet_token,
        mqtt_user: d.mqtt_user ?? p.mqtt_user,
      }));
      store.set("host", host.base);
    } catch (e) {
      setHostState({ ok: false, msg: `Host not reachable (${(e as Error).message}).` });
    }
  }

  async function loadManifest(src: Source) {
    setManifest(null); setManifestErr("");
    try {
      if (src === "host") {
        if (!host || mixedContentBlocked(host.base)) throw new Error("no reachable fleet host");
        setManifest(await host.manifest());
      } else if (src === "bundled") {
        const r = await fetch("./firmware/manifest.json", { cache: "no-cache" });
        if (!r.ok) throw new Error("this page has no bundled images (run firmware/build.py --copy-to flasher/public/firmware)");
        setManifest(await r.json());
      }
    } catch (e) {
      setManifestErr((e as Error).message);
    }
  }

  useEffect(() => { void probeHost(); }, [host]);                         // eslint-disable-line
  useEffect(() => { void loadManifest(source); }, [source, host]);        // eslint-disable-line
  useEffect(() => {                                                         // no host -> bundled
    if (manifestErr && source === "host") setSource("bundled");
  }, [manifestErr]);                                                        // eslint-disable-line

  // ---- serial helpers ---------------------------------------------------
  async function choosePort(): Promise<SerialPort> {
    const p = await navigator.serial.requestPort({});
    setPort(p); setChip(null); setBoard(null);
    const i = p.getInfo();
    log(`Port selected: ${bridgeName(i.usbVendorId)}${i.usbProductId ? ` (pid ${i.usbProductId.toString(16)})` : ""}`);
    return p;
  }

  async function closeSerial() {
    if (serialRef.current) { await serialRef.current.close(); serialRef.current = null; }
  }

  /** After a reset, native-USB boards re-enumerate and the old SerialPort can be
   *  dead. requestPort() needs a fresh click, so first look through the ports
   *  this page is already allowed to use for the same VID/PID. */
  async function reacquirePort(old: SerialPort): Promise<SerialPort> {
    const { usbVendorId, usbProductId } = old.getInfo();
    for (let i = 0; i < 20; i++) {
      for (const p of await navigator.serial.getPorts()) {
        const info = p.getInfo();
        if (info.usbVendorId !== usbVendorId || info.usbProductId !== usbProductId) continue;
        try {
          const s = new LineSerial(p, log);
          await s.open(115200);
          serialRef.current = s;
          setPort(p);
          return p;
        } catch { /* not back yet */ }
      }
      await new Promise((r) => setTimeout(r, 500));
    }
    throw new Error("The board did not come back on USB. Press RESET, click \"Select USB port\", then \"Provision board\" with Provision only ticked.");
  }

  async function openSerial(p: SerialPort): Promise<LineSerial> {
    await closeSerial();
    const s = new LineSerial(p, log);
    await s.open(115200);
    serialRef.current = s;
    return s;
  }

  function selectedBuild(c: ChipInfo | null): BuildInfo | undefined {
    if (!manifest) return undefined;
    if (variant !== "auto") return manifest.builds.find((b) => b.variant === variant);
    return c ? pickBuild(manifest, chipFamily(c.description), c.vid) : undefined;
  }

  async function identify() {
    setError("");
    try {
      const p = port ?? (await choosePort());
      await closeSerial();
      setPhase("connecting");
      const fl = new Flasher(p, log);
      const c = await fl.connect();
      setChip(c);
      log(`Chip: ${c.description}  MAC ${c.mac ?? "?"}  flash ${c.flashSize ?? "?"}`);
      await fl.resetAndRelease();
      setPhase("idle");
    } catch (e) {
      fail(e);
    }
  }

  async function readBoard() {
    setError("");
    try {
      const p = port ?? (await choosePort());
      const s = await openSerial(p);
      const b = await s.handshake(8000);
      setBoard(b);
      setF((x) => ({ ...x, ssid: x.ssid || b.ssid || "", server: x.server || b.server || "", site: b.site || x.site, group: b.group || x.group, label: x.label || (b.label !== b.device_id ? b.label ?? "" : "") }));
    } catch (e) {
      fail(e);
    }
  }

  async function scanWifi() {
    setError("");
    try {
      const p = port ?? (await choosePort());
      const s = serialRef.current ?? (await openSerial(p));
      if (!s.lastHello) await s.handshake(8000);
      log("Scanning WiFi from the board (2.4 GHz only)…");
      const r = await s.request("scan", {}, 15000);
      if (!r.ok) throw new Error(r.error ?? "WiFi scan failed");
      setNetworks(r.networks ?? []);
      log(`Board sees ${r.networks?.length ?? 0} networks: ${(r.networks ?? []).slice(0, 8).map((n) => `${n.ssid} (${n.rssi})`).join(", ")}`);
    } catch (e) {
      fail(e);
    }
  }

  function fail(e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    if (/No port selected|NotFoundError/.test(msg)) { setPhase("idle"); return; }
    setError(msg);
    log(msg, "error");
    setPhase("error");
  }

  // ---- the whole install ------------------------------------------------
  async function install() {
    setError(""); setVerify(null); setProgress(0);
    const fields = compactFields({ ...f, showPass: undefined } as ProvisionFields);
    const updateOnly = keepSettings && !skipFlash && !f.ssid;   // firmware update, settings untouched
    const errs = updateOnly ? [] : validateFields(fields);
    if (errs.length) { setError(errs.join(" ")); return; }
    ["ssid", "server", "site", "group"].forEach((k) => store.set(k, String((f as Record<string, unknown>)[k] ?? "")));

    try {
      const p = port ?? (await choosePort());
      await closeSerial();

      if (!skipFlash) {
        // 1. image
        setPhase("connecting");
        const fl = new Flasher(p, log);
        const c = await fl.connect();
        setChip(c);
        log(`Chip: ${c.description}  MAC ${c.mac ?? "?"}  flash ${c.flashSize ?? "?"}`);

        let image: Uint8Array;
        let expectSha: string | undefined;
        if (source === "file") {
          if (!file) throw new Error("choose a .bin file first");
          image = new Uint8Array(await file.arrayBuffer());
          log(`Image: ${file.name} (${image.length} bytes, local file)`);
        } else {
          const b = selectedBuild(c);
          if (!b) throw new Error(`no image for ${chipFamily(c.description)} in fw ${manifest?.version ?? "?"} - pick a variant or a .bin`);
          setPhase("downloading");
          const url = source === "host" && host ? host.imageUrl(b.file) : `./firmware/${b.file}`;
          image = await fetchBytes(url, setProgress);
          expectSha = b.sha256;
          log(`Image: ${b.name} fw ${manifest?.version} (${b.file}, ${image.length} bytes)`);
        }
        if (expectSha) {
          const got = await sha256Hex(image);
          if (got !== expectSha) throw new Error(`image checksum mismatch (got ${got.slice(0, 12)}…, manifest ${expectSha.slice(0, 12)}…)`);
          log("Checksum OK (sha256 matches the manifest)");
        }

        // 2. flash
        if (eraseAll) setPhase("erasing");
        setProgress(0);
        await fl.flash(image, { eraseAll: eraseAll && !keepSettings, keepSettings, onProgress: (pct) => { setPhase("flashing"); setProgress(pct); } });
        log("Flash written and verified by the ROM (MD5). Rebooting into the app…");
        await fl.resetAndRelease();
        await new Promise((r) => setTimeout(r, 1500));
      }

      // 3. talk to the firmware
      setPhase("booting");
      let s: LineSerial;
      try {
        s = await openSerial(p);
      } catch {
        // native USB re-enumerates on reset: the old port object can be dead
        log("Port went away during reset (normal on native USB) - waiting for it to come back…", "info");
        await reacquirePort(p);
        s = serialRef.current!;
      }
      const hello = await s.handshake(15000);
      setBoard(hello);
      log(`Board ${hello.device_id} fw ${hello.fw} is up (${hello.provisioned ? "was provisioned" : "not provisioned yet"})`);

      if (updateOnly) {
        log(`Firmware updated to ${hello.fw}; WiFi "${hello.ssid}" and MCP host kept.`);
        await closeSerial();
        if (host && !mixedContentBlocked(host.base) && hello.device_id) {
          setPhase("verifying");
          setVerify(await verifyOnHost(host, hello.device_id));
        }
        setPhase("done");
        return;
      }

      setPhase("provisioning");
      // Re-provisioning the same network with the password box left empty:
      // keep the stored password rather than turning it into an open network.
      if (fields.pass === "" && hello.has_pass && hello.ssid === fields.ssid) delete fields.pass;
      const r = await s.request("set", fields as Record<string, unknown>, 5000);
      if (!r.ok) throw new Error(`board refused the settings: ${r.error}`);
      log(`Stored in NVS: ${(r.changed ?? []).join(", ") || "nothing changed"}`);

      setPhase("testing");
      const t = await s.request("test", { timeout_ms: 20000 }, 26000);
      setBoard(t);
      if (!t.ok) throw new Error(`WiFi: ${t.error}. Fix the WiFi name/password and press Install again with "Provision only" ticked.`);
      log(`WiFi OK: ${t.ip} (${t.rssi} dBm). MCP host ${t.host_resolved} via ${t.host_via}, MQTT ${t.mqtt ? "connected" : "not yet"}`);
      if (t.mcp_url) log(`Board MCP endpoint: ${t.mcp_url}`);

      // 4. reboot so topics / site / OTA come up clean
      await s.request("reboot", {}, 3000).catch(() => undefined);
      await closeSerial();
      const deviceId = t.device_id ?? hello.device_id!;

      // 5. verify from the host side
      if (f.role !== "standalone" && host && !mixedContentBlocked(host.base)) {
        setPhase("verifying");
        const v = await verifyOnHost(host, deviceId);
        setVerify(v);
      } else {
        setVerify({ note: f.role === "standalone" ? "Standalone board: talk to its MCP endpoint directly." : "Open the fleet dashboard to confirm the board is online." });
      }
      setPhase("done");
    } catch (e) {
      fail(e);
    }
  }

  async function verifyOnHost(h: HostClient, id: string) {
    const until = Date.now() + 60000;
    let device: DeviceView | undefined;
    while (Date.now() < until) {
      try {
        device = (await h.device(id)).device;
        if (device.health === "online" || device.health === "alerting") break;
      } catch { /* not registered yet */ }
      await new Promise((r) => setTimeout(r, 2500));
    }
    if (!device || !["online", "alerting"].includes(device.health))
      return { device, note: "The host has not heard from the board yet. Check the MCP host address and that the broker is running." };
    log(`Fleet host sees ${id}: ${device.health} over ${device.transport}`);
    try {
      const r = await h.deviceMcp(id, { jsonrpc: "2.0", id: 1, method: "tools/list" });
      const tools = (r.result?.tools ?? []).map((t) => t.name);
      log(`Board MCP tools via the host: ${tools.join(", ")}`);
      return { device, tools };
    } catch (e) {
      return { device, note: `Online, but the board's MCP did not answer through the host: ${(e as Error).message}` };
    }
  }

  async function factoryReset() {
    if (!confirm("Erase WiFi, MCP host, tokens and labels from this board's NVS?")) return;
    try {
      const p = port ?? (await choosePort());
      const s = serialRef.current ?? (await openSerial(p));
      if (!s.lastHello) await s.handshake(8000);
      await s.request("factory_reset", {}, 4000);
      log("Board NVS cleared; it rebooted and is waiting for provisioning.");
    } catch (e) { fail(e); }
  }

  const build = selectedBuild(chip);
  const warnings = provisionWarnings(compactFields(f as ProvisionFields));

  // ---- render ------------------------------------------------------------
  return (
    <div className="wrap">
      <header className="topbar">
        <div className="brand">
          <div className="mark" aria-hidden>⚡</div>
          <div>
            <h1>IONITY ESP32 FLASHER</h1>
            <p className="tag">Flash · provision WiFi + MCP host · verify — Building Tomorrow, Today.</p>
          </div>
        </div>
        <div className="conn">
          <span className={`pill ${hostState?.ok ? "good" : hostState ? "badp" : ""}`}><span className={`dot ${hostState?.ok ? "live" : "down"}`} />{hostState?.ok ? "Host online" : "No host"}</span>
          {manifest && <span className="pill">fw <b>{manifest.version}</b></span>}
          <span className="pill">{PHASE_LABEL[phase]}</span>
        </div>
      </header>

      {!serialSupported && (
        <div className="banner bad">This browser has no Web Serial. Use Chrome, Edge or Opera on a desktop (Firefox and Safari cannot flash).</div>
      )}

      <main className="grid">
        {/* 1. host */}
        <section className="card">
          <h2><span className="n">1</span>MCP host (fleet server)</h2>
          <p className="hint">The server your boards report to and the AI talks to. When this page is opened from the server (…:8099/flasher/) it is filled in for you.</p>
          <label>Host URL
            <div className="row">
              <input value={hostUrl} placeholder="http://192.168.0.2:8099" onChange={(e) => setHostUrl(e.target.value.trim())} />
              <button className="ghost" onClick={() => void probeHost()}>Check</button>
            </div>
          </label>
          <label>Admin token <span className="opt">(only if IONITY_ADMIN_TOKEN is set; not stored)</span>
            <input type="password" value={adminToken} onChange={(e) => setAdminToken(e.target.value)} autoComplete="off" />
          </label>
          {hostState && <p className={hostState.ok ? "ok" : "warn"}>{hostState.msg}</p>}
        </section>

        {/* 2. board + firmware */}
        <section className="card">
          <h2><span className="n">2</span>Board & firmware</h2>
          <div className="row wrapbtn">
            <button disabled={!serialSupported || busy} onClick={() => void choosePort().catch(fail)}>{port ? "Change port" : "Select USB port"}</button>
            <button className="ghost" disabled={!serialSupported || busy} onClick={() => void identify()}>Identify chip</button>
            <button className="ghost" disabled={!serialSupported || busy} onClick={() => void readBoard()}>Read board settings</button>
          </div>
          {port && <p className="meta">Port: {bridgeName(port.getInfo().usbVendorId)}</p>}
          {chip && <p className="meta">Chip: <b>{chip.description}</b> · MAC {chip.mac ?? "?"} · flash {chip.flashSize ?? "?"}{chip.features?.length ? ` · ${chip.features.join(", ")}` : ""}</p>}
          {board && <p className="meta">Firmware: <b>{board.device_id}</b> fw {board.fw} · {board.provisioned ? `WiFi "${board.ssid}"` : "not provisioned"} · {board.wifi === "connected" ? `ip ${board.ip}` : "WiFi down"}</p>}

          <div className="seg" role="radiogroup" aria-label="Firmware source">
            {(["host", "bundled", "file"] as Source[]).map((s) => (
              <button key={s} role="radio" aria-checked={source === s} className={source === s ? "on" : ""} onClick={() => setSource(s)}>
                {s === "host" ? "From host" : s === "bundled" ? "Bundled" : "Local .bin"}
              </button>
            ))}
          </div>
          {source !== "file" && (
            <>
              {manifestErr && <p className="warn">{manifestErr}</p>}
              {manifest && (
                <label>Image
                  <select value={variant} onChange={(e) => setVariant(e.target.value)}>
                    <option value="auto">Auto-detect from the chip {build ? `→ ${build.name}` : ""}</option>
                    {manifest.builds.map((b) => (<option key={b.variant} value={b.variant}>{b.name} · {(b.size / 1024).toFixed(0)} KB</option>))}
                  </select>
                </label>
              )}
            </>
          )}
          {source === "file" && (
            <label>Merged image (flashed at 0x0)
              <input type="file" accept=".bin" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            </label>
          )}
          <label className="check"><input type="checkbox" checked={eraseAll && !keepSettings} disabled={skipFlash || keepSettings} onChange={(e) => setEraseAll(e.target.checked)} /> Erase the whole flash first (recommended for a new board)</label>
          <label className="check"><input type="checkbox" checked={keepSettings} disabled={skipFlash} onChange={(e) => setKeepSettings(e.target.checked)} /> Update only — keep the board's WiFi, MCP host and tokens (skips NVS)</label>
          <label className="check"><input type="checkbox" checked={skipFlash} onChange={(e) => setSkipFlash(e.target.checked)} /> Provision only — the board already runs fw 2.0</label>
        </section>

        {/* 3. network */}
        <section className="card span2">
          <h2><span className="n">3</span>WiFi & MCP connection</h2>
          <div className="cols">
            <label>WiFi name (2.4 GHz)
              <div className="row">
                <input value={f.ssid} list="nets" onChange={(e) => set("ssid", e.target.value)} placeholder="IONITY-LAB-IOT" />
                <button className="ghost" disabled={busy || !serialSupported} onClick={() => void scanWifi()} title="The board scans; needs fw 2.0 already on it">Scan</button>
              </div>
              <datalist id="nets">{networks?.map((n) => <option key={n.ssid + n.ch} value={n.ssid}>{`${n.rssi} dBm ch${n.ch}${n.open ? " open" : ""}`}</option>)}</datalist>
            </label>
            <label>WiFi password
              <div className="row">
                <input type={f.showPass ? "text" : "password"} value={f.pass} onChange={(e) => set("pass", e.target.value)} autoComplete="new-password" />
                <button className="ghost" onClick={() => set("showPass", !f.showPass)}>{f.showPass ? "Hide" : "Show"}</button>
              </div>
            </label>
            <label>Role
              <select value={f.role} onChange={(e) => set("role", e.target.value as "node" | "standalone")}>
                <option value="node">Node — reports to the MCP host (fleet)</option>
                <option value="standalone">Standalone — on-device MCP only, no host</option>
              </select>
            </label>
            <label>MCP host address <span className="opt">(IP or name; empty = ionity-fleet.local)</span>
              <input value={f.server} disabled={f.role === "standalone"} onChange={(e) => set("server", e.target.value.trim())} placeholder={hostState?.defaults?.server ?? "192.168.0.2"} />
            </label>
            <label>MQTT port<input type="number" value={f.mqtt_port} onChange={(e) => set("mqtt_port", Number(e.target.value))} /></label>
            <label>HTTP port<input type="number" value={f.http_port} onChange={(e) => set("http_port", Number(e.target.value))} /></label>
            <label>Fleet token <span className="opt">(X-Fleet-Token for HTTP ingest)</span>
              <input value={f.fleet_token} onChange={(e) => set("fleet_token", e.target.value)} placeholder={hostState?.defaults?.admin === false ? "enter the admin token above to fill this" : "dev-fleet-token-change-me"} />
            </label>
            <label>Board MCP token <span className="opt">(unlocks write tools on http://board/mcp)</span>
              <div className="row">
                <input value={f.mcp_token} onChange={(e) => set("mcp_token", e.target.value)} placeholder="empty = read-only" />
                <button className="ghost" onClick={() => set("mcp_token", randomToken())}>Generate</button>
              </div>
            </label>
            <label>Site<input value={f.site} onChange={(e) => set("site", e.target.value)} /></label>
            <label>Group<input value={f.group} onChange={(e) => set("group", e.target.value)} /></label>
            <label>Label <span className="opt">(shown on the dashboard / OLED)</span><input value={f.label} onChange={(e) => set("label", e.target.value)} placeholder="Bench S3 #1" /></label>
            <details className="adv">
              <summary>Advanced</summary>
              <div className="cols">
                <label>MQTT user<input value={f.mqtt_user} onChange={(e) => set("mqtt_user", e.target.value)} /></label>
                <label>MQTT password<input type="password" value={f.mqtt_pass} onChange={(e) => set("mqtt_pass", e.target.value)} /></label>
                <label>OTA password <span className="opt">(empty = OTA off)</span><input type="password" value={f.ota_pass} onChange={(e) => set("ota_pass", e.target.value)} /></label>
                <div className="row wrapbtn"><button className="danger" disabled={busy} onClick={() => void factoryReset()}>Factory reset board NVS</button></div>
              </div>
            </details>
          </div>
          {warnings.length > 0 && <ul className="warns">{warnings.map((w) => <li key={w}>{w}</li>)}</ul>}
        </section>

        {/* 4. install */}
        <section className="card span2">
          <h2><span className="n">4</span>Install</h2>
          <div className="row wrapbtn">
            <button className="primary" disabled={!serialSupported || busy || (!f.ssid && !(keepSettings && !skipFlash))} onClick={() => void install()}>
              {skipFlash ? "Provision board" : keepSettings && !f.ssid ? "Update firmware" : "Flash & provision"}
            </button>
            {busy && <span className="spin" aria-hidden />}
            <span className="phase">{PHASE_LABEL[phase]}{["downloading", "flashing"].includes(phase) ? ` · ${progress}%` : ""}</span>
          </div>
          <div className="bar" aria-hidden><div style={{ width: `${phase === "done" ? 100 : ["downloading", "flashing"].includes(phase) ? progress : busy ? 8 : 0}%` }} /></div>
          {error && <div className="banner bad">{error}</div>}
          {phase === "done" && (
            <div className="banner good">
              <b>{board?.device_id}</b> is provisioned{board?.ip ? ` · ${board.ip}` : ""}.
              {verify?.device && <> Host sees it <b>{verify.device.health}</b> over {verify.device.transport}.</>}
              {verify?.tools && <> Board MCP tools: <code>{verify.tools.join(", ")}</code>.</>}
              {verify?.note && <> {verify.note}</>}
              {board?.mcp_url && <> Direct MCP: <code>{board.mcp_url}</code></>}
            </div>
          )}
          <div className="log" ref={logRef} role="log" aria-live="polite">
            {logs.length === 0 && <div className="dim">Serial and flasher output appears here.</div>}
            {logs.map((l, i) => (<div key={i} className={l.kind}>{new Date(l.t).toLocaleTimeString()}  {l.text}</div>))}
          </div>
          <div className="row"><button className="ghost" onClick={() => setLogs([])}>Clear log</button></div>
        </section>
      </main>

      <footer className="foot">
        <p>AEDI · IONITY GLOBAL · Ionity ESP32-MCP · DOC-2026-09-ESP32MCP-FLASH v2.0.0 · Policy 986 AED · License AED 900</p>
        <p>(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd — All Rights Reserved — TM2 · <a href="https://www.ionity.today" target="_blank" rel="noreferrer">www.ionity.today</a> · <a href="https://www.ionity.world" target="_blank" rel="noreferrer">www.ionity.world</a> · Anything is Possible with God.</p>
      </footer>
    </div>
  );
}
