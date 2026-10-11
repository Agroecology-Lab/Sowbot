"""Small NiceGUI helpers shared by the System and Sim pages: status labels, request host and links."""
from html import escape

from nicegui import ui

from devkit_ui.process_registry import alive


def restore_label(lbl, proc) -> None:
    """Show a running process in a status label after a page reload."""
    if alive(proc):
        lbl.set_text(f'running (pid {proc.pid})')
        lbl.style('color:#1a7f37')


def report_if_exited(proc, lbl, log: str) -> None:
    """Two seconds after start, say so if the process already died."""
    def check() -> None:
        """Show the exit code and log path if the process has exited; otherwise keep the label."""
        if proc.poll() is not None:
            lbl.set_text(f'exited ({proc.returncode}) - see {log}')
            lbl.style('color:#cf222e')
    ui.timer(2.0, check, once=True)


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
