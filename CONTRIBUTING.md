# Contributing to OpenFTIR

Thank you for your interest in OpenFTIR! This guide covers local setup, coding standards, and how to add a new spectrometer driver.

## Developer Setup

### Prerequisites
- Python 3.10 or later
- `pip` and `venv`

### Environment Setup
```bash
# Clone the repository
git clone https://github.com/ryanewilliams/openftir.git
cd openftir

# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install in editable mode with dev dependencies
pip install -e ".[dev]"
```

### Running Tests
```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run a specific test file
pytest tests/test_spectrum.py

# With coverage
pytest --cov=openftir
```

### GUI Testing
On **Linux headless systems** (CI/Docker), set the Qt platform:
```bash
export QT_QPA_PLATFORM=offscreen
# Install required system libraries:
# sudo apt-get install libegl1 libxkbcommon-x11-0 libxcb-cursor0 libgl1 libdbus-1-3
pytest
```

## Code Style

- **Format**: Follow PEP 8; use 4 spaces for indentation.
- **Type hints**: Encouraged for new code.
- **Docstrings**: Use Google-style docstrings.
- **Imports**: Group stdlib, third-party, local (isort convention).

Example:
```python
def collect_single_beam(
    self,
    params: CollectParams,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> Spectrum:
    """Collect a single-beam spectrum.

    Args:
        params: Collection parameters (scans, resolution, etc.).
        progress: Optional callback called with (scans_done, total_scans).
        cancel: Optional threading.Event; if set during collection, abort.

    Returns:
        A Spectrum with YUnit.SINGLE_BEAM and the requested wavenumber range.

    Raises:
        InstrumentError: If the instrument is not connected or collection fails.
        CollectionCancelled: If cancel is signalled during acquisition.
    """
```

## Clean-Room Requirement

**Do not submit code derived from:**
- Decompiling vendor software
- Reverse-engineering proprietary algorithms
- Directly copying closed-source implementations

**Do submit:**
- Clean-room implementations based on hardware datasheets
- Published FTIR theory and algorithms (peer-reviewed papers, textbooks)
- Community contributions and documentation

## Adding a New Spectrometer Driver

See **[`docs/adding-a-driver.md`](docs/adding-a-driver.md)** for a step-by-step guide. In brief:

1. **Implement** the `openftir.instruments.base.Instrument` interface
2. **Register** your driver via entry-point or the built-in registry
3. **Test** against the simulated instrument reference implementation
4. **Document** any special setup (USB drivers, calibration, etc.)

## Pull Request Process

1. **Fork** the repository and create a feature branch (`git checkout -b feature/my-feature`)
2. **Write tests** for new functionality
3. **Run tests locally** (`pytest`) and ensure they pass
4. **Commit** with clear, descriptive messages
5. **Push** and open a pull request with:
   - A clear description of changes
   - Reference to any related issues
   - Confirmation that you have signed off on the clean-room contribution requirement
6. **CI checks** must pass (tests, linting on Ubuntu, macOS, Windows)

## Reporting Issues

- **Bugs**: Include OS, Python version, steps to reproduce, and error output
- **Feature requests**: Explain the use case and desired behavior
- **Hardware support**: Specify the instrument model and provide relevant documentation links

## License

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).

---

Questions? Open an issue or ask in a discussion.
