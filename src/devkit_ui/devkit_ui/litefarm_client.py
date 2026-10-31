"""Send a row plan to a LiteFarm server. Standard library only, no ROS imports.

The robot station uses this to import a plan: log in as a LiteFarm user, then POST the plan to
/row_plan/import. The user needs the edit:row_plan permission (owner, manager or extension officer).
"""
import json
import urllib.error
import urllib.request
from dataclasses import dataclass

DEFAULT_TIMEOUT_S = 30


class LiteFarmError(RuntimeError):
    """LiteFarm refused a request, or could not be reached. The message is safe to show to a person."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class ImportResult:
    """What LiteFarm answered to an import."""

    created: bool
    row_plan_id: str
    row_count: int | None
    warnings: list[str]


def login(base_url: str, email: str, password: str, timeout: float = DEFAULT_TIMEOUT_S) -> tuple[str, str]:
    """Log in with an email and password. Return (token, user_id). The token expires, so log in again later."""
    body = {'user': {'email': email, 'password': password}, 'screenSize': {'screen_width': 1280, 'screen_height': 720}}
    status, data = _post(f'{base_url}/login', body, {}, timeout)
    if status != 200 or not data.get('id_token'):
        raise LiteFarmError(_explain(status, data, 'login failed (check the email and password)'), status)
    return data['id_token'], data['user']['user_id']


def import_row_plan(base_url: str, token: str, user_id: str, farm_id: str, location_id: str, geojson: dict,
                    timeout: float = DEFAULT_TIMEOUT_S) -> ImportResult:
    """Import a plan for a field. Importing the same plan again is not an error: ``created`` is False.

    Raise LiteFarmError if LiteFarm refuses it, for example when the rows lie outside the field, the hash does
    not match, or the map_id is already used for different rows.
    """
    headers = {'Authorization': f'Bearer {token}', 'user_id': user_id, 'farm_id': farm_id}
    status, data = _post(f'{base_url}/row_plan/import', {'location_id': location_id, 'geojson': geojson}, headers,
                         timeout)
    if status not in (200, 201):
        raise LiteFarmError(_explain(status, data, 'import refused'), status)
    return ImportResult(
        created=status == 201,
        row_plan_id=data['row_plan']['row_plan_id'],
        row_count=data.get('row_count'),
        warnings=list(data.get('warnings', [])),
    )


def _post(url: str, body: dict, headers: dict[str, str], timeout: float) -> tuple[int, dict]:
    """POST JSON and return (status, decoded JSON body). HTTP error statuses are returned, not raised."""
    request = urllib.request.Request(
        url, data=json.dumps(body).encode('utf-8'), method='POST',
        headers={'Content-Type': 'application/json', **headers})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, _decode(response.read())
    except urllib.error.HTTPError as error:
        return error.code, _decode(error.read())
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise LiteFarmError(f'cannot reach {url}: {error}') from error


def _decode(raw: bytes) -> dict:
    try:
        data = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _explain(status: int, data: dict, fallback: str) -> str:
    return f'{data.get("error") or data.get("message") or fallback} (HTTP {status})'
