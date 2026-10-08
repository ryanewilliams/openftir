"""usbcap -- offline USB capture analysis tooling for OpenFTIR.

This package helps work out the USB wire protocol of a Thermo Nicolet Summit /
Summit X (or any other USB instrument) by analysing packet captures taken with
Wireshark + USBPcap on Windows (DLT_USBPCAP, link type 249) or with usbmon on
Linux (DLT_USB_LINUX / DLT_USB_LINUX_MMAPPED, link types 189 / 220).

It is pure Python + numpy: no ``scapy``/``pyshark``/``tshark`` dependency, so it
runs anywhere the rest of OpenFTIR's dev environment runs. It only *reads*
capture files that already exist -- it never talks to USB hardware itself and
is not part of the probe/instrument driver code.

See ``tools/usbcap/README.md`` for usage and ``docs/instrument-research.md``
for the reverse-engineering workflow this tool supports.
"""

from __future__ import annotations
