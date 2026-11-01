"""Unit tests on SYNTHETIC data only (made-up playlists and track ids, never dataset samples)."""

from collections import Counter

import numpy as np
import pytest

from swave import cooc
from swave.pipeline.evaluate import metrics
from swave.pipeline.ingest import kept_tracks, slice_rows
from swave.recommend import MAX_ALPHA, alpha_for, score

# 6 made-up playlists over 5 tracks (ids 0..4). Track 0 is very popular; 1 and 2 always co-occur.
PLAYLISTS = {
    10: [0, 1, 2],
    11: [0, 1, 2],
    12: [0, 3],
    13: [0, 3, 4],
    14: [0, 1, 2, 4],
    15: [0],
}


@pytest.fixture
def c() -> cooc.Cooc:
    pids = np.array([p for p, ts in PLAYLISTS.items() for _ in ts])
    tids = np.array([t for ts in PLAYLISTS.values() for t in ts])
    return cooc.from_matrix(cooc.playlist_matrix(pids, tids, n_tracks=5))


def test_matrix_shape_and_popularity(c):
    assert c.X.shape == (6, 5)
    assert c.P == 6
    assert c.n.tolist() == [6, 3, 3, 2, 2]


def test_duplicate_rows_stay_binary():
    X = cooc.playlist_matrix(np.array([1, 1, 2]), np.array([0, 0, 1]), n_tracks=2)
    assert X.toarray().tolist() == [[1, 0], [0, 1]]


def test_cosine_neighbours(c):
    src, nbr, sim, co = cooc.neighbours(c, np.array([1]), "cosine", k=10, min_co=1)
    assert 1 not in nbr  # never its own neighbour
    assert nbr[0] == 2 and sim[0] == pytest.approx(1.0)  # 1 and 2 always together
    assert co[0] == 3
    assert np.all(np.diff(sim) <= 0)  # sorted best first


def test_topk_and_min_co(c):
    _, nbr, _, _ = cooc.neighbours(c, np.array([0]), "cosine", k=2, min_co=1)
    assert len(nbr) == 2
    _, nbr, _, co = cooc.neighbours(c, np.array([0]), "cosine", k=10, min_co=3)
    assert set(nbr.tolist()) == {1, 2} and co.min() >= 3


def test_pmi_is_positive_only(c):
    _, nbr, sim, _ = cooc.neighbours(c, np.array([3]), "pmi", k=10, min_co=1)
    assert np.all(sim > 0)
    assert 4 in nbr.tolist()  # 3 and 4 share a playlist far above chance


def test_neighbours_multiple_rows_grouped(c):
    src, _, _, _ = cooc.neighbours(c, np.array([1, 3]), "cosine", k=10, min_co=1)
    assert src.tolist() == sorted(src.tolist())
    assert set(src.tolist()) == {1, 3}


def test_chunks_cover_all_tracks(c):
    parts = cooc.chunks(c, budget=5)
    assert np.concatenate(parts).tolist() == list(range(5))


def test_score_excludes_seeds_and_sums_across_seeds():
    nbr = np.array([7, 8, 7, 1])
    sim = np.array([0.5, 0.4, 0.3, 0.9])
    co = np.array([10, 5, 4, 2])
    pop = np.array([100, 50, 100, 10])
    res = score(nbr, sim, co, pop, alpha=0.0, exclude=np.array([1]), n=10)
    assert res.track.tolist() == [7, 8]
    assert res.score[0] == pytest.approx(0.8)
    assert res.seed_hits.tolist() == [2, 1]
    assert res.co_count.tolist() == [14, 5]


def test_alpha_penalises_popular_tracks():
    nbr, sim, co = np.array([1, 2]), np.array([1.0, 0.6]), np.array([1, 1])
    pop = np.array([10_000, 10])
    assert score(nbr, sim, co, pop, 0.0, np.array([]), 2).track[0] == 1
    assert score(nbr, sim, co, pop, 0.5, np.array([]), 2).track[0] == 2


def test_alpha_for_spreads_the_slider_evenly():
    # 60 synthetic candidates: similarity falls slowly, popularity spans 10..100k.
    rng = np.random.default_rng(0)
    nbr = np.arange(60)
    pop = np.round(10 ** rng.uniform(1, 5, 60))
    sim = np.sort(rng.uniform(0.2, 0.6, 60))[::-1]
    co = np.ones(60)
    args = (nbr, sim, co, pop, np.array([]), 20)
    assert alpha_for(0.0, *args) == 0.0
    alphas = [alpha_for(a, *args) for a in (0.25, 0.5, 0.75, 1.0)]
    assert alphas == sorted(alphas) and 0 < alphas[0] and alphas[-1] <= MAX_ALPHA
    med = lambda a: np.median(score(nbr, sim, co, pop, a, np.array([]), 20).pop)  # noqa: E731
    lo, mid, hi = med(0.0), med(alpha_for(0.5, *args)), med(MAX_ALPHA)
    # halfway on the slider ≈ halfway in log-popularity between the two ends
    assert abs(np.log(mid) - (np.log(lo) + np.log(hi)) / 2) < 0.35 * (np.log(lo) - np.log(hi))


def test_alpha_for_flat_popularity():
    nbr, sim, co, pop = np.arange(5), np.ones(5), np.ones(5), np.full(5, 100.0)
    assert alpha_for(0.5, nbr, sim, co, pop, np.array([]), 3) == pytest.approx(0.5 * MAX_ALPHA)


def test_score_empty():
    e = np.array([])
    assert len(score(e, e, e, e, 0.5, e, 10).track) == 0


def test_metrics():
    assert metrics(np.array([1, 2, 3]), np.array([3, 9])) == (1.0, 0.5)
    assert metrics(np.array([1, 2]), np.array([9])) == (0.0, 0.0)


def test_slice_rows_dedupes_and_counts():
    fake = {"playlists": [
        {"pid": 1, "tracks": [
            {"track_uri": "spotify:track:fake001", "track_name": "A", "artist_name": "X", "album_name": "L"},
            {"track_uri": "spotify:track:fake001", "track_name": "A", "artist_name": "X", "album_name": "L"},
            {"track_uri": "spotify:track:fake002", "track_name": "B", "artist_name": "Y", "album_name": "M"},
        ]},
        {"pid": 2, "tracks": [
            {"track_uri": "spotify:track:fake001", "track_name": "A", "artist_name": "X", "album_name": "L"},
        ]},
    ]}
    counts, meta = Counter(), {}
    rows = slice_rows(fake, counts, meta)
    assert sorted(rows.splitlines()) == sorted([
        "1\tspotify:track:fake001", "1\tspotify:track:fake002", "2\tspotify:track:fake001"])
    assert counts == {"spotify:track:fake001": 2, "spotify:track:fake002": 1}
    assert meta["spotify:track:fake002"] == ("B", "Y", "M")


def test_kept_tracks_prunes_and_orders_by_popularity():
    counts = Counter({"a": 5, "b": 9, "c": 4, "d": 5})
    assert kept_tracks(counts, 5) == [("b", 9), ("a", 5), ("d", 5)]
