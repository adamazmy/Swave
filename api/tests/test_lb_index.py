"""lb-index build steps on synthetic listens (made-up users and song hashes, no dataset data)."""

import argparse

import numpy as np
import pytest

from swave import cooc
from swave.pipeline import lb_index

H = 3600
CUTOFF = 100 * 24 * H


def args(**kw):
    base = {"max_len": 50, "user_cap": 0, "half_life": 0, "sample": 1.0}
    return argparse.Namespace(**{**base, **kw})


def listens(rows):
    user, ts, song = (np.array(c, dtype=np.int64) for c in zip(*rows))
    return {"user": user.astype(np.int32), "ts": ts.astype(np.int32), "song": song}


@pytest.fixture
def bucket(monkeypatch):
    data = {}
    monkeypatch.setattr(lb_index, "load_bucket", lambda dirs, b, cols=None: data)
    return data


def session(user, start, songs, gap=60):
    return [(user, start + i * gap, s) for i, s in enumerate(songs)]


def test_training_rows_respect_cutoff_vocab_and_min_length(bucket):
    bucket.update(listens(
        session(1, 0, [10, 11, 12, 99])          # 99 is outside the vocabulary
        + session(1, 10 * H, [10, 11])           # 2 songs: dropped
        + session(2, CUTOFF + H, [10, 11, 12])   # after the cutoff: held out, never trained on
    ))
    vocab = np.array([10, 11, 12])
    tid = np.array([0, 1, 1], dtype=np.int32)    # 11 and 12 map to one MPD track
    lens, t, w = lb_index.bucket_rows([], 0, CUTOFF, vocab, tid, args())
    assert lens.tolist() == [2] and sorted(t.tolist()) == [0, 1] and w.tolist() == [1.0]


def test_long_sessions_are_cut_and_heavy_users_capped(bucket):
    rows = session(1, 0, list(range(10, 22)))     # 12 songs → chunks of 5, 5, 2 (2 dropped)
    for i in range(4):                            # user 2: 4 sessions
        rows += session(2, (i + 1) * 10 * H, [10, 11, 12])
    bucket.update(listens(rows))
    vocab = np.arange(10, 22)
    tid = np.arange(12, dtype=np.int32)
    lens, _, w = lb_index.bucket_rows([], 0, CUTOFF, vocab, tid, args(max_len=5, user_cap=2))
    assert sorted(lens.tolist()) == [3, 3, 3, 3, 5, 5]
    assert sorted(w.tolist()) == [0.5, 0.5, 0.5, 0.5, 1.0, 1.0]  # user 2: cap 2 / 4 sessions


def test_recency_halves_weight_per_half_life(bucket):
    year = lb_index.YEAR
    bucket.update(listens(session(1, int(CUTOFF - year), [10, 11, 12])
                          + session(2, CUTOFF - H, [10, 11, 12])))
    _, _, w = lb_index.bucket_rows([], 0, CUTOFF, np.array([10, 11, 12]),
                                   np.arange(3, dtype=np.int32), args(half_life=1))
    assert sorted(np.round(w, 3).tolist()) == [0.5, 1.0]


def test_weighted_cooccurrence_and_binary_chunk_bound():
    import scipy.sparse as sp

    # 3 sessions over 3 tracks, weights 1, 0.5, 0.5
    X = sp.csr_matrix(np.array([[1, 1, 0], [1, 1, 1], [0, 1, 1]], dtype=np.float32))
    w = np.array([1.0, 0.5, 0.5], dtype=np.float32)
    Xt = sp.diags(w).dot(X).T.tocsr()
    c = cooc.Cooc(X=X, Xt=Xt, n=np.asarray(Xt.sum(axis=1)).ravel(), P=3)
    src, nbr, sim, co = cooc.neighbours(c, np.array([0]), "cosine", 10, 0.1)
    assert nbr.tolist() == [1, 2]
    assert sim[0] == pytest.approx(1.5 / np.sqrt(1.5 * 2.0))  # c01 = 1 + 0.5, n0 = 1.5, n1 = 2
    # work counts entries, not weights: track 1 is in sessions of length 2, 3, 2
    parts = lb_index.chunks(c, np.arange(3), budget=12)  # work 5, 7, 5
    assert [p.tolist() for p in parts] == [[0, 1], [2]]
