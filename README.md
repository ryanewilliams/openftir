# OpenFTIR

Open-source, cross-platform FTIR spectrometer control and spectral analysis software. A clean-room replacement for proprietary Windows-only vendor tools.

**Status**: Early development. Currently targeting Thermo Nicolet Summit; contributions welcome.

## Features

### Spectral I/O
- **OMNIC files**: read/write `.spa` and `.spg` (single-beam and absorbance/transmittance)
- **JCAMP-DX**: read `.jdx` / `.dx` files
- **CSV**: export processed spectra and import custom datasets

### Processing
- Baseline correction (polynomial, rubber band)
- Smoothing (Savitzky–Golay, FFT)
- ATR correction (automatic or manual)
- Peak picking and annotation
- Spectral subtraction
- Library search with user-built reference databases

### Instrument Control
- **Pluggable driver architecture**: add new spectrometers without UI changes
- **Simulated instrument** for testing and demos
- **Nicolet Summit driver**: in progress — see [`docs/instrument-research.md`](docs/instrument-research.md)
- Progress reporting and cancellation for long acquisitions
- Worker-thread isolation (GUI remains responsive)

### Graphical & Command-Line Interfaces
- `openftir-gui` — PySide6/Qt-based desktop application
- `openftir` — CLI with scripting support

## Installation

### From source (development / latest)
```bash
# Clone and enter the repo
git clone https://github.com/ryanewilliams/openftir.git
cd openftir

# Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows

# Install in editable mode with GUI dependencies
pip install -e ".[gui]"

# Run the GUI
openftir-gui

# Or the CLI
openftir --help
```

### Prebuilt binaries
Download the latest standalone application for your OS from the repository's GitHub Releases page, or from the artifacts of the latest "Build apps" run in the Actions tab.

### Building a standalone app yourself
On the OS you want to build for (PyInstaller cannot cross-compile), with Python 3.10+ installed:

```
python packaging/build.py
```

This creates `.build-venv/`, installs everything and writes `dist/OpenFTIR-windows.zip`, `dist/OpenFTIR-macos.zip` or `dist/OpenFTIR-linux.tar.gz`. The bundle contains the desktop app (`OpenFTIR`) and a console tool (`openftir-cli`).

## Quick Start

### Using the GUI
```bash
openftir-gui
```

### Using the CLI
```bash
# Collect a spectrum from the connected instrument
openftir collect --output myspectrum.spa

# Process a spectrum
openftir process baseline-correct myspectrum.spa --output processed.spa
openftir process smooth processed.spa --output smooth.spa

# Batch process
openftir process baseline-correct *.spa --output results/
```

## System Requirements

- **Python**: 3.10 or later
- **OS**: macOS (10.13+), Windows 10+, Linux (glibc 2.28+)
- **For GUI**: PySide6 (automatically installed with `pip install -e ".[gui]"`)

### USB/Instrument Access
- **Linux**: Requires udev rules for unprivileged access. See [Installation Guide](docs/install.md#linux-usb-access).
- **macOS**: libusb-based drivers typically work without additional drivers.
- **Windows**: May require WinUSB driver installation via [Zadig](https://zadig.akeo.ie/) if using libusb-based drivers (see [Installation Guide](docs/install.md#windows-usb-access)).

## Legal / Clean-Room Notice

OpenFTIR is **not affiliated with, endorsed by, or supported by Thermo Fisher Scientific**. 

- This project contains **no code derived from Thermo's proprietary software**.
- Thermo Nicolet, OMNIC, and related names are **trademarks of Thermo Fisher Scientific Inc.** and are used only to identify compatible hardware.
- Spectral reference libraries are **not included**. Users may build their own or import from compatible sources.
- **Contribution requirement**: Contributors must not submit code derived from decompiling or reverse-engineering vendor software. Clean-room FTIR knowledge, hardware datasheets, published standards, and community contributions are welcome.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for developer setup, contribution guidelines, and how to add support for a new spectrometer.

## License

MIT License — see [`LICENSE`](LICENSE).

## Acknowledgments

Built by the OpenFTIR community. Inspired by the need for open, cross-platform alternatives to closed vendor tools.