from typing import Optional
from pydantic import BaseModel


class PartyAxisStats(BaseModel):
    n: int
    mean: Optional[float] = None
    # None when there are fewer than 2 members or no spread
    sd: Optional[float] = None


class AxisSummary(BaseModel):
    """Distribution of one axis's scores across a chamber."""

    n: int
    mean: Optional[float] = None
    # Population SD (divide by n). None when it can't be used to scale scores:
    # fewer than 2 members, or no spread.
    sd: Optional[float] = None
    min: Optional[float] = None
    max: Optional[float] = None
    by_party: dict[str, PartyAxisStats] = {}


class PairCorrelation(BaseModel):
    # None when undefined: fewer than 3 members with both axes, or no spread
    r: Optional[float] = None
    n: int


class AxisStatsDoc(BaseModel):
    """
    One document per (spec_hash, current) in the `axis_stats` collection.
    current=True covers current members only; current=False covers all members.
    """

    spec_hash: str
    current: bool
    member_count: int
    # Members need at least this many bills on an axis to count toward its stats
    min_bills: int
    axes: dict[str, AxisSummary] = {}
    # scale -> axis -> other axis -> correlation. Symmetric; both orderings are stored.
    #   "raw":   Pearson r of scores (also the r for the "vs. chamber" scale, since
    #            rescaling by a positive constant doesn't change a correlation)
    #   "party": Pearson r of within_party_z, so members without one (independents,
    #            tiny parties) drop out
    correlations: dict[str, dict[str, dict[str, PairCorrelation]]] = {}
