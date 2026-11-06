"""make discogs-styles: `artist_styles(artist_key, style, n)` from the Discogs monthly masters
dump (CC0, https://data.discogs.com/): per primary artist, how many of their masters carry each
style (genres when a master has no style). Only artists in the catalogue (`artist_tracks`, make
artist-index) are kept. The dump stays under data/discogs/ (gitignored, never committed).

    python -m swave.pipeline.discogs_styles data/discogs/discogs_YYYYMMDD_masters.xml.gz
"""

from __future__ import annotations

import argparse
import gzip
import re
import time
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from swave import previews
from swave.config import connect

DUP_SUFFIX = re.compile(r"\s+\(\d+\)$")  # Discogs disambiguates "Nova (2)", "Nova (3)"


def artist_key(name: str) -> str:
    return previews.loose_artist(DUP_SUFFIX.sub("", name or ""))


def parse(path: Path, wanted: set[str], t0: float) -> Counter:
    """(artist key, style) → number of masters, for the `wanted` artist keys."""
    counts: Counter = Counter()
    n = 0
    with gzip.open(path, "rb") as f:
        for _, el in ET.iterparse(f, events=("end",)):
            if el.tag != "master":
                continue
            n += 1
            styles = [s.text for s in el.findall("styles/style") if s.text] or \
                     [g.text for g in el.findall("genres/genre") if g.text]
            if styles:
                for a in el.findall("artists/artist"):
                    k = artist_key(a.findtext("name") or "")
                    if k in wanted:
                        for st in set(styles):
                            counts[(k, st)] += 1
            el.clear()
            if n % 500_000 == 0:
                print(f"  {n:,} masters, {len(counts):,} artist-style pairs ({time.time() - t0:.0f}s)",
                      flush=True)
    print(f"  {n:,} masters read", flush=True)
    return counts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dump", type=Path, help="discogs_YYYYMMDD_masters.xml.gz")
    args = ap.parse_args()
    t0 = time.time()
    with connect() as conn:
        wanted = {r[0] for r in conn.execute("SELECT DISTINCT artist_key FROM artist_tracks")}
        print(f"{len(wanted):,} catalogue artists", flush=True)
        counts = parse(args.dump, wanted, t0)
        conn.execute("""DROP TABLE IF EXISTS artist_styles_new;
            CREATE TABLE artist_styles_new (artist_key text NOT NULL, style text NOT NULL, n int NOT NULL)""")
        with conn.cursor().copy("COPY artist_styles_new FROM STDIN") as copy:
            for (k, st), c in counts.items():
                copy.write_row((k, st, c))
        conn.execute("""CREATE INDEX artist_styles_new_key ON artist_styles_new (artist_key);
            DROP TABLE IF EXISTS artist_styles;
            ALTER TABLE artist_styles_new RENAME TO artist_styles;
            ALTER INDEX artist_styles_new_key RENAME TO artist_styles_key;
            ANALYZE artist_styles;""")
        conn.commit()
        n_art = len({k for k, _ in counts})
    print(f"discogs-styles: {n_art:,} artists with styles, {len(counts):,} rows "
          f"in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
