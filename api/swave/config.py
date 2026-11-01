"""Paths and settings. Everything secret comes from environment variables (.env at the repo root)."""

import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

# Windows consoles default to cp1252; script output uses a few non-ASCII symbols.
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"  # derived artefacts (gitignored with data/)
EVAL_CSV = ROOT / "eval" / "results.csv"


def mpd_path() -> Path:
    path = Path(os.environ.get("MPD_PATH", "data/raw"))
    return path if path.is_absolute() else ROOT / path


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set. Copy .env.example to .env and fill it in.")
    return url


def connect() -> psycopg.Connection:
    return psycopg.connect(database_url())
