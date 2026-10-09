# Adding a New Spectrometer Driver

This guide walks through implementing a new instrument driver for OpenFTIR. We'll use the simulated instrument as a reference.

## Overview

Every spectrometer driver inherits from `openftir.instruments.base.Instrument` and implements a set of abstract methods. The GUI and CLI only interact with this interface, so adding hardware support requires no UI changes.

## Step 1: Understand the Base Interface

Read `src/openftir/instruments/base.py`. Key classes and types:

- **`Instrument`**: Abstract base class your driver inherits from
  - `driver_id`: Short, unique identifier (e.g., `"simulated"`, `"summit"`)
  - `display_name`: User-facing name (e.g., `"Simulated Instrument"`)

- **`CollectParams`**: Configuration for a measurement
  - `scans`: Number of co-additions
  - `resolution`: Spectral resolution in cm⁻¹
  - `x_min`, `x_max`: Output wavenumber range
  - `apodization`, `zero_fill`, `phase_correction`: FFT parameters
  - `gain`: Detector gain setting (string, e.g., `"auto"`)
  - `extra`: Driver-specific options dict

- **`InstrumentInfo`**: Metadata about the connected instrument
  - `vendor`, `model`, `serial`, `firmware`
  - `laser_wavenumber`: For FFT axis calculation (HeNe ≈ 15798 cm⁻¹)
  - `resolutions`: Tuple of available spectral resolutions

- **`ProgressCallback`**: `Callable[[int, int], None]` — called with `(scans_done, scans_total)`

- **Exceptions**:
  - `InstrumentError`: General hardware error
  - `CollectionCancelled`: User cancelled measurement via `cancel` event

## Step 2: Implement Required Methods

Every driver must implement these abstract methods:

### `connect() -> InstrumentInfo`
Open the connection to the hardware and return device metadata.

```python
def connect(self) -> InstrumentInfo:
    """Establish connection to the instrument."""
    # Scan for connected devices, open USB/GPIB/serial
    # Perform initialization handshake
    self._connected = True
    return InstrumentInfo(
        vendor="Thermo Nicolet",
        model="Summit",
        serial=self._query_serial_number(),
        firmware=self._query_firmware_version(),
        laser_wavenumber=15798.0,
        resolutions=(0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0),
    )
```

### `disconnect() -> None`
Close the connection and clean up resources.

```python
def disconnect(self) -> None:
    """Close connection to the instrument."""
    if self._device:
        self._device.close()
    self._connected = False
```

### `connected` property
Return whether the instrument is currently connected.

```python
@property
def connected(self) -> bool:
    """True if instrument is connected and responsive."""
    return self._connected
```

### `collect_single_beam(params, progress, cancel) -> Spectrum`
**Required.** The main measurement method.

```python
def collect_single_beam(
    self,
    params: CollectParams,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> Spectrum:
    """Acquire and co-add scans, return single-beam spectrum.

    Args:
        params: CollectParams with scans, resolution, wavenumber range, etc.
        progress: Optional callback; called with (scans_done, total_scans).
        cancel: Optional threading.Event; if set, abort acquisition.

    Returns:
        Spectrum with YUnit.SINGLE_BEAM and x in the requested range.

    Raises:
        InstrumentError: If hardware error occurs.
        CollectionCancelled: If cancel event is set during acquisition.
    """
    if not self.connected:
        raise InstrumentError("Instrument not connected")

    # Configure the instrument
    self._set_resolution(params.resolution)
    self._set_gain(params.gain)
    self._set_apodization(params.apodization)

    # Acquire scans
    accumulated = np.zeros(self._num_points)
    for scan_num in range(params.scans):
        # Check for cancellation
        if cancel and cancel.is_set():
            raise CollectionCancelled()

        # Acquire one raw interferogram
        interferogram = self._acquire_raw_interferogram()

        # FFT: convert interferogram to single-beam spectrum
        single_beam = self._fft_to_wavenumbers(
            interferogram,
            laser_wn=15798.0,
            apodization=params.apodization,
            zero_fill=params.zero_fill,
            phase_correction=params.phase_correction,
        )

        # Co-add
        accumulated += single_beam

        # Report progress
        if progress:
            progress(scan_num + 1, params.scans)

    # Average and clip to requested range
    averaged = accumulated / params.scans
    wn = self._wavenumber_axis
    mask = (wn >= params.x_min) & (wn <= params.x_max)
    wn_out = wn[mask]
    intensity_out = averaged[mask]

    return Spectrum(
        x=wn_out,
        y=intensity_out,
        x_unit=XUnit.WAVENUMBER,
        y_unit=YUnit.SINGLE_BEAM,
        meta={
            "scans": params.scans,
            "resolution": params.resolution,
            "gain": params.gain,
        },
    )
```

