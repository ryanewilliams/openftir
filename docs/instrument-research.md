# Instrument Research: Thermo Nicolet Summit / Summit X

Research into how an open-source, cross-platform program could legitimately
control a Thermo Fisher Nicolet Summit / Summit X FTIR spectrometer, currently
driven only by Thermo's Windows-only OMNIC Paradigm software.

**Scope note:** this document and `tools/probe_instrument.py` were produced from
public manuals, spec sheets, and Thermo's own published documentation only. No
file belonging to Thermo's proprietary installer (which lives in the parent
`IR Software/` folder, outside this repo) was opened, unpacked, or inspected, and
no Thermo software or drivers were downloaded as part of this research.

## Confirmed on the user's instrument (2026-10-08)

Observed with `Get-PnpDevice` on the Windows PC that runs OMNIC Paradigm, with a
Nicolet Summit X attached (OS device metadata only; no Thermo software was inspected):

| Property | Value |
|---|---|
| USB VID:PID | **0EBB:000C** |
| Bus-reported description | "Nicolet Summit" |
| Windows device class | "FT-IR Spectrometer Devices" |
| Bound Windows driver (service) | **NicUsbWdf** (Thermo Fisher Scientific, v1.2.3.0) |
| Network interfaces added | none (plain USB device, no RNDIS/CDC-ECM) |

Consequences:

- **macOS / Linux:** no vendor driver exists, so an OpenFTIR driver can use
  libusb (pyusb) directly. On Linux install `packaging/linux/99-openftir.rules`.
- **Windows:** the device is claimed by Thermo's own kernel driver, so libusb
  cannot open it while that driver is bound. Options, in order of preference:
  1. UsbDk (filter driver; libusb's UsbDk backend can detach the device from
     NicUsbWdf on demand without uninstalling it, so Paradigm keeps working).
  2. Replace the driver with WinUSB via Zadig while using OpenFTIR (Paradigm
     will not see the instrument until the original driver is restored).
  Talking to NicUsbWdf's private interface is out of scope: it would require
  reverse engineering Thermo's software.
- Still unknown: interface/endpoint layout (next probe with libusb, ideally
  from the Mac/Linux side) and the protocol itself (Step 3 below).

## TL;DR

- **Your Summit X has no embedded computer.** It connects to a Windows PC over a
  **direct USB 2.0 cable**, and — per Thermo's own OMNIC Paradigm security guide —
  "OMNIC Paradigm software performs data analysis entirely on the companion PC."
  That means the real wire protocol you need is a **USB protocol between OMNIC
  Paradigm and the instrument**, not a network protocol. (Models with a built-in
  PC — Summit, Summit PRO, Summit OA — instead expose a documented set of network
  ports between the instrument and an external client; that protocol is
  irrelevant to your specific unit but is a useful side channel if you ever
  add support for those models.)
- **There is an official SDK** — the "OMNIC Paradigm SDK" (C#/.NET and Python,
  Windows-only, requires the OMNIC Paradigm software and apparently a purchased
  support contract) — but it automates the *existing Windows app*, it doesn't
  replace it, and it is not a documented wire protocol you could reimplement
  cross-platform. Worth requesting a quote for, as a reference/oracle, but not a
  path to a native Mac/Linux driver by itself.
- **No public prior art documents the Summit's USB protocol.** The closest
  precedent is `brukeropus` (MIT-licensed, Windows-only), which automates a
  *competitor's* instrument via its software's DDE interface rather than a raw
  device protocol — useful as a legal/structural precedent, not as source code.
