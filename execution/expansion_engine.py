"""
Stable import path: deterministic DTG → IG expansion (delegates to `core.expansion_engine`).
"""

from core.expansion_engine import (  # noqa: F401
    EXPANSION_STRATEGIES,
    EXPANSION_STRATEGIES_ALL_NAMES,
    EXPANSION_STRATEGY_ALIASES,
    canonical_expansion_strategy,
    expand_dtg_node,
    expand_node,
    normalize_expansion_strategy,
)

__all__ = [
    "EXPANSION_STRATEGIES",
    "EXPANSION_STRATEGIES_ALL_NAMES",
    "EXPANSION_STRATEGY_ALIASES",
    "canonical_expansion_strategy",
    "expand_dtg_node",
    "expand_node",
    "normalize_expansion_strategy",
]
