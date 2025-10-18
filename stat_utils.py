"""Helpful statistical utilities to be used in the future for calculating member ideologies"""

import numpy as np
from scipy.stats import norm
from scipy.special import expit


def consolidate_scores(model_scores, model_reliabilities=None):
    """
    Given model_scores and model_reliabilities:
    model_scores = {model_name: float}
    model_reliabilities = {model_name: reliability weight (0-1)}, optional
    Returns consolidated score.
    """

    scores = np.array(list(model_scores.values()))
    mean = np.mean(scores)
    std = np.std(scores)
    # Avoid divide by zero err
    if std == 0:
        return mean

    # Z-score based weights (less weight for outliers)
    z_scores = np.abs((scores - mean) / std)
    base_weights = 1 / (1 + z_scores)

    # Multiply by model reliability if provided
    if model_reliabilities:
        rel_weights = np.array([model_reliabilities.get(m, 1.0) for m in model_scores])
        weights = base_weights * rel_weights
    else:
        weights = base_weights

    weights /= weights.sum()
    return np.sum(scores * weights)


def bayesian_average(scores, variances):
    weights = [1 / v for v in variances]
    weights = np.array(weights) / np.sum(weights)
    return np.sum(np.array(scores) * weights)


def logistic_vote_likelihood(
    ideology, bill_partisan_score, bill_impact_score, beta_impact=0.5
):
    """
    Probability of a 'Yea' vote given ideology and bill features.
    Uses a logistic model: P(yea) = sigmoid(ideology * bill_partisan_score + beta * bill_impact_score)
    legislator ideology: -1 (liberal) → +1 (conservative)
    partisan_score: -1 (left-leaning bill) → +1 (right-leaning bill)
    impact_score: 0-1, magnitude of policy significance
    """
    return expit(ideology * bill_partisan_score + beta_impact * bill_impact_score)


def estimate_category_ideology(
    votes, bill_scores, impact_scores=None, prior_mean=0, prior_var=1
):
    """
    Estimate ideology for a given category (spectrum).
    votes: list of 0/1
    bill_scores: list of partisan scores for those bills
    impact_scores: optional list of 0-1 impact weights
    Returns (mean, variance, n_votes)
    """
    if len(votes) == 0:
        return prior_mean, prior_var, 0

    impact = (
        np.array(impact_scores) if impact_scores is not None else np.ones(len(votes))
    )
    weights = impact / impact.sum()

    # Weighted average ideology proxy
    mean_est = np.sum(weights * (2 * np.array(votes) - 1) * np.array(bill_scores))

    # Variance shrinks with more votes
    n = len(votes)
    # Heuristic shrinkage
    var_est = prior_var / (1 + n / 5)

    return mean_est, var_est, n


def ideology_confidence_interval(mean, var, confidence=0.95):
    """Compute confidence/credible interval from mean and variance."""
    z = norm.ppf(0.5 + confidence / 2)
    return mean - z * np.sqrt(var), mean + z * np.sqrt(var)


def update_ideology_bayesian(
    prior_mean, prior_var, votes, bills, beta_impact=0.5, noise_var=0.1
):
    """
    Bayesian update of ideology given observed votes.
    votes: list of {1 (yea), 0 (nay)}
    bills: list of (partisan_score, impact_score)
    Returns (posterior_mean, posterior_var)
    """
    posterior_mean, posterior_var = prior_mean, prior_var

    for v, (p_score, i_score) in zip(votes, bills):
        pred_prob = logistic_vote_likelihood(
            posterior_mean, p_score, i_score, beta_impact
        )
        error = v - pred_prob

        # Simple Kalman-style update
        gain = posterior_var / (posterior_var + noise_var)
        posterior_mean += gain * error * p_score
        posterior_var *= 1 - gain

    return posterior_mean, posterior_var


def weighted_correlation(x, y, weights=None):
    """Weighted correlation coefficient."""
    x, y = np.array(x), np.array(y)
    if weights is None:
        weights = np.ones_like(x)
    weights /= np.sum(weights)
    mx, my = np.sum(weights * x), np.sum(weights * y)
    cov_xy = np.sum(weights * (x - mx) * (y - my))
    var_x = np.sum(weights * (x - mx) ** 2)
    var_y = np.sum(weights * (y - my) ** 2)
    return cov_xy / np.sqrt(var_x * var_y)


def partisan_score_to_probability(score):
    """
    Convert partisan score (-1 to 1) to probability distribution for (Left, Right).
    """
    prob_right = (score + 1) / 2
    return {"left_prob": 1 - prob_right, "right_prob": prob_right}


def reliability_adjustment(past_errors, decay=0.9):
    """
    Compute reliability weights given a series of past errors.
    Lower error → higher reliability.
    Exponential decay gives more weight to recent performance.
    """
    errors = np.array(past_errors)
    time_weights = np.array([decay**i for i in range(len(errors) - 1, -1, -1)])
    weighted_error = np.sum(errors * time_weights) / np.sum(time_weights)
    # Bounded (0, 1)
    reliability = np.exp(-weighted_error)
    return reliability


def ideology_similarity(ideo_a, ideo_b):
    """Return similarity (1 - distance/2) for ideology values in [-1, 1]."""
    return 1 - abs(ideo_a - ideo_b) / 2


def sigmoid_transform(x, midpoint=0, steepness=1):
    """Flexible logistic scaling for bounded ideological or impact measures."""
    return expit(steepness * (x - midpoint))
