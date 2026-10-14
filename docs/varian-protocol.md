# Varian 3100 FT-IR (Excalibur series): USB protocol and `.bsp` files

Working notes, built from USB captures of a lab's own instrument running
Varian Resolutions Pro (in a virtual machine of the original PC), checked
against the `.bsp` files Resolutions saved in the same sessions. Nothing
here comes from Varian's software itself.

Status: decoded and implemented in `openftir/instruments/varian/`. The
driver sends exactly what Resolutions sent in the captures (checked by
replaying them, `tests/test_varian_replay.py`), and its spectra match the
ones Resolutions saved to within 0.004 %T. Only the lab's settings (4 cm⁻¹,
KBr, 5 kHz, Sensitivity 4) have been captured, so only those are supported.
Not yet run against the bench itself.

## Hardware

- USB 1.x full-speed device, VID:PID **0614:0001** (0614 = Bio-Rad; the
  Excalibur bench is a Bio-Rad/Digilab design). No string descriptors.
  Windows calls it "Excalibur Spectrometer".
- One interface: endpoint 0 (control) plus one **bulk IN endpoint 0x81**.
  All commands go over vendor control requests on endpoint 0.

## Firmware upload (every connect)

The bench runs a program that the PC loads each time it connects (and
Resolutions reloads it on reconnects): vendor request `0xF5` OUT
(`bmRequestType 0x40`), sending an Intel HEX text file in 4096-byte
chunks (`wValue` = chunk length, last chunk shorter). It's about 24 KB,
690 records.

The firmware is Varian's code. **OpenFTIR does not and must not include
or distribute it.** A driver will load it from a file the user supplies
(extracted from their own Resolutions Pro installation or from a USB
capture of it). `.gitignore` refuses `*firmware*.hex` and capture files.

## Requests (all vendor, `bmRequestType` 0x40 OUT / 0xC0 IN)

| Request | Dir | Use |
|---|---|---|
| `0x01` | IN 2 | status: `wIndex` 1 → `00 02`; `wIndex` 2 → `04 02` once the firmware runs (`00 00` before) |
| `0xF0` | OUT | set a register: `wIndex` = register, `wValue` = value (table below) |
| `0xF2` | IN 2 | bench command, `wIndex` = opcode << 8 \| operand: `0x02rr` reads register `rr`, `(0x40\|rr)vv` writes `vv` to it; other opcodes set acquisition values (below) |
| `0xF4` | IN 2 | arm/status before a read: `0x0303` alignment read, `0x0304` scan, `0x0305` calibration/monitor read |
| `0xF5` | OUT | firmware upload (above) |
| `0xF6` | IN 2 | two-wire bus: `wIndex` 0 = start + address (`wValue`), 1 = stop, 2 = write byte (`wValue`), 3 = read byte (`wValue` 1 = last) |
| `0xF7` | IN | accessory ID chips: `wValue` = channel with `wIndex` 0 (`00 00` = present, `00 03` = none), then `wIndex` 1 (10 bytes: ROM ID), 2, 4 (34 bytes: memory) |

`0xF0` registers:

| Register | Meaning |
|---|---|
| `0x08`, `0x09`, `0x0A`, `0x0B` | transfer layout of the next read: full 4096-byte transfers, their size, bytes in the last transfer, bytes per sample (2) |
| `0x0C` | 1 = scan continuously, 0 = stop after the current scan |
| `0x0D`, `0x0E` | both written `0xFFFF` to start a read |

Bench registers (via `0xF2`): `0x10` reads `0x08` once the cold-start
calibration has run since power-on, `0x00` before. `0x0E` bit 0 is set on a
calibrated bench and cleared on a cold one; bit 7 is always set. `0x01`
selects the signal channel (`0x07` normal, `0x0C`/`0x0D`/`0x0E` during
calibration). `0xA0`–`0xA9` program the read: points (A0:A1), scan start
offset (A2:A3, signed), gain-ranging zone start (A4:A5 = 475) and length
(A6:A7 = 80), points after it (A8:A9 = 3517).

## Sequences

### Connect

Status reads, firmware upload, accessory IDs (channel 7, then
`0x0801`/`0x0802` reads, status, channels 4, 5, 6, 2, 3, 4).

### Before every collection

Resolutions reloads the firmware and sets the bench up again each time:
stop (`0x0C` = 0, twice), `0x030D`, `0x0300`, read register `0x10`. On a
warm bench: `0x0135`, `0xB500`, `0xAB04`, rewrite registers `0x0E` and
`0x01`, `0xB6FF`, `0xB801`, set bit 0 then bit 7 of `0x0E`, read the bus
port expander at 0x41 and write it back to 0x40 with the top three bits
cleared (twice), probe bus addresses 0x44, 0x46, 0x4A, 0x48, 0x42, then
`0xB703`, `0x030E`, `0x030C`. A cold bench differs: an extra stop +
`0x030D`, `0xAB03`, bit 0 of `0x0E` cleared, no 0x44 probe, and the
calibration below runs before the warm setup.

