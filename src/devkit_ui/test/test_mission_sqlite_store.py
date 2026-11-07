import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from typing import cast

from devkit_ui.missions.sqlite import MissionSqliteStore


class TestMissionSqliteStore(unittest.TestCase):
    def setUp(self) -> None:
        self._store = MissionSqliteStore(':memory:')

    def tearDown(self) -> None:
        self._store.close()

    def test_missions_getter_returns_all_missions_in_id_order(self) -> None:
        self._store.add(rows=[3], action='spray', action_params={'dose': 3}, name='THIRD')
        self._store.add(rows=[1], action='weed', action_params={'dose': 1}, name='FIRST')
        self._store.add(rows=[2], action='seed', action_params={'dose': 2}, name='SECOND')

        missions = self._store.missions

        self.assertEqual(len(missions), 3)
        self.assertEqual(
            [mission['id'] for mission in missions], ['MISSION_1', 'MISSION_2', 'MISSION_3'])
        self.assertEqual([mission['name'] for mission in missions], ['THIRD', 'FIRST', 'SECOND'])
        self.assertEqual([mission['action'] for mission in missions], ['spray', 'weed', 'seed'])
        self.assertEqual([mission['rows'] for mission in missions], [[3], [1], [2]])
        self.assertEqual(
            [mission['action_params'] for mission in missions],
            [{'dose': 3}, {'dose': 1}, {'dose': 2}],
        )

    def test_add_and_find_round_trip(self) -> None:
        mission_id = self._store.add(
            rows=[1, 3, 5],
            action='spray',
            action_params={'dose': 2},
            repeat_every_hours=12,
            active=True,
        )

        self.assertEqual(mission_id, 'MISSION_1')
        mission = self._store.find(mission_id)
        self.assertIsNotNone(mission)
        mission = cast(dict, mission)
        self.assertEqual(mission['id'], mission_id)
        self.assertEqual(mission['name'], mission_id)
        self.assertEqual(mission['rows'], [1, 3, 5])
        self.assertEqual(mission['action'], 'spray')
        self.assertEqual(mission['action_params'], {'dose': 2})
        self.assertEqual(mission['repeat_every_hours'], 12)
        self.assertTrue(mission['active'])
        self.assertTrue(mission['created_at'])
        self.assertIsNone(mission['last_run_at'])
        self.assertIsNone(mission['last_run_success'])


    def test_find_by_name_returns_matching_mission(self) -> None:
        self._store.add(
            rows=[],
            action='noop',
            action_params={},
            name='CUSTOM_NAME',
            active=True,
        )

        mission = self._store.find_by_name('CUSTOM_NAME')
        self.assertIsNotNone(mission)
        mission = cast(dict, mission)
        self.assertEqual(mission['name'], 'CUSTOM_NAME')
        self.assertEqual(mission['id'], 'MISSION_1')


    def test_run_history_is_persisted_and_can_be_cleared(self) -> None:
        mission_id = cast(str, self._store.add(rows=[1], action='drive'))

        self._store.update(mission_id, last_run_at='2026-01-01T10:00:00Z', last_run_success=True)
        mission = cast(dict, self._store.find(mission_id))
        self.assertEqual(mission['last_run_at'], '2026-01-01T10:00:00Z')
        self.assertIs(mission['last_run_success'], True)

        self._store.update(mission_id, last_run_success=False)
        mission = cast(dict, self._store.find(mission_id))
        self.assertIs(mission['last_run_success'], False)

        self._store.update(mission_id, last_run_at=None, last_run_success=None)
        mission = cast(dict, self._store.find(mission_id))
        self.assertIsNone(mission['last_run_at'])
        self.assertIsNone(mission['last_run_success'])

    def test_update_allows_partial_mutable_fields(self) -> None:
        mission_id = self._store.add(
            rows=[1],
            action='old_action',
            action_params={'x': 1},
            name='OLD',
            repeat_every_hours=4,
            active=True,
        )
        self.assertIsNotNone(mission_id)
        mission_id = cast(str, mission_id)

        updated = self._store.update(
            mission_id,
            name='NEW',
            rows=[2, 4],
            action='new_action',
            action_params={'x': 2, 'y': 3},
            repeat_every_hours=8,
            active=False,
            id='MISSION_999',
        )
        self.assertTrue(updated)

        mission = self._store.find(mission_id)
        self.assertIsNotNone(mission)
        mission = cast(dict, mission)
        self.assertEqual(mission['id'], mission_id)
        self.assertEqual(mission['name'], 'NEW')
        self.assertEqual(mission['rows'], [2, 4])
        self.assertEqual(mission['action'], 'new_action')
        self.assertEqual(mission['action_params'], {'x': 2, 'y': 3})
        self.assertEqual(mission['repeat_every_hours'], 8)
        self.assertFalse(mission['active'])


    def test_delete_removes_mission(self) -> None:
        mission_id = self._store.add(rows=[], action='noop')
        self.assertIsNotNone(mission_id)
        mission_id = cast(str, mission_id)

        deleted = self._store.delete(mission_id)
        self.assertTrue(deleted)
        self.assertIsNone(self._store.find(mission_id))



