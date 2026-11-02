"""Helpers for the contested-vote agreement index."""


def contest_weight(yea: int, nay: int) -> float:
    """How contested a roll call was: 0 when unanimous, 1 when tied, 0 with no votes."""
    total = yea + nay
    if total == 0:
        return 0.0
    return 1 - abs(yea - nay) / total
