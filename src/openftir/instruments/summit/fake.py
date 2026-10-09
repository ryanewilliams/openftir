"""In-memory fake of the Summit's XML protocol + EP 0x83 wire format.

Used by the test suite (so the driver can be exercised without hardware)
and handy for GUI development: ``SummitInstrument(transport=FakeSummitTransport())``
behaves like a real (simulated) instrument.

Every command sent to :meth:`FakeSummitTransport.command` is recorded in
``self.commands`` (in order, exact XML), so tests can assert the exact
command sequence the driver sends. Replies are built from
``docs/summit-protocol.md``'s "Values seen on the user's Summit X" table.
On ``set collectState 1`` it synthesises a run of scans in the exact EP 0x83
wire format (see ``stream.py``), fed out through :meth:`read_data` in
arbitrary-sized chunks so :class:`~.stream.ScanAssembler` is exercised the
same way it would be against the real byte stream.
"""

from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
from typing import Any

import numpy as np

from openftir.instruments.base import InstrumentError
from openftir.processing.fft import single_beam_to_interferogram
from openftir.spectrum import Spectrum, XUnit, YUnit

from .stream import TYPE_DATA, TYPE_END_OF_SCAN

__all__ = ["FakeSummitTransport"]

_HEADER = struct.Struct("<IHHII")
_END_RECORD_PAYLOAD_LEN = 128
_DATA_BLOCK_FLOATS = 40  # "usually 40 or 41 per block" (docs/summit-protocol.md)

#: Keys with a fixed discrete value set (``...Cfg`` -> ``numEntries`` +
#: repeated entries, entry tag = the base key name). See
#: docs/summit-protocol.md "Configuration keys".
_LIST_CFG = {
    "detGainCfg": ("detGain", [1, 2, 4, 8]),
    "hpfCfg": ("hpf", [1, 10, 20, 200]),
    "lpfCfg": ("lpf", [11000, 20000, 50000, 90000]),
    "sourceCfg": ("source", [0, 2]),
}

#: Keys with a min/max/step range.
_RANGE_CFG = {
    "sdpCfg": (256, 68000, 1),
    "peakPosCfg": (1, 4128, 1),
    "fwdVelCfg": (0.4747, 0.4747, 0),
    "rtrVelCfg": (4, 4, 0),
}

#: Diagnostic values and their (fake, "healthy") reading + Cfg range.
_DIAG_DEFAULTS: dict[str, tuple[float, float, float]] = {
    # key -> (value, min, max)
    "plus33v": (33.0, 30.0, 36.0),
    "plus5v": (5.0, 4.5, 5.5),
    "plus12v": (12.0, 11.0, 13.0),
    "minus12v": (-12.0, -13.0, -11.0),
    "laserX": (0.1, -1.0, 1.0),
    "laserY": (0.1, -1.0, 1.0),
    "laserR": (1.0, 0.5, 1.5),
    "irPower": (1.0, 0.2, 2.0),
    "irCurrent": (0.5, 0.0, 1.0),
    "laserCurrent": (0.3, 0.0, 1.0),
    "boardTemp": (35.0, 10.0, 60.0),
    "modulTemp": (30.0, 10.0, 60.0),
    "laserTemp": (28.0, 10.0, 60.0),
    "humidity": (0.0, 0.0, 20.0),
    "driveVolt": (5.0, 4.0, 6.0),
}

_SCALAR_DEFAULTS: dict[str, str] = {
    "benchModel": "142",
    "firmVer": "1.18",
    "laserFreq": "11685.125977",
    "maxRes": "0.5",
    "apertureCfg": "100",
    "startupTime": "120",
    "warmupTime": "120",
    # Not documented in docs/summit-protocol.md (no serialNum capture was
    # analysed); a plausible placeholder so InstrumentInfo.serial isn't
    # empty when talking to the fake.
    "serialNum": "FAKE-SUMMIT-0001",
}


def _format_value(value: Any) -> str:
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value)


def _ideal_single_beam(wn: np.ndarray) -> np.ndarray:
    """A plausible, deterministic glowbar-ish single-beam envelope -- just
    enough shape for a round-trip FFT test, not a physical model."""
    return np.exp(-(((wn - 2000.0) / 1800.0) ** 2)) * (1.0 / (1.0 + np.exp(-(wn - 420.0) / 20.0)))