- **Lowest-risk path:** you own the hardware, so observing *your own instrument's*
  USB descriptors and the raw bytes it exchanges with OMNIC Paradigm is clean-room,
  interoperability-motivated reverse engineering with strong footing under US
  DMCA §1201(f) and (for an EU user) Directive 2009/24/EC Art. 6. In the EU an
  EULA cannot override Art. 6; in the US, however, an EULA anti-reverse-engineering
  clause may still be enforceable as a *contract* (Bowers v. Baystate), so a US
  user who captures traffic while running Paradigm should get institutional
  sign-off first (not legal advice; see
  [Question 4](#4-legal-landscape-not-legal-advice)). Start with descriptor
  enumeration (zero risk, no vendor software involved), then move to USB traffic
  capture only once you're ready to commit time to it.

## Recommended next steps

1. Run `tools/probe_instrument.py` on the Mac/Windows/Linux machine you'll use for
   development, both **with the Summit X unplugged** and **with it plugged in and
   powered on**, and diff the two JSON reports. This is step 1 of the plan in
   [Question 5](#5-protocol-discovery-plan) and needs no Thermo software at all.
2. Repeat the probe once more with **OMNIC Paradigm running and connected** to see
   if anything changes (new USB configuration, new interfaces claimed, extra
   descriptors). Still read-only, still no vendor commands sent by our script.
3. If after that you want to see the actual bytes (not just descriptors), install
   Wireshark + USBPcap (Windows) or use `usbmon`/`tshark` (Linux) and capture a
   single background scan and a single sample scan triggered from OMNIC Paradigm.
   See the capture checklist in [Question 5](#5-protocol-discovery-plan).
4. In parallel, it costs nothing to email Thermo sales/support and ask for a
   quote on the OMNIC Paradigm SDK — even if you end up not using it for the
   open driver, it is a legitimate, vendor-sanctioned way to double check your
   own interpretation of a capture (e.g. "does calling `StartSampleMeasurement`
   produce the same USB traffic I captured by hand?").

---

## 1. Physical connectivity

### Summary across the Summit family

| Model | Connection to PC | Embedded computer | Touchscreen |
|---|---|---|---|
| **Summit LITE** | USB cable to external PC | None | None |
| **Summit X** (your unit) | USB cable to external PC | None | None |
| **Summit** / **Summit PRO** / **Summit OA** | Ethernet or Wi-Fi (also able to host a client locally) | Yes — integrated Windows 10/11 PC | Optional, onboard |

Thermo's own comparison table lists the "User interface" row for LITE and X as
**"USB cable"**, and for OA as **"Integrated computer"** — i.e. your Summit X has
no network stack of its own; all control happens over the USB link.
[Nicolet Summit family brochure](https://documents.thermofisher.com/TFS-Assets/MSD/brochures/nicolet-summit-family-brochure-bc53614.pdf)

Thermo's OMNIC Paradigm **Product Security Information Guide** describes two
deployment architectures that confirm this split (confidence: high — this is
Thermo's own architecture diagram text, extracted via `pdftotext`):

> "Separate companion PC hardware connectivity ... The instrument connects to the
> companion PC through a direct USB 2.0 connection where OMNIC Paradigm software
> performs data analysis entirely on the companion PC."

> "Onboard PC hardware connectivity ... An external companion PC can be connected
> to an instrument with an embedded PC through an Ethernet connection."

— [OMNIC Paradigm Product Security Information Guide (PDF)](https://documents.thermofisher.com/TFS-Assets/CAD/Product-Guides/PSIG_OMNIC_ProductSecurity_TP_FINAL.pdf)

This is the single most important architectural fact for your unit: **on the
Summit X, the RabbitMQ/AMQP message bus, the "History WebAPI", and the "Workflow
Engine" Web API that Thermo's docs describe are all running locally on the
companion PC, talking to each other over localhost** — they are not what crosses
the USB cable to the instrument. (On the networked models, those same services
*do* cross the wire, over Ethernet/Wi-Fi, which is why Thermo's user guide
documents firewall ports for them — see the table in
[Question 2](#2-official-sdkautomation-api).) Don't be misled into thinking the
Summit X exposes a Web API over USB; the evidence says it doesn't.

### USB vendor/product ID

Confidence: **medium**. Two independent public USB-ID databases agree on one
entry for Thermo's USB vendor ID:

- VID **0x0EBB**, listed as "Thermo Nicolet Corp." / "Thermo Fisher Scientific",
  with product ID **0x0002** described as "FT-IR Spectrometer".
  - [devicehunt.com: USB\VID_0EBB](https://devicehunt.com/view/type/usb/vendor/0EBB)
  - [the-sz.com USB ID database](https://the-sz.com/products/usbid/index.php?n=T*)

This PID is generic ("FT-IR Spectrometer") and was almost certainly registered
for an older instrument family (Avatar/Nexus/iS-series are far older than
Summit); there is **no public record of the Summit/Summit X's own VID:PID pair**.
A vendor claiming 0x0EC7 appeared in a search result but could not be verified
against the USB-ID database (that VID resolves to an unrelated company there) —
treat it as unconfirmed. `tools/probe_instrument.py` reports whatever VID:PID
your actual unit presents; **this is the fact to trust once you run it**, not
anything below.

Also worth checking once you have a capture: Thermo's own internal path
reference `IR Instrument Engine\Instrument.IR.Engine.Service.exe` (named in the
Instrument Discovery section of the OMNIC Paradigm user guide, see Q2) hints the
instrument-facing software component is called the "IR Instrument Engine" —
a good search term for any future public documentation.

### No RNDIS/CDC-ECM evidence for the Summit X specifically

I looked for, but found no public evidence that the Summit X's USB link
enumerates as a USB network adapter (RNDIS/CDC-ECM) the way some embedded
instruments do. Thermo's security guide language ("direct USB 2.0 connection...
data analysis entirely on the companion PC") reads more like a vendor-specific
USB device class (bulk/interrupt transfers carrying a proprietary protocol) than
a virtual-Ethernet link — but this is an inference, not a documented fact.
**`tools/probe_instrument.py`'s network-interface listing step exists
specifically to settle this empirically**: if plugging in the Summit X makes a
new network interface appear (even one with no routable traffic), that changes
the whole discovery strategy toward "sniff the virtual Ethernet link" instead of
"capture raw USB bulk transfers."

---

## 2. Official SDK / automation API / protocol documentation

### OMNIC Paradigm SDK — real, but automates the app, not the wire

Thermo publishes a genuine **"OMNIC Paradigm SDK"** with a full API reference,
and getting-started guides for **C#, Python, and VB.NET**. Confidence: **high**
(primary Thermo documentation, confirmed via direct fetch).

- [OMNIC Paradigm SDK Guide](https://knowledge1.thermofisher.com/Molecular_Spectroscopy/Molecular_Spectroscopy_Software/OMNIC_Family/OMNIC_Paradigm_Software/OMNIC_Paradigm_SDK_Guide)
- [SDK API Reference](https://knowledge1.thermofisher.com/Molecular_Spectroscopy/Molecular_Spectroscopy_Software/OMNIC_Family/OMNIC_Paradigm_Software/OMNIC_Paradigm_SDK_Guide/OMNIC_Paradigm_SDK_API_Reference)
- [Paradigm SDK – Getting Started](https://knowledge1.thermofisher.com/Molecular_Spectroscopy/Molecular_Spectroscopy_Software/OMNIC_Family/OMNIC_Paradigm_Software/OMNIC_Paradigm_SDK_Guide/Paradigm_SDK_-_Getting_Started)
- [SDK Theory of Operation](https://knowledge1.thermofisher.com/Molecular_Spectroscopy/Molecular_Spectroscopy_Software/OMNIC_Family/OMNIC_Paradigm_Software/OMNIC_Paradigm_SDK_Guide/SDK_Theory_of_Operation)
- [SDK Release Notes](https://knowledge1.thermofisher.com/Molecular_Spectroscopy/Molecular_Spectroscopy_Software/OMNIC_Family/OMNIC_Paradigm_Release_Notes/OMNIC_Paradigm_Software_SDK_Release_Notes) (versions 2.2–2.5 found, all Windows 10/11 64-bit only)

What it exposes, via an `IRemoteParadigm` interface (method names as documented):
`StartSampleMeasurement`, `StartBackgroundMeasurement`, `GetInterferograms`,
`AlignSpectrometer`, `CalibrateLaser`, `CancelCollectInProgress`,
`BaselineCorrect`, `DoAdvancedATRCorrection`, `InitiateWorkflow`,
`DeployPackage`, `GetStatus`, `OpenConnection`/`CloseConnection`, plus event
handlers for alignment/calibration/measurement progress.

Architecturally, the SDK's own theory-of-operation doc describes its DLLs as "a
replacement for the Paradigm client" that talk to "Paradigm backends" — i.e. the
SDK **requires the OMNIC Paradigm backend/services to be installed and running**
on the same Windows PC; it does not independently talk to the instrument. It is
Windows-only (`.msi` installer, Visual Studio 2019+, `.NET Framework`,
`C:\Program Files\Thermo Scientific\SDK Client`).

**How to get it:** not published for self-service download. One search result
suggested an SDK support contract (and even a Chromeleon-support prerequisite),
but that may be conflating it with Thermo's unrelated Chromeleon (chromatography)
SDK — treat that detail as **unconfirmed**. The reliable path is to contact
Thermo Fisher sales/support and request a quote/access, as you would for any
commercial add-on.
[Instruments, Software, and Firmware licensing page](https://www.thermofisher.com/us/en/home/about-us/partnering-licensing/license-our-technology/intellectual-property-licensing/instruments-software-licensing.html)

**Why this doesn't solve your problem directly:** the SDK is a Windows automation
layer over the *existing* OMNIC Paradigm installation — useful as an oracle to
validate your own understanding of instrument behavior, and as a legitimate,
vendor-blessed way to drive the Summit X from a script, but it does nothing for
Mac/Linux and does not document the USB wire protocol.

### Legacy OMNIC Macros\Basic / DDE (older instruments, still documents a pattern)

Thermo's classic OMNIC software (pre-Paradigm, used on Avatar/Nexus/iS-series)
exposed automation via **OMNIC Macros\Basic** (VB6/VBA) and **DDE** (Dynamic Data
Exchange), documented in a public technical note. Confidence: **high** (primary
documents, mirrored by a university FTIR facility).

- [TN 9365: OMNIC Software Macro Programming and Dynamic Data Exchange](https://knowledge1.thermofisher.com/Molecular_Spectroscopy/Fourier_Transform_FT_Infrared_IR_and_Near_Infrared_NIR/FT-IR_Application_Notes/TN_9365_OMNIC_Software_Macro_Programming_and_Dynamic_Data_Exchange)
- [OMNIC DDE Commands and Parameters (PDF, Caltech FTIR facility mirror)](https://mmrc.caltech.edu/FTIR/Nicolet/Nicolet%20manuals/OMNIC%20DDE%20Commands%20&%20Parameters.pdf)
- [Macros\Pro User's Guide (PDF, same mirror)](https://mmrc.caltech.edu/FTIR/Nicolet/Nicolet%20manuals/MacrosPro%20User's%20Guide.pdf)

This is not applicable to Summit/Paradigm (DDE talks to the OMNIC *process*, not
the instrument directly, and OMNIC Paradigm is a different codebase from classic
OMNIC), but it establishes that Thermo has, in the past, published Windows
IPC-level automation docs for its FTIR line — background, not a solution.

### No OPC-UA / SiLA2 support found

I found no evidence Thermo implements SiLA2 or OPC-UA (the two open lab-automation
standards) for any Nicolet FTIR. SiLA2 (gRPC/HTTP2 + mDNS discovery) is the more
relevant open standard for this domain if you ever wanted to design the *open*
protocol your own driver exposes to other lab software, but it's not something
Thermo's instrument speaks today.
[SiLA 2 standard](https://sila-standard.com/faq/), [SiLA2 vs OPC-UA](https://evo-byte.com/sila-2-opc-ua-and-the-modern-lab-stack/)

### Reference: network ports Thermo documents for the *networked* Summit models

Not your instrument's wire protocol, but useful if OpenFTIR ever supports
Summit/Summit PRO/Summit OA, and useful context for understanding what Paradigm's
backend services are (the same services that, per Q1, run purely over localhost
on your Summit X setup):

| Port | Service (Thermo's term) | Protocol |
|---|---|---|
| UDP 50123 | "Thermo Scientific Instrument Discovery" | UDP broadcast/discovery |
| TCP 9004 | "Web API Server" | HTTPS |
| TCP 9002 | "History WebAPI" | HTTPS |
| TCP 9090 | "Workflow Engine" | HTTP(S) |
| TCP 4369, 5671, 5672, 15672 | RabbitMQ (EPMD, AMQP, AMQPS, management console) | AMQP |
| UDP 137, TCP 139, TCP 445 | NetBIOS/SMB (domain auth) | SMB |

Source: Thermo's own OMNIC Paradigm User Guide network-requirements section and
the Product Security Information Guide (both extracted via `pdftotext` from the
official PDFs).
[OMNIC Paradigm User Guide (PDF)](https://documents.thermofisher.com/TFS-Assets/MSD/Product-Information/OMNIC-Paradigm-UG.pdf),
[Connect to a Spectrometer](https://knowledge1.thermofisher.com/Molecular_Spectroscopy/Molecular_Spectroscopy_Software/OMNIC_Family/OMNIC_Paradigm_Software/OMNIC_Paradigm_Operator_Manuals/Latest_OMNIC_Paradigm_User_Guide/Connect_to_a_Spectrometer),
[Product Security Information Guide (PDF)](https://documents.thermofisher.com/TFS-Assets/CAD/Product-Guides/PSIG_OMNIC_ProductSecurity_TP_FINAL.pdf)

Interesting detail if you ever capture traffic to a networked Summit: the
"Web API Server" and "History WebAPI" are both documented as running over
**HTTPS**, which — unlike a raw binary USB protocol — is very amenable to
passive observation with a plain HTTPS proxy once you have a cert in place,
and may even be partially self-documenting (Swagger/OpenAPI is common for
this kind of ASP.NET service, though this is speculation, not something I
verified against a live instrument).

---

## 3. Existing open-source / academic projects

### Projects that talk to a live Thermo/Nicolet instrument

**None found.** No public open-source project documents a live wire protocol
for any Nicolet FTIR (Avatar, Nexus, iS5/iS10/iS50, or Summit). This appears to
be genuinely unexplored territory in the open-source world.

### Closest precedent: `brukeropus` (different vendor, same shape of problem)

- **[brukeropus](https://github.com/joshduran/brukeropus)** — MIT license.
  Communicates with a running **Bruker OPUS** application (a FTIR competitor to
  OMNIC) via its **DDE interface**, and separately parses OPUS's binary file
  format. Windows-only for the live-control half (DDE is Windows IPC); the
  file-reading half is pure Python/NumPy and should be cross-platform.
  Useful precedent: an individual built and published, under a permissive
  license, a tool that automates a vendor FTIR app that was never designed for
  third-party control — without any indication of having decompiled Bruker's
  software (it rides on DDE, which OPUS itself exposes).

### Projects that read OMNIC file formats (not live control, but relevant I/O prior art)

- **[spectrochempy-omnic](https://github.com/spectrochempy/spectrochempy-omnic)**
  — standalone reader for OMNIC `.spa`/`.spg`/`.srs` files. License:
  **CeCILL-B** (French free-software license, GPL-compatible).
- **[SpectroChemPy](https://www.spectrochempy.fr/)** itself — broader spectroscopy
  processing framework with `read_omnic`, `read_spa`, `read_opus`, etc. (check
  its own repo for the exact license; the `-omnic` reader above is the relevant
  piece).
- **[spa-on-python](https://github.com/lerkoah/spa-on-python)** — small script to
  read Nicolet/Thermo OMNIC `.spa` files in Python. License: check repo (no
  explicit license found during this research — verify before depending on it).
- **[brukeropusreader](https://github.com/qedsoftware/brukeropusreader)** (and
  forks, e.g. the SpectroChemPy and tommyhlw mirrors) — reads Bruker OPUS binary
  files. Licenses vary by fork: **LGPLv3** (qedsoftware original) vs. **AGPL-3.0**
  (spectrochempy fork) — if OpenFTIR ever adds OPUS file support, use the
  LGPLv3 original, not the AGPL fork, to avoid AGPL's stronger copyleft.

None of these give you instrument *control*, but they're relevant if OpenFTIR
wants to import legacy `.spa`/`.spg` files collected under OMNIC — which
`openftir.io.omnic` already appears scoped to do (see
[`docs/architecture.md`](architecture.md)).

---

## 4. Legal landscape (not legal advice)

**This section is informational background, not legal advice.** If the project
grows (outside contributors, commercial use, distribution of a driver that ships
with captured protocol details), get a lawyer familiar with IP and software
licensing in your jurisdiction before relying on any of this.

### US: DMCA §1201(f) and the Sega/Sony line

- **17 U.S.C. §1201(f)(1)** creates a specific exception to DMCA anti-circumvention
  liability for reverse engineering **"identifying and analyzing those elements
  of the program that are necessary to achieve interoperability of an
  independently created computer program with other programs."** This maps
  closely onto what you'd be doing: figuring out the Summit X's USB protocol
  solely to make an independent program (OpenFTIR) interoperate with it.
- **[Sega Enterprises Ltd. v. Accolade, Inc.](https://en.wikipedia.org/wiki/Sega_v._Accolade)**
  (9th Cir. 1992) and **Sony Computer Entertainment, Inc. v. Connectix Corp.**
  (9th Cir. 2000) both held that intermediate copying during reverse engineering,
  done solely to extract unprotected interface/interoperability information, is
  **fair use** under copyright law — the legislative history of §1201 explicitly
  says Congress intended to preserve this line of cases.
- Caveat: §1201(f) is about circumventing *technical protection measures*
  (encryption/access controls) on copyrighted *software*. Observing your own
  hardware's USB bus traffic and descriptors generally isn't "circumventing"
  anything at all (there's no access control to bypass to read what your own
  OS's USB stack already receives) — which if anything puts plain USB-traffic
  observation on *even safer* ground than the statute's own carve-out, since you
  likely don't need the exception to begin with.

### US: EULA anti-reverse-engineering clauses vs. this exception

- **[Bowers v. Baystate Technologies](https://en.wikipedia.org/wiki/Bowers_v._Baystate_Technologies,_Inc.)**
  (Fed. Cir. 2003) held that a **shrinkwrap/clickwrap contract term banning
  reverse engineering is enforceable**, as a matter of state contract law, even
  where copyright law (via fair use / §1201(f)) would otherwise permit the
  reverse engineering. This is the single biggest legal wrinkle for a *software*
  EULA like OMNIC Paradigm's or classic OMNIC's — Thermo's own license agreement
  text (found via a Caltech-hosted mirror of a Nicolet software license) does
  contain a standard "shall not reverse compile, decrypt, extract, disassemble,
  or otherwise reverse engineer" clause, and under *Bowers*-line reasoning that
  clause could be contractually binding on whoever clicked "I agree" to install
  that software, independent of copyright law.
  [Nicolet Software Licensing Agreement (PDF, Caltech mirror)](https://mmrc.caltech.edu/FTIR/restricted/Nicolet%20Win%207%20%20Software/Nicolet%20Win%207%20%20Software/Software%20Licensing%20Agreement.pdf)
- This is exactly why **observing the instrument's own USB wire traffic — without
  ever decompiling, disassembling, or even running OMNIC Paradigm's binaries in
  a way that requires agreeing to its EULA for that act — is the lower-risk
  approach.** A hardware protocol on a wire you own is not "the software"; USB
  descriptors are read directly from your own operating system's USB stack, not
  extracted from Thermo's code. If you do capture traffic *while OMNIC Paradigm
  happens to be running*, you are observing bytes on a bus, not copying,
  decompiling, or disassembling Thermo's program — a meaningfully different act
  from what *Bowers*-style clauses and §1201 both talk about. (Still, if you
  never want to touch this question at all, descriptor enumeration — the probe
  script's whole job — requires zero interaction with any Thermo software and
  sidesteps the EULA question entirely.)
- Not every circuit has followed *Bowers*; it's controversial and
  criticized by many commentators as in tension with copyright preemption
  doctrine (*cf.* **Vault Corp. v. Quaid Software**, 5th Cir. 1988, which struck
  down a state anti-reverse-engineering statute as preempted by federal
  copyright law). The law here is genuinely unsettled in the US; treat *Bowers*
  as "this has been enforced at least once at the federal appellate level," not
  as settled law everywhere.

### EU: Software Directive 2009/24/EC, Art. 6

- **Article 6** permits decompilation, without rightholder authorization, where
  "indispensable to obtain the information necessary to achieve the
  interoperability of an independently created computer program with other
  programs," subject to conditions (done by a lawful licensee, limited to the
  parts necessary for interoperability, information not already readily
  available, not used for other purposes).
- **Recital 17 / the Directive's effect**: a contractual term purporting to
  prohibit the Art. 6 decompilation right is **void** for that purpose — i.e.
  in the EU, unlike the US *Bowers* line, a EULA clause cannot override the
  statutory interoperability-decompilation right. This is a meaningfully
  stronger position for an EU-based contributor than the US position.
  [Directive 2009/24/EC Art. 6, UK-hosted copy of the EU text](https://www.legislation.gov.uk/eudr/2009/24/article/6/2020-01-31/data.xht?view=snippet&wrap=true)
- Recent CJEU case law (*Top System v. Belgium*, discussed in commentary found
  during this research) has clarified that decompilation **for error correction**
  is narrower than for interoperability — not directly on point here, but a sign
  EU courts read these exceptions fairly literally/narrowly, so "necessary for
  interoperability" should be taken seriously as a real limiting condition, not
  a blanket excuse to decompile everything.

### UK: CDPA s.50B

- **Section 50B** of the Copyright, Designs and Patents Act 1988 is the UK's
  implementation of the EU interoperability-decompilation right (pre-dates and
  substantively mirrors Art. 6 above); UK case law has confirmed decompilation
  must be *actually necessary* for interoperability and not go further than
  that (a 2025 UK case on mainframe software reverse engineering, found during
  this research, rejected a defendant's interoperability argument because their
  decompilation wasn't limited to what was necessary).
  [UK Court Rules on Reverse Engineering of Mainframe Software (Jones Day summary)](https://www.jonesday.com/en/insights/2025/05/uk-court-rules-on-reverse-engineering-of-mainframe-software)

### Practical takeaway (still not legal advice)

The **lowest-risk tier**, roughly ordered from safest to riskiest:

1. **USB descriptor/interface/endpoint enumeration** and **network interface
   enumeration** with the instrument plugged in — no Thermo software ever
   touched, no EULA ever agreed to, nothing resembling "the computer program"
   is copied or analyzed. This is what `tools/probe_instrument.py` does. Risk:
   negligible.
2. **Passive observation of raw USB bus traffic** between OMNIC Paradigm and the
   instrument (USBPcap/Wireshark, or Linux `usbmon`), on hardware you own, for
   the sole purpose of achieving interoperability. Squarely within the Sega/Sony
   fair-use line and §1201(f)/Art. 6/s.50B's purpose; the main residual risk in
   the US is a *Bowers*-style breach-of-contract claim if you installed/ran
   OMNIC Paradigm itself under a clickwrap EULA containing an anti-RE clause —
   worth reading that specific clause in your own copy of the EULA before this
   step, and worth noting you can also capture traffic from the instrument side
   alone in principle (though in practice you need the vendor app running to
   generate any traffic to observe).
3. **Decompiling or disassembling Thermo's binaries** (OMNIC Paradigm `.exe`/
   `.dll` files, or the SDK's assemblies) to read source-level logic. This is
   the highest-risk tier — still arguably covered by §1201(f)/Art. 6 for the
   narrow purpose of interoperability, but it's the activity most clearly named
   and prohibited by both the EULA and the case law discussed above, and the
   one where "indispensable"/"necessary" and "limited to what's needed" get
   scrutinized hardest by courts. **Not needed for this project** — steps 1–2
   should be more than sufficient to document a USB protocol, and this
   repository's own contribution policy already bars code derived from
   decompilation (see `README.md`).

---

## 5. Protocol-discovery plan

Ordered from lowest to highest risk/effort. Stop at whichever step gives you
enough information — you likely won't need to go past step 3 for a USB-only
instrument like the Summit X.

### Step 1 — Descriptor-only enumeration (no Thermo software involved)

Run `tools/probe_instrument.py` on your development machine:

```bash
# With the Summit X unplugged/powered off
.venv/bin/python tools/probe_instrument.py --output-dir docs/captures

# Power on the Summit X, connect the USB cable, wait for it to finish booting,
# then run again -- WITHOUT installing or running OMNIC Paradigm
.venv/bin/python tools/probe_instrument.py --output-dir docs/captures
```

Diff the two JSON reports (`diff` or a JSON-aware tool). Record, for the
instrument's new USB entry:
- VID:PID (confirm or correct the `0x0EBB` guess in this document)
- Manufacturer/product/serial-number strings
- Device class/subclass (e.g. `0xFF` vendor-specific vs. a standard class like
  CDC/RNDIS — this one fact determines whether you're dealing with a raw
  vendor protocol or a virtual-network link)
- Every interface's class/subclass/protocol and each endpoint's address,
  attributes (bulk/interrupt/isochronous), and max packet size
- Whether a **new network interface** appears at all (settles the RNDIS/CDC-ECM
  question from Q1 empirically)

This step requires no permission beyond physically having the instrument, and
touches nothing of Thermo's — pure "what does my OS see on the bus."

### Step 2 — Check for any network surface, if Step 1 found one

Only relevant if Step 1 revealed a new network interface. If so:
- Note the IP/link-local address it took (or assign one, if it's purely
  point-to-point)
- Run `tools/probe_instrument.py --scan-host <that IP>` to do a polite TCP
  connect-scan of common ports (the script's default list includes the
  Thermo-documented ports from Q2, in case the Summit X's embedded controller
  happens to speak the same service set as the networked models)
- Try plain `curl`/a browser against anything that answers on 80/443/8080/9004,
  in case there's a basic status page or an HTTP API that's partially
  self-describing (OpenAPI/Swagger, a `/status` JSON endpoint, etc.) — this
  alone could shortcut the rest of the discovery process if it exists
- Still zero Thermo software involved.

### Step 3 — Capture USB traffic between OMNIC Paradigm and the instrument

Only do this once you (and, if this instrument is used in any institutional
setting, whoever is responsible for its support/warranty) are comfortable with
step 2 of the legal discussion in Question 4. This is where you actually need
OMNIC Paradigm installed and running.

Tooling:
- **Windows:** [USBPcap](https://desowin.org/usbpcap/tour.html) + Wireshark
  (install Wireshark with the USBPcap option checked; capture on the `USBPcapN`
  interface corresponding to the Summit X's USB controller).
  [Wireshark USBPcap Windows guide](https://cahute.org/misc-guides/capture-usb-using-wireshark-on-windows.html)
- **Linux** (if you ever run OMNIC Paradigm in a Windows VM with USB passthrough
  disabled and instead want to sniff from the Linux host — unlikely to apply
  here since OMNIC Paradigm is Windows-only, but documented for completeness):
  Linux's `usbmon` kernel module + Wireshark's native USB capture support.

Checklist of what to record, in separate, clearly-labeled captures:
1. **Device enumeration only** — plug in the instrument fresh, start the
   capture, then launch OMNIC Paradigm and let it connect. Captures the
   connection handshake.
2. **Idle/status polling** — once connected, do nothing in the UI for ~30
   seconds. Captures any periodic heartbeat/status-polling traffic.
3. **A single background measurement** — trigger exactly one background
   collection, nothing else, start-to-finish.
4. **A single sample measurement** — same, for a sample scan.
5. **Alignment or laser calibration**, if you're willing to run it — these are
   named explicitly in the SDK's API reference (`AlignSpectrometer`,
   `CalibrateLaser`), so a capture gives you something to compare against the
   SDK's documented behavior if you later get SDK access.
6. **Disconnection/shutdown** of the OMNIC Paradigm connection.

For each capture: filter to just the Summit X's USB device (Wireshark: right
click a `GET DESCRIPTOR` response for the right device, "Apply as Filter" →
"Selected"), then save both the full `.pcapng` and a plain-text/CSV export of
just that device's URBs. Note the exact OMNIC Paradigm version and the exact
wall-clock action you took immediately before/after each capture started, so
you can correlate UI actions to byte sequences later.

### Step 4 (optional, costs nothing) — Request SDK access as a cross-check

Email Thermo sales/support asking for OMNIC Paradigm SDK pricing/access. If you
get it, you now have a Windows-only, vendor-blessed way to trigger the exact
same operations (`StartSampleMeasurement`, etc.) programmatically — useful
purely as an oracle to confirm "yes, this byte sequence I captured by hand is
what a background measurement looks like," without ever needing to read the
SDK's own implementation (you'd only be calling its public methods, which is
unambiguously fine under the SDK's own license terms).

---

## Source list

All URLs cited above, deduplicated:

- [OMNIC Paradigm SDK Guide](https://knowledge1.thermofisher.com/Molecular_Spectroscopy/Molecular_Spectroscopy_Software/OMNIC_Family/OMNIC_Paradigm_Software/OMNIC_Paradigm_SDK_Guide)
- [OMNIC Paradigm SDK API Reference](https://knowledge1.thermofisher.com/Molecular_Spectroscopy/Molecular_Spectroscopy_Software/OMNIC_Family/OMNIC_Paradigm_Software/OMNIC_Paradigm_SDK_Guide/OMNIC_Paradigm_SDK_API_Reference)
- [Paradigm SDK – Getting Started](https://knowledge1.thermofisher.com/Molecular_Spectroscopy/Molecular_Spectroscopy_Software/OMNIC_Family/OMNIC_Paradigm_Software/OMNIC_Paradigm_SDK_Guide/Paradigm_SDK_-_Getting_Started)
- [SDK Theory of Operation](https://knowledge1.thermofisher.com/Molecular_Spectroscopy/Molecular_Spectroscopy_Software/OMNIC_Family/OMNIC_Paradigm_Software/OMNIC_Paradigm_SDK_Guide/SDK_Theory_of_Operation)
- [OMNIC Paradigm SDK Release Notes](https://knowledge1.thermofisher.com/Molecular_Spectroscopy/Molecular_Spectroscopy_Software/OMNIC_Family/OMNIC_Paradigm_Release_Notes/OMNIC_Paradigm_Software_SDK_Release_Notes)
- [Connect to a Spectrometer (OMNIC Paradigm User Guide)](https://knowledge1.thermofisher.com/Molecular_Spectroscopy/Molecular_Spectroscopy_Software/OMNIC_Family/OMNIC_Paradigm_Software/OMNIC_Paradigm_Operator_Manuals/Latest_OMNIC_Paradigm_User_Guide/Connect_to_a_Spectrometer)
- [OMNIC Paradigm User Guide (PDF)](https://documents.thermofisher.com/TFS-Assets/MSD/Product-Information/OMNIC-Paradigm-UG.pdf)
- [OMNIC Paradigm Product Security Information Guide (PDF)](https://documents.thermofisher.com/TFS-Assets/CAD/Product-Guides/PSIG_OMNIC_ProductSecurity_TP_FINAL.pdf)
- [Nicolet Summit family brochure (PDF)](https://documents.thermofisher.com/TFS-Assets/MSD/brochures/nicolet-summit-family-brochure-bc53614.pdf)
- [269-3455 Nicolet Summit LITE and Summit X User Guide (index)](https://knowledge1.thermofisher.com/Molecular_Spectroscopy/Fourier_Transform_FT_Infrared_IR_and_Near_Infrared_NIR/Nicolet_Spectrometers/Nicolet_Summit_Family_Spectrometers/269-3455_Nicolet_Summit_LITE_and_Summit_X_FTIR_Spectrometer_User_Guide)
- [Instruments, Software, and Firmware licensing](https://www.thermofisher.com/us/en/home/about-us/partnering-licensing/license-our-technology/intellectual-property-licensing/instruments-software-licensing.html)
- [TN 9365: OMNIC Macro Programming and DDE](https://knowledge1.thermofisher.com/Molecular_Spectroscopy/Fourier_Transform_FT_Infrared_IR_and_Near_Infrared_NIR/FT-IR_Application_Notes/TN_9365_OMNIC_Software_Macro_Programming_and_Dynamic_Data_Exchange)
- [OMNIC DDE Commands and Parameters (PDF mirror)](https://mmrc.caltech.edu/FTIR/Nicolet/Nicolet%20manuals/OMNIC%20DDE%20Commands%20&%20Parameters.pdf)
- [Nicolet Software Licensing Agreement (PDF mirror)](https://mmrc.caltech.edu/FTIR/restricted/Nicolet%20Win%207%20%20Software/Nicolet%20Win%207%20%20Software/Software%20Licensing%20Agreement.pdf)
- [devicehunt.com USB\VID_0EBB](https://devicehunt.com/view/type/usb/vendor/0EBB)
- [the-sz.com USB ID database](https://the-sz.com/products/usbid/index.php?n=T*)
- [brukeropus (GitHub)](https://github.com/joshduran/brukeropus)
- [brukeropusreader (GitHub, qedsoftware/LGPLv3)](https://github.com/qedsoftware/brukeropusreader)
- [spectrochempy-omnic (GitHub, CeCILL-B)](https://github.com/spectrochempy/spectrochempy-omnic)
- [spa-on-python (GitHub)](https://github.com/lerkoah/spa-on-python)
- [SiLA 2 standard](https://sila-standard.com/faq/)
- [SiLA2 vs OPC-UA](https://evo-byte.com/sila-2-opc-ua-and-the-modern-lab-stack/)
- [Sega v. Accolade (Wikipedia)](https://en.wikipedia.org/wiki/Sega_v._Accolade)
- [Bowers v. Baystate Technologies (Wikipedia)](https://en.wikipedia.org/wiki/Bowers_v._Baystate_Technologies,_Inc.)
- [Directive 2009/24/EC, Art. 6 text](https://www.legislation.gov.uk/eudr/2009/24/article/6/2020-01-31/data.xht?view=snippet&wrap=true)
- [UK CDPA s.50B text](https://www.legislation.gov.uk/ukpga/1988/48/section/50B)
- [UK Court Rules on Reverse Engineering of Mainframe Software (Jones Day)](https://www.jonesday.com/en/insights/2025/05/uk-court-rules-on-reverse-engineering-of-mainframe-software)
- [USBPcap](https://desowin.org/usbpcap/tour.html)
- [Wireshark USBPcap Windows capture guide](https://cahute.org/misc-guides/capture-usb-using-wireshark-on-windows.html)
