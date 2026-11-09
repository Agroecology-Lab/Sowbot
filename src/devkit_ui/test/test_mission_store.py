import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from unittest.mock import Mock, patch

from devkit_ui.missions.sqlite import MissionSqliteStore
from devkit_ui.missions.store import DUE_FAILED, DUE_NOW, MissionStore, validate_mission
from devkit_ui.time_utils import parse_ts

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


class TestValidateMission(unittest.TestCase):
    def test_valid_mission_has_no_error(self) -> None:
        self.assertIsNone(validate_mission('NORTH_FIELD', ['R1'], 'drive', 6))

    def test_valid_mission_without_name_or_repeat_has_no_error(self) -> None:
        self.assertIsNone(validate_mission('', ['R1'], 'weed', None))

    def test_no_rows_is_rejected(self) -> None:
        self.assertEqual(validate_mission('A', [], 'drive', None), 'ERROR: no rows selected')

    def test_unknown_action_is_rejected(self) -> None:
        self.assertIn('unknown action', cast(str, validate_mission('A', ['R1'], 'teleport', None)))

    def test_non_integer_repeat_is_rejected(self) -> None:
        self.assertIn(
            'must be an integer',
            cast(str, validate_mission('A', ['R1'], 'drive', 'often')))  # type: ignore[arg-type]

    def test_non_positive_repeat_is_rejected(self) -> None:
        self.assertIn('must be > 0', cast(str, validate_mission('A', ['R1'], 'drive', 0)))
        self.assertIn('must be > 0', cast(str, validate_mission('A', ['R1'], 'drive', -3)))

    def test_uncleaned_name_is_rejected(self) -> None:
        self.assertIn(
            'invalid name', cast(str, validate_mission('not clean', ['R1'], 'drive', None)))


