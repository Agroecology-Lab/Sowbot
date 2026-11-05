"""Browser regression for the fullscreen map.

Run from the repository root after installing development dependencies and Chromium:
    python -m playwright install chromium
    PYTHONPATH=src/devkit_ui python -m unittest discover -s src/devkit_ui/browser_tests
"""
import os
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from playwright.sync_api import sync_playwright


class TestFullscreenMap(unittest.TestCase):
    def test_click_selects_a_node_and_drag_still_pans(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        server = subprocess.Popen(  # pylint: disable=consider-using-with
            [sys.executable, __file__, '--serve', str(port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])},
        )
        self.addCleanup(server.wait, timeout=10)
        self.addCleanup(server.terminate)
        url = f'http://127.0.0.1:{port}/run/map'
        deadline = time.monotonic() + 20
        while True:
            try:
                with urlopen(url, timeout=1):
                    break
            except URLError:
                if server.poll() is not None or time.monotonic() >= deadline:
                    self.fail('Fullscreen map test server did not start')
                time.sleep(0.1)

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={'width': 1000, 'height': 700}, has_touch=True)
            page.set_default_timeout(5000)
            page.goto(url)
            node_a = page.locator('.topo-node[data-node="A"]')
            node_b = page.locator('.topo-node[data-node="B"]')
            node_a.wait_for()
            node_a.click()
            page.wait_for_function("document.querySelector('[data-node=\"A\"]').getAttribute('fill') === '#fff8c5'")

            # Zoom, then drag from B; capture must prevent that drag from selecting B.
            node_b.hover()
            page.mouse.wheel(0, -500)
            page.wait_for_function("document.querySelector('[data-node=\"B\"]').closest('#topo-map')"
                                   ".parentElement.parentElement.style.transform.includes('scale(')")
            content = page.locator('#topo-map').locator('xpath=../..')
            transform_before_drag = content.evaluate('(element) => element.style.transform')
            box = node_b.bounding_box()
            self.assertIsNotNone(box)
            page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
            page.mouse.down()
            page.mouse.move(box['x'] + box['width'] / 2 - 80, box['y'] + box['height'] / 2 + 40, steps=5)
            page.mouse.up()
            self.assertNotEqual(content.evaluate('(element) => element.style.transform'), transform_before_drag)
            self.assertEqual(node_a.get_attribute('fill'), '#fff8c5')
            self.assertEqual(node_b.get_attribute('fill'), '#ffffff')

            # The delegated handler must still work after the selection redraw and a drag.
            node_b.click()
            page.wait_for_function("document.querySelector('[data-node=\"B\"]').getAttribute('fill') === '#fff8c5'")
            self.assertEqual(node_a.get_attribute('fill'), '#ffffff')

            # Touch pinch remains functional after capture is delayed.
            page.mouse.dblclick(500, 350)
            touch = page.context.new_cdp_session(page)
            touch.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [
                {'x': 450, 'y': 350, 'id': 0}, {'x': 550, 'y': 350, 'id': 1},
            ]})
            for right in (570, 600):
                touch.send('Input.dispatchTouchEvent', {'type': 'touchMove', 'touchPoints': [
                    {'x': 450, 'y': 350, 'id': 0}, {'x': right, 'y': 350, 'id': 1},
                ]})
            touch.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
            self.assertNotIn('scale(1)', content.evaluate('(element) => element.style.transform'))
            browser.close()


def serve(port):
    from unittest.mock import Mock  # pylint: disable=import-outside-toplevel

    from nicegui import ui  # pylint: disable=import-outside-toplevel

    from devkit_ui.constants import FULLSCREEN_MAP_PATH  # pylint: disable=import-outside-toplevel
    from devkit_ui.models import TopoDoc, TopoNode  # pylint: disable=import-outside-toplevel
    from devkit_ui.pages.run.node_map_card import NodeMapCard  # pylint: disable=import-outside-toplevel
    from devkit_ui.view_models.run_view_model import RunViewModel  # pylint: disable=import-outside-toplevel
    from devkit_ui.view_models.topology_view_model import TopologyViewModel  # pylint: disable=import-outside-toplevel

    topo_vm = TopologyViewModel(Mock())
    topo_vm.update_doc(TopoDoc(name='field', nodes=[
        TopoNode(name='A', x=0.0, y=0.0), TopoNode(name='B', x=2.0, y=3.0),
    ]))

    @ui.page(FULLSCREEN_MAP_PATH)
    def fullscreen_map():
        ui.query('.nicegui-content').classes('p-0')
        NodeMapCard(topo_vm, RunViewModel.NodeMap(), fullscreen=True)

    ui.run(host='127.0.0.1', port=port, reload=False, show=False)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--serve':
        serve(int(sys.argv[2]))
    else:
        unittest.main()
