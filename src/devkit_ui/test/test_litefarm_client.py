"""Tests for the LiteFarm client and the row plan command line, against a local stand-in for LiteFarm."""
import contextlib
import io
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import ClassVar
from unittest.mock import patch

from devkit_ui.litefarm_client import LiteFarmError, import_row_plan, login
from devkit_ui.row_plan_cli import PASSWORD_ENV, main

MAIZE_MAP = str(Path(__file__).resolve().parents[3] / 'maps' / 'maize_map')
GEOJSON = {'type': 'FeatureCollection', 'properties': {'version': 1}, 'features': []}
PLAN = {'row_plan': {'row_plan_id': 'plan-1'}, 'row_count': 4, 'warnings': ['careful']}
LOGIN_OK = (200, {'id_token': 'tok', 'user': {'user_id': 'u1'}})


class FakeLiteFarm(BaseHTTPRequestHandler):
    """Answers each path with a scripted (status, body) and records what it was sent."""

    responses: ClassVar[dict[str, tuple[int, dict]]] = {}
    seen: ClassVar[list[dict]] = []

    def do_POST(self):  # pylint: disable=invalid-name
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        FakeLiteFarm.seen.append({'path': self.path, 'headers': {k.lower(): v for k, v in self.headers.items()},
                                  'body': body})
        status, payload = FakeLiteFarm.responses.get(self.path, (404, {}))
        raw = json.dumps(payload).encode() if payload else b''
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_):
        """Keep test output quiet."""


class ServerTestCase(unittest.TestCase):
    def setUp(self):
        FakeLiteFarm.responses, FakeLiteFarm.seen = {}, []
        self.server = HTTPServer(('127.0.0.1', 0), FakeLiteFarm)
        threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f'http://127.0.0.1:{self.server.server_port}'


class LoginTest(ServerTestCase):
    def test_returns_the_token_and_user(self):
        FakeLiteFarm.responses['/login'] = LOGIN_OK
        self.assertEqual(login(self.url, 'a@b.c', 'secret'), ('tok', 'u1'))
        sent = FakeLiteFarm.seen[0]['body']
        self.assertEqual(sent['user'], {'email': 'a@b.c', 'password': 'secret'})
        self.assertTrue(sent['screenSize']['screen_width'] and sent['screenSize']['screen_height'])

    def test_a_wrong_password_is_reported_plainly(self):
        FakeLiteFarm.responses['/login'] = (401, {})
        with self.assertRaises(LiteFarmError) as caught:
            login(self.url, 'a@b.c', 'wrong')
        self.assertEqual(caught.exception.status, 401)
        self.assertIn('check the email and password', str(caught.exception))

    def test_a_server_that_is_not_there(self):
        self.server.shutdown()
        self.server.server_close()
        with self.assertRaises(LiteFarmError) as caught:
            login(self.url, 'a@b.c', 'secret')
        self.assertIn('cannot reach', str(caught.exception))
        self.assertIsNone(caught.exception.status)


class ImportTest(ServerTestCase):
    def import_plan(self):
        return import_row_plan(self.url, 'tok', 'u1', 'farm-1', 'field-1', GEOJSON)

    def test_a_new_plan(self):
        FakeLiteFarm.responses['/row_plan/import'] = (201, PLAN)
        result = self.import_plan()
        self.assertEqual((result.created, result.row_plan_id, result.row_count, result.warnings),
                         (True, 'plan-1', 4, ['careful']))

    def test_sends_what_litefarm_checks(self):
        FakeLiteFarm.responses['/row_plan/import'] = (201, PLAN)
        self.import_plan()
        sent = FakeLiteFarm.seen[0]
        self.assertEqual({k: sent['headers'][k] for k in ('authorization', 'user_id', 'farm_id')},
                         {'authorization': 'Bearer tok', 'user_id': 'u1', 'farm_id': 'farm-1'})
        self.assertEqual(sent['body'], {'location_id': 'field-1', 'geojson': GEOJSON})

    def test_the_same_plan_again_is_not_an_error(self):
        FakeLiteFarm.responses['/row_plan/import'] = (200, {'row_plan': {'row_plan_id': 'plan-1'}, 'warnings': []})
        result = self.import_plan()
        self.assertEqual((result.created, result.row_count, result.warnings), (False, None, []))

    def test_a_refusal_carries_the_reason(self):
        for status, reason in ((400, 'rows outside the field: 3'), (409, 'map_id already exists with different rows')):
            with self.subTest(status):
                FakeLiteFarm.responses['/row_plan/import'] = (status, {'error': reason})
                with self.assertRaises(LiteFarmError) as caught:
                    self.import_plan()
                self.assertEqual(caught.exception.status, status)
                self.assertIn(reason, str(caught.exception))

    def test_a_refusal_without_a_body(self):
        FakeLiteFarm.responses['/row_plan/import'] = (403, {})
        with self.assertRaises(LiteFarmError) as caught:
            self.import_plan()
        self.assertIn('HTTP 403', str(caught.exception))


