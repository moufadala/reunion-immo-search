#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('reunion_watch_pipeline', ROOT / 'scripts' / 'reunion_watch_pipeline.py')
assert spec is not None and spec.loader is not None
pipeline = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pipeline
spec.loader.exec_module(pipeline)


class ReunionWatchPipelineImmoAlertTest(unittest.TestCase):
    def test_summarize_immo_carries_failed_source_details_for_alerts(self):
        report = {
            'selected_count': 3,
            'db_summary': {'by_source': {'bienici': 2}, 'quality': {'total': 3}},
            'source_gate': {
                'ok': True,
                'failed_sources': ['leboncoin'],
                'failed_source_details': [{'source': 'leboncoin', 'motif': 'count=0'}],
            },
            'failed_sources': ['leboncoin'],
            'failed_source_details': [{'source': 'leboncoin', 'motif': 'count=0'}],
        }
        summary = pipeline.summarize_immo(report)
        self.assertIs(summary['ok'], True)
        self.assertEqual(summary['failed_sources'], ['leboncoin'])
        self.assertEqual(summary['failed_source_details'][0]['motif'], 'count=0')

    def test_immo_failed_sources_alert_line_names_source_and_motif(self):
        line = pipeline.immo_failed_sources_alert_line([
            {'source': 'leboncoin', 'motif': 'source leboncoin returned no successful exploitable status (exit=0)'},
            {'source': 'zimo', 'motif': 'timeout'},
        ])
        self.assertIn('Immo sources KO:', line)
        self.assertIn('leboncoin', line)
        self.assertIn('exit=0', line)
        self.assertIn('zimo', line)
        self.assertIn('timeout', line)

    def test_no_failed_sources_alert_line_is_empty(self):
        self.assertEqual(pipeline.immo_failed_sources_alert_line([]), '')


if __name__ == '__main__':
    unittest.main(verbosity=2)