## Step 3: Implement Optional Methods

### `collect_interferogram(params) -> Spectrum`
Return the raw, un-Fourier-transformed interferogram.

```python
def collect_interferogram(self, params: CollectParams) -> Spectrum:
    """Optional: return raw interferogram (no FFT)."""
    interferogram = self._acquire_raw_interferogram()
    return Spectrum(
        x=np.arange(len(interferogram)),  # or optical path difference
        y=interferogram,
        x_unit=XUnit.POINTS,
        y_unit=YUnit.INTERFEROGRAM,
    )
```

### `diagnostics() -> DiagnosticsReport`
Return health check results for display in the GUI.

```python
def diagnostics(self) -> DiagnosticsReport:
    """Optional: return device health status."""
    items = {}
    try:
        items["Laser"] = "ON" if self._check_laser() else "OFF"
        items["Detector"] = "OK" if self._detector_ok() else "ERROR"
        items["Chamber"] = f"{self._read_pressure_mtorr():.2e} mTorr"
    except Exception as e:
        return DiagnosticsReport(ok=False, items={"Error": str(e)})
    return DiagnosticsReport(ok=True, items=items)
```

## Step 4: Register the Driver

### Option A: Built-in Registry (in the OpenFTIR repo)

1. Add your driver module to `src/openftir/instruments/`:
   ```
   src/openftir/instruments/
   ├── base.py           # Interface
   ├── simulated.py      # Reference implementation
   ├── summit.py         # Nicolet Summit (your new driver)
   └── registry.py       # Driver registry
   ```

2. Create `src/openftir/instruments/summit.py`:
   ```python
   from openftir.instruments.base import Instrument, InstrumentInfo, CollectParams
   from openftir.spectrum import Spectrum, XUnit, YUnit

   class NicoletSummit(Instrument):
       driver_id = "summit"
       display_name = "Thermo Nicolet Summit"

       def __init__(self):
           self._device = None
           self._connected = False

       def connect(self) -> InstrumentInfo:
           # ... implementation
           pass

       # ... other methods
   ```

3. Register in `src/openftir/instruments/registry.py`:
   ```python
   from openftir.instruments.summit import NicoletSummit

   # In the registry initialization or a registration function
   def _register_builtin():
       from openftir.instruments.simulated import SimulatedInstrument
       from openftir.instruments.summit import NicoletSummit
       register(SimulatedInstrument)
       register(NicoletSummit)
   ```

### Option B: Third-Party Package (via Entry Point)

If you maintain a separate package (e.g., `openftir-summit`):

1. Implement your driver class in your package:
   ```python
   # mycompany/openftir_drivers/summit.py
   class NicoletSummit(Instrument):
       driver_id = "summit"
       display_name = "Thermo Nicolet Summit"
       # ... implementation
   ```

2. Add to your `pyproject.toml`:
   ```toml
   [project.entry-points."openftir.drivers"]
   summit = "mycompany.openftir_drivers.summit:NicoletSummit"
   ```

3. Users install it:
   ```bash
   pip install openftir openftir-summit
   ```

4. OpenFTIR automatically discovers and registers it at startup.

## Step 5: Test Your Driver

### Test Against the Simulated Instrument
```python
# tests/test_drivers_summit.py

from openftir.instruments.registry import get_instrument
from openftir.instruments.base import CollectParams

def test_summit_simulated():
    """Verify your driver works like the simulated instrument."""
    SimulatedInstr = get_instrument("simulated")
    YourInstr = get_instrument("summit")

    sim = SimulatedInstr()
    yours = YourInstr()

    with sim:
        params = CollectParams(scans=4, resolution=4.0)
        spec_sim = sim.collect_single_beam(params)

    with yours:
        spec_yours = yours.collect_single_beam(params)

    # Both should return Spectrum objects with matching shapes
    assert spec_sim.y.shape == spec_yours.y.shape
    assert spec_yours.y_unit == YUnit.SINGLE_BEAM
```

