"""Load driver handler modules without a ROS installation.

Registers stand-ins for rclpy and the message packages, and a bare devkit_driver package
whose __init__ is not executed, so only the handler under test is imported.
"""
import importlib
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

_PACKAGE_DIR = Path(__file__).resolve().parents[1] / 'devkit_driver'


class FakeMessage:
    """Message stand-in that keeps keyword arguments as attributes."""

    def __init__(self, **fields):
        self.__dict__.update(fields)


def _module(name: str, **attributes) -> ModuleType:
    module = ModuleType(name)
    module.__dict__.update(attributes)
    return module


def _package(name: str, path: Path) -> ModuleType:
    module = ModuleType(name)
    module.__path__ = [str(path)]
    return module


def install_stubs() -> None:
    """Install stand-ins once; safe to call repeatedly."""
    if getattr(sys.modules.get('devkit_driver'), '_test_stub', False):
        return
    class Bool(FakeMessage):
        pass

    class Empty(FakeMessage):
        pass

    class Twist(FakeMessage):
        def __init__(self, **fields):
            super().__init__(**fields)
            self.linear = SimpleNamespace(x=0.0, y=0.0, z=0.0)
            self.angular = SimpleNamespace(x=0.0, y=0.0, z=0.0)

    qos_names = ('DurabilityPolicy', 'Duration', 'LivelinessPolicy', 'QoSProfile', 'ReliabilityPolicy')
    stubs = {
        'rclpy': _module('rclpy'),
        'rclpy.node': _module('rclpy.node', Node=type('Node', (), {})),
        'rclpy.qos': _module('rclpy.qos', **{
            name: SimpleNamespace(RELIABLE='reliable', TRANSIENT_LOCAL='transient_local', AUTOMATIC='automatic')
            if name.endswith('Policy') else (lambda *a, **k: SimpleNamespace(args=a, kwargs=k))
            for name in qos_names}),
        'geometry_msgs': _module('geometry_msgs'),
        'geometry_msgs.msg': _module('geometry_msgs.msg', Twist=Twist),
        'std_msgs': _module('std_msgs'),
        'std_msgs.msg': _module('std_msgs.msg', Bool=Bool, Empty=Empty),
    }
    for name, module in stubs.items():
        sys.modules.setdefault(name, module)
    package = _package('devkit_driver', _PACKAGE_DIR)
    package._test_stub = True
    sys.modules['devkit_driver'] = package
    sys.modules['devkit_driver.modules'] = _package('devkit_driver.modules', _PACKAGE_DIR / 'modules')


def load(name: str) -> ModuleType:
    """Import devkit_driver.<name> with the stand-ins in place."""
    install_stubs()
    return importlib.import_module(f'devkit_driver.{name}')
