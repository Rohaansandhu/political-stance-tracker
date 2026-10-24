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


class CategoryStats(RankStats):
    score: float
    bills: list[str]
    bill_count: int


class AxisCategoryStats(BaseModel):
    """An axis score restricted to bills in one primary category."""

    score: float
    bill_count: int


class AxisStats(RankStats):
    score: float
    # Bills scored on this axis. For the computed institutional axis this counts roll calls.
    bill_count: int
    # Z-score among members of the same party, None when the group is too small
    within_party_z: Optional[float] = None
    # Only set for the computed institutional axis
    defection_rate: Optional[float] = None
    by_category: dict[str, AxisCategoryStats] = {}


class LegislatorProfile(BaseModel):
    member_id: str
    name: str
    party: str
    state: str

    model: str
    schema_version: int
    axis_schema_version: int
    spec_hash: str

    vote_count: int

    primary_categories: dict[str, CategoryStats] = {}
    subcategories: dict[str, CategoryStats] = {}
    # Keyed by axis id from political_axes.json
    axes: dict[str, AxisStats] = {}
