"""Small NiceGUI helpers shared by the System and Sim pages: status labels, request host and links."""
from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

from nicegui import ui

if TYPE_CHECKING:
    from devkit_ui.view_models.system_view_model import ToolStatusView

STATUS_COLOURS = {'ok': '#1a7f37', 'error': '#cf222e', 'muted': '#57606a'}


def show_status(lbl, view: ToolStatusView | None) -> None:
    """Apply a status to lbl; do nothing for None, and keep the colour if the level is None."""
    if view is None:
        return
    lbl.set_text(view.text)
    if view.level is not None:
        lbl.style(f'color:{STATUS_COLOURS[view.level]}')


def request_host() -> str:
    """Return the browser's request hostname, bracketed for IPv6 and HTML-escaped for links."""
    host = ui.context.client.request.url.hostname or 'localhost'
    if ':' in host:
        host = f'[{host}]'
    return escape(host, quote=True)


def link_button(url: str, label: str) -> None:
    """Render an outlined external link styled like the tool buttons.

    url and label are inserted as HTML; callers must escape any untrusted values.
    """
    ui.html(
        f'<a href="{url}" target="_blank" '
        'style="font-size:13px;color:var(--blue);text-decoration:none;'
        'padding:6px 12px;border:1px solid var(--blue);border-radius:4px;'
        f'font-family:\'Courier New\',monospace;">{label}</a>'
    )


def doc_link(url: str, label: str = '📄 Documentation') -> None:
    """Render a small muted documentation link.

    url and label are inserted as HTML; callers must escape any untrusted values.
    """
    ui.html(
        f'<a href="{url}" target="_blank" '
        'style="font-size:11px;color:var(--txt-muted);text-decoration:none;'
        f'font-family:\'Courier New\',monospace;">{label}</a>'
    )
