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
  | `0x83` IN | bulk | 512 | *guess: interferogram data (unused while idle)* |
  | `0x84` IN | interrupt (bInterval 4) | 512 | asynchronous `<benchMsg>` events |

- Every message on 0x01/0x82/0x84 is **a uint32 little-endian byte length
  followed by ASCII XML** (no terminator).
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
| `laserFreq` | 11685.125977 | reference laser wavenumber in cm⁻¹ (~855.8 nm diode laser, *guess*); sets the FFT x-axis instead of HeNe 15798 |
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

## Open questions (need captures 03–06)

- Command that starts a scan, and how co-adds / scan count are requested.
- Format of the data on EP 0x83 (raw interferogram? co-added? int32/float32?).
- How resolution maps to `sdp`/`peakPos` (compare 4 vs 8 cm⁻¹ captures).
- Meaning of `ssp` (*guess: sample spacing / undersampling ratio*).
