from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel

from schema.bill_analyses import AxisScore


class PartyTally(BaseModel):
    yea: int
    nay: int
    # Everyone who didn't cast Yea or Nay, including "Present"
    not_voting: int


class HwyvCard(BaseModel):
    """One final-passage roll call in the `hwyv_cards` collection, keyed by (spec_hash, vote_id)."""

    spec_hash: str
    vote_id: str
    bill_id: str
    chamber: str
    congress: int
    date: str
    short_title: str
    popular_title: Optional[str] = None
    brief: str
    key_provisions: List[str]
    needs_two_thirds: bool
    omnibus: bool
    contest: float
    cross: float
    featured: bool
    primary_categories: List[str]
    # reveal-only
    result: str
    # The roll call's vote threshold ("1/2", "2/3", "3/5")
    requires: Optional[str] = None
    tally: Dict[str, PartyTally]
    d_majority_yea: bool
    r_majority_yea: bool
    sponsor: Optional[str] = None
    sponsor_party: Optional[str] = None
    source_url: Optional[str] = None
    # Category -> partisan_score * impact_score, the left-right push of a Yea. Same scale as
    # member scores, and categories with a partisan_score of 0 are left out, as they are there.
    left_right: Dict[str, float]
    axes: List[AxisScore]
    model: str
    schema_version: int
    axis_schema_version: int
    last_modified: datetime


# What a user may see before voting. The website's API keeps its own copy of this list
# (backend/lib/hwyvCards.js).
BRIEF_FIELDS = (
    "vote_id",
    "bill_id",
    "chamber",
    "congress",
    "date",
    "short_title",
    "popular_title",
    "brief",
    "key_provisions",
    "needs_two_thirds",
    "omnibus",
    "primary_categories",
    "featured",
)


def brief_fields(card: dict) -> dict:
    """The part of a card that is safe to show before the user votes."""
    return {field: card.get(field) for field in BRIEF_FIELDS}
