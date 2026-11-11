import pytest

from utils.agreement_utils import (
    AgreementAccumulator,
    compute_shrink_k,
    contest_weight,
    finalize,
)


def test_contest_weight():
    assert contest_weight(0, 0) == 0.0
    assert contest_weight(10, 10) == 1.0
    assert contest_weight(400, 0) == 0.0
    assert contest_weight(300, 100) == pytest.approx(0.5)


def test_accumulator_skips_uncontested_and_missing():
    acc = AgreementAccumulator()
    acc.add(None, 0.5)
    acc.add(0.0, 0.5)
    acc.add(0.5, 0.4)
    acc.add(1.0, -0.2)
    sums = acc.as_sums()
    assert sums["vote_count"] == 2
    assert sums["num"] == pytest.approx(0.5 * 0.4 - 0.2)
    assert sums["den"] == pytest.approx(0.5 * 0.4 + 0.2)
    assert sums["weights"] == pytest.approx([0.2, 0.2])


def test_shrink_k_is_five_median_weights():
    assert compute_shrink_k([]) is None
    assert compute_shrink_k([0.1, 0.2, 0.9]) == pytest.approx(1.0)


def test_finalize_rounds_and_shrinks():
    block = finalize(-150.68, 204.69998, 1138, 0.49964)
    assert block == {
        "score": -0.734,
        "unshrunk": -0.736,
        "weight": 204.7,
        "vote_count": 1138,
        "shrink_k": 0.4996,
    }


def test_finalize_without_votes():
    block = finalize(0.0, 0.0, 0, None)
    assert block["score"] is None and block["unshrunk"] is None
    assert block["weight"] == 0.0
