from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASYNC_LAUNCHER = ROOT / "scripts" / "reunion_watch_daily_async.sh"


def test_async_launcher_preserves_caller_disk_threshold_over_dotenv():
    source = ASYNC_LAUNCHER.read_text(encoding="utf-8")
    capture = 'CALLER_IMMO_MIN_FREE_GB="${IMMO_MIN_FREE_GB-}"'
    dotenv = ". /opt/data/.env"
    restore = 'export IMMO_MIN_FREE_GB="$CALLER_IMMO_MIN_FREE_GB"'

    assert capture in source
    assert restore in source
    assert source.index(capture) < source.index(dotenv) < source.index(restore)


def test_async_worker_inherits_launcher_environment():
    source = ASYNC_LAUNCHER.read_text(encoding="utf-8")

    assert 'bash -lc "$WORKER_CMD"' in source
    assert "env -i" not in source
