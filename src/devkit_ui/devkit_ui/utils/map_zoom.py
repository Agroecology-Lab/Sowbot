import json

from nicegui import ui

_MAX_ZOOM = 20


def enable_zoom(viewport_id: str, content_id: str) -> None:
    """Make an element pannable and zoomable by wheel, drag and pinch.

    The transform sits on the content element so map and robot layers can be
    redrawn without losing alignment.
    ``viewport_id`` and ``content_id`` identify the DOM elements receiving input
    and the transform, respectively. Zoom is limited to 1–20 times; double-click
    resets zoom and pan. If either element is missing or zoom is already enabled
    on the viewport, the script does nothing.
    """
    script = """
        ((viewportId, contentId, maxZoom) => {
            const view = document.getElementById(viewportId);
            const content = document.getElementById(contentId);
            if (!view || !content || view.__zoomEnabled) return;
            view.__zoomEnabled = true;
            view.style.touchAction = 'none';
            content.style.transformOrigin = '0 0';
            let k = 1, x = 0, y = 0;
            const pointers = new Map();
            let pinch = 0;

            const apply = () => {
                const {width, height} = view.getBoundingClientRect();
                k = Math.min(Math.max(k, 1), maxZoom);
                x = Math.min(Math.max(x, width * (1 - k)), 0);
                y = Math.min(Math.max(y, height * (1 - k)), 0);
                content.style.transform = `translate(${x}px, ${y}px) scale(${k})`;
            };

            const zoomAt = (px, py, factor) => {
                const next = Math.min(Math.max(k * factor, 1), maxZoom);
                x = px - (px - x) * next / k;
                y = py - (py - y) * next / k;
                k = next;
                apply();
            };

            const local = (e) => {
                const r = view.getBoundingClientRect();
                return [e.clientX - r.left, e.clientY - r.top];
            };

            view.addEventListener('wheel', (e) => {
                e.preventDefault();
                zoomAt(...local(e), Math.exp(-e.deltaY * 0.0015));
            }, {passive: false});

            view.addEventListener('dblclick', () => {
                k = 1; x = 0; y = 0; apply();
            });

            view.addEventListener('pointerdown', (e) => {
                view.setPointerCapture(e.pointerId);
                pointers.set(e.pointerId, [e.clientX, e.clientY]);
                pinch = 0;
            });

            const release = (e) => {
                pointers.delete(e.pointerId);
                pinch = 0;
            };
            view.addEventListener('pointerup', release);
            view.addEventListener('pointercancel', release);

            view.addEventListener('pointermove', (e) => {
                const prev = pointers.get(e.pointerId);
                if (!prev) return;
                if (pointers.size === 1) {
                    x += e.clientX - prev[0];
                    y += e.clientY - prev[1];
                    apply();
                }
                pointers.set(e.pointerId, [e.clientX, e.clientY]);
                if (pointers.size === 2) {
                    const [a, b] = [...pointers.values()];
                    const dist = Math.hypot(a[0] - b[0], a[1] - b[1]);
                    const r = view.getBoundingClientRect();
                    if (pinch) {
                        zoomAt(
                            (a[0] + b[0]) / 2 - r.left,
                            (a[1] + b[1]) / 2 - r.top,
                            dist / pinch,
                        );
                    }
                    pinch = dist;
                }
            });
        })
    """
    # NOTE: the script is full of literal braces, so pass the values as JSON arguments instead of str.format.
    ui.run_javascript(f'{script}({json.dumps(viewport_id)}, {json.dumps(content_id)}, {_MAX_ZOOM});')
