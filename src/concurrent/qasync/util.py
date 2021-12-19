import sys
import logging
import functools
import asyncio

from src.env import QObject, Signal, QCoreApplication, Slot
from src.types.unbound import SIGNAL_TYPE

log = logging.getLogger(__name__)


def _make_signaller(*args):
    class Signaller(QObject):
        signal: SIGNAL_TYPE = Signal(*args)

    return Signaller()


class _SimpleTimer(QObject):
    def __init__(self):
        super().__init__()
        self.__callbacks = {}
        self._stopped = False
        self.__debug_enabled = False

    def add_callback(self, handle, delay=0):
        timerid = self.startTimer(int(delay * 1000))
        self.__log_debug("Registering timer id %s", timerid)
        assert timerid not in self.__callbacks
        self.__callbacks[timerid] = handle
        return handle

    def timerEvent(self, event):  # noqa: N802
        timerid = event.timerId()
        self.__log_debug("Timer event on id %s", timerid)
        if self._stopped:
            self.__log_debug("Timer stopped, killing %s", timerid)
            self.killTimer(timerid)
            del self.__callbacks[timerid]
        else:
            try:
                handle = self.__callbacks[timerid]
            except KeyError as e:
                self.__log_debug(e)
                pass
            else:
                if handle._cancelled:
                    self.__log_debug("Handle %s cancelled", handle)
                else:
                    self.__log_debug("Calling handle %s", handle)
                    handle._run()
            finally:
                del self.__callbacks[timerid]
                handle = None
            self.killTimer(timerid)

    def stop(self):
        self.__log_debug("Stopping timers")
        self._stopped = True

    def set_debug(self, enabled):
        self.__debug_enabled = enabled

    def __log_debug(self, *args, **kwargs):
        if self.__debug_enabled:
            log.debug(*args, **kwargs)


def _fileno(fd):
    if isinstance(fd, int):
        return fd
    try:
        return int(fd.fileno())
    except (AttributeError, TypeError, ValueError):
        raise ValueError(f"Invalid file object: {fd!r}") from None


def asyncClose(fn):
    """Allow to run async code before application is closed."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        f = asyncio.ensure_future(fn(*args, **kwargs))
        while not f.done():
            QCoreApplication.instance().processEvents()

    return wrapper


def asyncSlot(*args):
    """Make a Qt async slot run on asyncio loop."""

    def _error_handler(task):
        try:
            task.result()
        except Exception:
            sys.excepthook(*sys.exc_info())

    def outer_decorator(fn):
        @Slot(*args)
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            task = asyncio.ensure_future(fn(*args, **kwargs))
            task.add_done_callback(_error_handler)
            return task

        return wrapper

    return outer_decorator
