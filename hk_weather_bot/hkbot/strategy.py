"""Edge rules: compare our probability with the price you'd actually pay."""
from __future__ import annotations

from dataclasses import dataclass

from .config import Strategy
from .sources import MarketBucket


@dataclass
class TradeIdea:
    label: str
    side: str            # "YES" or "NO"
    price: float         # price paid per share for that side
    prob: float          # our probability that this side wins
    edge: float          # prob - price
    shares: float
    stake: float

    def describe(self) -> str:
        return (f"BUY {self.side:<3} {self.label:<16} @ {self.price*100:5.1f}c  "
                f"our {self.prob*100:5.1f}%  edge {self.edge*100:+5.1f}  "
                f"stake ${self.stake:.2f} ({self.shares:.1f} sh)")


def find_trades(market: list[MarketBucket], probs: list[float], strat: Strategy) -> list[TradeIdea]:
    ideas: list[TradeIdea] = []
    for mb, p in zip(market, probs):
        # YES: you pay the ask.
        if mb.yes_ask is not None and strat.min_price <= mb.yes_ask <= strat.max_price:
            edge = p - mb.yes_ask
            if edge >= strat.min_edge:
                ideas.append(_idea(mb, "YES", mb.yes_ask, p, strat))
        # NO: you pay 1 - bid (selling YES at the bid is the same bet).
        if mb.yes_bid is not None:
            no_price = 1.0 - mb.yes_bid
            if strat.min_price <= no_price <= strat.max_price:
                edge = (1 - p) - no_price
                if edge >= strat.min_edge:
                    ideas.append(_idea(mb, "NO", no_price, 1 - p, strat))
    ideas.sort(key=lambda t: t.edge, reverse=True)
    return ideas[: strat.max_trades_per_day]


def _idea(mb: MarketBucket, side: str, price: float, prob: float, strat: Strategy) -> TradeIdea:
    return TradeIdea(
        label=mb.bucket.label, side=side, price=price, prob=prob,
        edge=prob - price, shares=strat.stake_usd / price, stake=strat.stake_usd,
    )
