"""Run RoSys coroutines from the rclpy executor thread."""

import asyncio
import logging
from collections.abc import Coroutine
from concurrent.futures import Future
from typing import Any

from nicegui import core

_log = logging.getLogger('devkit_driver.loop')


def run_on_rosys_loop(coroutine: Coroutine[Any, Any, Any]) -> Future:
    """Schedule coroutine on the RoSys event loop and return a future for its result.

    rclpy callbacks run on their own thread, and the RoSys loop is not thread-safe:
    nicegui's background_tasks.create calls loop.create_task, which must only run on
    the loop thread. run_coroutine_threadsafe hands the coroutine over correctly.
    Exceptions raised by the coroutine are logged, never lost.
    Raise RuntimeError, after closing the coroutine, if the loop is not running.
    """
    loop = core.loop
    if loop is None or not loop.is_running():
        coroutine.close()
        raise RuntimeError('RoSys event loop is not running')
    future = asyncio.run_coroutine_threadsafe(coroutine, loop)
    future.add_done_callback(_log_failure)
    return future


def _log_failure(future: Future) -> None:
    """Log an exception raised by a scheduled coroutine."""
    if future.cancelled():
        return
    error = future.exception()
    if error is not None:
        _log.error('RoSys task failed: %r', error, exc_info=error)