class TestMissionStoreEditing(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())  # pylint: disable=consider-using-with
        self.ros = Mock()
        self.store = MissionStore(str(Path(self.tmp) / 'missions.db'))
        self.addCleanup(self.store.close)
        self.store.attach(self.ros)

    def test_close_releases_the_sqlite_connection(self) -> None:
        self.store.close()

        with self.assertRaises(sqlite3.ProgrammingError):
            self.store.find('MISSION_1')

    def test_attach_initialises_state(self) -> None:
        self.assertEqual(self.store.missions, ())
        self.assertEqual(self.store.version, 1)
        self.assertEqual(self.store.status, '')

    def test_add_cleans_the_name_and_updates_state(self) -> None:
        mission_id = self.store.add(rows=['R1', 'R2'], action='drive', name=' north field! ')

        self.assertEqual(mission_id, 'MISSION_1')
        mission = self.store.find_by_name('NORTH_FIELD')
        assert mission is not None
        self.assertEqual(mission['id'], 'MISSION_1')
        self.assertEqual(mission['rows'], ['R1', 'R2'])
        self.assertEqual([m['id'] for m in self.store.missions], ['MISSION_1'])
        self.assertEqual(self.store.version, 2)

    def test_add_rejects_invalid_mission_and_reports_why(self) -> None:
        self.assertIsNone(self.store.add(rows=[], action='drive'))

        self.assertEqual(self.store.status, 'ERROR: no rows selected')
        self.assertEqual(self.store.missions, ())
        self.assertEqual(self.store.version, 1)

    def test_delete_removes_the_mission(self) -> None:
        mission_id = cast(str, self.store.add(rows=['R1'], action='drive'))

        self.assertTrue(self.store.delete(mission_id))

        self.assertIsNone(self.store.find(mission_id))
        self.assertEqual(self.store.missions, ())

    def test_delete_unknown_mission_reports_not_found(self) -> None:
        self.assertFalse(self.store.delete('MISSION_42'))

        self.assertIn('not found', self.store.status)

    def test_update_changes_mutable_fields(self) -> None:
        mission_id = cast(str, self.store.add(rows=['R1'], action='drive', name='OLD'))

        self.assertTrue(
            self.store.update(mission_id, name='new name', rows=['R2', 'R3'], repeat_every_hours=8))

        mission = self.store.find(mission_id)
        assert mission is not None
        self.assertEqual(mission['name'], 'NEW_NAME')
        self.assertEqual(mission['rows'], ['R2', 'R3'])
        self.assertEqual(mission['repeat_every_hours'], 8)

    def test_update_with_blank_name_falls_back_to_the_id(self) -> None:
        mission_id = cast(str, self.store.add(rows=['R1'], action='drive', name='CUSTOM'))

        self.assertTrue(self.store.update(mission_id, name='!!!'))

        mission = self.store.find(mission_id)
        assert mission is not None
        self.assertEqual(mission['name'], mission_id)

    def test_update_rejects_immutable_fields(self) -> None:
        mission_id = cast(str, self.store.add(rows=['R1'], action='drive'))

        self.assertFalse(self.store.update(mission_id, created_at='2020-01-01T00:00:00Z'))

        self.assertIn('cannot update', self.store.status)

    def test_update_unknown_mission_reports_not_found(self) -> None:
        self.assertFalse(self.store.update('MISSION_42', name='X'))

        self.assertIn('not found', self.store.status)

    def test_update_that_fails_validation_is_rejected(self) -> None:
        mission_id = cast(str, self.store.add(rows=['R1'], action='drive'))

        self.assertFalse(self.store.update(mission_id, rows=[]))

        self.assertEqual(self.store.status, 'ERROR: no rows selected')
        mission = self.store.find(mission_id)
        assert mission is not None
        self.assertEqual(mission['rows'], ['R1'])

    def test_set_active_toggles_the_mission(self) -> None:
        mission_id = cast(str, self.store.add(rows=['R1'], action='drive'))

        self.assertTrue(self.store.set_active(mission_id, False))
        mission = self.store.find(mission_id)
        assert mission is not None
        self.assertFalse(mission['active'])

        self.assertTrue(self.store.set_active(mission_id, True))
        mission = self.store.find(mission_id)
        assert mission is not None
        self.assertTrue(mission['active'])

    def test_unknown_mission_has_no_due_time(self) -> None:
        self.assertIsNone(self.store.next_due_in_hours('MISSION_42'))


