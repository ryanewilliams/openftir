# Nicolet Summit USB protocol notes

Working notes on the Summit X wire protocol, learned only by observing USB
traffic between the user's own instrument and its vendor software (captured
with Wireshark + USBPcap; analysed with `python -m tools.usbcap`). No vendor
software was decompiled or inspected. Everything here is provisional until the
OpenFTIR driver reproduces it.

Status legend: **confirmed** = seen in captures; *guess* = inferred, unverified.

## Transport (confirmed, capture `01_plug`)

- USB 2.0 high-speed, VID:PID `0ebb:000c`, device class 0xFF (vendor), one
  configuration, one interface (class 0xFF):

  | Endpoint | Type | Max packet | Role |
  |---|---|---|---|
  | `0x01` OUT | bulk | 512 | commands |
  | `0x82` IN | bulk | 512 | replies |
  | `0x83` IN | bulk | 512 | interferogram data stream (see below) |
  | `0x84` IN | interrupt (bInterval 4) | 512 | asynchronous `<benchMsg>` events |

- **Commands** on 0x01 are a uint32 little-endian byte length followed by
  ASCII XML. **Replies** on 0x82 and events on 0x84 are bare XML with no
  length prefix (confirmed in captures 03/04).
- Request/response is strictly one command → one reply on 0x82.

## Message grammar (confirmed)

```
<cmd><get><a/><b/>...</get></cmd>        -> <reply><a>val</a><b>val</b>...</reply>
<cmd><set><a>val</a>...</set></cmd>      -> <reply><a>val</a>...</reply>   (echo)
unknown key                               -> <notRecognized>key</notRecognized> inside reply
bad argument                              -> <reply><error><key/></error></reply>
async (EP 0x84)                           -> <benchMsg><motorsDone>0</motorsDone></benchMsg>
```

Configuration keys (`...Cfg`) return `<min>/<max>/<step>` or
`<numEntries>` + repeated values.

## Values seen on the user's Summit X

| Key | Value | Notes |
|---|---|---|
| `benchModel` | 142 | |
| `firmVer` | 1.18 | |
| `laserFreq` | 11685.125977 | reference laser wavenumber in cm⁻¹ (~855.8 nm diode laser); the data are sampled at **2 × laserFreq** (see "Wavenumber axis") |
| `maxRes` | 0.5 | cm⁻¹ |
| `fwdVel` / `fwdVelCfg` | 0.4747 | mirror velocity, only option |
| `rtrVel` | 4 | retrace velocity |
| `detGainCfg` | 1, 2, 4, 8 | |
| `hpfCfg` / `lpfCfg` | 1/10/20/200 · 11000/20000/50000/90000 | Paradigm sets hpf 20, lpf 11000 |
| `apertureCfg` | 100 | fixed |
| `bpTable` | beampath 1 → detector 12; beampath 13 → detector 4 | Paradigm uses beampath 1 |
| `sourceCfg` | 0, 2 | Paradigm uses source 2 |
| `sdpCfg` | 256 – 68000 | *guess: number of data points per scan* |
| `peakPosCfg` | 1 – 4128 | *guess: expected centre-burst index* (Paradigm sent 6154 > max — unexplained) |
| `startupTime` / `warmupTime` | 120 / 120 | seconds, *guess* |

## Diagnostics keys (confirmed readable)

`plus33v plus5v plus12v minus12v laserX laserY laserR irPower irCurrent
laserCurrent boardTemp modulTemp laserTemp humidity driveVolt
laserGainX/Y/R endplatePosX/Y endplatePhzX/Y`. `humidity` read 0.

## Connection sequence used by Paradigm (confirmed)

1. `get benchModel`, then `get` of all `...Cfg` diagnostics ranges, `get firmVer`.
2. `get benchModel, laserFreq`; probe motors `get motorId` 43/12/8/49/13
   (8 exists: 2-position motor; others return `<error>`).
3. `get i2cMsg` addr 118 (errorFlag 128), `set valWheelPos 0`.
4. `get` optical config (`apertureCfg bpTable bsTable detGainCfg fwdVelCfg hpfCfg
   laserFreq lpfCfg maxRes peakPosCfg rtrVelCfg sdpCfg sourceCfg sspCfg`).
5. Set up a scan: `set sampleshuttle 1` (notRecognized on this model),
   `set ssp 1, fwdVel 0.4747, peakPos 6154, sdp 12426, detGain 1`,
   `set hpf 20`, `set lpf 11000`, `set rtrVel 4`,
   `set bsSlot 0, beampathId 1, sourceId 2, aperture 100`,
   `set settleTime 200, bidirScan 0`.
6. Poll `get motorsDone` (~every 0.4 s) until 1; also `set lightbar 1|100`
   (front LED, *guess*).
7. `get` live diagnostics (voltages, laser, temps, humidity), `driveVolt`,
   laser gains, endplate position/phase.

## Collecting scans (confirmed, captures 03, 04, 06)

