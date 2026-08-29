#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

LOCAL_ROOT = Path(__file__).resolve().parents[1]
ROOT = LOCAL_ROOT if (LOCAL_ROOT / 'scripts' / 'realestate_watch.py').exists() else Path('/opt/data/projects/reunion-immo-search')
REAL_RUN = Path('/opt/data/artifacts/reunion-watch/20260805T163047Z_daily/immo_public_refresh/realestate_watch')

MOD_PATH = ROOT / 'scripts' / 'realestate_watch.py'
spec = importlib.util.spec_from_file_location('realestate_watch', MOD_PATH)
assert spec is not None and spec.loader is not None
realestate_watch = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = realestate_watch
spec.loader.exec_module(realestate_watch)


def rr(source: str, ok: bool, error: str | None = None):
    return realestate_watch.RunnerResult(
        script=f'{source}.py',
        ok=ok,
        exit_code=0 if ok else 1,
        stdout_path='',
        stderr_path='',
        parsed_summary={'error': error} if error else {},
        source=source,
        duration_sec=1.0,
        timeout_sec=300,
        status_path=f'/tmp/{source}.status.json',
    )


def real_runner_results() -> list:
    report_path = REAL_RUN / 'realestate_watch_report.json'
    if os.name == 'nt' or not report_path.exists():
        fallback_sources = sorted(realestate_watch.CRITICAL_REFRESH_SOURCES | {'seloger'})
        return [rr(source, source != 'leboncoin', 'anti-bot count=0' if source == 'leboncoin' else None)
                for source in fallback_sources]
    report = json.loads(report_path.read_text(encoding='utf-8'))
    return [realestate_watch.RunnerResult(**item) for item in report['runner_results']]


class RealestateWatchSourceGateTest(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop('IMMO_SOURCE_OK_THRESHOLD', None)

    def test_real_20260805_13_of_14_blocks_and_names_leboncoin(self):
        gate = realestate_watch.evaluate_source_gate(real_runner_results())
        self.assertIs(gate['ok'], False)
        self.assertEqual(gate['ok_count'], 13)
        self.assertEqual(gate['total'], 14)
        self.assertGreaterEqual(gate['ok_ratio'], 0.92)
        self.assertEqual(gate['threshold'], 0.70)
        self.assertEqual(gate['failed_sources'], ['leboncoin'])
        self.assertEqual(gate['failed_source_details'][0]['source'], 'leboncoin')
        self.assertRegex(
            gate['failed_source_details'][0]['motif'],
            r'anti-bot count=0|no successful exploitable status',
        )

    def test_negative_5_of_14_blocks_and_names_the_9_failed_sources(self):
        results = [rr(f'source_{i:02d}', i < 5, f'motif source_{i:02d}') for i in range(14)]
        gate = realestate_watch.evaluate_source_gate(results)
        self.assertIs(gate['ok'], False)
        self.assertEqual(gate['ok_count'], 5)
        self.assertEqual(gate['total'], 14)
        self.assertEqual(gate['failed_sources'], [f'source_{i:02d}' for i in range(5, 14)])
        self.assertEqual([d['motif'] for d in gate['failed_source_details']], [f'motif source_{i:02d}' for i in range(5, 14)])

    def test_threshold_is_configurable_by_env(self):
        os.environ['IMMO_SOURCE_OK_THRESHOLD'] = '1.0'
        gate = realestate_watch.evaluate_source_gate(real_runner_results())
        self.assertIs(gate['ok'], False)
        self.assertEqual(gate['threshold'], 1.0)
        self.assertEqual(gate['failed_sources'], ['leboncoin'])

    def test_report_json_and_markdown_name_failed_sources(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            tmp_path = Path(d)
            results = [rr('bienici', True), rr('leboncoin', False, 'anti-bot count=0')]
            args = argparse.Namespace(max_price=900, min_price=450, min_rooms=None, city=None, limit=25, include_commercial=False)
            paths = realestate_watch.write_reports(tmp_path, tmp_path / 'missing.db', results, [], None, {}, args)
            payload = json.loads(Path(paths['json']).read_text(encoding='utf-8'))
            md = Path(paths['markdown']).read_text(encoding='utf-8')
            self.assertEqual(payload['source_gate']['failed_sources'], ['leboncoin'])
            self.assertEqual(payload['failed_sources'], ['leboncoin'])
            self.assertIn('Sources KO', md)
            self.assertIn('leboncoin', md)
            self.assertIn('anti-bot count=0', md)

    def test_main_blocks_when_gate_is_below_threshold_and_prints_the_9_ko(self):
        import tempfile
        original_argv = sys.argv[:]
        original_run_scrapers = realestate_watch.run_scrapers
        try:
            with tempfile.TemporaryDirectory() as d:
                sys.argv = ['realestate_watch.py', '--dry-run-scrapers', '--run-dir', d]
                realestate_watch.run_scrapers = lambda db, run_dir, dry_run=False: [
                    rr(f'source_{i:02d}', i < 5, f'motif source_{i:02d}') for i in range(14)
                ]
                out = StringIO()
                with self.assertRaises(SystemExit) as raised, redirect_stdout(out):
                    realestate_watch.main()
                self.assertEqual(raised.exception.code, 1)
                payload = json.loads(out.getvalue())
                self.assertIs(payload['ok'], False)
                self.assertEqual(payload['source_gate']['ok_count'], 5)
                self.assertEqual(payload['failed_sources'], [f'source_{i:02d}' for i in range(5, 14)])
        finally:
            realestate_watch.run_scrapers = original_run_scrapers
            sys.argv = original_argv

    def test_main_blocks_when_one_of_fourteen_critical_sources_fails(self):
        import tempfile
        original_argv = sys.argv[:]
        original_run_scrapers = realestate_watch.run_scrapers
        try:
            real_results = real_runner_results()
            with tempfile.TemporaryDirectory() as d:
                sys.argv = ['realestate_watch.py', '--dry-run-scrapers', '--run-dir', d]
                realestate_watch.run_scrapers = lambda db, run_dir, dry_run=False: real_results
                out = StringIO()
                with self.assertRaises(SystemExit) as raised, redirect_stdout(out):
                    realestate_watch.main()
                self.assertEqual(raised.exception.code, 1)
                payload = json.loads(out.getvalue())
                self.assertIs(payload['ok'], False)
                self.assertEqual(payload['source_gate']['failed_sources'], ['leboncoin'])
        finally:
            realestate_watch.run_scrapers = original_run_scrapers
            sys.argv = original_argv


if __name__ == '__main__':
    unittest.main(verbosity=2)
