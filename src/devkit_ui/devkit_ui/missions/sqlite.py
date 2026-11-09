import functools
import json
import sqlite3
import threading
from collections.abc import Callable

from devkit_ui.time_utils import now_utc_str

_MUTABLE_COLUMNS = frozenset[str]((
    'name', 'rows', 'action', 'action_params', 'repeat_every_hours', 'active',
    # Run history: written by MissionStore.record_run()/reset(), not by operator edits.
    'last_run_at', 'last_run_success',
    # Run result: written by the run path and intake, never by operator edits.
    'state', 'steps_done', 'stage', 'reason', 'started_at', 'ended_at', 'result_sent',
))

# Columns added after the first release. _ensure_schema adds any that are missing, because
# CREATE TABLE IF NOT EXISTS does nothing for a database that already has the table.
_ADDED_COLUMNS: tuple[tuple[str, str], ...] = (
    ('source',      "TEXT NOT NULL DEFAULT 'local'"),   # local | remote
    ('external_id', 'TEXT'),                            # remote only, unique
    ('map_hash',    'TEXT'),                            # hash8 the mission was made against
    ('groups',      'TEXT'),                            # JSON, LiteFarm row per entry; null if local
    ('state',       "TEXT NOT NULL DEFAULT 'idle'"),    # idle | running | ended
    ('steps_done',  'INTEGER'),
    ('stage',       'TEXT'),
    ('reason',      'TEXT'),
    ('started_at',  'TEXT'),
    ('ended_at',    'TEXT'),
    ('result_sent', 'INTEGER'),                         # remote only
)

SOURCES = frozenset({'local', 'remote'})
STATES = frozenset({'idle', 'running', 'ended'})


