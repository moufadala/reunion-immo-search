from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_product_v2.sh"


def test_product_build_recreates_candidate_webapp_from_locked_sources():
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'DIST="$APP/v2"' in source
    assert "npm ci" in source
    assert 'npm run build -- --outDir "$DIST" --emptyOutDir' in source
    assert source.index("npm ci") < source.index("npm run build") < source.index("export_feed_once")


def test_product_build_does_not_depend_on_ignored_project_dist_timestamps():
    source = SCRIPT.read_text(encoding="utf-8")

    assert "webapp/dist/index.html" not in source
    assert "-newer" not in source
    assert 'cp -r "$DIST/." "$APP/v2/"' not in source
