# Varian 3100 FT-IR (Excalibur series): USB protocol and `.bsp` files

Working notes, built from USB captures of a lab's own instrument running
Varian Resolutions Pro (in a virtual machine of the original PC), checked
against the `.bsp` files Resolutions saved in the same sessions. Nothing
here comes from Varian's software itself.

Status: the data path and the `.bsp` format are understood and verified;
the control commands are still being decoded. No OpenFTIR driver yet.

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

## Requests seen (all vendor, `bmRequestType` 0x40 OUT / 0xC0 IN)

| Request | Dir | Notes |
|---|---|---|
| `0x01` | IN, 2 bytes | status/version reads, `wIndex` 1 and 2 |
| `0xF0` | OUT, no data | commands/parameters in `wValue`/`wIndex` (e.g. `wIndex` 0x0D/0x0E with `wValue` 0xFFFF before each alignment read) |
| `0xF2` | IN, 2 bytes | register reads, `wIndex` 0x08xx |
| `0xF4` | IN, 2 bytes | status before a data read (`wIndex` 0x0305) |
| `0xF5` | OUT | firmware upload (above) |
| `0xF6` | IN, 2 bytes | the most frequent: register-style access, `wValue` = register, `wIndex` = operation |
| `0xF7` | IN, 2-34 bytes | info blocks, `wValue` = block number |

Decoding which of these set resolution, scans, gain and start a scan is
in progress (captures at 1 and 4 co-added scans are available).

## Data

### Alignment monitor

A loop of: `0xF0` arm commands, `0xF4` status, one **2112-byte** bulk
read (1056 big-endian `int16` samples, the live interferogram view), then
a 2-byte bulk read (`00 04`). 1990-byte reads (995 samples) also appear
in alignment.

### Collected interferogram

Each collected interferogram arrives as **two bulk reads of 4096 + 4048
bytes = 4072 big-endian `int16` samples**. With the lab's settings (4 cm⁻¹,
asymmetric) this is exactly the interferogram Resolutions stores in the
`.bsp`:

- one sample per laser fringe; retardation step = 1 / ELWN
  (Effective Laser Wavenumber, 15800.8235 cm⁻¹ on this instrument);
- zero path difference at sample **516** (asymmetric interferogram);
- **gain ranging**: within the gain-ranging radius (40 samples) either
  side of ZPD the bench lowers its gain by **16×**; multiply those 81
  samples by 16;
- Resolutions then subtracts the mean and scales to volts
  (≈ 4.7677 × 10⁻⁶ V per count at Sensitivity 4).

Verified: corrected samples match the stored interferograms of both a
background and a sample with correlation 1.000000 (differences at
floating-point rounding level).

Spectrum computation (from the `.bsp` processing history): FFT with
Norton-Beer **Medium** apodization and automatic phase correction, ratio
against the background, truncate to the save range, then absorbance →
%T. Single-beam point spacing = ELWN / 8192 (1.9288 cm⁻¹), 4096 points
to ELWN/2.

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