class TestMissionSqliteSchema(unittest.TestCase):
    def setUp(self) -> None:
        self._store = MissionSqliteStore(':memory:')

    def tearDown(self) -> None:
        self._store.close()

    def test_local_mission_defaults(self) -> None:
        mission = cast(dict, self._store.find(cast(str, self._store.add(rows=['E1'], action='drive'))))
        self.assertEqual(mission['source'], 'local')
        self.assertEqual(mission['state'], 'idle')
        for key in ('external_id', 'map_hash', 'groups', 'steps_done', 'stage',
                    'reason', 'started_at', 'ended_at', 'result_sent'):
            self.assertIsNone(mission[key], key)

    def test_remote_mission_round_trip(self) -> None:
        mid = cast(str, self._store.add(
            rows=['E1', 'E2', 'E3'], action='drive', source='remote', external_id='a7',
            map_hash='c50f60d8', groups=[1, 1, 2], state='running'))
        mission = cast(dict, self._store.find(mid))
        self.assertEqual(
            (mission['source'], mission['external_id'],
             mission['map_hash'], mission['groups'], mission['state']),
            ('remote', 'a7', 'c50f60d8', [1, 1, 2], 'running'))

    def test_external_id_is_unique_but_nulls_are_not(self) -> None:
        self._store.add(rows=[1], action='drive')
        self._store.add(rows=[1], action='drive')
        self._store.add(rows=[1], action='drive', source='remote', external_id='a1')
        with self.assertRaises(sqlite3.IntegrityError):
            self._store.add(rows=[1], action='drive', source='remote', external_id='a1')

    def test_unknown_source_or_state_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self._store.add(rows=[1], action='drive', source='cloud')
        with self.assertRaises(ValueError):
            self._store.add(rows=[1], action='drive', state='paused')

    def test_run_result_is_updatable_and_identity_is_not(self) -> None:
        mid = cast(str, self._store.add(
            rows=[1], action='drive', source='remote', external_id='a1', groups=[4]))
        self._store.update(
            mid, state='ended', steps_done=2, stage='row', reason='nav',
            started_at='2026-11-06T09:00:00Z', ended_at='2026-11-06T10:00:00Z',
            result_sent=True, source='local', external_id='b2', groups=[8])
        mission = cast(dict, self._store.find(mid))
        self.assertEqual(
            (mission['state'], mission['steps_done'], mission['stage'], mission['reason'],
             mission['ended_at'], mission['result_sent']),
            ('ended', 2, 'row', 'nav', '2026-11-06T10:00:00Z', True))
        self.assertEqual(
            (mission['source'], mission['external_id'], mission['groups']),
            ('remote', 'a1', [4]))

    def test_update_rejects_an_unknown_state_and_changes_nothing(self) -> None:
        mid = cast(str, self._store.add(rows=[1], action='drive'))
        with self.assertRaises(ValueError):
            self._store.update(mid, state='paused', reason='x')
        mission = cast(dict, self._store.find(mid))
        self.assertEqual((mission['state'], mission['reason']), ('idle', None))

    def test_concurrent_reads_and_writes_do_not_corrupt_the_connection(self) -> None:
        errors: list[Exception] = []

        def worker(tag: int) -> None:
            try:
                for n in range(50):
                    mid = cast(str, self._store.add(rows=[1], action='drive', name=f'{tag}-{n}'))
                    self._store.update(mid, state='running')
                    self.assertIsNotNone(self._store.find(mid))
                    self.assertGreater(len(self._store.missions), 0)
            except Exception as exc:  # pylint: disable=broad-except
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(self._store.missions), 200)

    def test_find_by_external_id(self) -> None:
        mid = self._store.add(rows=[1], action='drive', source='remote', external_id='a1')
        self.assertEqual(cast(dict, self._store.find_by_external_id('a1'))['id'], mid)
        self.assertIsNone(self._store.find_by_external_id('zz'))

    def test_ids_are_not_reused_after_a_delete(self) -> None:
        self._store.add(rows=[1], action='drive')
        last = cast(str, self._store.add(rows=[1], action='drive'))
        self.assertTrue(self._store.delete(last))
        self.assertEqual(self._store.add(rows=[1], action='drive'), 'MISSION_3')

    def test_delete_refuses_a_running_mission(self) -> None:
        mid = cast(str, self._store.add(rows=[1], action='drive', state='running'))
        self.assertFalse(self._store.delete(mid))
        self.assertIsNotNone(self._store.find(mid))
        self.assertFalse(self._store.delete('MISSION_99'))

    def test_attach_ends_missions_left_running(self) -> None:
        local = cast(str, self._store.add(rows=[1], action='drive', state='running'))
        remote = cast(str, self._store.add(
            rows=[1], action='drive', source='remote', external_id='a1', state='running'))
        self._store.update(remote, steps_done=2, stage='row', result_sent=True)
        idle = cast(str, self._store.add(rows=[1], action='drive'))

        self._store.attach(None)

        local_m, remote_m, idle_m = (cast(dict, self._store.find(m)) for m in (local, remote, idle))
        self.assertEqual((local_m['state'], local_m['reason'], local_m['active']),
                         ('ended', 'reboot', True))
        self.assertTrue(local_m['ended_at'])
        self.assertIsNone(local_m['last_run_success'])
        self.assertEqual((remote_m['state'], remote_m['reason'], remote_m['active'],
                          remote_m['result_sent'], remote_m['steps_done'], remote_m['stage']),
                         ('ended', 'reboot', False, False, 2, 'row'))
        self.assertEqual((idle_m['state'], idle_m['reason']), ('idle', None))


