"""Discogs style-consistency boost (`discogs_style`, CC0 data): rewards candidates whose artist
plays the same styles as the seeds' artists, so a one-off co-listening link (French chanson
under Arabic pop seeds) sinks below on-style candidates.

A BOOST scorer: it adds no candidates of its own; the ranker asks it to score the candidates
the other scorers found (`recommend.combine`). score(t) = cosine(style profile of t's artist,
summed style profile of the seed artists); artists without Discogs styles score 0.

`artist_styles(artist_key, style, n)` = per primary artist (`previews.loose_artist`), how many
of their Discogs masters carry each style (`make discogs-styles`, from the monthly CC0 dump).
"""

from __future__ import annotations

from typing import Callable

import numpy as np
import psycopg

from swave import previews
from swave.scorers import EMPTY, Candidates


class StyleBoost:
    boost = True

    def __init__(self, artists_of: Callable[[np.ndarray], dict[int, tuple[str, str]]],
                 styles_of: Callable[[list[str]], dict[str, dict[str, float]]],
                 name: str = "discogs_style"):
        self.name = name
        self.artists_of = artists_of  # track ids → {id: (artist key, display name)}
        self.styles_of = styles_of  # artist keys → {key: {style: count}}

    def candidates(self, seeds: np.ndarray, adventure: float | None) -> Candidates:
        return EMPTY  # boost only: see score_tracks

    def score_tracks(self, seeds: np.ndarray, tracks: np.ndarray) -> Candidates:
        if len(tracks) == 0:
            return EMPTY
        seed_art = {k for k, _ in self.artists_of(seeds).values() if k}
        cand_art = self.artists_of(tracks)
        prof = self.styles_of(sorted(seed_art | {k for k, _ in cand_art.values() if k}))
        seed_vec: dict[str, float] = {}
        for k in seed_art:
            v = prof.get(k, {})
            norm = np.sqrt(sum(x * x for x in v.values())) or 1.0
            for st, x in v.items():
                seed_vec[st] = seed_vec.get(st, 0.0) + x / norm
        s_norm = np.sqrt(sum(x * x for x in seed_vec.values()))
        if s_norm == 0:
            return EMPTY
        ids, raw = [], []
        for t in tracks.tolist():
            k = cand_art.get(t, ("", ""))[0]
            v = prof.get(k)
            if not v:
                continue
            dot = sum(seed_vec.get(st, 0.0) * x for st, x in v.items())
            if dot > 0:
                ids.append(t)
                raw.append(dot / (s_norm * np.sqrt(sum(x * x for x in v.values()))))
        if not ids:
            return EMPTY
        ids_a, raw_a = np.array(ids, np.int64), np.array(raw)
        o = np.argsort(ids_a)
        return Candidates(ids_a[o], raw_a[o], float(raw_a.max()), {})

    def reason(self, data: dict[str, float], n_seeds: int) -> str:
        return "same style as your seeds"


def db_lookups(conn: psycopg.Connection) -> StyleBoost:
    def artists_of(ids: np.ndarray) -> dict[int, tuple[str, str]]:
        tl = [int(i) for i in ids]
        rows = conn.execute("""SELECT track_id, artist FROM tracks WHERE track_id = ANY(%s)
                               UNION ALL SELECT track_id, artist FROM lb_tracks
                               WHERE track_id = ANY(%s)""", (tl, tl)).fetchall()
        return {t: (previews.loose_artist(a), a) for t, a in rows}

    def styles_of(keys: list[str]) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        if not keys:
            return out
        try:
            rows = conn.execute("SELECT artist_key, style, n FROM artist_styles WHERE artist_key = ANY(%s)",
                                (keys,)).fetchall()
        except psycopg.errors.UndefinedTable:
            conn.rollback()  # `make discogs-styles` not run yet: no boost
            return out
        for k, st, n in rows:
            out.setdefault(k, {})[st] = float(n)
        return out

    return StyleBoost(artists_of, styles_of)