class TestMissionStoreRunHistory(unittest.TestCase):
    def setUp(self) -> None:
        tmp = self.enterContext(tempfile.TemporaryDirectory())  # pylint: disable=consider-using-with
        self.path = Path(tmp) / 'missions.db'
        self.ros = Mock()
        self.store = MissionStore(str(self.path))
        self.addCleanup(self.store.close)
        self.store.attach(self.ros)

    def test_successful_one_shot_run_is_recorded_and_deactivates_the_mission(self) -> None:
        mission_id = cast(str, self.store.add(rows=['R1'], action='drive'))

        self.assertTrue(self.store.record_run(mission_id, True))

        mission = cast(dict, self.store.find(mission_id))
        self.assertIs(mission['last_run_success'], True)
        self.assertIsNotNone(parse_ts(mission['last_run_at']))
        self.assertFalse(mission['active'])
        self.assertEqual(self.store.today_queue(), [])
        self.assertIsNone(self.store.next_due_in_hours(mission_id))

    def test_failed_run_keeps_the_mission_active_and_queued_for_retry(self) -> None:
        mission_id = cast(str, self.store.add(rows=['R1'], action='drive'))

        self.assertTrue(self.store.record_run(mission_id, False))

        mission = cast(dict, self.store.find(mission_id))
        self.assertIs(mission['last_run_success'], False)
        self.assertTrue(mission['active'])
        self.assertEqual(self.store.today_queue(), [(mission_id, 'R1', 'drive', {})])
        self.assertEqual(self.store.next_due_in_hours(mission_id), DUE_FAILED)

    def test_successful_recurring_run_stays_active_and_waits_for_its_interval(self) -> None:
        mission_id = cast(str, self.store.add(rows=['R1'], action='drive', repeat_every_hours=6))

        self.assertTrue(self.store.record_run(mission_id, True))

        mission = cast(dict, self.store.find(mission_id))
        self.assertTrue(mission['active'])
        self.assertEqual(self.store.today_queue(), [])
        self.assertAlmostEqual(cast(float, self.store.next_due_in_hours(mission_id)), 6.0, places=1)

    def test_record_run_updates_state(self) -> None:
        mission_id = cast(str, self.store.add(rows=['R1'], action='drive'))
        version = self.store.version

        self.store.record_run(mission_id, True)

        self.assertEqual(self.store.version, version + 1)
        self.assertIs(self.store.missions[0]['last_run_success'], True)

    def test_record_run_for_unknown_mission_reports_not_found(self) -> None:
        self.assertFalse(self.store.record_run('MISSION_42', True))

        self.assertIn('not found', self.store.status)

    def test_reset_rearms_a_completed_mission(self) -> None:
        mission_id = cast(str, self.store.add(rows=['R1'], action='drive'))
        self.store.record_run(mission_id, True)
        version = self.store.version

        self.assertTrue(self.store.reset(mission_id))

        mission = cast(dict, self.store.find(mission_id))
        self.assertTrue(mission['active'])
        self.assertIsNone(mission['last_run_at'])
        self.assertIsNone(mission['last_run_success'])
        self.assertEqual(self.store.next_due_in_hours(mission_id), DUE_NOW)
        self.assertEqual(self.store.version, version + 1)

    def test_recorded_run_is_restored_after_reopening_the_store(self) -> None:
        mission_id = cast(str, self.store.add(rows=['R1'], action='drive'))
        self.assertTrue(self.store.record_run(mission_id, True))
        self.store.close()

        reopened = MissionStore(str(self.path))
        self.addCleanup(reopened.close)
        mission = cast(dict, reopened.find(mission_id))

        self.assertIs(mission['last_run_success'], True)
        self.assertIsNotNone(parse_ts(mission['last_run_at']))
        self.assertFalse(mission['active'])

    def test_reset_state_is_restored_after_reopening_the_store(self) -> None:
        mission_id = cast(str, self.store.add(
            rows=['R1', 'R2'],
            action='weed',
            action_params={'rpm': 3},
            repeat_every_hours=6,
        ))
        self.store.record_run(mission_id, True)
        self.assertTrue(self.store.reset(mission_id))
        self.store.close()

        reopened = MissionStore(str(self.path))
        self.addCleanup(reopened.close)
        mission = cast(dict, reopened.find(mission_id))

        self.assertIsNone(mission['last_run_at'])
        self.assertIsNone(mission['last_run_success'])
        self.assertTrue(mission['active'])
        self.assertEqual(mission['rows'], ['R1', 'R2'])
        self.assertEqual(mission['action'], 'weed')
        self.assertEqual(mission['action_params'], {'rpm': 3})
        self.assertEqual(mission['repeat_every_hours'], 6)

    def test_reset_for_unknown_mission_reports_not_found(self) -> None:
        self.assertFalse(self.store.reset('MISSION_42'))

        self.assertIn('not found', self.store.status)


