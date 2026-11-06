import json
import sqlite3
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
    ('task_id',     'TEXT'),                            # remote only, the LiteFarm task
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

class MissionSqliteStore:
    """Owns the mission list and its SQLite persistence."""

    def __init__(self, path: str, on_write_done: Callable[[str], None] | None = None) -> None:
        self._path     = path
        self._on_write_done = on_write_done or (lambda _: None)
        # We can disable same-thread check because we use locks to synchronize access.
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()

    @property
    def missions(self) -> tuple[dict, ...]:
        """Fetches the list of missions."""
        cursor = self._conn.cursor()
        cursor.execute("SELECT * FROM missions ORDER BY id")
        result = cursor.fetchall()
        return tuple(self._deserialize_row(row) for row in result)

    # ── lifecycle ─────────────────────────────────────────────────────────

    def attach(self, node) -> None:
        """Attaches the store to a node."""
        # Nothing needed here.

    def close(self) -> None:
        """Closes the store."""
        self._conn.close()

    # ── public API ────────────────────────────────────────────────────────

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
            task_id: str | None = None,
            map_hash: str | None = None,
            groups: list | None = None,
            state: str = 'idle') -> str | None:
        """Add a mission. Returns its allocated id, or None on failure.
        Status carries the reason either way.

        The keyword-only arguments are for remote missions. A repeated external_id raises
        sqlite3.IntegrityError. ValueError for an unknown source or state."""

        if source not in SOURCES:
            raise ValueError(f'unknown source {source!r}')
        if state not in STATES:
            raise ValueError(f'unknown state {state!r}')

        ts = now_utc_str()

        mid = f"MISSION_{self._next_id()}"

        mission = self._serialize_row({
            'id':                 mid,
            'name':               name or mid,
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
            'task_id':            task_id,
            'map_hash':           map_hash,
            'groups':             groups,
            'state':              state,
        })

        cursor = self._conn.cursor()
        cursor.execute("""
        INSERT INTO missions
            (id, name, rows, action, action_params, repeat_every_hours, active, created_at,
             last_run_at, last_run_success,
             source, external_id, task_id, map_hash, groups, state)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            mission['id'],
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
            mission['task_id'],
            mission['map_hash'],
            mission['groups'],
            mission['state'],
        ))
        self._conn.commit()
        self._on_write_done(f'{mid} created')
        return mid

    def delete(self, mid: str) -> bool:
        """Deletes a mission."""
        cursor = self._conn.cursor()
        cursor.execute("DELETE FROM missions WHERE id = ?", (self._db_id(mid),))
        self._conn.commit()
        self._on_write_done(f'{mid} deleted')
        return True

    def update(self, mid: str, **fields) -> bool:
        """Updates mutable fields on a mission.

        Allowed fields: name, rows, action, action_params, repeat_every_hours, active,
        last_run_at, last_run_success, and the run result (state, steps_done, stage, reason,
        started_at, ended_at, result_sent). Other keys are ignored, including the identity of a
        remote mission (source, external_id, task_id, map_hash, groups), which is set once.
        """

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

    def find(self, mid: str) -> dict | None:
        """Finds a mission by id."""
        cursor = self._conn.cursor()
        cursor.execute("SELECT * FROM missions WHERE id = ?", (self._db_id(mid),))
        result = cursor.fetchone()
        if result is None:
            return None
        return self._deserialize_row(result)

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
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS intake_epoch (
                epoch TEXT PRIMARY KEY,
                max_counter INTEGER NOT NULL
            )
        """)
        self._conn.commit()

    # ── remote intake ─────────────────────────────────────────────────────

    def max_counter(self, epoch: str) -> int | None:
        """Return the highest counter accepted for an epoch, or None if none was."""
        cursor = self._conn.cursor()
        cursor.execute("SELECT max_counter FROM intake_epoch WHERE epoch = ?", (epoch,))
        result = cursor.fetchone()
        return None if result is None else int(result[0])

    def record_counter(self, epoch: str, counter: int) -> None:
        """Raise an epoch's highest accepted counter to `counter`; never lowers it."""
        cursor = self._conn.cursor()
        cursor.execute("""
            INSERT INTO intake_epoch (epoch, max_counter) VALUES (?, ?)
            ON CONFLICT(epoch) DO UPDATE SET max_counter = MAX(max_counter, excluded.max_counter)
        """, (epoch, int(counter)))
        self._conn.commit()

    def prune_remote(self, keep: int = 20) -> int:
        """Delete all but the newest `keep` remote missions and return how many went.

        Local missions are untouched. intake_epoch is not pruned, so replay protection
        outlives the records.
        """
        cursor = self._conn.cursor()
        cursor.execute("""
            DELETE FROM missions
            WHERE source = 'remote' AND id NOT IN (
                SELECT id FROM missions WHERE source = 'remote' ORDER BY id DESC LIMIT ?
            )
        """, (int(keep),))
        self._conn.commit()
        return cursor.rowcount

    def _next_id(self) -> int:
        """Gets the next ID."""
        cursor = self._conn.cursor()
        cursor.execute("SELECT MAX(id) FROM missions")
        result = cursor.fetchone()
        if result is None or result[0] is None:
            return 1
        return int(result[0]) + 1

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
