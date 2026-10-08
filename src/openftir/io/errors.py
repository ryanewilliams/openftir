"""Shared exception types for openftir.io."""

from __future__ import annotations


class FormatError(ValueError):
    """Raised when a file cannot be parsed as the format it claims to be.

    Readers should raise this (with a message that names the file and the
    specific thing that looked wrong) instead of letting a raw struct/parse
    exception bubble up, so callers can show a sensible error to the user.
    """