class CommandLineTest(ServerTestCase):
    def run_main(self, *args, password='secret'):
        out, err = io.StringIO(), io.StringIO()
        env = {PASSWORD_ENV: password} if password else {}
        with patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = main([MAIZE_MAP, *args])
        return status, out.getvalue(), err.getvalue()

    def import_args(self):
        return ['--import-to', self.url + '/', '--email', 'a@b.c', '--farm-id', 'farm-1', '--location-id', 'field-1']

    def test_writes_the_file(self):
        with TemporaryDirectory() as folder:
            path = str(Path(folder) / 'rows.geojson')
            status, out, _ = self.run_main('-o', path, password='')
            plan = json.loads(Path(path).read_text(encoding='utf-8'))
        self.assertEqual(status, 0)
        self.assertIn('4 rows', out)
        self.assertEqual(plan['properties']['source'], 'robot')
        self.assertEqual(len(plan['features']), 4)

    def test_imports_the_plan(self):
        FakeLiteFarm.responses.update({'/login': LOGIN_OK, '/row_plan/import': (201, PLAN)})
        status, out, err = self.run_main(*self.import_args())
        self.assertEqual(status, 0)
        self.assertIn('imported as row plan plan-1', out)
        self.assertIn('LiteFarm warning: careful', err)
        sent = FakeLiteFarm.seen[1]['body']
        self.assertEqual(sent['location_id'], 'field-1')
        self.assertEqual(sent['geojson']['properties']['field_location_id'], 'field-1')

    def test_says_when_the_plan_was_already_there(self):
        FakeLiteFarm.responses.update({'/login': LOGIN_OK,
                                       '/row_plan/import': (200, {'row_plan': {'row_plan_id': 'plan-1'}})})
        status, out, _ = self.run_main(*self.import_args())
        self.assertEqual((status, 'already imported' in out), (0, True))

    def test_a_refused_import_is_a_failure(self):
        FakeLiteFarm.responses.update({'/login': LOGIN_OK,
                                       '/row_plan/import': (400, {'error': 'rows outside the field: 1, 2'})})
        status, _, err = self.run_main(*self.import_args())
        self.assertEqual(status, 1)
        self.assertIn('rows outside the field: 1, 2', err)

    def test_import_needs_its_options_and_a_password(self):
        status, _, err = self.run_main('--import-to', self.url)
        self.assertEqual(status, 2)
        self.assertIn('--email', err)
        status, _, err = self.run_main(*self.import_args(), password='')
        self.assertEqual(status, 2)
        self.assertIn(PASSWORD_ENV, err)
        self.assertEqual(FakeLiteFarm.seen, [])

    def test_a_map_that_cannot_be_exported_is_a_failure(self):
        with TemporaryDirectory() as folder:
            empty = Path(folder) / 'empty_map'
            empty.write_text('name: empty\nnodes: []\n', encoding='utf-8')
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                status = main([str(empty)])
        self.assertEqual(status, 1)
        self.assertIn('no row nodes', err.getvalue())


if __name__ == '__main__':
    unittest.main()
