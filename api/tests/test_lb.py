"""ListenBrainz helpers on synthetic listens (made-up users, times and songs)."""

import numpy as np

from swave import lb

MIN = 60


def run(rows, **kw):
    user, ts, song = (np.array(c) for c in zip(*rows))
    s = lb.sessions(user, ts, song, **kw)
    return [s.song[s.sid == i].tolist() for i in range(int(s.sid.max()) + 1 if len(s.sid) else 0)], s


def test_gap_splits_sessions_and_short_ones_are_dropped():
    rows = [(1, 0, 10), (1, 5 * MIN, 11), (1, 10 * MIN, 12),        # session A: 3 songs
            (1, 50 * MIN, 13), (1, 55 * MIN, 14),                  # 40-min gap → B: 2 songs, dropped
            (2, 0, 10), (2, 29 * MIN, 11), (2, 58 * MIN, 12)]       # user 2: gaps < 30 min → 1 session
    got, s = run(rows)
    assert got == [[10, 11, 12], [10, 11, 12]]
    assert s.user.tolist() == [1, 2] and s.start.tolist() == [0, 0]


def test_repeats_collapse_and_order_follows_time():
    rows = [(1, 3 * MIN, 12), (1, 0, 10), (1, 1 * MIN, 11), (1, 2 * MIN, 10), (1, 4 * MIN, 11)]
    got, _ = run(rows)
    assert got == [[10, 11, 12]]


def test_repeats_do_not_count_towards_the_minimum():
    got, _ = run([(1, 0, 10), (1, MIN, 10), (1, 2 * MIN, 11), (1, 3 * MIN, 11)])
    assert got == []


def test_long_sessions_are_chunked():
    rows = [(7, i * MIN, 100 + i) for i in range(11)]
    got, s = run(rows, max_len=4, min_len=3)
    assert got == [[100, 101, 102, 103], [104, 105, 106, 107], [108, 109, 110]]
    assert s.start.tolist() == [0, 4 * MIN, 8 * MIN]
    got, _ = run(rows[:9], max_len=4, min_len=3)  # last chunk has 1 song → dropped
    assert got == [[100, 101, 102, 103], [104, 105, 106, 107]]


def test_empty():
    s = lb.sessions(np.array([]), np.array([]), np.array([]))
    assert len(s.sid) == 0 and len(s.user) == 0


def test_song_hash_collapses_versions_and_differs_between_songs():
    h = lb.song_hash("Static Bloom - 2011 Remaster", "The Fakes")
    assert h == lb.song_hash("Static Bloom (Live)", "the fakes")
    assert h != lb.song_hash("Static Bloom", "Other Band")
    assert -(2**63) <= h < 2**63


# ── remote tar walk (synthetic tar of parquet files served with HTTP ranges) ──

def _tar_of_parquets(tmp_path, sizes):
    import tarfile

    import pyarrow as pa
    import pyarrow.parquet as pq

    names = []
    for i, n in enumerate(sizes):
        t = pa.table({"user_id": list(range(n)), "recording_name": [f"song {j}" for j in range(n)]})
        pq.write_table(t, tmp_path / f"{i}.parquet")
        names.append(f"dump/{i}.parquet")
    (tmp_path / "COPYING").write_text("CC0")
    tar = tmp_path / "dump.tar"
    with tarfile.open(tar, "w", format=tarfile.USTAR_FORMAT) as tf:
        tf.add(tmp_path / "COPYING", "dump/COPYING")
        for i, name in enumerate(names):
            tf.add(tmp_path / f"{i}.parquet", name)
    return tar.read_bytes()


def _range_server(data: bytes):
    import httpx

    def handler(req):
        if req.method == "HEAD":
            return httpx.Response(200, headers={"content-length": str(len(data))})
        a, b = map(int, req.headers["range"].removeprefix("bytes=").split("-"))
        return httpx.Response(206, content=data[a:b + 1])

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_walk_back_lists_parquet_members_newest_first(tmp_path):
    import pyarrow.parquet as pq

    data = _tar_of_parquets(tmp_path, [5, 300, 40])
    c = _range_server(data)
    members = list(lb.walk_back(c, "http://dump/x.tar"))
    assert [m.name for m in members] == ["dump/2.parquet", "dump/1.parquet", "dump/0.parquet"]
    for m, n in zip(members, [40, 300, 5]):
        assert data[m.offset:m.offset + 4] == b"PAR1"
        t = pq.ParquetFile(lb.Remote(c, "http://dump/x.tar", m.offset, m.size)).read(
            columns=["user_id"])
        assert t.num_rows == n
    # resume after a member
    rest = list(lb.walk_back(c, "http://dump/x.tar", after=members[0]))
    assert [m.name for m in rest] == ["dump/1.parquet", "dump/0.parquet"]


def test_session_matrix_is_binary_and_keeps_row_order():
    from swave.pipeline.lb_build import session_matrix

    rows = np.array([0, 0, 0, 3, 3, 7])
    cols = np.array([5, 2, 5, 1, 4, 2])  # session 0 hits track 5 twice (two songs → one track)
    X = session_matrix(rows, cols, 8)
    assert X.shape == (3, 8)
    assert X.toarray().tolist() == [[0, 0, 1, 0, 0, 1, 0, 0], [0, 1, 0, 0, 1, 0, 0, 0],
                                    [0, 0, 1, 0, 0, 0, 0, 0]]


def test_build_matching_exact_loose_and_new():
    import pandas as pd

    from swave.pipeline.lb_build import match

    class Conn:
        def execute(self, *_):
            class R:
                def fetchall(self):
                    return [(0, "Static Bloom", "Mock Orchestra", 200),
                            (1, "Static Bloom - Live", "Mock Orchestra", 50),
                            (2, "Paper Lanterns Extended Mix", "The Placeholders", 30)]
            return R()

    titles = ["Static Bloom (2020 Remaster)", "Paper Lanterns Extended", "Brand New Song", None]
    artists = ["mock orchestra", "The Placeholders", "Mock Orchestra", None]
    names = pd.DataFrame({"song": [lb.song_hash(t or "", a or "") for t, a in zip(titles, artists)],
                          "title": titles, "artist": artists})
    tid, how = match(Conn(), names)
    assert tid.tolist() == [0, 2, -1, -1]  # most popular version wins; prefix ≥ 60% matches
    assert how.tolist() == [1, 2, 0, 0]