class FakeSummitTransport:
    """A :class:`~.transport.SummitTransport` that never touches real hardware."""

    def __init__(
        self,
        *,
        rng_seed: int = 0,
        chunk_seed: int = 1,
        scans_available: int = 200,
        noise: float = 0.0006,
        corrupt_scan_ids: "set[int] | tuple[int, ...]" = (),
    ) -> None:
        self.commands: list[str] = []
        self._is_open = False
        self._rng = np.random.default_rng(rng_seed)
        self._chunk_rng = np.random.default_rng(chunk_seed)
        self._noise = float(noise)
        self._scans_available = int(scans_available)
        self._corrupt_scan_ids = set(corrupt_scan_ids)

        self._state: dict[str, str] = dict(_SCALAR_DEFAULTS)
        for key, (value, _lo, _hi) in _DIAG_DEFAULTS.items():
            self._state[key] = str(value)
        # Settable scan-setup keys the driver writes before collecting.
        self._state.update(
            {
                "sampleshuttle": "1",
                "ssp": "1",
                "fwdVel": "0.4747",
                "peakPos": "6154",
                "sdp": "12426",
                "detGain": "1",
                "hpf": "20",
                "lpf": "11000",
                "rtrVel": "4",
                "bsSlot": "0",
                "beampathId": "1",
                "sourceId": "2",
                "aperture": "100",
                "settleTime": "200",
                "bidirScan": "0",
                "stepScanState": "0",
                "collectState": "0",
                "servoState": "0",
                "extTrigger": "0",
                "lightbar": "1",
                "motorsDone": "1",  # the fake has no real motors to wait on
            }
        )

        self._next_scan_id = 1
        self._stream_buffer = bytearray()
        self._chunks: list[bytes] = []
        self._collect_state_history: list[int] = []

    # -- SummitTransport -----------------------------------------------------

    def open(self) -> None:
        self._is_open = True

    def close(self) -> None:
        self._is_open = False

    def command(self, xml: str, timeout: float = 5.0) -> str:
        if not self._is_open:
            raise InstrumentError("fake Summit transport is not open")
        self.commands.append(xml)
        root = ET.fromstring(xml)
        if root.tag != "cmd" or len(root) != 1:
            raise InstrumentError(f"malformed command: {xml!r}")
        node = root[0]
        if node.tag == "get":
            keys = [child.tag for child in node]
            return self._handle_get(keys)
        if node.tag == "set":
            values = {child.tag: (child.text or "").strip() for child in node}
            return self._handle_set(values)
        raise InstrumentError(f"malformed command: {xml!r}")

    def read_data(self, size: int, timeout: float = 5.0) -> bytes:
        if not self._is_open:
            raise InstrumentError("fake Summit transport is not open")
        if not self._chunks:
            return b""
        return self._chunks.pop(0)

    def read_event(self, timeout: float = 0.1) -> str | None:
        return None

    # -- command handling -----------------------------------------------------

    def _handle_get(self, keys: list[str]) -> str:
        parts: list[str] = []
        for key in keys:
            parts.append(self._get_value_xml(key))
        return f"<reply>{''.join(parts)}</reply>"

    def _get_value_xml(self, key: str) -> str:
        if key in _LIST_CFG:
            entry_tag, values = _LIST_CFG[key]
            entries = "".join(f"<{entry_tag}>{v}</{entry_tag}>" for v in values)
            return f"<{key}><numEntries>{len(values)}</numEntries>{entries}</{key}>"
        if key in _RANGE_CFG:
            lo, hi, step = _RANGE_CFG[key]
            return f"<{key}><min>{_format_value(lo)}</min><max>{_format_value(hi)}</max><step>{_format_value(step)}</step></{key}>"
        if key.endswith("Cfg") and key[: -len("Cfg")] in _DIAG_DEFAULTS:
            _value, lo, hi = _DIAG_DEFAULTS[key[: -len("Cfg")]]
            return f"<{key}><min>{lo:g}</min><max>{hi:g}</max><step>0.01</step></{key}>"
        if key == "bpTable":
            return (
                "<bpTable>"
                "<beampath><beampathId>1</beampathId><detector>12</detector></beampath>"
                "<beampath><beampathId>13</beampathId><detector>4</detector></beampath>"
                "</bpTable>"
            )
        if key in self._state:
            return f"<{key}>{self._state[key]}</{key}>"
        return f"<notRecognized>{key}</notRecognized>"

    def _handle_set(self, values: dict[str, str]) -> str:
        parts: list[str] = []
        for key, value in values.items():
            if key in ("sampleshuttle", "stepScanState", "extTrigger"):
                # Documented as notRecognized on this bench model; echo
                # nothing back for them, matching the real protocol.
                parts.append(f"<notRecognized>{key}</notRecognized>")
                continue
            self._state[key] = value
            parts.append(f"<{key}>{value}</{key}>")
            if key == "collectState":
                self._on_collect_state(value)
        return f"<reply>{''.join(parts)}</reply>"

    def _on_collect_state(self, value: str) -> None:
        state = int(float(value))
        self._collect_state_history.append(state)
        if state == 1:
            self._generate_scans()
        # state == 0 (stop): nothing to clean up -- read_data just starts
        # returning b"" once the pre-generated chunks run out.

    # -- EP 0x83 synthesis -----------------------------------------------------

    def _generate_scans(self) -> None:
        peak_pos = int(float(self._state["peakPos"]))
        sdp = int(float(self._state["sdp"]))
        laser_freq = float(self._state["laserFreq"])
        laser_wavenumber = 2.0 * laser_freq

        n_points = 2 * peak_pos
        wn_grid = np.linspace(50.0, min(0.99 * laser_wavenumber / 2.0, 7800.0), 2048)
        ideal_sb = Spectrum(
            x=wn_grid,
            y=_ideal_single_beam(wn_grid),
            x_unit=XUnit.WAVENUMBER,
            y_unit=YUnit.SINGLE_BEAM,
        )
        ideal_igram = single_beam_to_interferogram(ideal_sb, n_points, laser_wavenumber=laser_wavenumber).y
        noise_scale = self._noise * np.max(np.abs(ideal_igram))
        tail_len = max(sdp - n_points, 0)

        buf = bytearray()
        for _ in range(self._scans_available):
            scan_id = self._next_scan_id
            self._next_scan_id += 1

            noisy = ideal_igram + self._rng.normal(0.0, noise_scale, size=n_points)
            if tail_len:
                # "the last few dozen samples of a scan are unreliable" --
                # modelled here as extra noise beyond the trustworthy
                # symmetric window, never used by the co-add (callers trim
                # to 2*peakPos before the FFT).
                tail = self._rng.normal(0.0, noise_scale, size=tail_len)
                samples = np.concatenate([noisy, tail]).astype("<f4")
            else:
                samples = noisy.astype("<f4")

            corrupt = scan_id in self._corrupt_scan_ids
            buf += self._encode_scan(scan_id, samples, corrupt=corrupt)

        self._stream_buffer += buf
        self._chunk_stream()

    def _encode_scan(self, scan_id: int, samples: np.ndarray, *, corrupt: bool) -> bytes:
        out = bytearray()
        raw = samples.tobytes()
        block_bytes = _DATA_BLOCK_FLOATS * 4
        block = 0
        for offset in range(0, len(raw), block_bytes):
            payload = raw[offset : offset + block_bytes]
            out += _HEADER.pack(TYPE_DATA, scan_id & 0xFFFF, block & 0xFFFF, 0, len(payload))
            out += payload
            block += 1
        end_payload = bytes(_END_RECORD_PAYLOAD_LEN)
        out += _HEADER.pack(TYPE_END_OF_SCAN, scan_id & 0xFFFF, block & 0xFFFF, 0, len(end_payload))
        out += end_payload

        if corrupt:
            # Scramble the *second* data record's header (first record's
            # header+payload, i.e. right after it) so the record framing
            # itself goes out of sync -- corrupting payload bytes wouldn't
            # do that, they'd just change sample values. ScanAssembler must
            # resync past this and drop the whole scan.
            out = bytearray(out)
            second_header_offset = _HEADER.size + _DATA_BLOCK_FLOATS * 4
            for i in range(second_header_offset, min(second_header_offset + _HEADER.size, len(out))):
                out[i] ^= 0xFF
        return bytes(out)

    def _chunk_stream(self) -> None:
        """Split ``self._stream_buffer`` into arbitrarily-sized pieces (as a
        real USB bulk stream would arrive in independent, differently-sized
        transfers) and queue them for :meth:`read_data`."""
        buf = self._stream_buffer
        self._stream_buffer = bytearray()
        pos = 0
        n = len(buf)
        while pos < n:
            size = int(self._chunk_rng.integers(1, 600))
            size = min(size, n - pos)
            self._chunks.append(bytes(buf[pos : pos + size]))
            pos += size
