from typing import Optional
from pydantic import BaseModel

class CategoryStats(BaseModel):
    score: float
    bills: list[str]
    bill_count: int

    rank: Optional[int] = None
    percentile_rank: Optional[float] = None
    total_members: Optional[int] = None
    total_current_members: Optional[int] = None
    current_rank: Optional[int] = None
    current_percentile_rank: Optional[float] = None


class LegislatorProfile(BaseModel):
    member_id: str
    name: str
    party: str
    state: str

    model: str
    schema_version: int
    spec_hash: str

    vote_count: int

    primary_categories: dict[str, CategoryStats] = {}
    subcategories: dict[str, CategoryStats] = {}

    # Removed in v3
    # main_categories: dict[str, CategoryStats]
    # secondary_categories: Dict[str, CategoryStats]
    # detailed_spectrums: Dict[str, CategoryStats]
