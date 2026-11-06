"""Licence of every data source the app can load, and the public-mode gate.

SWAVE_MODE=personal (default): everything may load. SWAVE_MODE=public: the API refuses to start
if any enabled component is `non_commercial` (a scorer with weight > 0, a preview source in
SWAVE_PREVIEW_SOURCES). `unreviewed` components are allowed but logged at startup: their terms
haven't been checked for commercial use yet.

Known gap for a public release: the catalogue itself (`tracks`: ids, Spotify URIs, playlist
counts) and the ids the ListenBrainz index matched to it come from the MPD. Turning the MPD
scorer off is not enough: the catalogue must be rebuilt from ListenBrainz/MusicBrainz first.

Public mode media (owner, 2026-11-06): covers and previews will come from Apple (the iTunes
Search API now, MusicKit later), not Spotify (oEmbed covers) or Deezer (previews).
"""

from __future__ import annotations

import logging
import os
from typing import Literal

log = logging.getLogger(__name__)

Licence = Literal["open", "non_commercial", "unreviewed"]

COMPONENTS: dict[str, tuple[Licence, str]] = {
    "scorer:cooccurrence_mpd": ("non_commercial", "Spotify Million Playlist Dataset: non-commercial research only"),
    "scorer:cooccurrence_lb": ("open", "ListenBrainz listens: CC0"),
    "scorer:related_artists": ("non_commercial", "Deezer API terms: non-commercial use"),
    "scorer:lb_similar_artists": ("open", "ListenBrainz similar artists: CC0"),
    "scorer:discogs_style": ("open", "Discogs monthly data dumps: CC0"),
    "previews:deezer": ("non_commercial", "Deezer API terms: non-commercial use"),
    "previews:itunes": ("unreviewed", "iTunes Search API: Apple's terms not reviewed for commercial use"),
    "covers:spotify": ("unreviewed", "Spotify oEmbed cover links (hotlinked): terms not reviewed"),
    "catalogue:mpd": ("non_commercial", "tracks table built from the MPD (see module docstring)"),
}


class LicenceError(RuntimeError):
    pass


def mode() -> str:
    m = os.environ.get("SWAVE_MODE", "personal").strip().lower() or "personal"
    if m not in ("personal", "public"):
        raise LicenceError(f"SWAVE_MODE must be personal or public, not {m!r}")
    return m


def check(enabled: list[str], current_mode: str | None = None) -> None:
    """Fail (public mode) on any non-commercial component in `enabled`; log unreviewed ones."""
    current_mode = current_mode or mode()
    unknown = [c for c in enabled if c not in COMPONENTS]
    if unknown:
        raise LicenceError(f"no licence recorded for {unknown}: add it to licences.COMPONENTS")
    if current_mode != "public":
        return
    blocked = [f"{c} ({COMPONENTS[c][1]})" for c in enabled if COMPONENTS[c][0] == "non_commercial"]
    if blocked:
        raise LicenceError("SWAVE_MODE=public refuses non-commercial components: " + "; ".join(blocked))
    for c in enabled:
        if COMPONENTS[c][0] == "unreviewed":
            log.warning("public mode: %s is unreviewed (%s)", c, COMPONENTS[c][1])
