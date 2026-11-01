"""Scoring a seed set from neighbour lists. Shared by the API and the offline eval."""

from __future__ import annotations

from typing import NamedTuple

import numpy as np


class Scored(NamedTuple):
    track: np.ndarray  # track ids, best first
    score: np.ndarray
    seed_hits: np.ndarray  # how many seeds list this track as a neighbour
    co_count: np.ndarray  # summed co-occurrence counts with those seeds


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
        return Scored(empty, np.array([]), empty, empty)
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
    return Scored(uniq[idx], total[idx], hits[idx], co_sum[idx].astype(np.int64))
