"""Structured, redacted events for Application Insights.

The Functions host forwards `logging` records to App Insights; `extra`
fields land in customDimensions. Questions are redacted before logging and
users are identified only by a one-way pseudonym.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("groundwork")

_REDACTIONS = [
    (re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+"), "[email]"),
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "[card]"),
    (re.compile(r"\+?\d[\d\s().-]{7,}\d"), "[number]"),
]


def redact(text: str, limit: int = 400) -> str:
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text[:limit]


def event(name: str, **dimensions) -> None:
    log.info(name, extra={"custom_dimensions": dimensions})
