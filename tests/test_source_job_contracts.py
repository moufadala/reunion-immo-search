from pathlib import Path

from scripts import realestate_multi_sources_scraper as multi
from scripts import realestate_watch
from src import source_health


def test_ofim_job_uses_the_catalogue_scraper_and_has_no_duplicate_rss_job():
    jobs = {job["source"]: job for job in realestate_watch.SOURCE_JOBS}

    assert jobs["ofim"]["script"] == realestate_watch.MULTI_SCRAPER
    assert jobs["ofim"]["args"] == ["--only", "ofim"]
    assert "ofim_rss" not in jobs
    assert "ofim_rss" not in source_health.CRITICAL_SOURCES
    assert "ofim_rss" in source_health.AUXILIARY_SOURCES


def test_every_watch_portal_is_blocking_and_source_health_tracks_all_fourteen():
    assert realestate_watch.NON_BLOCKING_REFRESH_SOURCES == set()
    assert realestate_watch.CRITICAL_REFRESH_SOURCES == {
        job["source"] for job in realestate_watch.SOURCE_JOBS
    }
    assert len(source_health.CRITICAL_SOURCES) == 14


def test_domimmo_rejects_commercial_and_land_inventory():
    assert multi.is_domimmo_residential("Location local commercial", "Bureau de 80 m2") is False
    assert multi.is_domimmo_residential("Terrain à louer", "Parcelle disponible") is False
    assert multi.is_domimmo_residential("Appartement T3 à louer", "Deux chambres") is True
    assert multi.is_domimmo_residential("Maison en location", "Villa familiale") is True
