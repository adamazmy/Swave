"""lb-names merge on synthetic per-file names (made-up songs, no dataset data)."""

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from swave.pipeline import lb_names


def write(path, rows):
    song, artist, title, mbid, plays = zip(*rows)
    pq.write_table(pa.table({"song": pa.array(song, pa.int64()), "artist": list(artist),
                             "title": list(title), "mbid": pa.array(mbid, pa.string()),
                             "plays": pa.array(plays, pa.int64())}), path)


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    for name, sub in (("NAMES", "names"), ("ALL", "names_all"), ("TMP", "names_tmp")):
        monkeypatch.setattr(lb_names, name, tmp_path / sub)
    monkeypatch.setattr(lb_names, "LB_DIR", tmp_path)
    monkeypatch.setattr(lb_names, "BATCH", 2)
    (tmp_path / "names").mkdir()
    return tmp_path


def table(d):
    t = pa.concat_tables(pq.read_table(f) for f in sorted((d / "names_all").glob("s*.parquet")))
    return {r["song"]: r for r in t.to_pylist()}


def test_reduce_picks_most_played_spelling_and_mbid_and_sums(dirs):
    write(dirs / "names/1.parquet", [(1, "Fake Band", "Song A", None, 5), (2, "Mock Duo", "B", "m2", 1)])
    write(dirs / "names/2.parquet", [(1, "FAKE BAND", "Song A (Live)", "m1", 3)])
    write(dirs / "names/3.parquet", [(1, "fake band", "song a", None, 7), (-5, "Neg", "Hash", None, 2)])
    songs, plays = lb_names.merge(0.0)
    assert (songs, plays) == (3, 18)
    rows = {r["song"]: r for f in sorted((dirs / "names_all.new").glob("s*.parquet")) for r in pq.read_table(f).to_pylist()}
    assert rows[1]["title"] == "song a" and rows[1]["plays"] == 15 and rows[1]["top"] == 7
    assert rows[1]["mbid"] == "m1" and rows[1]["mbid_top"] == 3  # the only spelling with an MBID
    assert rows[-5]["plays"] == 2  # negative 64-bit hashes shard fine


def test_main_verifies_then_deletes_inputs_and_merges_incrementally(dirs, monkeypatch):
    write(dirs / "names/1.parquet", [(1, "Fake Band", "Song A", None, 5)])
    write(dirs / "names/2.parquet", [(2, "Mock Duo", "B", "m2", 4)])
    monkeypatch.setattr("sys.argv", ["lb-names", "--delete-inputs"])
    lb_names.main()
    assert not list((dirs / "names").glob("*.parquet")) and len(table(dirs)) == 2
    # a later ingest adds a file: folded into the existing table
    write(dirs / "names/3.parquet", [(1, "Fake Band", "Song A (Remastered)", "m1", 9)])
    lb_names.main()
    rows = table(dirs)
    assert rows[1]["plays"] == 14 and rows[1]["title"] == "Song A (Remastered)" and rows[2]["plays"] == 4
