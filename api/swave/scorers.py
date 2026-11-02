"""Scorers: each turns a seed set into candidate tracks with a 0–1 score and reason data.

The ranker (`recommend.rank`) combines scorers as a weighted sum (weights from scorers.json),
then applies the slider's popularity penalty and drops seeds/excluded tracks. A scorer knows
nothing about popularity, exclusions or the other scorers.
"""

from __future__ import annotations

from typing import Callable, NamedTuple, Protocol

import numpy as np


class Candidates(NamedTuple):
    track: np.ndarray  # unique track ids, ascending
    raw: np.ndarray  # the scorer's own score (≥ 0)
    top: float  # max of `raw`: score = raw / top is normalised to 0..1 (best candidate = 1)
    data: dict[str, np.ndarray]  # reason data, aligned with `track`

    @property
    def score(self) -> np.ndarray:
        return self.raw / self.top if self.top > 0 else self.raw


class Scorer(Protocol):
    name: str

    def candidates(self, seeds: np.ndarray, adventure: float | None) -> Candidates:
        """Candidates for these seed track ids. `adventure` (slider 0..1, None = raw alpha) is
        available to scorers that want it; the popularity penalty is applied by the ranker."""
        ...

    def reason(self, data: dict[str, float], n_seeds: int) -> str:
        """Human reason for one candidate, from its row of `Candidates.data`."""
        ...


# seed track ids (ascending) -> aligned (src, neighbour, sim, co_count) arrays
NeighbourLookup = Callable[[np.ndarray], tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]

EMPTY = Candidates(np.array([], dtype=np.int64), np.array([]), 0.0, {})


class Cooccurrence:
    """Item-item co-occurrence: score(t) = Σ_seeds sim(seed, t) over precomputed top-k lists."""

    def __init__(self, name: str, lookup: NeighbourLookup, unit: str = "shared playlists"):
        self.name = name
        self.lookup = lookup
        self.unit = unit

    def candidates(self, seeds: np.ndarray, adventure: float | None) -> Candidates:
        _, nbr, sim, co = self.lookup(seeds)
        if len(nbr) == 0:
            return EMPTY
        uniq, inv = np.unique(nbr, return_inverse=True)
        total = np.bincount(inv, weights=sim)
        data = {"seed_hits": np.bincount(inv).astype(np.int64),
                "co_count": np.bincount(inv, weights=co).astype(np.int64)}
        return Candidates(uniq, total, float(total.max()), data)

    def reason(self, data: dict[str, float], n_seeds: int) -> str:
        hits, co = int(data["seed_hits"]), int(data["co_count"])
        seeds = "your seed" if n_seeds == 1 else f"{hits} of your {n_seeds} seeds"
        return f"appears with {seeds} in {co:,} {self.unit}"


def from_db(conn) -> NeighbourLookup:  # noqa: ANN001 (psycopg connection)
    """Neighbour lists from the `neighbours` table (the API's path)."""

    def lookup(seeds: np.ndarray):
        rows = conn.execute(
            "SELECT track_id, neighbour_id, sim, co_count FROM neighbours WHERE track_id = ANY(%s)",
            (seeds.astype(np.int64).tolist(),),
        ).fetchall()
        a = np.array(rows, dtype=np.float64).reshape(-1, 4)
        return a[:, 0].astype(np.int64), a[:, 1].astype(np.int64), a[:, 2], a[:, 3]

    return lookup


def from_dict(nb: dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]]) -> NeighbourLookup:
    """Neighbour lists held in memory, {seed: (nbr, sim, co)} (the eval's path)."""

    def lookup(seeds: np.ndarray):
        found = [(int(s), nb[int(s)]) for s in seeds if int(s) in nb]
        if not found:
            e = np.array([], dtype=np.int64)
            return e, e, np.array([]), np.array([])
        src = np.concatenate([np.full(len(x[0]), s, dtype=np.int64) for s, x in found])
        lists = [x for _, x in found]
        return (src, np.concatenate([x[0] for x in lists]),
                np.concatenate([x[1] for x in lists]), np.concatenate([x[2] for x in lists]))

    return lookup


NAMES = ("cooccurrence_mpd",)
