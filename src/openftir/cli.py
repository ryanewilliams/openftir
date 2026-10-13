"""``openftir`` command-line interface.

Subcommands:

- ``drivers``                      list available instrument drivers.
- ``info``                         connect to a driver and print info/diagnostics.
- ``collect``                      collect a background + sample and save a ratioed spectrum.
- ``convert``                      convert a spectrum file between formats.
- ``info-file``                    print a summary of a spectrum file.
- ``process``                      apply processing steps and save the result.
- ``peaks``                        print a peak table for a spectrum file.
- ``library create|add``           manage a local spectral library.
- ``search``                       search a library for matches to a spectrum.
- ``probe``                        read-only USB/network discovery probe for the instrument.

Every subcommand reports user errors (bad files, bad arguments, instrument
errors) as a single ``error: ...`` line on stderr and a non-zero exit code,
without a Python traceback.
"""

from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path
from typing import Any, Sequence

from openftir.instruments import registry
from openftir.instruments.base import CollectionCancelled, CollectParams, Instrument, InstrumentError
from openftir.spectrum import Spectrum, YUnit

#: Fixed order in which `process` applies its steps, regardless of the order
#: the flags were given on the command line.
_PROCESS_ORDER = ("atr", "baseline", "smooth", "normalize", "crop")

_UNIT_CHOICES = {"absorbance": YUnit.ABSORBANCE, "transmittance": YUnit.TRANSMITTANCE}

_BASELINE_METHODS = ("als", "poly", "rubberband")

_EXPECTED_ERRORS = (InstrumentError, CollectionCancelled, ValueError, OSError, KeyError, RuntimeError)


class CliError(Exception):
    """Raised for user-facing errors; caught by ``main`` and printed cleanly."""


def _err(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)


def _parse_driver_opt(spec: str) -> tuple[str, Any]:
    key, sep, raw = spec.partition("=")
    if not sep:
        raise CliError(f"--driver-opt must be key=value, got {spec!r}")
    value: Any = raw
    if raw.lower() in ("true", "false"):
        value = raw.lower() == "true"
    else:
        try:
            value = int(raw)
        except ValueError:
            try:
                value = float(raw)
            except ValueError:
                value = raw
    return key, value


def _build_driver_kwargs(opts: Sequence[str] | None) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    for spec in opts or ():
        key, value = _parse_driver_opt(spec)
        kwargs[key] = value
    return kwargs


def _create_instrument(driver_id: str, opts: Sequence[str] | None) -> Instrument:
    kwargs = _build_driver_kwargs(opts)
    try:
        return registry.create(driver_id, **kwargs)
    except KeyError as exc:
        raise CliError(str(exc)) from None
    except TypeError as exc:
        raise CliError(f"bad --driver-opt for driver {driver_id!r}: {exc}") from None


def _progress_bar(done: int, total: int) -> None:
    width = 30
    filled = int(width * done / total) if total else width
    bar = "#" * filled + "-" * (width - filled)
    end = "\n" if done >= total else ""
    print(f"\r[{bar}] {done}/{total}", end=end, file=sys.stderr, flush=True)


# -- drivers ------------------------------------------------------------------


def cmd_drivers(args: argparse.Namespace) -> int:
    drivers = registry.available_drivers()
    if not drivers:
        print("no drivers available")
        return 0
    for driver_id, cls in sorted(drivers.items()):
        print(f"{driver_id}\t{cls.display_name}")
    return 0


# -- check-update -------------------------------------------------------------


def cmd_check_update(args: argparse.Namespace) -> int:
    from openftir import __version__, updater

    try:
        release = updater.fetch_latest_release()
    except updater.UpdateError as exc:
        _err(str(exc))
        return 1
    status = "update available" if updater.is_newer(release.version) else "up to date"
    print(f"this version: {__version__}\nlatest release: {release.version} ({status})")
    return 0


# -- info ---------------------------------------------------------------------


