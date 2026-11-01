"""Item-item co-occurrence: playlist×track matrix and top-k neighbours (cosine or PPMI)."""

from __future__ import annotations

import time
from typing import NamedTuple

import numpy as np
import pandas as pd
import psycopg
import scipy.sparse as sp

from swave.config import CACHE_DIR

METHODS = ("cosine", "pmi")
TRAIN_CACHE = CACHE_DIR / "train_matrix.npz"


class Cooc(NamedTuple):
    X: sp.csr_matrix  # playlists × tracks, binary
    Xt: sp.csr_matrix  # tracks × playlists
    n: np.ndarray  # playlists containing each track (training set)
    P: int  # number of training playlists


def playlist_matrix(pids: np.ndarray, tids: np.ndarray, n_tracks: int) -> sp.csr_matrix:
    """Binary playlists × tracks CSR matrix. Playlist ids are remapped to 0..P-1."""
    _, rows = np.unique(pids, return_inverse=True)
    n_rows = int(rows.max()) + 1 if len(rows) else 0
    X = sp.csr_matrix(
        (np.ones(len(tids), dtype=np.float32), (rows, tids)), shape=(n_rows, n_tracks)
    )
    X.data[:] = 1.0  # duplicates were summed; keep it binary
    return X


def from_matrix(X: sp.csr_matrix) -> Cooc:
    Xt = X.T.tocsr()
    return Cooc(X=X, Xt=Xt, n=np.diff(Xt.indptr).astype(np.float64), P=X.shape[0])


def load_training(conn: psycopg.Connection) -> Cooc:
    """Training matrix = every playlist except the eval hold-out. Cached in data/cache/."""
    if TRAIN_CACHE.exists():
        return from_matrix(sp.load_npz(TRAIN_CACHE).tocsr())

    t0 = time.time()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tsv = CACHE_DIR / "train_pairs.tsv"
    n_tracks = conn.execute("SELECT count(*) FROM tracks").fetchone()[0]
    with open(tsv, "wb") as f, conn.cursor().copy(
        """COPY (SELECT pt.pid, t.track_id FROM playlist_tracks pt JOIN tracks t USING (track_uri)
                 WHERE NOT EXISTS (SELECT 1 FROM eval_holdout h WHERE h.pid = pt.pid)) TO STDOUT"""
    ) as copy:
        for chunk in copy:
            f.write(chunk)
    pairs = pd.read_csv(tsv, sep="\t", header=None, names=["pid", "tid"], dtype=np.int32)
    tsv.unlink()
    X = playlist_matrix(pairs["pid"].to_numpy(), pairs["tid"].to_numpy(), n_tracks)
    sp.save_npz(TRAIN_CACHE, X, compressed=False)
    print(f"training matrix {X.shape[0]:,} playlists × {X.shape[1]:,} tracks, "
          f"{X.nnz:,} entries ({time.time() - t0:.0f}s)")
    return from_matrix(X)


def neighbours(
    c: Cooc, rows: np.ndarray, method: str, k: int, min_co: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Top-k neighbours of each track in `rows`.

    Returns flat arrays (src, nbr, sim, co) sorted by src order in `rows`, then sim descending.
    cosine = c_ij / sqrt(n_i n_j);  pmi = max(0, log(c_ij P / (n_i n_j)))  (positive PMI).
    """
    if method not in METHODS:
        raise ValueError(f"method must be one of {METHODS}")
    C = (c.Xt[rows] @ c.X).tocsr()
    r = np.repeat(np.arange(len(rows)), np.diff(C.indptr))
    src, nbr, co = rows[r], C.indices, C.data
    keep = (nbr != src) & (co >= min_co)
    r, src, nbr, co = r[keep], src[keep], nbr[keep], co[keep]

    ni, nj = c.n[src], c.n[nbr]
    if method == "cosine":
        sim = co / np.sqrt(ni * nj)
    else:
        sim = np.log(co * c.P / (ni * nj))
        pos = sim > 0
        r, src, nbr, co, sim = r[pos], src[pos], nbr[pos], co[pos], sim[pos]

    order = np.lexsort((-sim, r))
    r, src, nbr, co, sim = r[order], src[order], nbr[order], co[order], sim[order]
    rank = np.arange(len(r)) - np.searchsorted(r, r, side="left")
    top = rank < k
    return (
        src[top].astype(np.int32),
        nbr[top].astype(np.int32),
        sim[top].astype(np.float32),
        co[top].astype(np.int32),
    )


def chunks(
    c: Cooc, rows: np.ndarray | None = None, budget: int = 40_000_000
) -> list[np.ndarray]:
    """Split `rows` (default: all tracks, ascending) into chunks whose co-occurrence product
    stays under ~`budget` entries, so memory is bounded however popular the tracks are."""
    rows = np.arange(c.Xt.shape[0]) if rows is None else np.asarray(rows)
    playlist_len = np.diff(c.X.indptr).astype(np.float64)
    work = (c.Xt @ playlist_len)[rows]  # upper bound on output entries per track
    out, start, acc = [], 0, 0.0
    for i, w in enumerate(work):
        if acc + w > budget and i > start:
            out.append(rows[start:i])
            start, acc = i, 0.0
        acc += w
    if start < len(rows):
        out.append(rows[start:])
    return out
