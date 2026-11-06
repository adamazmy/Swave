"""Public mode refuses non-commercial components at startup (no DB needed)."""

from __future__ import annotations

import pytest

from swave import licences
from swave.main import create_app


def test_personal_mode_loads_everything(monkeypatch):
    monkeypatch.delenv("SWAVE_MODE", raising=False)
    licences.check(["scorer:cooccurrence_mpd", "scorer:related_artists", "previews:deezer"])


def test_public_mode_refuses_non_commercial_components():
    with pytest.raises(licences.LicenceError) as e:
        licences.check(["scorer:cooccurrence_lb", "scorer:related_artists", "previews:deezer"], "public")
    assert "scorer:related_artists" in str(e.value) and "previews:deezer" in str(e.value)
    assert "cooccurrence_lb" not in str(e.value)
    licences.check(["scorer:cooccurrence_lb", "scorer:lb_similar_artists", "scorer:discogs_style",
                    "previews:itunes"], "public")  # open + unreviewed: allowed


def test_every_scorer_and_preview_source_has_a_licence():
    from swave import previews, scorers
    for name in scorers.NAMES:
        assert f"scorer:{name}" in licences.COMPONENTS
    for s in previews.KNOWN_SOURCES:
        assert f"previews:{s}" in licences.COMPONENTS
    with pytest.raises(licences.LicenceError, match="no licence recorded"):
        licences.check(["scorer:mystery"], "personal")


def test_api_fails_at_startup_in_public_mode(monkeypatch):
    monkeypatch.setenv("SWAVE_MODE", "public")
    with pytest.raises(licences.LicenceError, match="cooccurrence_mpd"):
        create_app("postgresql://unused", weights={"cooccurrence_mpd": 1.0})
    # even with open scorers only, the MPD-built catalogue blocks a public start (known gap)
    with pytest.raises(licences.LicenceError, match="catalogue:mpd"):
        create_app("postgresql://unused", weights={"cooccurrence_lb": 1.0})
    monkeypatch.setenv("SWAVE_MODE", "commercial")
    with pytest.raises(licences.LicenceError, match="personal or public"):
        create_app("postgresql://unused", weights={"cooccurrence_lb": 1.0})
