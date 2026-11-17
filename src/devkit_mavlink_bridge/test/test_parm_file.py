"""params/sowbot_rover.parm must parse and keep the values the docs and bridge rely on."""

from pathlib import Path

PARM = Path(__file__).parents[1] / 'params' / 'sowbot_rover.parm'


def _load():
    values = {}
    for line in PARM.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        name, _, value = line.partition(',')
        assert name and value and name == name.upper() and name not in values, line
        values[name] = float(value)
    return values


def test_file_parses_with_unique_numeric_values():
    """Verify every non-comment line is NAME,number and no name repeats."""
    assert len(_load()) > 5


def test_values_match_mavlink_md_and_the_bridge():
    """Verify the serial, GPS, fence and failsafe values documented elsewhere are the ones loaded."""
    values = _load()
    assert values['SERIAL2_PROTOCOL'] == values['SERIAL3_PROTOCOL'] == 5  # GPS
    assert values['SERIAL5_PROTOCOL'] == 2  # MAVLink2, the bridge link
    assert values['SERIAL5_BAUD'] == 115  # 115200, the bridge default
    assert (values['GPS1_TYPE'], values['GPS2_TYPE']) == (17, 18)  # moving-base base, rover
    assert 'GPS_TYPE' not in values  # the pre-4.6 name is ignored by 4.6 firmware
    assert values['FENCE_ACTION'] == 2 and values['FS_ACTION'] == 2  # Hold, not RTL
    assert values['FENCE_ENABLE'] == 1 and values['FENCE_TYPE'] == 4  # polygon only
