"""Run before OpenFTIR starts in the Windows 7 build (Python 3.8).

Python 3.9+ and numpy 2 are not available on Windows 7. Swap in backports
for the few newer library features OpenFTIR uses before the app imports them.
"""

import importlib.metadata
import importlib.resources

import importlib_metadata
import importlib_resources
import numpy

if not hasattr(importlib.resources, "files"):
    importlib.resources.files = importlib_resources.files  # Python 3.9+

try:
    importlib.metadata.entry_points(group="openftir.drivers")
except TypeError:  # Python < 3.10: entry_points() takes no arguments
    importlib.metadata.entry_points = importlib_metadata.entry_points

if not hasattr(numpy, "trapezoid"):
    numpy.trapezoid = numpy.trapz  # renamed in numpy 2.0
