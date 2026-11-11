from typing import Optional
from pydantic import BaseModel


class RankStats(BaseModel):
    """Position of a member's score among all members, added by generate_rankings."""

    rank: Optional[int] = None
    percentile_rank: Optional[float] = None
    total_members: Optional[int] = None
    total_current_members: Optional[int] = None
    current_rank: Optional[int] = None
    current_percentile_rank: Optional[float] = None


class AgreementStats(BaseModel):
    """
    Contested-vote agreement index, -1..+1: votes are weighted by how contested the roll
    call was and divided by the most the member could have reached.
    """

    # Shrunk toward 0 by shrink_k; None when the member had no contested scored votes
    score: Optional[float] = None
    # Same index without shrinkage
    unshrunk: Optional[float] = None
    # Total reachable weight (the denominator before shrinkage)
    weight: float = 0.0
    # Votes on contested roll calls (contest weight above 0)
    vote_count: int = 0
    shrink_k: Optional[float] = None
    within_party_z: Optional[float] = None
    # Set by create_axis_stats.py (axes only), which must be rerun after profiles are rebuilt
    chamber_z: Optional[float] = None


class CategoryStats(RankStats):
    score: float
    bills: list[str]
    bill_count: int
    agreement: Optional[AgreementStats] = None


class AxisCategoryStats(BaseModel):
    """An axis score restricted to bills in one primary category."""

    score: float
    bill_count: int
    agreement: Optional[AgreementStats] = None


class AxisStats(RankStats):
    score: float
    # Bills scored on this axis. For the computed institutional axis this counts roll calls.
    bill_count: int
    # Z-score among members of the same party, None when the group is too small
    within_party_z: Optional[float] = None
    # Z-score against current members of the same chamber (None if undefined).
    # Set by create_axis_stats.py, which must be rerun after profiles are rebuilt.
    chamber_z: Optional[float] = None
    # Only set for the computed institutional axis
    defection_rate: Optional[float] = None
    # Not set for the computed institutional axis
    agreement: Optional[AgreementStats] = None
    by_category: dict[str, AxisCategoryStats] = {}


class LegislatorProfile(BaseModel):
    member_id: str
    name: str
    party: str
    # Party the member is compared with: `party` for D/R, the caucus for independents
    # (Sanders and King: "D"). None if neither. Older profiles don't have it.
    caucus: Optional[str] = None
    state: str

    model: str
    schema_version: int
    axis_schema_version: int
    spec_hash: str

    vote_count: int

    # Whether the member is currently in office. Set by create_axis_stats.py.
    current: Optional[bool] = None

    primary_categories: dict[str, CategoryStats] = {}
    subcategories: dict[str, CategoryStats] = {}
    # Keyed by axis id from political_axes.json
    axes: dict[str, AxisStats] = {}