### Test with the Real Device
If you have hardware, write integration tests:
```python
@pytest.mark.skipif(not DEVICE_CONNECTED, reason="Device not connected")
def test_summit_real_device():
    instr = NicoletSummit()
    info = instr.connect()
    try:
        assert info.vendor == "Thermo Nicolet"
        params = CollectParams(scans=1)
        spec = instr.collect_single_beam(params)
        assert spec.x.size > 0
        assert spec.y.size > 0
    finally:
        instr.disconnect()
```

### Test Progress Reporting and Cancellation
```python
import threading
from unittest.mock import Mock

def test_summit_progress_callback():
    instr = YourInstrument()
    instr.connect()
    progress = Mock()
    try:
        params = CollectParams(scans=10)
        instr.collect_single_beam(params, progress=progress)
        # Verify progress was called ~10 times
        assert progress.call_count >= 5  # at least some updates
    finally:
        instr.disconnect()

def test_summit_cancellation():
    instr = YourInstrument()
    instr.connect()
    cancel_event = threading.Event()

    def abort_after_delay():
        import time
        time.sleep(1)
        cancel_event.set()

    threading.Thread(target=abort_after_delay, daemon=True).start()
    try:
        params = CollectParams(scans=100)
        with pytest.raises(CollectionCancelled):
            instr.collect_single_beam(params, cancel=cancel_event)
    finally:
        instr.disconnect()
```

## Step 6: Documentation

### Add a Section to docs/instrument-research.md
```markdown
## Thermo Nicolet Summit

- **Status**: In progress / Stable
- **Connection**: USB / GPIB
- **FFT Engine**: Built-in / via scipy.fftpack
- **Challenges**: [any blockers or notes]
- **References**: [datasheets, manuals, papers]
```

### Add Hardware Setup Notes to docs/install.md
```markdown
### Nicolet Summit USB Setup

1. **Windows**: [Driver installation steps if needed]
2. **macOS**: [Any special setup]
3. **Linux**: [udev rule and VID:PID from `lsusb`]
```

## Checklist

- [ ] Implement all abstract methods in `Instrument`
- [ ] Handle `progress` and `cancel` callbacks in `collect_single_beam`
- [ ] Return correct `Spectrum` with proper units and metadata
- [ ] Raise `InstrumentError` / `CollectionCancelled` appropriately
- [ ] Register driver in registry (builtin or entry-point)
- [ ] Write unit and integration tests
- [ ] Test with real hardware (if available)
- [ ] Document hardware setup and any quirks
- [ ] Update `docs/instrument-research.md` and `docs/install.md`

## Reference Implementation

Study `src/openftir/instruments/simulated.py` for a complete, working example.

## Driver status

| Driver | Status | Notes |
|---|---|---|
| `simulated` | Stable | No hardware; synthetic spectra for dev/tests. |
| `summit` | Real hardware driver, protocol reverse-engineered from USB captures | See `docs/summit-protocol.md`. Talks to the bench directly over USB (pyusb + bundled libusb) on macOS/Linux; on Windows the instrument must first be switched off Thermo's `NicUsbWdf` driver (see `docs/install.md`). Package: `src/openftir/instruments/summit/`. Tested against an in-memory fake transport (`summit.fake.FakeSummitTransport`) plus an opt-in validation suite (`tests/test_summit_validation.py`, gated on `OPENFTIR_SUMMIT_DATA`) that replays real captures of the user's own Summit X and reproduces OMNIC's saved spectrum to ~0.44 %T rms. Only `4` and `8` cm⁻¹ resolutions have a confirmed `peakPos`/`sdp`; others use an unverified extrapolation (flagged in `InstrumentInfo.extra["unverified_resolutions"]`). |

## Questions?

See [`CONTRIBUTING.md`](../CONTRIBUTING.md) for discussion channels and issue reporting.
