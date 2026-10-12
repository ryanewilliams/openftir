"""pyqtgraph 0.14's axis tick spacing, for the Windows 7 build.

The Windows 7 build is stuck on pyqtgraph 0.13.3 (0.14 needs Python 3.10),
which spaces ticks more sparsely (every 1000 cm-1 instead of every 500) and
draws no minor grid. This is AxisItem.tickSpacing from pyqtgraph 0.14.0
(MIT licence, Copyright (c) 2012 University of North Carolina at Chapel
Hill, Luke Campagnola), so the plot looks the same in every build.
"""

import importlib.abc
import importlib.util
import sys
from math import floor, frexp, sqrt


def _tick_spacing(self, minVal, maxVal, size):
    """
    Determine the spacing of ticks on the axis.

    This method is called whenever the axis needs to be redrawn and is a
    good method to override in subclasses that require control over tick locations.

    Parameters
    ----------
    minVal : float
        Minimum value being displayed on the axis.
    maxVal : float
        Maximum value being displayed on the axis.
    size : float
        Length of the axis in pixels.

    Returns
    -------
    list of tuple of float, float
        A list of tuples, one for each tick level.
        Each tuple contains two values: ``(spacing, offset)``.  The spacing value
        is the distance between ticks, and the offset is the first tick relative to
        *minVal*. For example, if ``result[0]`` is ``(10, 0)``, then major ticks 
        will be displayed every 10 units and the first major tick will correspond to
        ``minVal``. If instead ``result[0]`` is ``(10, 5)``, then major ticks will
        be displayed every 10 units, but the first major tick will correspond to
        ``minVal + 5``.

        .. code-block:: python

            [
                (major_tick_spacing, offset),
                (minor_tick_spacing, offset),
                (sub_minor_tick_spacing, offset),
                ...
            ]
    """
    # First check for explicit tick spacing
    if self._tickSpacing is not None:
        return self._tickSpacing

    dif = abs(maxVal - minVal)
    if dif == 0:
        return []

    ref_size = 300. # axes longer than this display more than the minimum number of major ticks
    minNumberOfIntervals = max(
        2.25,       # 2.0 ensures two tick marks. Fudged increase to 2.25 allows room for tick labels.
        2.25 * getattr(self, "_tickDensity", 1.0) * sqrt(size/ref_size) # sub-linear growth of tick spacing with size
    )

    majorMaxSpacing = dif / minNumberOfIntervals

    # We want to calculate the power of 10 just below the maximum spacing.
    # Then divide by ten so that the scale factors for subdivision all become intergers.
    # p10unit = 10**( floor( log10(majorMaxSpacing) ) ) / 10

    # And we want to do it without a log operation:
    mantissa, exp2 = frexp(majorMaxSpacing) # IEEE 754 float already knows its exponent, no need to calculate
    p10unit = 10. ** ( # approximate a power of ten base factor just smaller than the given number
        floor(            # int would truncate towards zero to give wrong results for negative exponents
            (exp2-1)      # IEEE 754 exponent is ceiling of true exponent --> estimate floor by subtracting 1
            / 3.32192809488736 # division by log2(10)=3.32 converts base 2 exponent to base 10 exponent
        ) - 1             # subtract one extra power of ten so that we can work with integer scale factors >= 5
    )
    # neglecting the mantissa can underestimate by one power of 10 when the true value is JUST above the threshold.
    if 100. * p10unit <= majorMaxSpacing: # Cheaper to check this than to use a more complicated approximation.
        majorScaleFactor = 10
        p10unit *= 10.
    else:
        for majorScaleFactor in (50, 20, 10):
            if majorScaleFactor * p10unit <= majorMaxSpacing:
                break # find the first value that is smaller or equal
    majorInterval = majorScaleFactor * p10unit
    # manual sanity check: print(f"{majorMaxSpacing:.2e} > {majorInterval:.2e} = {majorScaleFactor:.2e} x {p10unit:.2e}")
    levels = [
        (majorInterval, 0),
    ]

    if self.style['maxTickLevel'] >= 1:
        minorMinSpacing = 2 * dif/size   # no more than one minor tick per two pixels
        trials = (5, 10) if majorScaleFactor == 10 else (10, 20, 50)
        for minorScaleFactor in trials:
            minorInterval = minorScaleFactor * p10unit
            if minorInterval >= minorMinSpacing:
                break # find the first value that is larger or equal to allowed minimum of 1 per 2px
        levels.append((minorInterval, 0))

    # extra ticks at 10% of major interval are pretty, but eat up CPU
    if self.style['maxTickLevel'] >= 2: # consider only when enabled
        if majorScaleFactor == 10:
            trials = (1, 2, 5, 10) # start at 10% of major interval, increase if needed
        elif majorScaleFactor == 20:
            trials = (2, 5, 10, 20) # start at 10% of major interval, increase if needed
        elif majorScaleFactor == 50:
            trials = (5, 10, 50) # start at 10% of major interval, increase if needed
        else: # invalid value
            trials = () # skip extra interval
            extraInterval = minorInterval
        for extraScaleFactor in trials:
            extraInterval = extraScaleFactor * p10unit
            if extraInterval >= minorMinSpacing or extraInterval == minorInterval:
                break # find the first value that is larger or equal to allowed minimum of 1 per 2px
        if extraInterval < minorInterval: # add extra interval only if it is visible
            levels.append((extraInterval, 0))
    return levels


class _PatchingLoader(importlib.abc.Loader):
    """Runs the real loader for pyqtgraph's AxisItem module, then patches it."""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def create_module(self, spec):
        return self._inner.create_module(spec)

    def exec_module(self, module):
        self._inner.exec_module(module)
        module.AxisItem.tickSpacing = _tick_spacing


class _AxisItemFinder(importlib.abc.MetaPathFinder):
    # Patch when pyqtgraph is first imported rather than importing it here,
    # so the splash screen still appears before the heavy libraries load.
    _NAME = "pyqtgraph.graphicsItems.AxisItem"

    def find_spec(self, name, path=None, target=None):
        if name != self._NAME:
            return None
        sys.meta_path.remove(self)
        spec = importlib.util.find_spec(name)
        if spec is not None and spec.loader is not None:
            spec.loader = _PatchingLoader(spec.loader)
        return spec


def install():
    if "pyqtgraph.graphicsItems.AxisItem" in sys.modules:
        sys.modules["pyqtgraph.graphicsItems.AxisItem"].AxisItem.tickSpacing = _tick_spacing
    elif not any(isinstance(f, _AxisItemFinder) for f in sys.meta_path):
        sys.meta_path.insert(0, _AxisItemFinder())
