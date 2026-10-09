# Installation Guide

## For End Users

### macOS
1. Download the latest `OpenFTIR-macos.zip` from [Releases](https://github.com/ryanewilliams/openftir/releases)
2. Unzip and move `OpenFTIR.app` to `/Applications`
3. Launch from Spotlight (Cmd+Space, type "OpenFTIR") or Finder

**Note on Gatekeeper**: If you see "OpenFTIR cannot be opened because the developer cannot be verified", run:
```bash
xattr -dr com.apple.quarantine /Applications/OpenFTIR.app
```

### Windows
1. Download the latest `OpenFTIR-windows.zip` from [Releases](https://github.com/ryanewilliams/openftir/releases)
2. Unzip to a location (e.g., `C:\Program Files\OpenFTIR` or `%APPDATA%\OpenFTIR`)
3. Run `OpenFTIR\OpenFTIR.exe`

### Linux
1. Download the latest `OpenFTIR-linux.tar.gz` from [Releases](https://github.com/ryanewilliams/openftir/releases)
2. Extract: `tar xzf OpenFTIR-linux.tar.gz`
3. Run: `./OpenFTIR/OpenFTIR`

Optionally, create a symlink or desktop shortcut for easier access.

## For Developers

### Prerequisites
- Python 3.10 or later
- `pip` and `venv`
- Git

### Setup

```bash
# Clone the repository
git clone https://github.com/ryanewilliams/openftir.git
cd openftir

# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install in editable mode with GUI
pip install -e ".[gui]"

# Run the GUI
openftir-gui

# Or the CLI
openftir --help
```

### For Instrument Development
If you are adding a new instrument driver:

```bash
# Install with development dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run with the simulated instrument for testing
openftir-gui  # defaults to the simulated instrument
```

## USB / Instrument Access Setup

### Linux

1. **Install udev rules** (grants unprivileged access):
   ```bash
   # Copy the template rule file
   sudo cp packaging/linux/99-openftir.rules /etc/udev/rules.d/

   # Edit the rule file and add your instrument's VID:PID
   # (See the template comments for how to find it)
   sudo nano /etc/udev/rules.d/99-openftir.rules

   # Reload udev rules
   sudo udevadm control --reload-rules
   sudo udevadm trigger

   # Unplug and replug your instrument
   ```

2. **Ensure your user is in the plugdev group**:
   ```bash
   sudo usermod -aG plugdev $USER
   # Log out and log back in for the group change to take effect
   ```

3. **Verify access**:
   ```bash
   lsusb  # Should list your instrument
   openftir-gui  # Should connect without elevated privileges
   ```

### macOS

Most libusb-based instruments work without additional setup on macOS. If your instrument uses a proprietary driver:

1. Check if a driver is available from the manufacturer
2. Alternatively, run `openftir-gui` with elevated privileges as a workaround:
   ```bash
   sudo openftir-gui
   ```

### Windows

#### Nicolet Summit

On Windows the Summit is normally bound to Thermo's own kernel driver
(`NicUsbWdf`, installed with OMNIC Paradigm). That driver stays attached
whether or not Paradigm is running, so **closing Paradigm is not enough**:
OpenFTIR (libusb) cannot open the device until it uses the generic WinUSB
driver instead. macOS and Linux need none of this.

Switch to WinUSB (reversible):

1. Quit OMNIC Paradigm.
2. Download and run [Zadig](https://zadig.akeo.ie/).
3. **Options → List All Devices**, select **Nicolet Summit** and check the
   USB ID shows `0EBB 000C`.
4. Choose **WinUSB** as the target driver and click **Replace Driver**.
5. In OpenFTIR, pick the **summit** driver in the Collect panel and connect.

Switch back to Paradigm: Device Manager → right-click the Summit →
Properties → Driver → **Roll Back Driver** (or *Update driver → Browse →
Let me pick* and choose the Thermo Fisher Scientific driver). Paradigm will
not see the instrument while WinUSB is installed, and OpenFTIR will not see
it while Thermo's driver is installed.

#### Other instruments

Only replace a driver with WinUSB (via Zadig as above) if the OpenFTIR driver
for that instrument is libusb-based and the device shows up as "Unknown
Device" or is bound to a vendor driver.

## Fallback: Running Vendor Software in a Virtual Machine

While OpenFTIR is in development, you can run the vendor's software (e.g., OMNIC) in a Windows VM:

### VirtualBox (macOS / Linux / Windows)
```bash
# Download and install VirtualBox
# Create a Windows 10/11 VM with at least 2 vCPUs and 4 GB RAM
# Enable USB support in VM settings
# Install the vendor software inside the VM
# Attach your spectrometer via USB

# Guest Additions (for better performance)
# Install in the VM: VirtualBox Guest Additions ISO
```

### UTM (macOS / Apple Silicon)
```bash
# Download UTM from Mac App Store or utm.app
# Create a Windows ARM64 VM (or x86_64 with Rosetta)
# Enable USB pass-through
# Install the vendor software
```

### virt-manager (Linux / KVM)
```bash
# Install virt-manager and virt-viewer
sudo apt-get install virt-manager virt-viewer

# Create a Windows VM with USB pass-through
# Launch, install vendor software, attach spectrometer
```

## Troubleshooting

### "Device not found" / "Not connected"
- **Linux**: Verify udev rules are installed and your user is in `plugdev` group (see above)
- **macOS**: Try running with `sudo openftir-gui` to rule out permissions
- **Windows**: Check Device Manager and install WinUSB if needed (see above)
- **All**: Verify USB cable is firmly connected and the device is powered on

### GUI crashes on startup (Linux)
- Ensure Qt platform plugin is available: `QT_QPA_PLATFORM=offscreen openftir-gui`
- Install missing system libraries (see CI config for the full list)

### Import errors when running from source
- Ensure you've activated the virtual environment: `source .venv/bin/activate`
- Reinstall dependencies: `pip install -e ".[gui]"`

## Getting Help

- Check existing [issues](https://github.com/ryanewilliams/openftir/issues)
- Open a new issue with: OS, Python version, command output, and error messages
- See [CONTRIBUTING.md](../CONTRIBUTING.md) for the development discussion channels
