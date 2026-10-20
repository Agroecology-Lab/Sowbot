"""Buttons, links and status labels for the System page tools; process control is in SystemViewModel."""
from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import run as ng_run
from nicegui import ui

from devkit_ui.constants import TOOL_EXPLORER, TOOL_GRAPHER, TOOL_MEDKIT, TOOL_RVIZ
from devkit_ui.pages.widgets import STATUS_COLOURS, doc_link, link_button, request_host, show_status

if TYPE_CHECKING:
    from devkit_ui.view_models.system_view_model import SystemViewModel

_EXIT_CHECK_S = 2.0


class ToolsCard(ui.card):
    """Launch and stop the developer tools: Graph Explorer, ros2grapher, RViz and the Medkit gateway."""

    def __init__(self, system_vm: SystemViewModel) -> None:
        """Build one control group per tool, separated by rules.

        Parameters:
            system_vm (SystemViewModel): Starts, stops and reports on the tool processes.
        """
        super().__init__()
        with self:
            ui.label('Tools').classes('font-semibold mb-2')
            with ui.row().classes('items-center gap-3 flex-wrap'):
                host = request_host()
                _tool_controls(system_vm, TOOL_EXPLORER, 'Start Graph Explorer', 'Stop', host)
                link_button(f'http://{host}:8734/', '↗ Graph Explorer')
                doc_link('https://github.com/nilseuropa/ros2graph_explorer#build--launch')
                ui.separator().classes('w-full my-1')
                _tool_controls(system_vm, TOOL_GRAPHER, 'Start ros2grapher', 'Stop', host)
                link_button(f'http://{host}:8888/', '↗ ros2grapher')
                doc_link('https://github.com/Supull/ros2grapher')
                ui.separator().classes('w-full my-1')
                _tool_controls(system_vm, TOOL_RVIZ, 'Launch RViz', 'Stop RViz', host)
                link_button(f'http://{host}:6081/vnc.html', '↗ RViz (noVNC)')
                ui.separator().classes('w-full my-1')
                _tool_controls(system_vm, TOOL_MEDKIT, 'Start Medkit Gateway', 'Stop Medkit Gateway', host)
                _medkit_links(host)


def _tool_controls(vm: SystemViewModel, tool: str, start_text: str, stop_text: str, host: str) -> None:
    """Add a status label with start and stop buttons for tool.

    The label shows a tool that is already running, e.g. after a page reload. Starting and stopping
    run off the UI loop; a request that the view model ignores leaves the label as it was.
    """
    label = ui.label('').classes('text-xs font-mono').style(f'color:{STATUS_COLOURS["muted"]}')
    show_status(label, vm.status(tool))

    def report_if_exited() -> None:
        """Show the exit code and log path if the process has died since it started."""
        show_status(label, vm.exited(tool))

    async def start() -> None:
        """Start the tool, then check two seconds later that it is still alive."""
        view = await ng_run.io_bound(vm.start, tool, host)
        show_status(label, view)
        if view is not None:
            ui.timer(_EXIT_CHECK_S, report_if_exited, once=True)

    async def stop() -> None:
        """Show an interim label while the stop runs, and put the old one back if it was ignored."""
        previous = label.text
        label.set_text(vm.stopping().text)
        view = await ng_run.io_bound(vm.stop, tool)
        if view is None:
            label.set_text(previous)
        show_status(label, view)

    ui.button(start_text, on_click=start).props('outline no-caps').classes('px-4')
    ui.button(stop_text, on_click=stop).props('outline no-caps').classes('px-4')


def _medkit_links(host: str) -> None:
    """Link to the Medkit gateway and explain how to run its SOVD web UI on the host."""
    link_button(f'http://{host}:8080/', f'↗ Gateway ({host}:8080)')
    ui.html(
        '<div style="font-size:11px;color:var(--txt-muted);'
        'font-family:\'Courier New\',monospace;line-height:1.5;">'
        'SOVD web UI runs on the host, not in this container. On host:<br>'
        '<code>docker run -p 3000:80 ghcr.io/selfpatch/sovd_web_ui:latest</code><br>'
        f'Then open <a href="http://{host}:3000/" target="_blank" '
        f'style="color:var(--blue);text-decoration:none;">{host}:3000</a> '
        f'and set its gateway URL to <code>http://{host}:8080</code>.<br>'
        'The gateway starts on loopback for host-local use. Remote access requires '
        'authentication, TLS, and restricted network access.'
        '</div>'
    )
