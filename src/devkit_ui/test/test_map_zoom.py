"""Unit tests for the zoom helper's generated script."""
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

scripts: list[str] = []
nicegui = ModuleType('nicegui')
nicegui.ui = SimpleNamespace(run_javascript=scripts.append)

with patch.dict(sys.modules, {'nicegui': nicegui}):
    from devkit_ui.utils import map_zoom


class TestEnableZoom(unittest.TestCase):
    def setUp(self):
        """Start each test with no recorded scripts."""
        scripts.clear()

    def test_script_is_built_without_error_and_receives_the_element_ids(self):
        """Verify the literal JS braces survive and the ids and zoom limit are passed as arguments."""
        map_zoom.enable_zoom('c1', 'c2')

        self.assertEqual(len(scripts), 1)
        self.assertIn('const {width, height} = view.getBoundingClientRect();', scripts[0])
        max_zoom = map_zoom._MAX_ZOOM  # pylint: disable=protected-access
        self.assertTrue(scripts[0].rstrip().endswith(f'("c1", "c2", {max_zoom});'))
