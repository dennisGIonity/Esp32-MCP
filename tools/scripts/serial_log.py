#!/usr/bin/env python3
"""
AEDI - IONITY GLOBAL | Print a board's serial log for N seconds (no reset held)
Policy 986 AED | (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd

    python scripts/serial_log.py COM3 30
    python3 serial_log.py /dev/ttyUSB0 30 --reset     # pulse EN first to see the boot
"""
import sys
import time

import serial

port = sys.argv[1]
secs = float(sys.argv[2]) if len(sys.argv) > 2 else 20
p = serial.Serial(port, 115200, timeout=0.5, dsrdtr=False, rtscts=False)
p.dtr = False
p.rts = False
if "--reset" in sys.argv:
    p.rts = True; time.sleep(0.15); p.rts = False
end = time.time() + secs
while time.time() < end:
    line = p.readline().decode("utf-8", "replace").rstrip()
    if line:
        print(line, flush=True)
