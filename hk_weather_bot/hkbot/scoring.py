"""Scoring: how good were the probabilities, and did the trades make money?"""
from __future__ import annotations

import math


def brier(probs: list[float], winner_index: int) -> float:
    """Sum of squared errors across buckets. 0 = perfect, lower is better.
    (Same score used in the manual audit: v1 = 0.62 vs market 0.33 on Oct 19.)"""
    return sum((p - (1.0 if i == winner_index else 0.0)) ** 2 for i, p in enumerate(probs))


def log_loss(probs: list[float], winner_index: int) -> float:
    """-ln(prob given to the winner). Punishes confident misses hard. Lower is better."""
    return -math.log(max(probs[winner_index], 1e-6))


def trade_pnl(side: str, price: float, shares: float, bucket_won: bool) -> float:
    """Profit in USD for a paper trade held to resolution (no fees modelled)."""
    side_won = bucket_won if side == "YES" else not bucket_won
    return shares * (1.0 - price) if side_won else -shares * price
