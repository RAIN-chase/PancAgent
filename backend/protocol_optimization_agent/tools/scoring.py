from __future__ import annotations

from typing import Any


class MaturityScoringTool:
    """Adapter boundary for an existing maturity scoring implementation."""

    def score(self, adata: Any, *, groupby: str) -> Any:
        raise NotImplementedError("Connect the existing maturity scoring tool here.")


class SimilarityScoringTool:
    """Adapter boundary for an existing primary-islet similarity implementation."""

    def score(self, adata: Any, *, reference: Any, groupby: str) -> Any:
        raise NotImplementedError("Connect the existing similarity scoring tool here.")
