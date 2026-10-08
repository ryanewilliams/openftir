# Architecture

OpenFTIR is organized in layers, each with a clear responsibility. This design allows new spectrometers to be added without modifying the user interfaces.

## Layers

```
┌─────────────────────────────────────────────┐
│           CLI / GUI (User Interface)        │
│        (openftir.cli, openftir.gui)         │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────┴──────────────────────────┐
│        Instrument Drivers Layer             │
│ (openftir.instruments: base + registry)     │
│  ├─ Simulated (built-in, reference)        │
│  ├─ Nicolet Summit (in progress)           │
│  └─ [Third-party via entry-points]         │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────┴──────────────────────────┐
│  Processing Layer                           │
│      (openftir.processing)                  │
│  ├─ Baseline correction                    │
│  ├─ Smoothing                              │
│  ├─ ATR correction                         │
│  ├─ Peak picking                           │
│  └─ Spectral subtraction                   │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────┴──────────────────────────┐
│      I/O Layer                              │
│       (openftir.io)                         │
│  ├─ OMNIC (.spa, .spg)                     │
│  ├─ JCAMP-DX (.jdx, .dx)                   │
│  └─ CSV                                    │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────┴──────────────────────────┐
│     Core Data Model                         │
│      (openftir.spectrum)                    │
│        Spectrum class                       │
│  (x, y, units, metadata, history)          │
└─────────────────────────────────────────────┘
```

## Module Breakdown

### Core: `openftir.spectrum`
- **`Spectrum`**: Immutable data model for spectral data
  - `x`: wavenumber/wavelength array
  - `y`: intensity array
  - `x_unit`, `y_unit`: enums (WAVENUMBER, ABSORBANCE, etc.)
  - `meta`: free-form JSON-serializable metadata
  - `history`: processing steps applied (for traceability)
  - Methods: `copy()`, `derive()`, `sorted_ascending()`

### I/O: `openftir.io`
Modules for reading and writing common spectral formats:
- **`omnic.py`**: Thermo OMNIC `.spa` and `.spg` files
- **`jcamp.py`**: JCAMP-DX `.jdx` / `.dx` files
- **`csv.py`**: Delimited text export/import

Each module exposes:
```python
def read(path: str) -> Spectrum: ...
def write(spectrum: Spectrum, path: str) -> None: ...
```

### Processing: `openftir.processing`
Algorithms that transform spectra, returning new `Spectrum` objects:
- **Baseline correction**: polynomial fitting, rubber band
- **Smoothing**: Savitzky–Golay, FFT
- **ATR correction**: reflectance-to-absorbance
- **Peak detection**: local maxima, area integration
- **Subtraction**: overlay removal

Each function signature:
```python
def algorithm(spectrum: Spectrum, **params) -> Spectrum: ...
```

### Instruments: `openftir.instruments`

#### Base Interface
**`base.py`** defines the abstract `Instrument` class that all drivers implement:

```python
class Instrument(abc.ABC):
    driver_id: str          # e.g., "simulated", "summit"
    display_name: str       # e.g., "Simulated Instrument"

    def connect(self) -> InstrumentInfo: ...
    def disconnect(self) -> None: ...
    @property
    def connected(self) -> bool: ...

    def collect_single_beam(
        self,
        params: CollectParams,
        progress: ProgressCallback | None = None,
        cancel: threading.Event | None = None,
    ) -> Spectrum: ...

    def collect_interferogram(self, params: CollectParams) -> Spectrum: ...  # optional
    def diagnostics(self) -> DiagnosticsReport: ...  # optional
```

#### Registry
**`registry.py`** manages available drivers:
```python
def register(instrument_class: type[Instrument]) -> None: ...
def get_instrument(driver_id: str) -> type[Instrument]: ...
def list_available() -> list[str]: ...  # all registered driver IDs
```

#### Built-in Drivers
- **`simulated.py`**: Reference implementation, generates synthetic spectra (no hardware needed)
- **`summit.py`** (in progress): Thermo Nicolet Summit control via USB/GPIB

#### Third-party Drivers
External packages can register drivers via `openftir.drivers` entry-point group in their `pyproject.toml`:
```toml
[project.entry-points."openftir.drivers"]
my-instrument = "my_package.drivers:MyInstrument"
```

### CLI: `openftir.cli`
Command-line interface for scripting and batch processing:
```bash
openftir collect --driver simulated --output spectrum.spa
openftir process baseline-correct spectrum.spa --output corrected.spa
openftir process smooth corrected.spa --output smooth.spa
```

### GUI: `openftir.gui`
PySide6-based desktop application for interactive work:
- Live preview of spectra
- Real-time processing
- Instrument control and diagnostics
- Library search and annotation

## Threading Model

**Driver calls are synchronous but may block**. To keep the GUI responsive:

1. **Main thread** runs the Qt event loop
2. **Worker thread** executes `Instrument` methods:
   - `connect()`, `disconnect()`
   - `collect_single_beam()`, `collect_interferogram()`
   - `diagnostics()`

Long-running operations (e.g., `collect_single_beam`) must:
- Call `progress(scans_done, scans_total)` regularly (if provided)
- Check `cancel.is_set()` and raise `CollectionCancelled` if signalled

Example driver implementation:
```python
def collect_single_beam(
    self,
    params: CollectParams,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> Spectrum:
    data_points = []
    for i in range(params.scans):
        if cancel and cancel.is_set():
            raise CollectionCancelled()
        scan = self._acquire_raw_scan()
        data_points.append(scan)
        if progress:
            progress(i + 1, params.scans)
    return Spectrum(x=self.wavenumbers, y=np.mean(data_points, axis=0))
```

## Data Flow Example: Collect and Process

```
1. User clicks "Collect" in GUI
   │
2. GUI spawns worker thread, calls Instrument.collect_single_beam()
   │
3. Driver acquires raw interferogram, FFTs it, returns Spectrum
   │
4. Worker sends Spectrum back to main thread
   │
5. GUI displays raw spectrum, offers processing options
   │
6. User selects "Baseline Correct"
   │
7. GUI calls processing.baseline_correct(spectrum)
   │
8. Processing returns new Spectrum with updated y and history
   │
9. GUI updates plot, enables "Save" and "Export"
   │
10. User saves to .spa file via io.omnic.write()
```

## Extension Points

### Adding a New Driver
See [`docs/adding-a-driver.md`](adding-a-driver.md).

### Adding a New Processing Algorithm
1. Add function to `openftir/processing/<module>.py`
2. Expose in `__all__` and `openftir/processing/__init__.py`
3. Add GUI widget in `openftir/gui/processing_panel.py`
4. Write tests in `tests/test_processing_<module>.py`

### Adding a New File Format
1. Add module to `openftir/io/`
2. Implement `read(path)` and `write(spectrum, path)`
3. Update `openftir/gui/file_dialog.py` with MIME type and extension
4. Write tests in `tests/test_io_<format>.py`

## Dependency Notes

- **Core**: numpy, scipy (spectrum model, processing)
- **GUI**: PySide6, pyqtgraph (visualization, real-time plotting)
- **Instruments**: pyusb (optional, for USB-based drivers)
- **Dev**: pytest, pytest-qt (testing)

See `pyproject.toml` for version constraints.
