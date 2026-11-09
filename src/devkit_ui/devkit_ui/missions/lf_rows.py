"""Find the entry nodes of a LiteFarm (field, row) in the topological map. No ROS imports.

A farm map import writes ``lf_field``, ``lf_row`` and ``lf_frag`` to the metadata of every node of
a swath, and ``row_id`` per swath. A LiteFarm row split by an obstacle is several swaths (fragments)
with the same field and row. The map is searched, so no row id is ever derived from a field and row.
"""
from devkit_ui.models import TopoDoc


class LfRowError(ValueError):
    """A (field, row) is not in the map, or the map names it ambiguously."""


def _int(value: object) -> int | None:
    """Return value if it is a real int (not a bool), else None."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def lf_row_entries(doc: TopoDoc) -> dict[tuple[int, int], list[tuple[int, int, str]]]:
    """Return ``{(field, row): [(row_id, frag, entry node name), ...]}`` for every LiteFarm row in doc.

    Only entry nodes count. Fragments are in row id order. A node with no integer lf_field, lf_row and
    row_id is ignored. A missing lf_frag is 0.
    """
    found: dict[tuple[int, int], list[tuple[int, int, str]]] = {}
    for node in doc.nodes:
        meta = node.meta
        field, row, row_id = _int(meta.get('lf_field')), _int(meta.get('lf_row')), _int(meta.get('row_id'))
        frag = _int(meta.get('lf_frag', 0))
        if meta.get('row_role') != 'entry' or None in (field, row, row_id, frag):
            continue
        found.setdefault((field, row), []).append((row_id, frag, node.name))
    for fragments in found.values():
        fragments.sort()
    return found


def resolve_lf_row(doc: TopoDoc, field: int, row: int) -> list[str]:
    """Return the entry node names of a LiteFarm row, one per fragment, in row id order.

    Raise LfRowError when the map has no such row, two swaths claim the same fragment, or the
    fragment numbers do not rise with the row ids.
    """
    fragments = lf_row_entries(doc).get((field, row))
    if not fragments:
        raise LfRowError(f'field {field} row {row} is not in the map')
    frags = [frag for _, frag, _ in fragments]
    if len(set(frags)) != len(frags):
        raise LfRowError(f'field {field} row {row}: two swaths have the same fragment')
    if frags != sorted(frags):
        raise LfRowError(f'field {field} row {row}: fragments are out of order')
    return [name for _, _, name in fragments]
