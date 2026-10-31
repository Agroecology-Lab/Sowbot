"""Command line: write a topological map's rows as a LiteFarm row plan, and optionally import it.

    python -m devkit_ui.row_plan_cli maps/maize_map -o rows.geojson
    LITEFARM_PASSWORD=... python -m devkit_ui.row_plan_cli maps/maize_map --import-to http://localhost:5000 \
        --email me@example.org --farm-id <uuid> --location-id <field uuid>
"""
import argparse
import json
import os
import sys

from devkit_ui.litefarm_client import LiteFarmError, import_row_plan, login
from devkit_ui.parse import parse_topo_yaml
from devkit_ui.row_plan_export import export_row_plan
from devkit_ui.row_plan_format import MAX_BYTES, RowPlanError, row_length_m

PASSWORD_ENV = 'LITEFARM_PASSWORD'


def main(argv: list[str] | None = None) -> int:
    """Run the command. Return the exit status: 0 on success, 1 for a refused or unreadable plan, 2 for bad use."""
    args = _parser().parse_args(argv)
    if args.import_to and not (args.email and args.farm_id and args.location_id):
        print('--import-to needs --email, --farm-id and --location-id', file=sys.stderr)
        return 2
    password = os.environ.get(PASSWORD_ENV, '')
    if args.import_to and not password:
        print(f'set {PASSWORD_ENV} to the LiteFarm password', file=sys.stderr)
        return 2
    try:
        plan = export_row_plan(parse_topo_yaml(args.topo_map), map_id=args.map_id, supersedes=args.supersedes,
                               field_location_id=args.location_id)
        text = json.dumps(plan.geojson, separators=(',', ':'))
        if len(text) > MAX_BYTES:
            raise RowPlanError('plan is larger than LiteFarm accepts')
    except (OSError, ValueError) as error:  # RowPlanError is a ValueError, so is a YAML or number error
        print(f'cannot export {args.topo_map}: {error}', file=sys.stderr)
        return 1
    metres = sum(row_length_m(r) for r in plan.rows)
    print(f'{len(plan.rows)} rows, {metres:.1f} m, map_id {plan.map_id}, {plan.ignored_nodes} other nodes ignored')
    for warning in plan.warnings:
        print(f'warning: {warning}', file=sys.stderr)
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as out:
            out.write(text)
        print(f'wrote {args.output}')
    if not args.import_to:
        return 0
    try:
        token, user_id = login(args.import_to.rstrip('/'), args.email, password)
        result = import_row_plan(args.import_to.rstrip('/'), token, user_id, args.farm_id, args.location_id,
                                 plan.geojson)
    except LiteFarmError as error:
        print(f'LiteFarm: {error}', file=sys.stderr)
        return 1
    print(f'{"imported" if result.created else "already imported"} as row plan {result.row_plan_id}')
    for warning in result.warnings:
        print(f'LiteFarm warning: {warning}', file=sys.stderr)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split('\n', maxsplit=1)[0])
    parser.add_argument('topo_map', help='topological map file (YAML), for example maps/maize_map')
    parser.add_argument('-o', '--output', help='write the row plan GeoJSON to this file')
    parser.add_argument('--map-id', help='plan id in LiteFarm (default: map name and the first 12 hash characters)')
    parser.add_argument('--supersedes', metavar='MAP_ID', help='map_id of the plan this one replaces')
    parser.add_argument('--import-to', metavar='URL', help='LiteFarm API address, for example http://localhost:5000')
    parser.add_argument('--email', help='LiteFarm user to log in as')
    parser.add_argument('--farm-id', help='LiteFarm farm id')
    parser.add_argument('--location-id', help='LiteFarm location id of the field the rows are in')
    return parser


if __name__ == '__main__':
    sys.exit(main())
