"""Scorers + ranker on synthetic candidates (made-up track ids, no dataset data)."""

import json

import numpy as np
import pytest

from swave import recommend as rk
from swave import scorers
from swave.recommend import MAX_ALPHA


def flat(nbr, sim, co, name="cooccurrence_mpd", unit="shared playlists"):
    """A co-occurrence scorer whose neighbour lists are fixed flat arrays (seeds ignored)."""
    nbr, sim, co = np.asarray(nbr), np.asarray(sim, float), np.asarray(co, float)
    return scorers.Cooccurrence(name, lambda seeds: (np.zeros(len(nbr), np.int64), nbr, sim, co),
                                unit)


def pops(values: dict[int, float]):
    return lambda t: np.array([values.get(int(x), 0) for x in t], dtype=float)


def rank(active, pop, alpha=0.0, exclude=(), n=10):
    c = rk.combine(active, np.array([0]), None, pops(pop))
    return c, rk.penalise(c, alpha, np.array(exclude), n)


def test_cooccurrence_sums_across_seeds_and_normalises():
    c = flat([7, 8, 7, 1], [0.5, 0.4, 0.3, 0.9], [10, 5, 4, 2]).candidates(np.array([0]), None)
    assert c.track.tolist() == [1, 7, 8]
    assert c.raw.tolist() == pytest.approx([0.9, 0.8, 0.4])
    assert c.score.tolist() == pytest.approx([1.0, 0.8 / 0.9, 0.4 / 0.9])
    assert c.data["seed_hits"].tolist() == [1, 2, 1]
    assert c.data["co_count"].tolist() == [2, 14, 5]


def test_rank_excludes_and_keeps_raw_scores_for_one_scorer():
    s = flat([7, 8, 7, 1], [0.5, 0.4, 0.3, 0.9], [10, 5, 4, 2])
    c, r = rank([(s, 1.0)], {7: 100, 8: 50, 1: 10}, exclude=[1])
    assert r.track.tolist() == [7, 8]
    assert r.score[0] == pytest.approx(0.8)  # single scorer: v1's raw sum, unscaled
    assert c.data["cooccurrence_mpd"]["seed_hits"][r.rows].tolist() == [2, 1]


def test_alpha_penalises_popular_tracks():
    s = flat([1, 2], [1.0, 0.6], [1, 1])
    pop = {1: 10_000, 2: 10}
    assert rank([(s, 1.0)], pop, alpha=0.0)[1].track[0] == 1
    assert rank([(s, 1.0)], pop, alpha=0.5)[1].track[0] == 2


def test_weighted_sum_uses_normalised_scores():
    # Scorer A's raw scale is 100x scorer B's: normalising makes the weights comparable.
    a = flat([1, 2], [100.0, 50.0], [1, 1], name="a")
    b = flat([2, 3], [1.0, 0.9], [1, 1], name="b")
    pop = {1: 1, 2: 1, 3: 1}
    c, r = rank([(a, 1.0), (b, 1.0)], pop)
    assert r.track.tolist() == [2, 1, 3]  # 0.5 + 1.0 > 1.0 > 0.9
    assert c.parts["a"][r.rows].tolist() == pytest.approx([0.5, 1.0, 0.0])
    assert c.parts["b"][r.rows].tolist() == pytest.approx([1.0, 0.0, 0.9])
    # weight b down: track 1 (a only) overtakes 2
    assert rank([(a, 1.0), (b, 0.2)], pop)[1].track.tolist() == [1, 2, 3]


def test_reasons_name_the_top_contributors():
    a = flat([1, 2], [1.0, 0.5], [40, 9], name="a", unit="shared playlists")
    b = flat([1, 3], [0.1, 1.0], [3, 7], name="b", unit="listening sessions")
    active = [(a, 1.0), (b, 1.0)]
    c, r = rank(active, {1: 1, 2: 1, 3: 1})
    why = dict(zip(r.track.tolist(), rk.reasons(active, c, r, 1)))
    assert why[1] == "appears with your seed in 40 shared playlists"  # b gives 0.1 < half of 1.0
    assert why[3] == "appears with your seed in 7 listening sessions"
    c, r = rank([(a, 1.0), (b, 5.0)], {1: 1, 2: 1, 3: 1})
    why = dict(zip(r.track.tolist(), rk.reasons([(a, 1.0), (b, 5.0)], c, r, 1)))
    assert why[1] == ("appears with your seed in 40 shared playlists · "
                      "appears with your seed in 3 listening sessions")


