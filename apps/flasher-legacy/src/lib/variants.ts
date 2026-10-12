// AEDI - IONITY GLOBAL | pick the right image for the board | Policy 986 AED

export interface BuildInfo {
  variant: string;
  name: string;
  chip_family: string;
  native_usb: boolean;
  file: string;
  offset: number;
  size: number;
  sha256: string;
  app_pct?: number | null;
}

export interface Manifest {
  product: string;
  version: string;
  built_at?: string;
  builds: BuildInfo[];
}

export const ESPRESSIF_VID = 0x303a;
const BRIDGES: Record<number, string> = {
  0x1a86: "WCH CH340/CH343",
  0x10c4: "Silicon Labs CP210x",
  0x0403: "FTDI",
  0x303a: "Espressif native USB",
};

export function bridgeName(vid?: number): string {
  return vid === undefined ? "unknown USB" : BRIDGES[vid] ?? `USB ${vid.toString(16).padStart(4, "0")}`;
}

/** esptool-js reports e.g. "ESP32-S3 (QFN56) (revision v0.2)". Normalise to a family. */
export function chipFamily(desc: string): string {
  const d = desc.toUpperCase();
  for (const f of ["ESP32-S3", "ESP32-S2", "ESP32-C3", "ESP32-C6", "ESP32-C5", "ESP32-H2", "ESP32-P4", "ESP32-C2"])
    if (d.includes(f)) return f;
  return d.includes("ESP32") ? "ESP32" : d;
}

/** Native-USB boards (VID 0x303A) need the image whose Serial is USB CDC,
 *  or the provisioning handshake would go out of UART0 pins nobody is listening on. */
export function pickBuild(m: Manifest, family: string, vid?: number): BuildInfo | undefined {
  const same = m.builds.filter((b) => b.chip_family === family);
  if (!same.length) return undefined;
  const native = vid === ESPRESSIF_VID;
  return same.find((b) => b.native_usb === native) ?? same[0];
}
