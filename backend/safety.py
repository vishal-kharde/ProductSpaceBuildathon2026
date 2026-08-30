"""Safety helpers for JSON/API boundaries."""
from __future__ import annotations
import math
from typing import Any


def sanitize_json(value: Any) -> Any:
    """Return a JSON-safe copy; non-finite floats become None."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): sanitize_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [sanitize_json(v) for v in value]
    return value


def finite_number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default
