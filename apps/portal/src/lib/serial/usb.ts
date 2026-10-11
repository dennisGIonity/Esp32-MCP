/*
 * AEDI - IONITY GLOBAL | @ionity/portal | usb.ts | Policy 986 AED
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * USB identities of the serial interfaces ESP32 boards ship with.
 * The bridge decides two things: which firmware image prints its console
 * where (UART0 vs native USB CDC), and whether a reset makes the port vanish.
 */

export const ESPRESSIF_VID = 0x303a;

const BRIDGES: Readonly<Record<number, string>> = {
  0x1a86: 'WCH CH340/CH343',
  0x10c4: 'Silicon Labs CP210x',
  0x0403: 'FTDI',
  [ESPRESSIF_VID]: 'Espressif native USB',
};

/** Shown first in the browser's port picker; "Show all" still lists other ports. */
export const USB_FILTERS: SerialPortFilter[] = Object.keys(BRIDGES).map((vid) => ({ usbVendorId: Number(vid) }));

export interface PortDescription {
  readonly vid: number | undefined;
  readonly pid: number | undefined;
  readonly bridge: string;
  /**
   * ESP32-S3/C3/C6 native USB (USB-Serial-JTAG): a chip reset drops the USB
   * device and it enumerates again - the page sees disconnect + connect.
   */
  readonly nativeUsb: boolean;
}

export function describePort(port: SerialPort): PortDescription {
  const info = port.getInfo();
  const vid = info.usbVendorId;
  return {
    vid,
    pid: info.usbProductId,
    bridge: vid === undefined ? (info.bluetoothServiceClassId !== undefined ? 'Bluetooth serial' : 'unknown port') : (BRIDGES[vid] ?? `USB ${hex4(vid)}`),
    nativeUsb: vid === ESPRESSIF_VID,
  };
}

export function hex4(n: number | undefined): string {
  return n === undefined ? '----' : n.toString(16).padStart(4, '0');
}

export function sameUsbDevice(a: PortDescription, b: PortDescription): boolean {
  return a.vid !== undefined && a.vid === b.vid && a.pid === b.pid;
}
