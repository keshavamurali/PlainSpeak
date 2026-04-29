"""
Execution-layer entrypoints. Deterministic expansion lives in `core.expansion_engine`.
"""

from core.expansion_engine import (
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