1. Send the scan setup from step 5 above (`peakPos`/`sdp` depend on resolution).
2. Poll `get motorsDone` until 1.
3. `set stepScanState 0, collectState 0, servoState 1, extTrigger 0`
   (stepScanState/extTrigger are notRecognized on this model).
4. `set ... collectState 1 ...` → the instrument streams **every scan
   individually** on EP 0x83. The PC co-adds. Paradigm also sets `lightbar 4`
   while collecting and `lightbar 1` when idle.
5. `set ... collectState 0 ...` to stop; the scan in progress is cut short and
   must be discarded.

Paradigm's background is a fixed 4 scans (+1 aborted). On close it sends
nothing special; it just stops polling.

### EP 0x83 data stream

A byte stream (USB transfer boundaries are meaningless) of records, each a
16-byte little-endian header followed by a payload:

| Offset | Type | Meaning |
|---|---|---|
| 0 | uint32 | record type: `0x10000000` data, `0x90000000` end-of-scan |
| 4 | uint16 | scan counter (global, increments per scan) |
| 6 | uint16 | block index within the scan (0, 1, 2 …) |
| 8 | uint32 | 0 |
| 12 | uint32 | payload length in bytes |

- Data payloads are **float32 LE** interferogram samples, usually 40 or 41
  per block. A 4 cm⁻¹ scan is ~12 390–12 425 samples (≤ `sdp`).
- The end-of-scan record has a 128-byte payload (not yet decoded).
- The centre burst (ZPD) sits exactly at sample index `peakPos`.
- The last few dozen samples of a scan are unreliable: trim symmetrically
  around `peakPos` (2·peakPos samples) before the FFT.

### Wavenumber axis

The effective sampling wavenumber is **2 × laserFreq** (two samples per
laser fringe): 23 370.25 cm⁻¹ on this unit. Verified on the atmospheric CO₂
Q-branch (found at 668 cm⁻¹, true 667.4) and the H₂O bending band.

### Resolution → scan length

| Resolution | peakPos | sdp |
|---|---|---|
| 4 cm⁻¹ | 6154 | 12426 |
| 8 cm⁻¹ | 3124 | 6367 |

In both, `sdp = 2·peakPos + 118`; `peakPos ≈ 1.0372·(2·laserFreq / res) + 94`
is a two-point fit, unverified for other resolutions.

### Validation

Decoding capture 04 (4 sample scans) and capture 03's first run (4 background
scans), then FFT with OpenFTIR (Happ–Genzel, Mertz, 2× laser), reproduces the
`%T` spectrum Paradigm saved to within **0.44 %T rms**.

## Open questions

- Content of the 128-byte end-of-scan record.
- `peakPos`/`sdp` for resolutions other than 4 and 8 cm⁻¹ (needs captures).
- What capture 03's second, 10-scan run was for (not used by Paradigm's
  background).

## Previously open questions (answered above)


- Command that starts a scan, and how co-adds / scan count are requested.
- Format of the data on EP 0x83 (raw interferogram? co-added? int32/float32?).
- How resolution maps to `sdp`/`peakPos` (compare 4 vs 8 cm⁻¹ captures).
- Meaning of `ssp` (*guess: sample spacing / undersampling ratio*).

## Driver status

A real driver (`src/openftir/instruments/summit/`) implements everything
above; registered as `"summit"` in the driver registry. It only ever sends
the commands/values documented here (`peakPos`/`sdp` aside, which depend on
resolution); it never sends a motor, `valWheelPos`, `i2cMsg` or mapping
command.

Two corrections found while building and validating it against real
captures (`tests/test_summit_validation.py`, gated on `OPENFTIR_SUMMIT_DATA`
since those captures embed the instrument's serial number):

- The end-of-scan record's offset-8 "zero" field is **not** reliably zero
  (real captures show varying nonzero values there -- still not decoded).
  Only the offset-0 data/end-of-scan records rely on that field being zero
  for validity checking.
- Its payload is **not** a fixed 128 bytes -- a real 4-scan, 4 cm⁻¹ capture
  showed 44/96/148/36-byte end-of-scan payloads across 4 consecutive scans.

Validated end-to-end: co-adding `bg10_scan2..5` (background) and
`s4_scan20..23` (sample) through the driver's (hardware-free) co-add+FFT
path and ratioing reproduces OMNIC's saved `04.spa` to ~0.44 %T rms over
650-3950 cm⁻¹, matching the number above. Feeding the real
`6185ac80-04_sample_4scan_4cm.pcapng` capture's EP 0x83 bytes through
`ScanAssembler` yields scans 20-23 with ZPD at index 6153-6154, as expected
for `peakPos=6154`.

Only 4 and 8 cm⁻¹ have a confirmed `peakPos`/`sdp` (the exact table above);
other resolutions use the extrapolation formula and are marked unverified
in `InstrumentInfo.extra["unverified_resolutions"]`.
