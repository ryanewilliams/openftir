# Changelog

Each release's section here becomes its notes on the Releases page and in the
app's "Update available" window.

## 0.2.4

- The Export button is bigger and sits right next to + Open, so it's easier to find.

## 0.2.3

- Sessions: Export → Session file (File → Save Session in Advanced mode) saves everything open in one file: spectra, names, colours, peak picks and the background. Open it later, or on another computer, to carry on where you left off.
- OpenFTIR saves your session automatically as you work. If it closes unexpectedly, it offers to restore everything next time.
- OpenFTIR always opens in Simple mode, even if Advanced mode was used last time.

## 0.2.2

- Mac: Check for Updates works. It failed with a certificate error because the app couldn't find a list of trusted certificates. Mac users on 0.2.0 or 0.2.1 need to download this version by hand once; updates are automatic after that.
- File → Export PDF Report and File → Save now use the selected spectrum. They used the first one in the list (usually the background), so the report complained it wasn't %Transmittance. The Rename and Colour buttons in Advanced mode had the same problem.
- Update checks still work when GitHub's limit for a shared network connection has been reached.

## 0.2.1

- Export PDF reports for several samples at once: Export → PDF reports for all samples…. Tick the samples, fix any names, pick a folder, and they all save together.
- Double-click a sample at the bottom of the window to rename it.

## 0.2.0

- OpenFTIR now updates itself: it checks for a new version on startup, or use Help → Check for Updates….
- Mac: typing works again. The app never became the active app, so text boxes (and the save and report dialogs) ignored the keyboard.
- Peak labels no longer sit on top of the spectrum or each other. A label that has to move gets a thin line back to its peak.
- PDF reports label every peak listed in the table (close peaks could lose their label).
- PDF reports show the date a spectrum was collected.

## 0.1.0

- First release.