class TestMissionStoreScheduling(unittest.TestCase):
    """Scheduling reads last_run_* fields, so the missions are prepared in a SQLite file."""

    def setUp(self) -> None:
        tmp = self.enterContext(tempfile.TemporaryDirectory())  # pylint: disable=consider-using-with
        self.store = MissionStore(str(Path(tmp) / 'missions.db'))
        self.addCleanup(self.store.close)
        self.store.attach(Mock())
        backend = cast(MissionSqliteStore, self.store._store)  # pylint: disable=protected-access
        self.ids: dict[str, str] = {}

        def make(name: str, rows: list, action: str, params: dict | None = None,
                 repeat: int | None = None, active: bool = True, ran: str | None = None,
                 ok: bool | None = None) -> None:
            mid = cast(str, backend.add(rows, action, params, name, repeat, active))
            if ran is not None:
                backend.update(mid, last_run_at=ran, last_run_success=ok)
            self.ids[name] = mid

        make('NEVER_RAN', ['R1', 'R2'], 'weed', {'rpm': 3}, repeat=6)
        make('FAILED', ['R3'], 'drive', repeat=6, ran='2026-01-01T11:00:00Z', ok=False)
        make('RECURRING_WAITING', ['R4'], 'drive', repeat=6, ran='2026-01-01T10:00:00Z', ok=True)
        make('RECURRING_ELAPSED', ['R5'], 'drive', repeat=6, ran='2026-01-01T05:00:00Z', ok=True)
        make('MALFORMED_TIMESTAMP', ['R9'], 'drive', ran='not a timestamp', ok=True)
        make('ONE_SHOT_DONE', ['R6'], 'drive', ran='2026-01-01T09:00:00Z', ok=True)
        make('INACTIVE', ['R7'], 'drive', active=False)
        make('BAD_ACTION', ['R8'], 'teleport')
        patcher = patch('devkit_ui.missions.store.now_utc', return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_today_queue_lists_only_due_active_missions_with_valid_actions(self) -> None:
        queue = self.store.today_queue()

        self.assertEqual(queue, [
            (self.ids['NEVER_RAN'], 'R1', 'weed', {'rpm': 3}),
            (self.ids['NEVER_RAN'], 'R2', 'weed', {'rpm': 3}),
            (self.ids['FAILED'], 'R3', 'drive', {}),
            (self.ids['RECURRING_ELAPSED'], 'R5', 'drive', {}),
            (self.ids['MALFORMED_TIMESTAMP'], 'R9', 'drive', {}),
        ])

    def test_never_run_mission_is_due_now(self) -> None:
        self.assertEqual(self.store.next_due_in_hours(self.ids['NEVER_RAN']), DUE_NOW)

    def test_failed_mission_reports_the_retry_sentinel(self) -> None:
        self.assertEqual(self.store.next_due_in_hours(self.ids['FAILED']), DUE_FAILED)

    def test_recurring_mission_reports_hours_until_the_interval_elapses(self) -> None:
        self.assertAlmostEqual(cast(float, self.store.next_due_in_hours(self.ids['RECURRING_WAITING'])), 4.0)

    def test_recurring_mission_past_its_interval_is_due_now(self) -> None:
        self.assertEqual(self.store.next_due_in_hours(self.ids['RECURRING_ELAPSED']), DUE_NOW)

    def test_malformed_timestamp_is_treated_as_never_run(self) -> None:
        self.assertIn((self.ids['MALFORMED_TIMESTAMP'], 'R9', 'drive', {}), self.store.today_queue())
        self.assertEqual(self.store.next_due_in_hours(self.ids['MALFORMED_TIMESTAMP']), DUE_NOW)

    def test_completed_one_shot_has_no_due_time(self) -> None:
        self.assertIsNone(self.store.next_due_in_hours(self.ids['ONE_SHOT_DONE']))

    def test_inactive_mission_has_no_due_time(self) -> None:
        self.assertIsNone(self.store.next_due_in_hours(self.ids['INACTIVE']))


class TestRecordResult(unittest.TestCase):
    def test_sqlite_stores_the_run_result_and_bumps_the_version(self) -> None:
        tmp = self.enterContext(tempfile.TemporaryDirectory())  # pylint: disable=consider-using-with
        store = MissionStore(str(Path(tmp) / 'missions.db'))
        self.addCleanup(store.close)
        store.attach(Mock())
        mid = cast(str, store.add(rows=['R1'], action='drive'))
        version = store.version

        self.assertTrue(store.record_result(mid, state='ended', steps_done=3, stage='row',
                                            reason='timeout', ended_at='2026-11-06T10:00:00Z'))

        mission = cast(dict, store.find(mid))
        self.assertEqual((mission['state'], mission['steps_done'], mission['stage'],
                          mission['reason']), ('ended', 3, 'row', 'timeout'))
        self.assertGreater(store.version, version)

    def test_it_rejects_other_fields_and_unknown_missions(self) -> None:
        tmp = self.enterContext(tempfile.TemporaryDirectory())  # pylint: disable=consider-using-with
        store = MissionStore(str(Path(tmp) / 'missions.db'))
        self.addCleanup(store.close)
        store.attach(Mock())
        mid = cast(str, store.add(rows=['R1'], action='drive'))

        self.assertFalse(store.record_result(mid, source='remote'))
        self.assertIn('cannot record', store.status)
        self.assertFalse(store.record_result('MISSION_99', state='ended'))
        self.assertIn('not found', store.status)


class TestRemoteMissions(unittest.TestCase):
    def setUp(self) -> None:
        tmp = self.enterContext(tempfile.TemporaryDirectory())  # pylint: disable=consider-using-with
        self.store = MissionStore(str(Path(tmp) / 'missions.db'))
        self.addCleanup(self.store.close)
        self.store.attach(Mock())

    def _add(self, external_id: str = 'a1b2c3d4e5', **overrides) -> str | None:
        args = {'external_id': external_id, 'rows': ['E1', 'E2'], 'action': 'drive',
                'map_hash': 'c50f60d8', 'groups': [1, 1], **overrides}
        return self.store.add_remote(**args)

    def test_add_remote_stores_an_idle_active_remote_mission(self) -> None:
        mid = cast(str, self._add())

        mission = cast(dict, self.store.find(mid))
        self.assertEqual(
            (mission['source'], mission['external_id'], mission['state'], mission['active'],
             mission['rows'], mission['groups'], mission['map_hash']),
            ('remote', 'a1b2c3d4e5', 'idle', True, ['E1', 'E2'], [1, 1], 'c50f60d8'))
        self.assertEqual(cast(dict, self.store.find_by_external_id('a1b2c3d4e5'))['id'], mid)
        self.assertIsNone(self.store.find_by_external_id('other'))

    def test_add_remote_refuses_bad_input_and_a_repeated_id(self) -> None:
        self.assertIsNotNone(self._add())
        for overrides, text in (
                ({}, 'already stored'),
                ({'external_id': ''}, 'empty external_id'),
                ({'external_id': 'x', 'map_hash': ''}, 'empty map_hash'),
                ({'external_id': 'x', 'groups': [1]}, 'differ in length'),
                ({'external_id': 'x', 'action': 'teleport'}, 'unknown action'),
                ({'external_id': 'x', 'rows': [], 'groups': []}, 'no rows')):
            with self.subTest(text):
                self.assertIsNone(self._add(**overrides))
                self.assertIn(text, self.store.status)
        self.assertEqual(len(self.store.missions), 1)

    def test_delete_refuses_a_running_mission(self) -> None:
        mid = cast(str, self._add())
        self.store.record_result(mid, state='running')

        self.assertFalse(self.store.delete(mid))
        self.assertIn('is running', self.store.status)
        self.assertIsNotNone(self.store.find(mid))

        self.store.record_result(mid, state='ended')
        self.assertTrue(self.store.delete(mid))

    def test_today_queue_skips_remote_missions_even_after_a_failed_run(self) -> None:
        local = cast(str, self.store.add(rows=['R1'], action='drive'))
        remote = cast(str, self._add())
        self.store.record_run(remote, False)

        self.assertEqual([entry[0] for entry in self.store.today_queue()], [local])


if __name__ == '__main__':
    unittest.main()