def cmd_info(args: argparse.Namespace) -> int:
    instrument = _create_instrument(args.driver, args.driver_opt)
    with instrument:
        info = instrument.connect()
        print(f"vendor:     {info.vendor}")
        print(f"model:      {info.model}")
        print(f"serial:     {info.serial}")
        print(f"firmware:   {info.firmware}")
        print(f"laser:      {info.laser_wavenumber} cm-1")
        print(f"resolutions: {', '.join(str(r) for r in info.resolutions)}")
        for key, value in info.extra.items():
            print(f"{key}:  {value}")
        diag = instrument.diagnostics()
        print(f"diagnostics ok: {diag.ok}")
        for key, value in diag.items.items():
            print(f"  {key}: {value}")
    return 0


# -- collect ------------------------------------------------------------------


def _load_one(path: str) -> Spectrum:
    from openftir import io as ioapi

    spectra = ioapi.load(path)
    if not spectra:
        raise CliError(f"{path}: contains no spectra")
    return spectra[0]


def cmd_collect(args: argparse.Namespace) -> int:
    from openftir import io as ioapi

    instrument = _create_instrument(args.driver, args.driver_opt)
    params = CollectParams(scans=args.scans, resolution=args.resolution)
    cancel = threading.Event()

    with instrument:
        if args.background:
            background = _load_one(args.background)
        else:
            original_sample = getattr(instrument, "sample", None)
            set_sample = getattr(instrument, "set_sample", None)
            if callable(set_sample):
                set_sample("none")
            print("collecting background...", file=sys.stderr)
            try:
                background = instrument.collect_single_beam(params, progress=_progress_bar, cancel=cancel)
            except KeyboardInterrupt:
                cancel.set()
                raise
            if callable(set_sample) and original_sample is not None:
                set_sample(original_sample)

        if not args.no_prompt:
            input("place sample and press Enter to continue...")

        print("collecting sample...", file=sys.stderr)
        try:
            sample = instrument.collect_single_beam(params, progress=_progress_bar, cancel=cancel)
        except KeyboardInterrupt:
            cancel.set()
            raise

    from openftir.processing import ratio

    to_unit = _UNIT_CHOICES[args.units]
    result = ratio(sample, background, to=to_unit)
    ioapi.save([result], args.output)
    print(f"wrote {args.output}")
    return 0


# -- convert ------------------------------------------------------------------


def cmd_convert(args: argparse.Namespace) -> int:
    from openftir import io as ioapi

    spectra = ioapi.load(args.input)
    if not spectra:
        raise CliError(f"{args.input}: contains no spectra")

    if args.index is not None:
        if not (0 <= args.index < len(spectra)):
            raise CliError(f"--index {args.index} out of range (file has {len(spectra)} spectra)")
        ioapi.save([spectra[args.index]], args.output)
        print(f"wrote {args.output}")
        return 0

    if len(spectra) == 1:
        ioapi.save(spectra, args.output)
        print(f"wrote {args.output}")
        return 0

    out_path = Path(args.output)
    for i, spectrum in enumerate(spectra):
        suffixed = out_path.with_name(f"{out_path.stem}_{i}{out_path.suffix}")
        ioapi.save([spectrum], str(suffixed))
        print(f"wrote {suffixed}")
    return 0


# -- info-file ----------------------------------------------------------------


def cmd_info_file(args: argparse.Namespace) -> int:
    from openftir import io as ioapi

    spectra = ioapi.load(args.file)
    if not spectra:
        raise CliError(f"{args.file}: contains no spectra")

    for i, s in enumerate(spectra):
        if len(spectra) > 1:
            print(f"--- spectrum {i} ---")
        print(f"title:   {s.title!r}")
        print(f"points:  {len(s)}")
        if len(s):
            print(f"x range: {s.x.min():g} .. {s.x.max():g} {s.x_unit.value}")
        print(f"y unit:  {s.y_unit.value}")
        print(f"acquired: {s.acquired}")
        if s.meta:
            print("meta:")
            for key, value in s.meta.items():
                print(f"  {key}: {value}")
    return 0


# -- process ------------------------------------------------------------------


