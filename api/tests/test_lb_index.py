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
    B = np.array([[1, 1, 0], [1, 1, 1], [0, 1, 1]], dtype=np.float32)
    w = np.array([1.0, 0.5, 0.5], dtype=np.float32)
    X = sp.csr_matrix(B * w[:, None])  # X carries the weights, Xt is sliced on demand
    Xt = lb_index.BinaryRowsOfT(X)
    assert (Xt[np.array([2, 0])].toarray() == B.T[[2, 0]]).all()  # binary rows of Xt, in order
    n = lb_index.column_sums(X, step=2)  # blocks of 2 rows
    assert n.tolist() == [1.5, 2.0, 1.0]
    c = cooc.Cooc(X=X, Xt=Xt, n=n, P=3)
    src, nbr, sim, co = cooc.neighbours(c, np.array([0]), "cosine", 10, 0.1)
    assert nbr.tolist() == [1, 2]
    assert sim[0] == pytest.approx(1.5 / np.sqrt(1.5 * 2.0))  # c01 = 1 + 0.5, n0 = 1.5, n1 = 2
    # work counts entries, not weights: track 1 is in sessions of length 2, 3, 2
    parts = lb_index.chunks(c, np.arange(3), budget=12)  # work 5, 7, 5
    assert [p.tolist() for p in parts] == [[0, 1], [2]]


def test_ingest_skips_files_below_the_free_disk_floor(tmp_path, monkeypatch):
    from collections import namedtuple

    from swave import lb
    from swave.pipeline import lb_ingest

    monkeypatch.setattr(lb_ingest, "LB_DIR", tmp_path)
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(lb_ingest.shutil, "disk_usage", lambda p: usage(100e9, 95e9, 5e9))
    m = lb.Member("dump/0042.parquet", 0, 1)
    assert lb_ingest.ingest_member("http://unused", m, 0, min_free_gb=20) == ("0042", -2)
    assert not (tmp_path / "listens" / "0042").exists()  # nothing written, so a rerun resumes it


def test_ingest_until_keeps_only_older_listens_and_skips_groups_by_stats(tmp_path, monkeypatch):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from swave import lb
    from swave.pipeline import lb_ingest

    old, new = lb_ingest.unix("2020-05-01"), lb_ingest.unix("2024-05-01")
    def group(ts, user):
        return pa.table({
            "user_id": pa.array([user, user], pa.int32()),
            "listened_at": pa.array([ts * 10**9, (ts + 60) * 10**9], pa.timestamp("ns")),
            "artist_name": ["Fake Artist", "Fake Artist"],
            "recording_name": ["Fake Song A", "Fake Song B"],
            "recording_mbid": [None, None]})
    f = tmp_path / "x.parquet"
    with pq.ParquetWriter(f, group(old, 1).schema) as w:
        w.write_table(group(old, 1))
        w.write_table(group(new, 2))
    pf = pq.ParquetFile(f)
    assert lb_ingest.group_range(pf, 1) == (new, new + 60)

    read_groups = []
    real = pq.ParquetFile.read_row_group
    monkeypatch.setattr(pq.ParquetFile, "read_row_group",
                        lambda self, g, **kw: (read_groups.append(g), real(self, g, **kw))[1])
    monkeypatch.setattr(lb, "Remote", lambda client, url, off, size: open(f, "rb"))
    monkeypatch.setattr(lb_ingest, "LB_DIR", tmp_path)
    m = lb.Member("listenbrainz-spark-dump-2759-20261101-000003-full/7.parquet", 0, 1)
    until = lb_ingest.unix("2023-11-01")
    assert lb_ingest.ingest_member("http://unused", m, 0, until=until) == ("2759-7", 2)
    assert read_groups == [0]  # the 2024 group was skipped from its statistics
    ts = np.load(tmp_path / "listens" / "2759-7" / "ts.npy")
    assert ts.max() < until
    # a file entirely after `until` writes nothing
    m2 = lb.Member("listenbrainz-spark-dump-2759-20261101-000003-full/8.parquet", 0, 1)
    assert lb_ingest.ingest_member("http://unused", m2, 0, until=old) == ("2759-8", 0)
    assert not (tmp_path / "listens" / "2759-8").exists()


def test_older_than_binary_searches_the_time_ordered_files():
    from swave import lb
    from swave.pipeline import lb_ingest

    # newest → oldest; file i's earliest listen is 100 * (9 - i)
    members = [lb.Member(f"d/{9 - i}.parquet", 0, 1) for i in range(10)]
    first = {m.name: 100 * int(m.name[2:-8]) for m in members}
    reads = []
    def min_ts(m):
        reads.append(m.name)
        return first[m.name]
    got = lb_ingest.older_than(members, 450, min_ts)
    assert [m.name for m in got] == ["d/4.parquet", "d/3.parquet", "d/2.parquet", "d/1.parquet", "d/0.parquet"]
    assert len(reads) <= 4  # log2(10), not one read per file


def test_time_split_uses_the_newest_listen_of_any_file(tmp_path):
    from swave.pipeline import lb_index

    dirs = []
    for name, top in (("961", 5_000_000), ("1511", 9_000_000), ("2759-12", 1_000_000)):
        d = tmp_path / name
        d.mkdir()
        np.save(d / "ts.npy", np.array([top - 10, top], np.int32))
        dirs.append(d)
    # the older dump's folder sorts last but holds the oldest listens
    assert lb_index.time_split(dirs, 1) == (9_000_000 - 86400, 9_000_000)
