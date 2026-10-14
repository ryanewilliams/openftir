"""Varian 3100 FT-IR (Excalibur) wire constants, firmware file and scan data.

Everything here comes from USB captures of Varian Resolutions Pro driving
the bench; see ``docs/varian-protocol.md``.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from openftir.instruments.base import InstrumentFileError

VENDOR_ID = 0x0614   # Bio-Rad (the Excalibur bench is a Bio-Rad/Digilab design)
PRODUCT_ID = 0x0001
EP_DATA_IN = 0x81

# Vendor requests (bmRequestType 0x40 OUT / 0xC0 IN).
REQ_STATUS = 0x01      # IN 2: wIndex 1 -> 00 02, wIndex 2 -> 04 02 once firmware runs
REQ_SET = 0xF0         # OUT: wIndex = register, wValue = value
REQ_BENCH = 0xF2       # IN 2: wIndex = opcode << 8 | operand
REQ_ARM_STATUS = 0xF4  # IN 2: wIndex 0x0303 alignment read, 0x0304 scan
REQ_FIRMWARE = 0xF5    # OUT: firmware file in 4096-byte chunks, wValue = chunk length
REQ_BUS = 0xF6         # IN 2: two-wire bus; wIndex 0 = address, 1 = stop, 2 = write, 3 = read
REQ_ID = 0xF7          # IN: accessory ID blocks, wValue = channel

STATUS_FIRMWARE_RUNNING = b"\x04\x02"

# REQ_SET registers.
REG_CHUNKS = 0x08       # full 4096-byte transfers per read
REG_CHUNK_SIZE = 0x09
REG_LAST_CHUNK = 0x0A   # bytes in the final (short) transfer
REG_SAMPLE_BYTES = 0x0B
REG_CONTINUOUS = 0x0C   # 1 = keep scanning, 0 = stop after the current scan
REG_ARM = 0x0D          # both written 0xFFFF to start a read
REG_TRIGGER = 0x0E

FIRMWARE_CHUNK = 4096

# Alignment read used to find the centre burst before a scan.
ALIGN_POINTS = 995
# Collected interferogram at 4 cm-1 (the only setting captured so far).
SCAN_POINTS = 4072
SCAN_TRANSFERS = (4096, 4048)
TERMINATOR = b"\x00\x04"
ZPD_INDEX = 516
# Gain ranging: the bench lowers its gain 16x for 80 samples around ZPD
# (scan registers A4:A5 = 475, A6:A7 = 80). In the data those are samples
# 476..555, checked against Resolutions' stored interferograms.
GAIN_START = 476
GAIN_POINTS = 80
GAIN_FACTOR = 16.0
# Detector volts per count at Sensitivity 4 (Resolutions' interferogram units).
VOLTS_PER_COUNT = 4.767705e-6
# "Effective Laser Wavenumber" from Resolutions Pro on the lab's bench.
DEFAULT_LASER_WAVENUMBER = 15800.8235

FIRMWARE_ENV = "OPENFTIR_VARIAN_FIRMWARE"


def load_firmware(path: str | os.PathLike | None) -> bytes:
    """Read and sanity-check the user's firmware file (Intel HEX text).

    OpenFTIR does not ship this file: it is Varian's code, loaded into the
    bench on every connect. Users keep their own copy (from their
    Resolutions Pro install or a USB capture of it). Only the file's
    Intel HEX structure is checked; the bytes are sent unchanged.
    """
    if not path:
        path = os.environ.get(FIRMWARE_ENV) or None
    if not path:
        raise InstrumentFileError("firmware", "No Varian firmware file has been chosen.")
    p = Path(path)
    try:
        data = p.read_bytes()
    except OSError as exc:
        raise InstrumentFileError("firmware", f"Could not read the Varian firmware file {p}: {exc}") from None
    problem = _intel_hex_problem(data)
    if problem:
        raise InstrumentFileError("firmware", f"{p.name} is not a Varian firmware file ({problem}).")
    return data


def _intel_hex_problem(data: bytes) -> str | None:
    if not 1000 <= len(data) <= 1_000_000:
        return f"unexpected size {len(data)} bytes"
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError:
        return "not a text file"
    records = [line.strip() for line in text.splitlines() if line.strip()]
    if not records:
        return "empty"
    for n, rec in enumerate(records, 1):
        if not rec.startswith(":") or len(rec) < 11:
            return f"line {n} is not an Intel HEX record"
        try:
            raw = bytes.fromhex(rec[1:])
        except ValueError:
            return f"line {n} is not hexadecimal"
        if len(raw) != raw[0] + 5:
            return f"line {n} has the wrong length"
        if sum(raw) & 0xFF:
            return f"line {n} fails its checksum"
    if records[-1].upper() != ":00000001FF":
        return "no end-of-file record"
    return None


def firmware_chunks(firmware: bytes) -> list[bytes]:
    return [firmware[i : i + FIRMWARE_CHUNK] for i in range(0, len(firmware), FIRMWARE_CHUNK)]


def transfer_layout(nbytes: int) -> tuple[int, int, int]:
    """(full chunks, chunk size, last chunk bytes) for REG_CHUNKS..REG_LAST_CHUNK,
    as Resolutions programs them: 1990 bytes -> (0, 0, 1990), 8144 -> (1, 4096, 4048)."""
    full, last = divmod(nbytes, 4096)
    if last == 0 and full:
        full, last = full - 1, 4096
    return full, (4096 if full else 0), last


def decode_samples(raw: bytes) -> np.ndarray:
    """Big-endian int16 samples as floats."""
    return np.frombuffer(raw, ">i2").astype(float)


def centre_burst(samples: np.ndarray) -> int:
    return int(np.argmax(np.abs(samples - np.median(samples))))


def scan_offset(align_samples: np.ndarray) -> int:
    """Start offset for the scan so its centre burst lands on ZPD_INDEX:
    Resolutions sends (alignment centre burst index - 516) as registers A2:A3."""
    return centre_burst(align_samples) - ZPD_INDEX


def correct_scan(raw: bytes) -> np.ndarray:
    """One collected interferogram in detector counts, gain ranging undone."""
    samples = decode_samples(raw)
    if samples.size != SCAN_POINTS:
        raise ValueError(f"expected {SCAN_POINTS} samples, got {samples.size}")
    samples[GAIN_START : GAIN_START + GAIN_POINTS] *= GAIN_FACTOR
    return samples


def to_volts(counts: np.ndarray) -> np.ndarray:
    """Mean-subtracted interferogram in volts (what Resolutions stores)."""
    return VOLTS_PER_COUNT * (counts - counts.mean())