def _locked(method):
    """Run a store method holding the connection lock, so threads never share the cursor state."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:  # pylint: disable=protected-access
            return method(self, *args, **kwargs)
    return wrapper


class MissionSqliteStore:
    """Owns the mission list and its SQLite persistence."""

    def __init__(self, path: str, on_write_done: Callable[[str], None] | None = None) -> None:
        """Open the database at path and create or upgrade its mission schema.

        on_write_done receives a status message after an add, update or delete is committed.
        SQLite initialization errors propagate.
        """
        self._path     = path
        self._on_write_done = on_write_done or (lambda _: None)
        # Every public method holds this lock, reads included, because the connection is shared
        # by the UI, the mission worker and (later) the radio thread. It is reentrant so a method
        # may call another, and so an on_write_done callback may read the store.
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()

    @property
    @_locked
    def missions(self) -> tuple[dict, ...]:
        """Fetches the list of missions."""
        cursor = self._conn.cursor()
        cursor.execute("SELECT * FROM missions ORDER BY id")
        result = cursor.fetchall()
        return tuple(self._deserialize_row(row) for row in result)

    # ── lifecycle ─────────────────────────────────────────────────────────

    @_locked
    def attach(self, _node) -> None:
        """Attaches the store to a node and ends every mission left running.

        A restart does not resume a run, so a mission still stored as running is ended with the
        reason reboot. Its steps_done and stage keep their last values and last_run_* are left
        alone, because the run did not finish. A remote mission also becomes inactive and its end
        report is marked unsent. Only one process may attach to a database: a second one would
        end a mission that is really running.
        """
        cursor = self._conn.cursor()
        cursor.execute("""
            UPDATE missions
            SET state = 'ended', reason = 'reboot', ended_at = ?,
                active = CASE WHEN source = 'remote' THEN 0 ELSE active END,
                result_sent = CASE WHEN source = 'remote' THEN 0 ELSE result_sent END
            WHERE state = 'running'
        """, (now_utc_str(),))
        self._conn.commit()

    @_locked
    def close(self) -> None:
        """Closes the store."""
        self._conn.close()

    # ── public API ────────────────────────────────────────────────────────

    @_locked
    def add(self,
            rows: list,
            action: str,
            action_params: dict | None = None,
            name: str = '',
            repeat_every_hours: int | None = None,
            active: bool = True,
            *,
            source: str = 'local',
            external_id: str | None = None,
            map_hash: str | None = None,
            groups: list | None = None,
            state: str = 'idle') -> str | None:
        """Add a mission and return its allocated id.

        The keyword-only arguments set the source, remote identity and initial state. A repeated
        non-null external_id raises sqlite3.IntegrityError. ValueError for an unknown source or
        state. SQLite and JSON serialization errors propagate; callback errors propagate after
        the commit."""

        if source not in SOURCES:
            raise ValueError(f'unknown source {source!r}')
        if state not in STATES:
            raise ValueError(f'unknown state {state!r}')

        ts = now_utc_str()

        mission = self._serialize_row({
            'name':               name,
            'rows':               list(rows),
            'action':             action,
            'action_params':      action_params or {},
            'repeat_every_hours': (None if repeat_every_hours is None
                                        else int(repeat_every_hours)),
            'active':             bool(active),
            'created_at':         ts,
            'last_run_at':        None,
            'last_run_success':   None,
            'source':             source,
            'external_id':        external_id,
            'map_hash':           map_hash,
            'groups':             groups,
            'state':              state,
        })

        cursor = self._conn.cursor()
        cursor.execute("""
        INSERT INTO missions
            (name, rows, action, action_params, repeat_every_hours, active, created_at,
             last_run_at, last_run_success,
             source, external_id, map_hash, groups, state)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            mission['name'],
            mission['rows'],
            mission['action'],
            mission['action_params'],
            mission['repeat_every_hours'],
            mission['active'],
            mission['created_at'],
            mission['last_run_at'],
            mission['last_run_success'],
            mission['source'],
            mission['external_id'],
            mission['map_hash'],
            mission['groups'],
            mission['state'],
        ))
        mid = f'MISSION_{cursor.lastrowid}'
        if not name:
            cursor.execute("UPDATE missions SET name = ? WHERE id = ?", (mid, cursor.lastrowid))
        self._conn.commit()
        self._on_write_done(f'{mid} created')
        return mid

    @_locked
    def delete(self, mid: str) -> bool:
        """Deletes a mission. Returns False, deleting nothing, if it is running or unknown:
        a running mission's result still has to be written to it."""
        cursor = self._conn.cursor()
        cursor.execute("DELETE FROM missions WHERE id = ? AND state != 'running'",
                       (self._db_id(mid),))
        self._conn.commit()
        if cursor.rowcount == 0:
            return False
        self._on_write_done(f'{mid} deleted')
        return True

    @_locked
    def update(self, mid: str, **fields) -> bool:
        """Updates mutable fields on a mission.

        Allowed fields: name, rows, action, action_params, repeat_every_hours, active,
        last_run_at, last_run_success, and the run result (state, steps_done, stage, reason,
        started_at, ended_at, result_sent). Other keys are ignored, including the identity of a
        remote mission (source, external_id, map_hash, groups), which is set once.
        ValueError for an unknown state, as in add().
        """

        if 'state' in fields and fields['state'] not in STATES:
            raise ValueError(f"unknown state {fields['state']!r}")

        serialized_fields = self._serialize_row(fields)

        set_blocks = []
        values = []

        for key, value in serialized_fields.items():
            if key not in _MUTABLE_COLUMNS:
                continue
            set_blocks.append(f"{key} = ?")
            values.append(value)

        if not set_blocks:
            return True

        cursor = self._conn.cursor()
        cursor.execute(f"""
            UPDATE missions
            SET {', '.join(set_blocks)}
            WHERE id = ?
        """, [*values, self._db_id(mid)])
        self._conn.commit()
        self._on_write_done(f'{mid} updated')
        return True

    @_locked
    def find(self, mid: str) -> dict | None:
        """Finds a mission by id."""
        cursor = self._conn.cursor()
        cursor.execute("SELECT * FROM missions WHERE id = ?", (self._db_id(mid),))
        result = cursor.fetchone()
        if result is None:
            return None
        return self._deserialize_row(result)

    @_locked
    def find_by_name(self, name: str) -> dict | None:
        """Finds a mission by name."""
        cursor = self._conn.cursor()
        cursor.execute("SELECT * FROM missions WHERE name = ?", (name,))
        result = cursor.fetchone()
        if result is None:
            return None
        return self._deserialize_row(result)

    def _ensure_schema(self) -> None:
        """Create the tables and bring an older database up to date.

        Adds each column of _ADDED_COLUMNS the missions table lacks, so a database written
        by an earlier version keeps its rows (source becomes local, state becomes idle).
        """
        cursor = self._conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS missions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                rows TEXT,
                action TEXT,
                action_params TEXT,
                repeat_every_hours INTEGER,
                active INTEGER,
                created_at TEXT,
                last_run_at TEXT,
                last_run_success INTEGER
            )
        """)
        have = {row['name'] for row in cursor.execute("PRAGMA table_info(missions)")}
        for column, ddl in _ADDED_COLUMNS:
            if column not in have:
                cursor.execute(f"ALTER TABLE missions ADD COLUMN {column} {ddl}")
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS missions_external_id
            ON missions(external_id) WHERE external_id IS NOT NULL
        """)
        self._conn.commit()

    @_locked
    def find_by_external_id(self, external_id: str) -> dict | None:
        """Finds a remote mission by the id the station gave it."""
        cursor = self._conn.cursor()
        cursor.execute("SELECT * FROM missions WHERE external_id = ?", (external_id,))
        result = cursor.fetchone()
        return None if result is None else self._deserialize_row(result)

    def _serialize_row(self, row: dict) -> dict:
        """
        Serialize a mission row to something that can be stored in the database.

        Some implementation notes:
        * The ID is stored as an integer, but we expose it as a MISSION_<id> string.
        * Complex fields are saved as JSON strings.

        """
        serialized = dict(row)
        if 'id' in serialized:
            serialized['id'] = self._db_id(serialized['id'])
        if 'rows' in serialized:
            serialized['rows'] = json.dumps(serialized['rows'] or [])
        if 'action_params' in serialized:
            serialized['action_params'] = json.dumps(serialized['action_params'] or {})
        if 'active' in serialized:
            serialized['active'] = 1 if serialized['active'] else 0
        if 'groups' in serialized and serialized['groups'] is not None:
            serialized['groups'] = json.dumps(serialized['groups'])
        if serialized.get('result_sent') is not None:
            serialized['result_sent'] = 1 if serialized['result_sent'] else 0
        return serialized

    def _db_id(self, mission_id: str | int) -> int:
        """Converts a mission ID to a database ID."""
        if isinstance(mission_id, int):
            return mission_id
        return int(mission_id.replace('MISSION_', ''))

    def _deserialize_row(self, db_row: sqlite3.Row | dict) -> dict:
        """Return a mission dict with its ID, JSON fields, and boolean fields decoded."""
        row = dict(db_row)
        return {
            **row,
            'id': f"MISSION_{row['id']}",
            'rows': json.loads(row.get('rows', '[]')),
            'action_params': json.loads(row.get('action_params', '{}')),
            'active': row.get('active', 1) == 1,
            'last_run_success': (
                None if row.get('last_run_success') is None else bool(row['last_run_success'])),
            'groups': None if row.get('groups') is None else json.loads(row['groups']),
            'result_sent': (
                None if row.get('result_sent') is None else bool(row['result_sent'])),
        }
