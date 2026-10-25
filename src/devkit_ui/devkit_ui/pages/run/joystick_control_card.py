from nicegui import ui

from devkit_ui.pages.widgets import request_host
from devkit_ui.view_models.global_view_model import GlobalViewModel
from devkit_ui.view_models.run_view_model import RunViewModel

# web_video_server port (neo.launch.py: web_video_port, default 8081) and the row-navigation debug image.
WEB_VIDEO_PORT = 8081
ROW_DEBUG_TOPIC = '/caatinga_vision/row_nav/debug_image'
# Size requested from web_video_server; the stream is embedded directly as an <img>, because the
# stream_viewer page adds a topic heading and body margin that push the image out of a fixed frame.
ROW_DEBUG_WIDTH = 260
ROW_DEBUG_HEIGHT = 195


class JoystickControlCard(ui.card):
    def __init__(self, global_vm: GlobalViewModel, run_vm: RunViewModel):
        """
        Initialize the joystick control card with its state and control callbacks.

        Parameters:
            global_vm (GlobalViewModel): Global application state containing the emergency-stop status.
            run_vm (RunViewModel): Run view model containing the joystick state and controls.
        """

        super().__init__()

        self.classes('shrink-0')

        with self:
            ui.label('Joystick').classes('sec-label')

            with ui.row().classes('items-center gap-6'):
                joystick = ui.joystick(
                    color='#1a7f37',
                    size=130,
                    on_move=lambda e: run_vm.move_joystick(float(e.y), float(e.x)),
                    on_end=lambda _: run_vm.stop_joystick()
                )
                joystick.on('start', run_vm.heartbeat_joystick, js_handler=f'''() => {{
                    const stick = getElement({joystick.id});
                    clearInterval(stick.inputHeartbeat);
                    stick.inputHeartbeat = setInterval(() => {{
                        if (!stick.$el.isConnected || document.hidden || !window.socket?.connected) {{
                            clearInterval(stick.inputHeartbeat);
                            return;
                        }}
                        emit();
                    }}, 100);
                }}''')
                joystick.on('end', js_handler=f'''() => {{
                    clearInterval(getElement({joystick.id}).inputHeartbeat);
                }}''')
                # NOTE: the bridge zeroes any cmd_vel older than ~0.5 s, so keep a held stick alive.
                ui.timer(0.1, run_vm.repeat_joystick)

                with ui.column().classes('gap-3 items-center'):
                    # Emergency stop
                    self.estop_btn = ui.button(
                        on_click=lambda _: global_vm.toggle_estop()
                    ).props('outline no-caps').classes('estop-btn')

                    def sync_estop_state(is_active: bool) -> str:
                        self.estop_btn.props(f'color={"negative" if is_active else "primary"}')
                        return 'STOPPED' if is_active else 'E-Stop'

                    self.estop_btn.bind_text_from(
                        global_vm, 'soft_estop_active',
                        backward=sync_estop_state
                    )

                    # Pose label
                    ui.label().bind_text_from(run_vm.joystick, 'pose_lbl').classes(
                        'text-xs font-mono text-[#57606a] text-center max-w-[130px] whitespace-pre-line'
                    )

            # Resolved from the browser's own request host, so it also works from another machine.
            ui.element('img').props(
                f'src="http://{request_host()}:{WEB_VIDEO_PORT}/stream?topic={ROW_DEBUG_TOPIC}'
                f'&width={ROW_DEBUG_WIDTH}&height={ROW_DEBUG_HEIGHT}&type=mjpeg" '
                'alt="Row navigation debug image"'
            ).style(
                f'width:{ROW_DEBUG_WIDTH}px;max-width:100%;aspect-ratio:4/3;object-fit:contain;'
                'border-radius:4px;background:#000'
            )
