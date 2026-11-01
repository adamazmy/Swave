"""Scoring a seed set from neighbour lists. Shared by the API and the offline eval."""

from __future__ import annotations

from typing import NamedTuple

import numpy as np


MAX_ALPHA = 2.0  # beyond this the top-200 neighbour pool is exhausted (measured 2026-11-01)


class Scored(NamedTuple):
    track: np.ndarray  # track ids, best first
    score: np.ndarray
    seed_hits: np.ndarray  # how many seeds list this track as a neighbour
    co_count: np.ndarray  # summed co-occurrence counts with those seeds
    pop: np.ndarray  # playlist_count of each track


def score(
    nbr: np.ndarray,
    sim: np.ndarray,
    co: np.ndarray,
    pop: np.ndarray,
    alpha: float,
    exclude: np.ndarray,
    n: int,
) -> Scored:
    """Score = Σ_seeds sim(seed, t) / pop(t)^alpha, seeds/excluded tracks removed, top n.

    `nbr`, `sim`, `co`, `pop` are aligned flat arrays: the concatenated neighbour lists of every
    seed, with `pop` = playlist_count of each neighbour.
    """
    if len(nbr) == 0:
        empty = np.array([], dtype=np.int64)
        return Scored(empty, np.array([]), empty, empty, np.array([]))
    uniq, inv = np.unique(nbr, return_inverse=True)
    total = np.bincount(inv, weights=sim)
    hits = np.bincount(inv)
    co_sum = np.bincount(inv, weights=co)
    p = np.empty(len(uniq))
    p[inv] = pop
    total = total / np.maximum(p, 1.0) ** alpha

    allowed = ~np.isin(uniq, exclude)
    idx = np.flatnonzero(allowed)
    idx = idx[np.argsort(-total[idx], kind="stable")[:n]]
    return Scored(uniq[idx], total[idx], hits[idx], co_sum[idx].astype(np.int64), p[idx])


def alpha_for(
    adventure: float,
    nbr: np.ndarray,
    sim: np.ndarray,
    co: np.ndarray,
    pop: np.ndarray,
    exclude: np.ndarray,
    n: int,
    iters: int = 12,
) -> float:
    """Alpha that makes the slider feel even for every seed set.

    `adventure` 0..1 = how far the median popularity of the top n moves, on a log scale, from
    the familiar end (alpha 0) to the most adventurous this seed set allows (alpha MAX_ALPHA).
    A fixed slider→alpha mapping doesn't work: popular seed sets barely change until alpha ~1,
    mid-popularity sets collapse into ultra-rare tracks by 0.5.
    """
    if adventure <= 0 or len(nbr) == 0:
        return 0.0

    def median_pop(alpha: float) -> float:
        r = score(nbr, sim, co, pop, alpha, exclude, n)
        return float(np.median(r.pop)) if len(r.pop) else 1.0

    lo_med, hi_med = median_pop(0.0), median_pop(MAX_ALPHA)
    if hi_med >= lo_med:  # nothing to trade: popularity is flat across candidates
        return adventure * MAX_ALPHA
    target = np.exp(np.log(lo_med) + adventure * (np.log(hi_med) - np.log(lo_med)))
    lo, hi = 0.0, MAX_ALPHA
    for _ in range(iters):  # median popularity falls (roughly monotonically) as alpha grows
        mid = (lo + hi) / 2
        if median_pop(mid) > target:
            lo = mid
        else:
            hi = mid
    return round(hi, 4)
