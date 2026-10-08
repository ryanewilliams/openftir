"""Command-line interface for :mod:`tools.usbcap`.

See ``tools/usbcap/README.md`` for an overview of each subcommand.
"""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING, Optional, Sequence

if TYPE_CHECKING:
    from .analysis import Capture

from .analysis import (
    DEFAULT_PID,
    DEFAULT_VID,
    diff_captures,
    extract,
    guess_arrays_report,
    load_capture,
    messages_report,
    summarize,
    timeline,
)


def _parse_hex_id(text: str) -> int:
    return int(text, 16)


def _parse_endpoint(text: str) -> int:
    """Parse an endpoint address like ``0x81`` or ``81`` (always hex)."""
    return int(text, 16)


def _add_vid_pid_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--vid", type=_parse_hex_id, default=DEFAULT_VID, help="USB vendor ID, hex (default: 0ebb)"
    )
    p.add_argument(
        "--pid", type=_parse_hex_id, default=DEFAULT_PID, help="USB product ID, hex (default: 000c)"
    )
    p.add_argument(
        "--no-filter",
        action="store_true",
        help="do not filter transfers to the matching VID:PID device; analyse the whole capture",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.usbcap",
        description="Offline analysis of USBPcap (Windows) / usbmon (Linux) USB captures.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_summary = sub.add_parser("summary", help="show devices, descriptors and per-endpoint stats")
    p_summary.add_argument("capture")
    _add_vid_pid_args(p_summary)

    p_timeline = sub.add_parser("timeline", help="one line per transfer, oldest first")
    p_timeline.add_argument("capture")
    p_timeline.add_argument("--limit", type=int, default=None)
    p_timeline.add_argument(
        "--endpoint", type=_parse_endpoint, default=None, help="filter to one endpoint address, e.g. 0x81"
    )
    p_timeline.add_argument("--min-bytes", type=int, default=0)
    _add_vid_pid_args(p_timeline)

    p_messages = sub.add_parser("messages", help="cluster distinct OUT command payloads")
    p_messages.add_argument("capture")
    p_messages.add_argument("--prefix-len", type=int, default=8)
    _add_vid_pid_args(p_messages)

    p_diff = sub.add_parser("diff", help="diff the OUT message sets of two captures")
    p_diff.add_argument("capture_a")
    p_diff.add_argument("capture_b")
    _add_vid_pid_args(p_diff)

    p_extract = sub.add_parser("extract", help="dump IN payloads from one endpoint to a file")
    p_extract.add_argument("capture")
    p_extract.add_argument("--endpoint", type=_parse_endpoint, required=True)
    p_extract.add_argument("--out", required=True)
    p_extract.add_argument("--gap-ms", type=float, default=None, help="split into bursts on IN gaps > this many ms")
    p_extract.add_argument("--as", dest="as_dtype", default=None, help="also dump a .npy of this dtype, e.g. float32le")
    _add_vid_pid_args(p_extract)

    p_guess = sub.add_parser("guess-arrays", help="scan large IN payloads for plausible numeric arrays")
    p_guess.add_argument("capture")
    p_guess.add_argument("--min-bytes", type=int, default=512)
    _add_vid_pid_args(p_guess)

    return parser


def _load(args) -> "Capture":
    vid = None if args.no_filter else args.vid
    pid = None if args.no_filter else args.pid
    return load_capture(args.capture, vid=vid, pid=pid)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "summary":
        cap = _load(args)
        print(summarize(cap))
    elif args.command == "timeline":
        cap = _load(args)
        print(timeline(cap, limit=args.limit, endpoint=args.endpoint, min_bytes=args.min_bytes))
    elif args.command == "messages":
        cap = _load(args)
        print(messages_report(cap, prefix_len=args.prefix_len))
    elif args.command == "diff":
        vid = None if args.no_filter else args.vid
        pid = None if args.no_filter else args.pid
        cap_a = load_capture(args.capture_a, vid=vid, pid=pid)
        cap_b = load_capture(args.capture_b, vid=vid, pid=pid)
        print(diff_captures(cap_a, cap_b))
    elif args.command == "extract":
        cap = _load(args)
        try:
            written = extract(
                cap, endpoint=args.endpoint, out_path=args.out, gap_ms=args.gap_ms, as_dtype=args.as_dtype
            )
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        for path in written:
            print(f"wrote {path}")
    elif args.command == "guess-arrays":
        cap = _load(args)
        print(guess_arrays_report(cap, min_bytes=args.min_bytes))
    else:  # pragma: no cover - argparse enforces a valid subcommand
        parser.error(f"unknown command {args.command!r}")
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
