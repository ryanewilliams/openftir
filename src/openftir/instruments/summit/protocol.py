"""Summit XML command/reply grammar.

See ``docs/summit-protocol.md`` ("Message grammar") for the wire format this
module implements::

    <cmd><get><a/><b/>...</get></cmd>        -> <reply><a>val</a><b>val</b>...</reply>
    <cmd><set><a>val</a>...</set></cmd>      -> <reply><a>val</a>...</reply>   (echo)
    unknown key                               -> <notRecognized>key</notRecognized> inside reply
    bad argument                              -> <reply><error><key/></error></reply>
    async (EP 0x84)                           -> <benchMsg><motorsDone>0</motorsDone></benchMsg>

Replies are parsed into a small :class:`ParsedReply` wrapper: a nested
``dict``/``list`` tree (repeated sibling tags, e.g. the ``<detGain>`` entries
inside a ``detGainCfg`` reply, or the ``<beampath>`` entries inside a
``bpTable`` reply, become a ``list``), plus the ``notRecognized`` keys and
``error`` keys collected separately so callers don't have to dig through the
tree for them.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

__all__ = ["ProtocolError", "ParsedReply", "build_get", "build_set", "parse_reply"]


class ProtocolError(ValueError):
    """The Summit sent something that isn't a well-formed ``<reply>``."""


def _fmt_value(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        # Avoid "6154.0" for values that happen to be whole numbers, while
        # keeping real decimals (e.g. fwdVel=0.4747) as Paradigm sent them.
        if value == int(value):
            return str(int(value))
        return repr(value)
    return str(value)


def build_get(keys: "list[str] | tuple[str, ...]") -> str:
    """Build ``<cmd><get><a/><b/>...</get></cmd>`` for the given keys."""
    inner = "".join(f"<{key}/>" for key in keys)
    return f"<cmd><get>{inner}</get></cmd>"


def build_set(values: dict[str, Any]) -> str:
    """Build ``<cmd><set><a>val</a>...</set></cmd>`` for the given values."""
    inner = "".join(f"<{key}>{_fmt_value(val)}</{key}>" for key, val in values.items())
    return f"<cmd><set>{inner}</set></cmd>"


def _elem_to_value(elem: ET.Element) -> Any:
    children = list(elem)
    if not children:
        return (elem.text or "").strip()
    groups: dict[str, list[Any]] = {}
    for child in children:
        groups.setdefault(child.tag, []).append(_elem_to_value(child))
    return {tag: (vals[0] if len(vals) == 1 else vals) for tag, vals in groups.items()}


@dataclass
class ParsedReply:
    """A parsed ``<reply>...</reply>`` (or ``<benchMsg>...</benchMsg>``)."""

    values: dict[str, Any] = field(default_factory=dict)
    not_recognized: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def get_float(self, key: str, default: float | None = None) -> float | None:
        value = self.values.get(key)
        if value is None:
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def get_int(self, key: str, default: int | None = None) -> int | None:
        value = self.values.get(key)
        if value is None:
            return default
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default


def parse_reply(xml_text: str, root_tag: str | None = None) -> ParsedReply:
    """Parse a ``<reply>`` (or, for EP 0x84 events, a ``<benchMsg>``) string.

    ``root_tag``, if given, is checked against the parsed root's tag;
    otherwise any root tag is accepted (``<reply>`` and ``<benchMsg>`` are
    both used by the protocol, see module docstring).
    """
    # The Summit NUL-terminates every reply/event ("...</reply>\x00"), and a
    # read may carry trailing padding; parse only up to the root's end tag.
    xml_text = xml_text.replace("\x00", "").strip()
    for end_tag in ("</reply>", "</benchMsg>"):
        end = xml_text.find(end_tag)
        if end != -1:
            xml_text = xml_text[: end + len(end_tag)]
            break
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ProtocolError(f"malformed XML from Summit: {exc}: {xml_text!r}") from exc

    if root_tag is not None and root.tag != root_tag:
        raise ProtocolError(f"expected <{root_tag}>, got <{root.tag}>: {xml_text!r}")

    values: dict[str, list[Any]] = {}
    not_recognized: list[str] = []
    errors: list[str] = []
    for child in root:
        if child.tag == "notRecognized":
            not_recognized.append((child.text or "").strip())
            continue
        if child.tag == "error":
            errors.extend(c.tag for c in child)
            if not list(child):
                # <error/> with no nested key -- nothing more to report.
                pass
            continue
        values.setdefault(child.tag, []).append(_elem_to_value(child))

    merged = {tag: (vals[0] if len(vals) == 1 else vals) for tag, vals in values.items()}
    return ParsedReply(values=merged, not_recognized=not_recognized, errors=errors)