class TestMissionSqliteMigration(unittest.TestCase):
    def test_database_from_before_the_new_columns_keeps_its_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'missions.db')
            old = sqlite3.connect(path)
            old.execute("""CREATE TABLE missions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, rows TEXT, action TEXT,
                action_params TEXT, repeat_every_hours INTEGER, active INTEGER,
                created_at TEXT, last_run_at TEXT, last_run_success INTEGER)""")
            old.execute("INSERT INTO missions (name, rows, action, action_params, active) "
                        "VALUES ('OLD', '[\"E1\"]', 'drive', '{}', 1)")
            old.commit()
            old.close()

            store = MissionSqliteStore(path)
            try:
                mission = cast(dict, store.find('MISSION_1'))
                self.assertEqual((mission['name'], mission['rows']), ('OLD', ['E1']))
                self.assertEqual((mission['source'], mission['state']), ('local', 'idle'))
                self.assertIsNone(mission['groups'])
                self.assertIsNotNone(store.add(
                    rows=['E2'], action='drive', source='remote', external_id='a1'))
                self.assertEqual(store.add(rows=['E3'], action='drive'), 'MISSION_3')
            finally:
                store.close()
            # Opening it again must not try to add the columns twice.
            MissionSqliteStore(path).close()


if __name__ == '__main__':
    unittest.main()
