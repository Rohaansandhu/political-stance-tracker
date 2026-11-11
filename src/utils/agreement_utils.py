"""
Helpers for the contested-vote agreement index.

Each scored vote adds `contest * weighted_score` to the numerator and `contest * |weighted_score|`
(the most the member could have reached on that vote) to the denominator, where `contest` is
how contested the roll call was. The index is num / den, -1..+1, shrunk toward 0 by adding
`shrink_k` to the denominator: 5 typical per-vote weights (the median over every member's
contested votes on that score, in one run).
"""

from statistics import median

# How many typical votes' worth of weight the index is shrunk by
SHRINK_VOTES = 5

SCORE_DECIMALS = 3
WEIGHT_DECIMALS = 4


def contest_weight(yea: int, nay: int) -> float:
    """How contested a roll call was: 0 when unanimous, 1 when tied, 0 with no votes."""
    total = yea + nay
    if total == 0:
        return 0.0
    return 1 - abs(yea - nay) / total


class AgreementAccumulator:
    """Running sums for one score. Votes with no contest weight, or a weight of 0, are skipped."""

    def __init__(self):
        self.num = 0.0
        self.den = 0.0
        self.vote_count = 0
        self.weights = []

    def add(self, contest, weighted_score):
        if not contest or contest <= 0:
            return
        weight = contest * abs(weighted_score)
        self.num += contest * weighted_score
        self.den += weight
        self.vote_count += 1
        self.weights.append(weight)

    def as_sums(self):
        return {
            "num": self.num,
            "den": self.den,
            "vote_count": self.vote_count,
            "weights": self.weights,
        }


def compute_shrink_k(weights):
    """SHRINK_VOTES times the median per-vote weight; None when there are no weights."""
    if not weights:
        return None
    return SHRINK_VOTES * median(weights)


def finalize(num, den, vote_count, shrink_k):
    """The stored `agreement` block (without within_party_z / chamber_z)."""
    k = shrink_k or 0.0
    unshrunk = num / den if den > 0 else None
    score = num / (den + k) if den > 0 else None
    return {
        "score": None if score is None else round(score, SCORE_DECIMALS),
        "unshrunk": None if unshrunk is None else round(unshrunk, SCORE_DECIMALS),
        "weight": round(den, WEIGHT_DECIMALS),
        "vote_count": vote_count,
        "shrink_k": None if shrink_k is None else round(shrink_k, WEIGHT_DECIMALS),
    }
