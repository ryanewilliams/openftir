# usbcap

Offline analysis of USB packet captures, built to help work out the wire
protocol of the Thermo Nicolet Summit X (VID:PID `0ebb:000c`) from captures
taken with Wireshark + USBPcap on Windows, or with `usbmon` on Linux. It only
*reads* `.pcap`/`.pcapng` files you already captured -- it never touches USB
hardware itself (that's `src/openftir/probe.py`, a separate, unrelated tool).

Pure Python + numpy. No `scapy`/`pyshark`/`tshark` dependency -- Wireshark is
only needed to *capture*, not to analyse.

## Running it

From the repo root, with the project's venv:

```bash
.venv/bin/python -m tools.usbcap <command> ...
```

### Commands

- `summary CAPTURE [--vid 0ebb] [--pid 000c] [--no-filter]`
  Lists every device identified via its `GET_DESCRIPTOR` responses (VID:PID,
  device/config/interface/endpoint descriptors), capture duration, and
  per-endpoint transfer counts/byte totals/types. Defaults to the Summit X's
  VID:PID; pass `--no-filter` to see every device in the capture.

- `timeline CAPTURE [--limit N] [--endpoint 0x81] [--min-bytes N]`
  One line per transfer (relative time, endpoint, direction, type, length,
  first 32 bytes as hex+ASCII), oldest first. Runs of 3+ consecutive,
  byte-identical transfers collapse into one line ("x120 identical, every
  0.500 s"). `--endpoint` takes an endpoint *address* byte (bit 7 = IN).

- `messages CAPTURE [--prefix-len 8]`
  Clusters OUT payloads by (first `prefix-len` bytes, length) and shows each
  distinct command candidate with its count, an example, and the endpoint/
  length of the IN transfer that typically follows it.

- `diff CAPTURE_A CAPTURE_B`
  Aligns the two captures' distinct OUT message sets (by length + first
  byte) and reports messages only in A, only in B, and for same-shaped
  messages that differ, the exact differing byte offsets (e.g. running this
  on a 1-scan vs 4-scan background capture should point straight at the
  scan-count byte).

- `extract CAPTURE --endpoint 0x82 --out data.bin [--gap-ms 50] [--as float32le]`
  Concatenates IN payloads from one endpoint into `data.bin`; with
  `--gap-ms`, splits on IN-to-IN gaps bigger than that into `data_000.bin`,
  `data_001.bin`, ... With `--as`, also writes a matching `.npy`
  (dtypes: `int16le/be`, `int32le/be`, `uint16le`, `uint32le`,
  `float32le/be`, `float64le/be`).

- `guess-arrays CAPTURE [--min-bytes 512]`
  Concatenates all IN data per endpoint and, for each endpoint with enough
  bytes, tries every dtype above and reports which look like real numeric
  data (finite, sane dynamic range) versus garbage, plus whether a
  centre-burst (the FTIR interferogram's sharp, symmetric-decaying peak) was
  found and at what index.

All commands accept `--vid`/`--pid` (default `0ebb`/`000c`) and `--no-filter`
to disable the VID:PID filter.

## Layout

- `pcap_reader.py` -- classic pcap and pcapng container reader (both byte
  orders, multiple interfaces); yields `Frame(t, linktype, data)`.
- `usbpcap.py` -- parses Windows USBPcap frames (DLT 249).
- `usbmon.py` -- parses Linux usbmon frames (DLT 189 / 220, 48- or 64-byte
  header, chosen by link type, not by guessing from frame length).
- `model.py` -- `UsbEvent`/`Transfer` data model and submit+complete pairing
  by IRP id / URB id.
- `descriptors.py` -- USB device/config/interface/endpoint descriptor
  parsing and VID:PID device identification from captured
  `GET_DESCRIPTOR` responses.
- `analysis.py` -- the logic behind each CLI command.
- `cli.py` / `__main__.py` -- argparse wiring for `python -m tools.usbcap`.
- `synth.py` -- synthetic pcapng/USBPcap frame builder used only by the test
  suite (no real capture exists yet).

## Limitations

- The USBPcap control-transfer stage model is simplified: SETUP/DATA/STATUS
  records sharing one IRP id are merged into a single `Transfer`, taking the
  setup packet from whichever stage carried one and concatenating
  matching-direction payload bytes. This hasn't been checked against a real
  USBPcap capture yet (none exists); if real captures show a different
  stage-grouping pattern, `pair_events` in `model.py` is the place to adjust.
- `guess-arrays`' centre-burst detector and plausibility scoring are
  heuristics tuned against synthetic data, not validated against a real
  interferogram; treat its ranking as a starting point for manual inspection,
  not a verdict.
- usbmon timestamps are taken from the pcap/pcapng frame header, not the
  packet's own embedded `ts_sec`/`ts_usec` fields (both should agree; the
  frame header is simpler to handle uniformly across link types).
- No pcapng Name Resolution/Interface Statistics blocks, no compressed
  captures, and no `tshark`/`dumpcap` fallback -- only what's needed to read
  Wireshark-written USBPcap/usbmon captures.
