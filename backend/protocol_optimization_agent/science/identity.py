"""Stable scientific entity IDs, independent of persistence."""
from __future__ import annotations

import hashlib
import json
from typing import Any


def stable_id(prefix: str, payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True, allow_nan=False).encode()
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()[:24]}"
