"""Cold-start calibration of the Varian 3100 bench.

After power-on the bench reports itself uninitialised (bench register 0x10
reads 0x00 instead of 0x08). Resolutions Pro then runs a ~3 minute routine
before anything else, seen in two captures (``docs/varian-protocol.md``):

1. **Raster.** With signal channel 0x0E selected and all three channel
   gains (BD/BE/BF) at 0xFF, it steps three actuator settings (B0/B1/AF)
   through a fixed 77-point raster and takes a 1056-sample read at each.
   Whenever a read comes close to the ADC's limit it halves all three gains
   and reads the same point again.
2. **Approach and fine search.** It drives the actuators to zero, then
   retraces the raster path into the best point (highest level per unit
   gain), and hill-climbs from there in small steps along two directions,
   (+1, -1, 0) and (+1, +1, -1), with step sizes shrinking from 7 to 1.
3. **Gains.** For channels 0x0E, 0x0C and 0x0D in turn it sets that
   channel's gain so its level comes out at about 20,500 counts.
4. It marks the bench initialised (register 0x10 = 0x08).

Step 1 (and the fixed approach) is reproduced exactly; the raster is
identical in both captures. Step 2 is OpenFTIR's own hill climb with the
same goal and moves; Resolutions' exact acceptance rules can't be recovered
from two noisy runs. Step 3 uses the same target. Every setting stays in
the range Resolutions itself uses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

Point = tuple[int, int, int]   # (B0, B1, AF)

#: The coarse raster, identical in both captured cold starts.
RASTER: tuple[Point, ...] = (
    (0x90, 0x10, 0xB0), (0xA0, 0x20, 0xA0), (0xB0, 0x30, 0x90), (0xC0, 0x40, 0x80),
    (0xD0, 0x50, 0x70), (0xE0, 0x60, 0x60), (0xF0, 0x70, 0x50), (0xE8, 0x88, 0x48),
    (0xD8, 0x78, 0x58), (0xC8, 0x68, 0x68), (0xB8, 0x58, 0x78), (0xA8, 0x48, 0x88),
    (0x98, 0x38, 0x98), (0x88, 0x28, 0xA8), (0x78, 0x18, 0xB8), (0x60, 0x20, 0xC0),
    (0x70, 0x30, 0xB0), (0x80, 0x40, 0xA0), (0x90, 0x50, 0x90), (0xA0, 0x60, 0x80),
    (0xB0, 0x70, 0x70), (0xC0, 0x80, 0x60), (0xD0, 0x90, 0x50), (0xE0, 0xA0, 0x40),
    (0xD8, 0xB8, 0x38), (0xC8, 0xA8, 0x48), (0xB8, 0x98, 0x58), (0xA8, 0x88, 0x68),
    (0x98, 0x78, 0x78), (0x88, 0x68, 0x88), (0x78, 0x58, 0x98), (0x68, 0x48, 0xA8),
    (0x58, 0x38, 0xB8), (0x48, 0x28, 0xC8), (0x40, 0x40, 0xC0), (0x50, 0x50, 0xB0),
    (0x60, 0x60, 0xA0), (0x70, 0x70, 0x90), (0x80, 0x80, 0x80), (0x90, 0x90, 0x70),
    (0xA0, 0xA0, 0x60), (0xB0, 0xB0, 0x50), (0xC0, 0xC0, 0x40), (0xB8, 0xD8, 0x38),
    (0xA8, 0xC8, 0x48), (0x98, 0xB8, 0x58), (0x88, 0xA8, 0x68), (0x78, 0x98, 0x78),
    (0x68, 0x88, 0x88), (0x58, 0x78, 0x98), (0x48, 0x68, 0xA8), (0x38, 0x58, 0xB8),
    (0x28, 0x48, 0xC8), (0x20, 0x60, 0xC0), (0x30, 0x70, 0xB0), (0x40, 0x80, 0xA0),
    (0x50, 0x90, 0x90), (0x60, 0xA0, 0x80), (0x70, 0xB0, 0x70), (0x80, 0xC0, 0x60),
    (0x90, 0xD0, 0x50), (0xA0, 0xE0, 0x40), (0x88, 0xE8, 0x48), (0x78, 0xD8, 0x58),
    (0x68, 0xC8, 0x68), (0x58, 0xB8, 0x78), (0x48, 0xA8, 0x88), (0x38, 0x98, 0x98),
    (0x28, 0x88, 0xA8), (0x18, 0x78, 0xB8), (0x10, 0x90, 0xB0), (0x20, 0xA0, 0xA0),
    (0x30, 0xB0, 0x90), (0x40, 0xC0, 0x80), (0x50, 0xD0, 0x70), (0x60, 0xE0, 0x60),
    (0x70, 0xF0, 0x50),
)

#: How far back along the raster the approach starts (Resolutions retraced
#: 12 points into its best point in both captures).
APPROACH_POINTS = 12
DIRECTIONS: tuple[Point, ...] = ((1, -1, 0), (1, 1, -1))
STEPS = (7, 5, 3, 1)
MAX_FINE_READS = 90

FULL_GAIN = 0xFF
MIN_GAIN = 0x10
#: A raster read whose lowest sample reaches this halves the gains. Over
#: both captures Resolutions halved at minima of 24960 and above and not at
#: 22103 and below (a read can touch the 32767 limit without halving).
SATURATION = 0x6000
#: Channel level Resolutions set each gain for (it settled at 20,190-21,230).
TARGET_LEVEL = 20700
LEVEL_WINDOW = (20300, 21300)
#: The gain DACs behave like gain proportional to (setting - 3).
GAIN_ZERO = 3
SETTING_RANGE = (0x10, 0xF0)

#: Signal channel (bench register 0x01) -> gain register, in Resolutions' order.
GAIN_CHANNELS = ((0x0E, 0xBD), (0x0C, 0xBE), (0x0D, 0xBF))


@dataclass
class Reading:
    mean: float
    low: float
    ok: bool = True


@dataclass
class CalibrationResult:
    point: Point
    gains: dict[int, int]
    raster_best: Point
    reads: int = 0
    log: list[str] = field(default_factory=list)


def clamp(point: Point) -> Point:
    lo, hi = SETTING_RANGE
    return tuple(min(max(v, lo), hi) for v in point)  # type: ignore[return-value]


def approach_path(best_index: int) -> list[Point]:
    """Points to drive through (no reads) to reach the raster's best point
    the same way the raster did: zero, the raster start, then the last
    ``APPROACH_POINTS`` raster points up to and including the best (the
    first of those twice, as Resolutions does)."""
    start = max(best_index - APPROACH_POINTS, 0)
    return [(0, 0, 0), RASTER[0], RASTER[start], *RASTER[start : best_index + 1]]


def gain_for_level(gain: int, level: float, target: float = TARGET_LEVEL) -> int:
    if level <= 0:
        return FULL_GAIN
    estimate = GAIN_ZERO + (gain - GAIN_ZERO) * target / level
    return int(min(max(round(estimate), MIN_GAIN), FULL_GAIN))


class Calibrator:
    """Runs the routine against callbacks, so it can be tested without USB.

    ``move(point)`` applies B0/B1/AF; ``set_gains(gains, force=False)``
    applies BD/BE/BF (only the changed ones unless ``force``);
    ``select(channel)`` picks the signal channel; ``read()`` returns a
    :class:`Reading` of the selected channel; ``check()`` raises to cancel.
    """

    def __init__(
        self,
        move: Callable[[Point], None],
        set_gains: Callable[..., None],
        select: Callable[[int], None],
        read: Callable[[], Reading],
        check: Callable[[], None] = lambda: None,
        status: Callable[[str], None] = lambda _msg: None,
    ) -> None:
        self._move, self._set_gains, self._select = move, set_gains, select
        self._read_raw, self._check, self._status = read, check, status
        self.reads = 0
        self.log: list[str] = []

    def _read(self) -> Reading:
        self._check()
        self.reads += 1
        return self._read_raw()

    def run(self) -> CalibrationResult:
        gains = {reg: FULL_GAIN for _ch, reg in GAIN_CHANNELS}
        self._select(0x0E)
        self._set_gains(gains)

        self._status("Aligning the bench: coarse search")
        best_index, best_score = 0, float("-inf")
        for index, point in enumerate(RASTER):
            self._move(point)
            if index == 0:
                # Resolutions reads the first point, sets it again and reads it again.
                self._read()
                self._move(point)
            reading = self._read()
            while reading.ok and reading.low >= SATURATION and gains[0xBD] > MIN_GAIN:
                gains = {reg: max(g // 2, MIN_GAIN) if g < FULL_GAIN else 0x80 for reg, g in gains.items()}
                self._set_gains(gains)
                self.log.append(f"gains -> {gains[0xBD]:#04x} at raster point {index}")
                reading = self._read()
            if reading.ok:
                score = reading.mean / (gains[0xBD] - GAIN_ZERO)
                if score > best_score:
                    best_index, best_score = index, score
            else:
                # After a failed read Resolutions sets the gains again.
                self._set_gains(gains, force=True)
        raster_best = RASTER[best_index]
        self.log.append(f"raster best {raster_best} (point {best_index})")

        self._status("Aligning the bench: fine search")
        for point in approach_path(best_index):
            self._move(point)
        point = self._fine_search(raster_best)

        self._status("Aligning the bench: setting gains")
        for channel, reg in GAIN_CHANNELS:
            self._select(channel)
            level = self._level()
            gains[reg] = gain_for_level(gains[reg], level)
            self._set_gains(gains)
            for _ in range(4):
                level = self._level()
                if level < LEVEL_WINDOW[0] and gains[reg] < FULL_GAIN:
                    gains[reg] += 1
                elif level > LEVEL_WINDOW[1] and gains[reg] > MIN_GAIN:
                    gains[reg] -= 1
                else:
                    break
                self._set_gains(gains)
            self.log.append(f"channel {channel:#04x}: gain {gains[reg]:#04x}, level {level:.0f}")
        return CalibrationResult(point=point, gains=dict(gains), raster_best=raster_best, reads=self.reads, log=self.log)

    def _level(self) -> float:
        for _ in range(3):
            reading = self._read()
            if reading.ok:
                return reading.mean
        return 0.0

    def _measure(self, point: Point) -> float:
        self._move(point)
        reading = self._read()
        return reading.mean if reading.ok else float("-inf")

    def _fine_search(self, start: Point) -> Point:
        limit = self.reads + MAX_FINE_READS
        base = clamp(start)
        reading = self._read()   # the approach ended on the start point
        base_level = reading.mean if reading.ok else float("-inf")
        # One pass per step size: each direction both ways, carrying on while
        # it keeps improving, then the next smaller step.
        for step in STEPS:
            for direction in DIRECTIONS:
                for sign in (-1, 1):
                    improved = False
                    while self.reads < limit:
                        cand = clamp(tuple(b + sign * step * d for b, d in zip(base, direction)))
                        if cand == base:
                            break
                        level = self._measure(cand)
                        if level > base_level * 1.002:
                            base, base_level, improved = cand, level, True
                            continue
                        # Back to the base and re-measure it, as Resolutions does.
                        base_level = self._measure(base)
                        break
                    if improved:
                        break   # no point trying the way it just came from
        self._move(base)
        self.log.append(f"fine search ended at {base}, level {base_level:.0f}")
        return base
