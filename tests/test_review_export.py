import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from gateway import create_app

from export_aegis_review import build_review, read_report


class ReviewExportTests(unittest.TestCase):
    def report(self, mode='LIVE: test fixture', **changes):
        data = dict(mode=mode, checks={'example_check': True}, passed=True,
                    rows=[dict(label='one', http_status=200, round_trip_ms=5.275)],
                    model='<script>bad()</script>')
        return {**data, **changes}

    def test_simulated_data_cannot_be_loaded_as_a_real_run(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'report.json'
            path.write_text(json.dumps(self.report('SIMULATED: fixture')), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'explicitly labeled LIVE'):
                read_report(path, 'LIVE')

    def test_string_pass_values_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'report.json'
            path.write_text(json.dumps(self.report(checks={'check': 'true'})), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'boolean'):
                read_report(path, 'LIVE')

    def test_false_check_overrules_claimed_pass_and_html_is_escaped(self):
        live = self.report(checks={'<img onerror=bad()>': False})
        simulation = self.report('SIMULATED: fixture')
        document = build_review((live, 'abc'), (simulation, 'def'))
        self.assertIn('Checks did not all pass', document)
        self.assertIn('&lt;script&gt;', document)
        self.assertNotIn('<script>', document)
        self.assertNotIn('<img onerror=', document)
        self.assertIn('SIMULATED RECOVERY', document)
        self.assertIn('5.275 ms', document)

    def test_input_fingerprint_and_recorded_status_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'report.json'
            path.write_text(json.dumps(self.report()), encoding='utf-8')
            data, digest = read_report(path, 'LIVE')
            self.assertEqual(len(digest), 64)
            document = build_review((data, digest), (self.report('SIMULATED: fixture'), 'def'))
            self.assertIn(digest, document)
            self.assertIn('not a live dashboard', document)


class ReviewRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_review_does_not_call_a_model_and_missing_file_is_explicit(self):
        calls = []
        app = create_app(transport=httpx.MockTransport(lambda request: calls.append(request)))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
                with tempfile.TemporaryDirectory() as directory, patch('gateway.ROOT', Path(directory)):
                    self.assertEqual((await client.get('/aegis/review')).status_code, 404)
                    results = Path(directory) / 'results'
                    results.mkdir()
                    (results / 'aegis-review.html').write_text('<h1>Recorded evidence</h1>', encoding='utf-8')
                    response = await client.get('/aegis/review')
                    self.assertEqual(response.status_code, 200)
                    self.assertIn('Recorded evidence', response.text)
                    self.assertIn("default-src 'none'", response.headers['content-security-policy'])
                    self.assertEqual(calls, [])
