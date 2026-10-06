"""Extracted  values calculations; no agent execution."""

from __future__ import annotations

import math
from typing import Any


def _unknown_spec(value: Any) -> bool:
    if isinstance(value, dict):
        return not value or any(_unknown_spec(v) for v in value.values())
    if isinstance(value, list):
        return not value or any(_unknown_spec(v) for v in value)
    return value is None or (
        isinstance(value, str) and (not _known(value) or "to_be_determined" in value or "derive from source" in value)
    )


def _known(value: Any) -> bool:
    return (
        value is not None
        and not isinstance(value, bool)
        and not str(value).strip().lower().startswith("replace")
        and str(value).strip().lower()
        not in {
            "",
            "unknown",
            "unspecified",
            "unreported",
            "source_scale",
            "pending",
            "none",
            "n/a",
            "na",
        }
    )


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None