### Alignment read, then the scan

1. Layout 1990 bytes, A0:A1 = 995 points, queue a bulk read, `0x0D`/`0x0E`
   = `0xFFFF`, `0xF4 0x0303`. 995 samples come back, then `00 04`.
2. Find the centre burst in them. Its index minus 516 is the scan offset,
   sent as A3 (low byte) then A2 (high byte). This puts the scan's centre
   burst on sample 516.
3. Layout 8144 bytes, the A4–A9 geometry, `0xBA00`.
4. Queue a bulk read, `0x0C` = 1 for several scans (0 for one), `0x0D`/
   `0x0E` = `0xFFFF`, `0xF4 0x0304`. Each scan arrives as 4096 + 4048
   bytes and `00 04`, about 1.5 s apart. With several scans, `0x0C` = 0 is
   sent when the second-to-last one has arrived; the last still comes.
   Co-adding is done on the PC.
5. Stop (`0x0C` = 0), `0x030D`.

### Cold-start calibration (after power-on)

About 3 minutes; seen in two captures, identical in structure. Signal
reads here are 1056 samples (layout 2112 bytes, status `0x0305`).

1. Register `0x10` = 0, channel `0x0E` selected (`0x0E` = 0, `0x01` =
   `0x0E`), gains BD/BE/BF = `0xFF` (applied with `0x0301`).
2. Raster: 77 fixed settings of B0/B1/AF (applied with `0x0308`), one read
   each (the first twice). When a read's lowest sample is 0x6000 or more,
   all three gains are halved (FF → 80 → 40 → 20) and the point is read
   again.
3. Drive to 0/0/0, the first raster point, then back along the raster
   into the best point (highest level per unit gain), and hill-climb from
   there in directions (+1, −1, 0) and (+1, +1, −1), steps 7, 5, 3, 1.
4. For channels `0x0E`, `0x0C`, `0x0D` (gains BD, BE, BF): set the gain so
   the level is about 20,500 counts.
5. Register `0x10` = `0x08`, restore `0x0E` and `0x01`.

OpenFTIR reproduces steps 1, 2, 3 (up to the first fine-search probes), 4
and 5; Resolutions' later fine-search moves are a line search whose exact
rules two noisy runs don't pin down, so OpenFTIR finishes the climb its own
way (`calibration.py`).

## Data

### Alignment and calibration reads

995 samples (1990 bytes) for the alignment read before each scan, 1056
samples (2112 bytes) during the cold-start calibration; each followed by a
2-byte `00 04` marker (a failed read returns `0x5555` samples and a
different marker).

### Collected interferogram

Each collected interferogram arrives as **two bulk reads of 4096 + 4048
bytes = 4072 big-endian `int16` samples**. With the lab's settings (4 cm⁻¹,
asymmetric) this is exactly the interferogram Resolutions stores in the
`.bsp`:

- one sample per laser fringe; retardation step = 1 / ELWN
  (Effective Laser Wavenumber, 15800.8235 cm⁻¹ on this instrument);
- zero path difference at sample **516** (asymmetric interferogram);
- **gain ranging**: around ZPD the bench lowers its gain by **16×** for
  80 samples (476–555); multiply those by 16;
- Resolutions then subtracts the mean and scales to volts
  (≈ 4.7677 × 10⁻⁶ V per count at Sensitivity 4).

Verified: corrected samples match the stored interferograms of both a
background and a sample with correlation 1.000000 (differences at
floating-point rounding level).

Spectrum computation (from the `.bsp` processing history, then matched
numerically): Mertz phase correction with **ramp weighting** of the
double-sided region (0 at the first sample, 0.5 at ZPD), Norton-Beer
**Medium** apodization over the long side, phase from the 516 points either
side of ZPD with a triangular window, an 8192-point FFT (1.9288 cm⁻¹
spacing), then ratio to the background. OpenFTIR's
`asymmetric_mertz_single_beam` reproduces Resolutions' %T to 0.004 %T rms
from the raw USB data.

## `.bsp` files (Resolutions Pro)

OLE2 compound document. Spectra are streams under `Spectra/` (listed in
`Spectra/IndexTable`). Each stream: a `Data` block (doubles on
`x = (first + i) * dx`), a `Parms` block with the axis labels (e.g.
`Wavenumber` / `%Transmittance`; `Response` for a single beam),
`Properties` (name/value text: Time Stamp, Resolution, Scans, Background
Scans, Effective Laser Wavenumber, Sensitivity, Speed, ...), and for
collected spectra the raw `Interferogram` (second `Data` block, volts vs
retardation) and a processing `History`. A sample saved after collecting
a background holds both spectra. Reader: `openftir/io/_bsp.py`.
