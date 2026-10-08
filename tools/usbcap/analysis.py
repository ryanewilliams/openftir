"""Higher-level analysis built on top of the parsed transfer list: summaries,
collapsed timelines, OUT-message clustering, two-capture diffing, IN-payload
extraction, and numeric-array guessing. Each public function returns plain
data/strings; :mod:`tools.usbcap.cli` wires them to argparse and stdout.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Optional

import numpy as np

from .descriptors import DeviceInfo, filter_transfers_by_vid_pid, find_devices
from .model import Transfer, TransferType, events_from_frames, pair_events
from .pcap_reader import read_frames

DEFAULT_VID = 0x0EBB
DEFAULT_PID = 0x000C


@dataclass
class Capture:
    transfers: list[Transfer]
    all_devices: dict[tuple[int, int], DeviceInfo]
    path: Path


def load_capture(path, vid: Optional[int] = None, pid: Optional[int] = None) -> Capture:
    """Read a capture file, pair submit/complete events into transfers, find
    devices by their descriptor responses, and (if ``vid``/``pid`` are given
    and a matching device is present) filter transfers down to that device."""
    frames = list(read_frames(path))
    events = events_from_frames(frames)
    transfers = pair_events(events)
    devices = find_devices(transfers)
    if vid is not None and pid is not None:
        transfers = filter_transfers_by_vid_pid(transfers, devices, vid, pid)
    return Capture(transfers=transfers, all_devices=devices, path=Path(path))


# --------------------------------------------------------------------------
# hex/ascii formatting helpers
# --------------------------------------------------------------------------


def hex_ascii(data: bytes, limit: int = 32) -> str:
    chunk = data[:limit]
    hex_part = chunk.hex(" ")
    ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
    suffix = "..." if len(data) > limit else ""
    return f"{hex_part}{suffix}  |{ascii_part}{suffix}|"


# --------------------------------------------------------------------------
# summary
# --------------------------------------------------------------------------


def summarize(cap: Capture) -> str:
    lines: list[str] = []
    lines.append(f"capture: {cap.path}")
    lines.append(f"transfers (after any VID:PID filter): {len(cap.transfers)}")

    if cap.transfers:
        t0 = min(tr.t for tr in cap.transfers)
        t1 = max(tr.t for tr in cap.transfers)
        lines.append(f"duration: {t1 - t0:.3f} s")
    lines.append("")

    lines.append("devices identified via GET_DESCRIPTOR responses:")
    if not cap.all_devices:
        lines.append("  (none -- no GET_DESCRIPTOR(DEVICE) response seen in this capture)")
    for (bus, device), info in sorted(cap.all_devices.items()):
        dd = info.device_descriptor
        vidpid = f"{dd.vendor_id:04x}:{dd.product_id:04x}" if dd else "????:????"
        lines.append(f"  bus {bus} device {device}: {vidpid}")
        if dd:
            lines.append(
                f"    bcdUSB=0x{dd.bcd_usb:04x} class={dd.device_class} "
                f"subclass={dd.device_subclass} proto={dd.device_protocol} "
                f"bMaxPacketSize0={dd.max_packet_size0} bcdDevice=0x{dd.bcd_device:04x} "
                f"numConfigurations={dd.num_configurations}"
            )
        cd = info.config_descriptor
        if cd:
            lines.append(
                f"    config value={cd.value} numInterfaces={cd.num_interfaces} "
                f"attributes=0x{cd.attributes:02x} maxPower={cd.max_power * 2}mA"
            )
            for iface in cd.interfaces:
                lines.append(
                    f"      interface {iface.number}.{iface.alternate}: "
                    f"class={iface.interface_class} subclass={iface.interface_subclass} "
                    f"proto={iface.interface_protocol}"
                )
                for ep in iface.endpoints:
                    lines.append(f"        {ep}")

    lines.append("")
    lines.append("per-endpoint activity:")
    stats: dict[tuple[int, int, int, bool], dict] = {}
    for tr in cap.transfers:
        key = (tr.bus, tr.device, tr.endpoint, tr.direction_in)
        s = stats.setdefault(
            key, {"count": 0, "bytes": 0, "types": set(), "bus": tr.bus, "device": tr.device}
        )
        s["count"] += 1
        s["bytes"] += len(tr.data)
        s["types"].add(TransferType.label(tr.transfer_type))

    for (bus, device, ep, direction_in), s in sorted(stats.items()):
        dirn = "IN" if direction_in else "OUT"
        types = ",".join(sorted(s["types"]))
        lines.append(
            f"  bus {bus} dev {device} EP{ep:02d} {dirn:3s} [{types}]: "
            f"{s['count']} transfers, {s['bytes']} bytes"
        )

    return "\n".join(lines)


# --------------------------------------------------------------------------
# timeline
# --------------------------------------------------------------------------


def _same_shape(a: Transfer, b: Transfer) -> bool:
    return (
        a.bus == b.bus
        and a.device == b.device
        and a.endpoint == b.endpoint
        and a.direction_in == b.direction_in
        and a.transfer_type == b.transfer_type
        and a.data == b.data
    )


def _format_transfer_line(tr: Transfer, t0: float, count: int = 1, avg_dt: Optional[float] = None) -> str:
    base = (
        f"{tr.t - t0:10.6f}s  EP{tr.endpoint:02d} {tr.direction_name:3s} "
        f"{tr.type_name:11s} len={len(tr.data):5d}  {hex_ascii(tr.data)}"
    )
    if count > 1:
        every = f"{avg_dt:.3f}" if avg_dt is not None else "?"
        return f"{base}   (x{count} identical, every {every} s)"
    return base


def timeline(
    cap: Capture,
    limit: Optional[int] = None,
    endpoint: Optional[int] = None,
    min_bytes: int = 0,
) -> str:
    """Render one line per transfer (oldest first), collapsing runs of 3+
    consecutive identical transfers into a single "xN identical, every T s"
    line.

    ``endpoint`` (if given) is an endpoint *address* byte (bit7 = IN), e.g.
    0x81 for EP1 IN -- matching the ``--endpoint`` CLI convention used by
    ``extract`` too.
    """
    filtered = [tr for tr in cap.transfers if len(tr.data) >= min_bytes]
    if endpoint is not None:
        want_in = bool(endpoint & 0x80)
        want_ep = endpoint & 0x7F
        filtered = [tr for tr in filtered if tr.endpoint == want_ep and tr.direction_in == want_in]

    if not filtered:
        return "(no transfers match the given filters)"

    t0 = filtered[0].t
    lines: list[str] = []
    i = 0
    n = len(filtered)
    while i < n:
        j = i + 1
        while j < n and _same_shape(filtered[j], filtered[i]):
            j += 1
        run = filtered[i:j]
        if len(run) >= 3:
            deltas = [run[k + 1].t - run[k].t for k in range(len(run) - 1)]
            avg_dt = mean(deltas) if deltas else None
            lines.append(_format_transfer_line(run[0], t0, count=len(run), avg_dt=avg_dt))
        else:
            for tr in run:
                lines.append(_format_transfer_line(tr, t0))
        i = j
        if limit is not None and len(lines) >= limit:
            break

    if limit is not None:
        lines = lines[:limit]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# messages
# --------------------------------------------------------------------------


def _is_out_message(tr: Transfer) -> bool:
    if tr.direction_in:
        return False
    if tr.transfer_type == TransferType.CONTROL:
        return len(tr.data) > 0
    return len(tr.data) > 0


def cluster_out_messages(transfers: list[Transfer], prefix_len: int = 8) -> dict:
    """Group OUT payloads by (first ``prefix_len`` bytes, total length).

    Returns ``{key: {"examples": [bytes, ...], "count": int, "responses":
    {(endpoint, length): count}}}``.
    """
    clusters: dict[tuple[bytes, int], dict] = {}
    out_msgs = [(i, tr) for i, tr in enumerate(transfers) if _is_out_message(tr)]
    for idx, (i, tr) in enumerate(out_msgs):
        key = (tr.data[:prefix_len], len(tr.data))
        c = clusters.setdefault(key, {"examples": [], "count": 0, "responses": {}})
        c["count"] += 1
        if tr.data not in c["examples"]:
            c["examples"].append(tr.data)

        # typical response: nearest following IN transfer before the next OUT
        next_out_t = out_msgs[idx + 1][1].t if idx + 1 < len(out_msgs) else float("inf")
        for later in transfers[i + 1 :]:
            if later.t >= next_out_t:
                break
            if later.direction_in and len(later.data) > 0:
                rkey = (later.endpoint_address, len(later.data))
                c["responses"][rkey] = c["responses"].get(rkey, 0) + 1
                break
    return clusters


def messages_report(cap: Capture, prefix_len: int = 8) -> str:
    clusters = cluster_out_messages(cap.transfers, prefix_len=prefix_len)
    if not clusters:
        return "(no OUT messages found)"

    lines = [f"{len(clusters)} distinct OUT message shape(s) (grouped by first {prefix_len} bytes + length):", ""]
    for key, c in sorted(clusters.items(), key=lambda kv: -kv[1]["count"]):
        prefix, length = key
        lines.append(f"* len={length:5d} count={c['count']:4d} prefix={prefix.hex(' ')}")
        for ex in c["examples"][:3]:
            lines.append(f"    example: {hex_ascii(ex, limit=max(32, prefix_len))}")
        if c["responses"]:
            top = sorted(c["responses"].items(), key=lambda kv: -kv[1])
            resp_str = ", ".join(f"EP{ep:#04x} len={ln} (x{n})" for (ep, ln), n in top[:3])
            lines.append(f"    typical response: {resp_str}")
        else:
            lines.append("    typical response: (none observed)")
        lines.append("")
    return "\n".join(lines).rstrip()


# --------------------------------------------------------------------------
# diff
# --------------------------------------------------------------------------


def _message_key(data: bytes) -> tuple[int, Optional[int]]:
    return (len(data), data[0] if data else None)


def diff_captures(cap_a: Capture, cap_b: Capture) -> str:
    out_a = [tr.data for tr in cap_a.transfers if _is_out_message(tr)]
    out_b = [tr.data for tr in cap_b.transfers if _is_out_message(tr)]

    by_key_a: dict[tuple, set[bytes]] = {}
    by_key_b: dict[tuple, set[bytes]] = {}
    for d in out_a:
        by_key_a.setdefault(_message_key(d), set()).add(d)
    for d in out_b:
        by_key_b.setdefault(_message_key(d), set()).add(d)

    keys = sorted(set(by_key_a) | set(by_key_b))
    lines: list[str] = []
    for key in keys:
        a_set = by_key_a.get(key, set())
        b_set = by_key_b.get(key, set())
        length, first_byte = key
        label = f"len={length} first_byte={first_byte!r}"
        if a_set and not b_set:
            lines.append(f"only in A ({label}): {len(a_set)} distinct payload(s)")
            for d in sorted(a_set)[:3]:
                lines.append(f"  A: {hex_ascii(d, limit=length)}")
        elif b_set and not a_set:
            lines.append(f"only in B ({label}): {len(b_set)} distinct payload(s)")
            for d in sorted(b_set)[:3]:
                lines.append(f"  B: {hex_ascii(d, limit=length)}")
        else:
            common = a_set & b_set
            only_a = a_set - common
            only_b = b_set - common
            if not only_a and not only_b:
                continue  # identical on both sides -- not interesting
            lines.append(f"differs ({label}): {len(only_a)} variant(s) only in A, {len(only_b)} only in B")
            if only_a and only_b:
                da = sorted(only_a)[0]
                db = sorted(only_b)[0]
                lines.append(f"  A: {hex_ascii(da, limit=length)}")
                lines.append(f"  B: {hex_ascii(db, limit=length)}")
                diff_offsets = [i for i in range(min(len(da), len(db))) if da[i] != db[i]]
                if diff_offsets:
                    lines.append(f"  differing byte offsets: {diff_offsets}")
                    for off in diff_offsets[:8]:
                        lines.append(f"    offset {off}: A=0x{da[off]:02x} B=0x{db[off]:02x}")
        lines.append("")

    if not lines:
        return "(no differences found between the two captures' OUT message sets)"
    return "\n".join(lines).rstrip()


# --------------------------------------------------------------------------
# extract
# --------------------------------------------------------------------------

_DTYPE_MAP = {
    "int16le": "<i2",
    "int16be": ">i2",
    "int32le": "<i4",
    "int32be": ">i4",
    "float32le": "<f4",
    "float32be": ">f4",
    "float64le": "<f8",
    "float64be": ">f8",
    "uint16le": "<u2",
    "uint32le": "<u4",
}


def _endpoint_transfers(cap: Capture, endpoint: int) -> list[Transfer]:
    want_in = bool(endpoint & 0x80)
    want_ep = endpoint & 0x7F
    return [
        tr
        for tr in cap.transfers
        if tr.endpoint == want_ep and tr.direction_in == want_in and len(tr.data) > 0
    ]


def split_bursts(transfers: list[Transfer], gap_s: float) -> list[list[Transfer]]:
    if not transfers:
        return []
    bursts: list[list[Transfer]] = [[transfers[0]]]
    for prev, cur in zip(transfers, transfers[1:]):
        if cur.t - prev.t > gap_s:
            bursts.append([])
        bursts[-1].append(cur)
    return bursts


def extract(
    cap: Capture,
    endpoint: int,
    out_path,
    gap_ms: Optional[float] = None,
    as_dtype: Optional[str] = None,
) -> list[Path]:
    """Concatenate payload bytes from ``endpoint`` into ``out_path`` (or, if
    ``gap_ms`` splits the data into multiple bursts, into
    ``<stem>_000<suffix>``, ``<stem>_001<suffix>``, ... files next to it).
    Returns the list of files written."""
    out_path = Path(out_path)
    trs = _endpoint_transfers(cap, endpoint)
    if not trs:
        raise ValueError(f"no data found on endpoint 0x{endpoint:02x}")

    if gap_ms is not None:
        groups = split_bursts(trs, gap_s=gap_ms / 1000.0)
    else:
        groups = [trs]

    written: list[Path] = []
    multi = len(groups) > 1
    for i, group in enumerate(groups):
        payload = b"".join(tr.data for tr in group)
        if multi:
            path = out_path.with_name(f"{out_path.stem}_{i:03d}{out_path.suffix}")
        else:
            path = out_path
        path.write_bytes(payload)
        written.append(path)

        if as_dtype:
            if as_dtype not in _DTYPE_MAP:
                raise ValueError(f"unknown --as dtype {as_dtype!r}; choose one of {sorted(_DTYPE_MAP)}")
            dt = np.dtype(_DTYPE_MAP[as_dtype])
            usable = len(payload) - (len(payload) % dt.itemsize)
            arr = np.frombuffer(payload[:usable], dtype=dt)
            npy_path = path.with_suffix(path.suffix + ".npy")
            np.save(npy_path, arr)
            written.append(npy_path)

    return written


# --------------------------------------------------------------------------
# guess-arrays
# --------------------------------------------------------------------------


@dataclass
class CentreBurst:
    index: int
    peak_value: float
    score: float


def detect_centre_burst(x: np.ndarray) -> Optional[CentreBurst]:
    """Heuristic: an FTIR interferogram has a single dominant, roughly
    symmetric, decaying oscillation centred on a sharp peak. Look for the
    sample furthest from the mean, then check that the envelope decays
    moving away from it on both sides."""
    n = x.size
    if n < 32 or not np.all(np.isfinite(x)):
        return None

    centred = x - np.mean(x)
    idx = int(np.argmax(np.abs(centred)))
    peak = float(np.abs(centred[idx]))
    if peak <= 0:
        return None

    half_window = max(8, n // 16)
    left_start = max(0, idx - half_window)
    right_end = min(n, idx + half_window + 1)
    if idx - left_start < 8 or right_end - idx - 1 < 8:
        return None

    # Does the envelope (|value|) fall off moving away from the peak on each
    # side? Compare the mean |amplitude| near the peak to further out.
    near = np.abs(centred[max(0, idx - 2) : idx + 3])
    far_left = np.abs(centred[left_start : max(left_start + 1, idx - 2)])
    far_right = np.abs(centred[min(right_end, idx + 3) : right_end])
    far = np.concatenate([far_left, far_right]) if (far_left.size or far_right.size) else np.array([0.0])

    near_mean = float(np.mean(near)) if near.size else 0.0
    far_mean = float(np.mean(far)) if far.size else 0.0
    if near_mean <= far_mean:
        return None

    # Also require the global peak to stand out from the overall RMS --
    # otherwise a noisy/flat array can spuriously "decay" from an arbitrary
    # local maximum.
    rms = float(np.sqrt(np.mean(centred**2))) or 1e-12
    prominence = peak / rms
    if prominence < 2.0:
        return None

    score = prominence * (near_mean / (far_mean + 1e-9))
    return CentreBurst(index=idx, peak_value=float(x[idx]), score=float(score))


@dataclass
class DecodeGuess:
    dtype: str
    length: int
    frac_finite: float
    value_range: float
    centre_burst: Optional[CentreBurst]
    plausible: bool


def guess_dtype(data: bytes, dtype_key: str) -> Optional[DecodeGuess]:
    dt = np.dtype(_DTYPE_MAP[dtype_key])
    usable = len(data) - (len(data) % dt.itemsize)
    if usable < dt.itemsize * 16:
        return None
    raw = np.frombuffer(data[:usable], dtype=dt)
    # Reinterpreting arbitrary bytes as float32/float64 can produce huge or
    # non-finite values (e.g. bit patterns that happen to be NaN/Inf); that's
    # an expected signal that this dtype is the wrong guess, not a bug, so
    # the overflow/invalid warnings from the cast and the range/RMS math
    # below are suppressed rather than surfaced.
    with np.errstate(over="ignore", invalid="ignore"):
        arr = raw.astype(np.float64)

        finite_mask = np.isfinite(arr)
        frac_finite = float(np.mean(finite_mask)) if arr.size else 0.0
        clean = arr[finite_mask]
        value_range = float(np.ptp(clean)) if clean.size else 0.0

        burst = detect_centre_burst(clean) if clean.size >= 32 else None
    plausible = frac_finite >= 0.99 and 0 < value_range < 1e12

    return DecodeGuess(
        dtype=dtype_key,
        length=int(raw.size),
        frac_finite=frac_finite,
        value_range=value_range,
        centre_burst=burst,
        plausible=plausible,
    )


def guess_arrays_for_payload(data: bytes) -> list[DecodeGuess]:
    guesses = []
    for key in _DTYPE_MAP:
        g = guess_dtype(data, key)
        if g is not None:
            guesses.append(g)
    # Most plausible first; among centre-burst detections, the highest
    # prominence score (not just "a burst was found somewhere") bubbles up,
    # since a wrong-dtype decode of noise can spuriously trip the heuristic
    # with a much weaker score than the true interferogram decoding.
    guesses.sort(
        key=lambda g: (
            g.centre_burst is not None,
            g.plausible,
            g.centre_burst.score if g.centre_burst else 0.0,
        ),
        reverse=True,
    )
    return guesses


def guess_arrays_report(cap: Capture, min_bytes: int = 512) -> str:
    """Scan large IN payloads (per-endpoint concatenation of all IN data)
    for plausible numeric array decodings."""
    by_ep: dict[int, bytearray] = {}
    for tr in cap.transfers:
        if tr.direction_in and tr.data:
            by_ep.setdefault(tr.endpoint_address, bytearray()).extend(tr.data)

    lines: list[str] = []
    found_any = False
    for ep, buf in sorted(by_ep.items()):
        if len(buf) < min_bytes:
            continue
        found_any = True
        lines.append(f"EP{ep:#04x}: {len(buf)} bytes of IN data")
        guesses = guess_arrays_for_payload(bytes(buf))
        if not guesses:
            lines.append("  no plausible fixed-width numeric decoding found")
        for g in guesses:
            tag = "PLAUSIBLE" if g.plausible else "unlikely"
            lines.append(
                f"  {g.dtype:10s} n={g.length:7d} finite={g.frac_finite:5.1%} "
                f"range={g.value_range:.6g}  [{tag}]"
            )
            if g.centre_burst:
                cb = g.centre_burst
                lines.append(
                    f"      centre-burst candidate at index {cb.index} "
                    f"(peak={cb.peak_value:.6g}, score={cb.score:.2f})"
                )
        lines.append("")

    if not found_any:
        return f"(no IN payload >= {min_bytes} bytes found to analyse)"
    return "\n".join(lines).rstrip()