def cmd_process(args: argparse.Namespace) -> int:
    from openftir import io as ioapi
    from openftir import processing as proc

    s = _load_one(args.input)

    for step in _PROCESS_ORDER:
        if step == "atr" and args.atr:
            if s.y_unit != YUnit.ABSORBANCE:
                s = proc.to_absorbance(s)
            s = proc.atr_correction(s)
        elif step == "baseline" and args.baseline:
            if args.baseline == "als":
                s = proc.baseline_als(s)
            elif args.baseline == "poly":
                s = proc.baseline_polynomial(s)
            else:
                s = proc.baseline_rubberband(s)
        elif step == "smooth" and args.smooth:
            s = proc.savgol(s, window=args.smooth)
        elif step == "normalize" and args.normalize:
            s = proc.normalize(s, method=args.normalize)
        elif step == "crop" and args.crop:
            s = proc.crop(s, args.crop[0], args.crop[1])

    ioapi.save([s], args.output)
    print(f"wrote {args.output}")
    return 0


# -- peaks --------------------------------------------------------------------


def cmd_peaks(args: argparse.Namespace) -> int:
    from openftir import processing as proc

    s = _load_one(args.file)
    peaks = proc.find_peaks(s, prominence=args.prominence)
    if not peaks:
        print("no peaks found")
        return 0

    print(f"{'position':>10}  {'height':>10}  {'width':>10}")
    for p in peaks:
        width = f"{p.width:.2f}" if p.width is not None else "-"
        print(f"{p.position:10.2f}  {p.height:10.4f}  {width:>10}")
    return 0


# -- library / search -----------------------------------------------------------


def cmd_library_create(args: argparse.Namespace) -> int:
    from openftir.processing import SpectralLibrary

    SpectralLibrary.create(args.directory)
    print(f"created library at {args.directory}")
    return 0