def test_alpha_for_spreads_the_slider_evenly():
    # 60 synthetic candidates: similarity falls slowly, popularity spans 10..100k.
    rng = np.random.default_rng(0)
    nbr = np.arange(1, 61)
    pop = dict(zip(nbr.tolist(), np.round(10 ** rng.uniform(1, 5, 60))))
    s = flat(nbr, np.sort(rng.uniform(0.2, 0.6, 60))[::-1], np.ones(60))
    c = rk.combine([(s, 1.0)], np.array([0]), None, pops(pop))
    none = np.array([])
    assert rk.alpha_for(0.0, c, none, 20) == 0.0
    alphas = [rk.alpha_for(a, c, none, 20) for a in (0.25, 0.5, 0.75, 1.0)]
    assert alphas == sorted(alphas) and 0 < alphas[0] and alphas[-1] <= MAX_ALPHA
    med = lambda a: np.median(rk.penalise(c, a, none, 20).pop)  # noqa: E731
    lo, mid, hi = med(0.0), med(rk.alpha_for(0.5, c, none, 20)), med(MAX_ALPHA)
    # halfway on the slider ≈ halfway in log-popularity between the two ends
    assert abs(np.log(mid) - (np.log(lo) + np.log(hi)) / 2) < 0.35 * (np.log(lo) - np.log(hi))


def test_alpha_for_flat_popularity():
    s = flat(np.arange(1, 6), np.ones(5), np.ones(5))
    c = rk.combine([(s, 1.0)], np.array([0]), None, pops({i: 100 for i in range(1, 6)}))
    assert rk.alpha_for(0.5, c, np.array([]), 3) == pytest.approx(0.5 * MAX_ALPHA)


def test_empty_candidates():
    s = flat([], [], [])
    c, r = rank([(s, 1.0)], {}, alpha=0.5)
    assert len(r.track) == 0 and rk.alpha_for(0.5, c, np.array([]), 10) == 0.0


def test_from_dict_lookup_skips_unknown_seeds():
    nb = {5: (np.array([1, 2]), np.array([0.5, 0.4]), np.array([3, 2])),
          9: (np.array([2]), np.array([0.7]), np.array([4]))}
    src, nbr, sim, co = scorers.from_dict(nb)(np.array([9, 4, 5]))
    assert src.tolist() == [9, 5, 5] and nbr.tolist() == [2, 1, 2]
    assert sim.tolist() == [0.7, 0.5, 0.4] and co.tolist() == [4, 3, 2]


def test_load_weights(tmp_path):
    cfg = tmp_path / "w.json"
    cfg.write_text(json.dumps({"weights": {"cooccurrence_mpd": 1}}))
    assert rk.load_weights(cfg) == {"cooccurrence_mpd": 1.0}
    assert rk.weights_label({"b": 0.25, "a": 1.0}) == "a=1 b=0.25"
    for bad in ({"weights": {"nope": 1}}, {"weights": {"cooccurrence_mpd": -1}},
                {"weights": {"cooccurrence_mpd": 0}}, {"weights": {}}, {}):
        cfg.write_text(json.dumps(bad))
        with pytest.raises(ValueError):
            rk.load_weights(cfg)


def test_repo_config_is_valid():
    assert rk.load_weights(rk.weights_path())  # loads, known scorers, at least one weight > 0


def test_cap_artists_keeps_best_per_artist_and_counts_the_list():
    r = rk.Ranked(np.array([10, 11, 12, 13, 14]), np.array([5.0, 4, 3, 2, 1]), np.ones(5),
                  np.arange(5))
    artist = {10: "a", 11: "a", 12: "a", 13: "b"}  # 14: unknown artist, never capped
    got = rk.cap_artists(r, artist, 2, 4)
    assert got.track.tolist() == [10, 11, 13, 14] and got.rows.tolist() == [0, 1, 3, 4]
    assert rk.cap_artists(r, artist, 2, 2).track.tolist() == [10, 11]
    assert rk.cap_artists(r, artist, 2, 3, {"a": 2, "b": 2}).track.tolist() == [14, 10, 11]
    # soft cap: too few other candidates, so the over-cap ones fill the end, best first
    assert rk.cap_artists(r, artist, 2, 10).track.tolist() == [10, 11, 13, 14, 12]
    assert rk.cap_artists(r, artist, 1, 5).track.tolist() == [10, 13, 14, 11, 12]
