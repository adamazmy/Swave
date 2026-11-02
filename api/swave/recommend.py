"""Ranking a seed set: weighted sum of scorers → popularity penalty → top n. Shared by API and eval.

    final(t) = Σ_i w_i · score_i(t)  /  playlist_count(t)^alpha        (seeds/excluded removed)

Each scorer's score is normalised to 0..1 (raw / its top), so weights are comparable across
scorers. To rank, the sum is rescaled so the first scorer keeps its raw values (factor x/x = 1.0
exactly): the order is the same as the normalised sum, and with a single scorer it is bit-for-bit
the v1 order (dividing by the top would merge scores 1 ulp apart into ties).
Weights come from a JSON config (`scorers.json` at the repo root, or $SWAVE_SCORERS).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable, NamedTuple

import numpy as np

from swave.config import ROOT
from swave.scorers import NAMES, Scorer

MAX_ALPHA = 2.0  # beyond this the top-200 neighbour pool is exhausted (measured 2026-11-01)
REASON_SHARE = 0.5  # a scorer is named in the reason if it gives ≥ half the top contribution
MAX_REASONS = 2

PopLookup = Callable[[np.ndarray], np.ndarray]  # track ids -> playlist_count


def weights_path() -> Path:
    return Path(os.environ.get("SWAVE_SCORERS") or ROOT / "scorers.json")


def load_weights(path: Path | str | None = None) -> dict[str, float]:
    """{scorer name: weight} from the config; zero weights dropped. Raises ValueError if bad."""
    path = Path(path) if path else weights_path()
    raw = json.loads(path.read_text(encoding="utf-8")).get("weights")
    if not isinstance(raw, dict) or not raw:
        raise ValueError(f"{path}: expected {{\"weights\": {{\"<scorer>\": <weight>, ...}}}}")
    unknown = set(raw) - set(NAMES)
    if unknown:
        raise ValueError(f"{path}: unknown scorer(s) {sorted(unknown)}; known: {list(NAMES)}")
    if any(not isinstance(w, (int, float)) or w < 0 for w in raw.values()):
        raise ValueError(f"{path}: weights must be numbers ≥ 0")
    weights = {k: float(v) for k, v in raw.items() if v > 0}
    if not weights:
        raise ValueError(f"{path}: at least one weight must be > 0")
    return weights


def weights_label(weights: dict[str, float]) -> str:
    """Compact, stable form for eval/results.csv, e.g. 'cooccurrence_mpd=1'."""
    return " ".join(f"{k}={v:g}" for k, v in sorted(weights.items()))


class Combined(NamedTuple):
    """All candidates of a seed set, before the popularity penalty."""

    track: np.ndarray  # unique track ids, ascending
    base: np.ndarray  # Σ w_i · score_i, times a positive per-request constant (see module doc)
    pop: np.ndarray  # playlist_count of each candidate
    parts: dict[str, np.ndarray]  # weighted contribution per scorer (0 where absent)
    data: dict[str, dict[str, np.ndarray]]  # each scorer's reason data (0 where absent)


class Ranked(NamedTuple):
    track: np.ndarray  # best first
    score: np.ndarray  # final score
    pop: np.ndarray
    rows: np.ndarray  # index of each result in Combined (for parts/data lookup)


def combine(scorers: list[tuple[Scorer, float]], seeds: np.ndarray, adventure: float | None,
            pop_of: PopLookup) -> Combined:
    got = [(s, w, s.candidates(seeds, adventure)) for s, w in scorers]
    tracks = [c.track for _, _, c in got if len(c.track)]
    track = np.unique(np.concatenate(tracks)) if tracks else np.array([], dtype=np.int64)
    base = np.zeros(len(track))
    parts: dict[str, np.ndarray] = {}
    data: dict[str, dict[str, np.ndarray]] = {}
    ref = next((w / c.top for _, w, c in got if c.top > 0), 1.0)
    for s, w, c in got:
        at = np.searchsorted(track, c.track)
        if c.top > 0:
            base[at] += (w / c.top) / ref * c.raw
        part = np.zeros(len(track))
        part[at] = w * c.score
        parts[s.name] = part
        data[s.name] = {}
        for key, vals in c.data.items():
            full = np.zeros(len(track), dtype=vals.dtype)
            full[at] = vals
            data[s.name][key] = full
    return Combined(track, base, pop_of(track).astype(np.float64), parts, data)


def penalise(c: Combined, alpha: float, exclude: np.ndarray, n: int) -> Ranked:
    """Apply the popularity penalty, drop excluded tracks, keep the top n (stable by track id)."""
    total = c.base / np.maximum(c.pop, 1.0) ** alpha
    idx = np.flatnonzero(~np.isin(c.track, exclude))
    idx = idx[np.argsort(-total[idx], kind="stable")[:n]]
    return Ranked(c.track[idx], total[idx], c.pop[idx], idx)


def alpha_for(adventure: float, c: Combined, exclude: np.ndarray, n: int, iters: int = 12) -> float:
    """Alpha that makes the slider feel even for every seed set.

    `adventure` 0..1 = how far the median popularity of the top n moves, on a log scale, from
    the familiar end (alpha 0) to the most adventurous this seed set allows (alpha MAX_ALPHA).
    A fixed slider→alpha mapping doesn't work: popular seed sets barely change until alpha ~1,
    mid-popularity sets collapse into ultra-rare tracks by 0.5.
    """
    if adventure <= 0 or len(c.track) == 0:
        return 0.0

    def median_pop(alpha: float) -> float:
        r = penalise(c, alpha, exclude, n)
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


def reasons(scorers: list[tuple[Scorer, float]], c: Combined, r: Ranked, n_seeds: int) -> list[str]:
    """Reason line per result: the top contributing scorer(s), e.g. 'a · b'."""
    by_name = {s.name: s for s, _ in scorers}
    out = []
    for row in r.rows:
        contrib = sorted(((c.parts[k][row], k) for k in c.parts if c.parts[k][row] > 0),
                         reverse=True)
        top = [k for v, k in contrib if v >= REASON_SHARE * contrib[0][0]][:MAX_REASONS]
        out.append(" · ".join(
            by_name[k].reason({f: v[row] for f, v in c.data[k].items()}, n_seeds) for k in top))
    return out