def cmd_library_add(args: argparse.Namespace) -> int:
    from openftir import io as ioapi
    from openftir.processing import SpectralLibrary

    library = SpectralLibrary.open(args.directory)
    added = 0
    for file in args.files:
        for spectrum in ioapi.load(file):
            name = library.add(spectrum)
            print(f"added {name!r} from {file}")
            added += 1
    print(f"added {added} spectra to {args.directory}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    from openftir.processing import SpectralLibrary, search

    query = _load_one(args.file)
    library = SpectralLibrary.open(args.library)
    matches = search(query, library, top=args.top)
    if not matches:
        print("no matches (library is empty)")
        return 0

    print(f"{'rank':>4}  {'score':>8}  name")
    for i, m in enumerate(matches, start=1):
        print(f"{i:4d}  {m.score:8.4f}  {m.name}")
    return 0


# -- probe ----------------------------------------------------------------------


def cmd_probe(args: argparse.Namespace) -> int:
    from openftir.probe import _print_summary, run_probe, write_report

    report = run_probe(scan_host=args.scan_host, ports=args.ports)
    out_path = write_report(report, args.output_dir)
    if not args.quiet:
        _print_summary(report, out_path)
    return 0


# -- argument parser ------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="openftir", description="OpenFTIR command-line interface")
    sub = parser.add_subparsers(dest="command", required=True)

    p_drivers = sub.add_parser("drivers", help="list available instrument drivers")
    p_drivers.set_defaults(func=cmd_drivers)

    p_update = sub.add_parser("check-update", help="check GitHub for a newer OpenFTIR release")
    p_update.set_defaults(func=cmd_check_update)

    p_info = sub.add_parser("info", help="connect to a driver and print info/diagnostics")
    p_info.add_argument("--driver", required=True)
    p_info.add_argument("--driver-opt", action="append", metavar="key=value")
    p_info.set_defaults(func=cmd_info)

    p_collect = sub.add_parser("collect", help="collect a background + sample and save a ratioed spectrum")
    p_collect.add_argument("--driver", required=True)
    p_collect.add_argument("--driver-opt", action="append", metavar="key=value")
    p_collect.add_argument("--scans", type=int, default=16)
    p_collect.add_argument("--resolution", type=float, default=4.0)
    bg_group = p_collect.add_mutually_exclusive_group()
    bg_group.add_argument("--background", metavar="FILE", help="load background from an existing spectrum file")
    bg_group.add_argument(
        "--collect-background", action="store_true", help="collect a fresh background from the instrument (default)"
    )
    p_collect.add_argument("--output", "-o", required=True)
    p_collect.add_argument(
        "--units", choices=sorted(_UNIT_CHOICES), default="absorbance", help="result y units (default: absorbance)"
    )
    p_collect.add_argument("--no-prompt", action="store_true", help="skip the 'place sample' prompt")
    p_collect.set_defaults(func=cmd_collect)

    p_convert = sub.add_parser("convert", help="convert a spectrum file between formats")
    p_convert.add_argument("input")
    p_convert.add_argument("output")
    p_convert.add_argument("--index", type=int, default=None, help="convert only this spectrum (0-based)")
    p_convert.set_defaults(func=cmd_convert)

    p_info_file = sub.add_parser("info-file", help="print a summary of a spectrum file")
    p_info_file.add_argument("file")
    p_info_file.set_defaults(func=cmd_info_file)

    p_process = sub.add_parser(
        "process",
        help="apply processing steps and save the result",
        epilog=(
            "Steps are applied in this fixed order regardless of flag order: "
            "atr -> baseline -> smooth -> normalize -> crop."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_process.add_argument("input")
    p_process.add_argument("--baseline", choices=_BASELINE_METHODS)
    p_process.add_argument("--smooth", type=int, metavar="WINDOW")
    p_process.add_argument("--normalize", metavar="METHOD")
    p_process.add_argument("--atr", action="store_true")
    p_process.add_argument("--crop", type=float, nargs=2, metavar=("X_MIN", "X_MAX"))
    p_process.add_argument("--output", "-o", required=True)
    p_process.set_defaults(func=cmd_process)

    p_peaks = sub.add_parser("peaks", help="print a peak table for a spectrum file")
    p_peaks.add_argument("file")
    p_peaks.add_argument("--prominence", type=float, default=None)
    p_peaks.set_defaults(func=cmd_peaks)

    p_library = sub.add_parser("library", help="manage a local spectral library")
    lib_sub = p_library.add_subparsers(dest="library_command", required=True)

    p_lib_create = lib_sub.add_parser("create", help="create a new (empty) library")
    p_lib_create.add_argument("directory")
    p_lib_create.set_defaults(func=cmd_library_create)

    p_lib_add = lib_sub.add_parser("add", help="add spectra to a library")
    p_lib_add.add_argument("directory")
    p_lib_add.add_argument("files", nargs="+")
    p_lib_add.set_defaults(func=cmd_library_add)

    p_search = sub.add_parser("search", help="search a library for matches to a spectrum")
    p_search.add_argument("file")
    p_search.add_argument("--library", required=True)
    p_search.add_argument("--top", type=int, default=5)
    p_search.set_defaults(func=cmd_search)

    p_probe = sub.add_parser(
        "probe",
        help="read-only USB/network discovery probe for the instrument",
        description=(
            "Read-only discovery probe for a USB/network-attached FTIR spectrometer "
            "(written for the Thermo Nicolet Summit / Summit X). Lists USB devices "
            "and network interfaces and writes a timestamped JSON report. Never "
            "sends any vendor-specific command to the instrument."
        ),
    )
    p_probe.add_argument(
        "--scan-host",
        metavar="IP",
        help=(
            "Optionally run a polite TCP connect-scan of a short list of common "
            "ports against this single host. Only attempts a TCP handshake; "
            "sends no payload."
        ),
    )
    p_probe.add_argument(
        "--ports",
        metavar="PORT",
        type=int,
        nargs="+",
        default=None,
        help="Override the default list of ports used with --scan-host.",
    )
    p_probe.add_argument(
        "--output-dir",
        metavar="DIR",
        default=".",
        help="Directory to write the timestamped JSON report into (default: cwd).",
    )
    p_probe.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the human-readable summary printed to stdout.",
    )
    p_probe.set_defaults(func=cmd_probe)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        return args.func(args)
    except CliError as exc:
        _err(str(exc))
        return 1
    except KeyboardInterrupt:
        _err("cancelled")
        return 1
    except _EXPECTED_ERRORS as exc:
        _err(str(exc))
        return 1
    except FileNotFoundError as exc:
        _err(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
