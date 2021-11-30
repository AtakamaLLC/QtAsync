import logging
import os
from enum import Enum
from typing import Callable, Any, Iterable, Optional, Iterator, TypeVar, Union, TYPE_CHECKING, List, Dict
from concurrent.futures import Executor, Future, CancelledError, ThreadPoolExecutor, InvalidStateError
from concurrent.futures import TimeoutError as FutureTimeoutError

from PySide2.QtCore import QObject, Signal, QThreadPool, QRunnable, Slot

from .locks import PythonicQMutex, QThreadEvent, PythonicQWaitCondition
from ..types.bound import PYTHON_TIME
from ..types.unbound import SIGNAL_TYPE

if TYPE_CHECKING:
    from PySide2.QtCore import SignalInstance  # noqa: F401

log = logging.getLogger(__name__)


class FutureStatus(Enum):
    PENDING = 1
    RUNNING = 2
    CANCELLED = 3
    CANCELLED_AND_NOTIFIED = 4
    FINISHED = 5


class _QRunnable(QRunnable):
    def __init__(self, future: "PythonicQFuture", fn: Callable, args, kwargs):
        super().__init__()
        self.future = future
        self.fn = fn
        self.args = args
        self.kwargs = kwargs

    def run(self):
        if not self.future.set_running_or_notify_cancel():
            return

        try:
            result = self.fn(*self.args, **self.kwargs)
        except BaseException as ex:
            self.future.set_exception(ex)
            # Copied from concurrent.futures.thread._WorkItem.run()
            # self = None
        else:
            self.future.set_result(result)


class PythonicQFuture(QObject, Future):
    _cancelled: SIGNAL_TYPE = Signal()

    def __init__(self, parent: "QObject" = None):
        super().__init__(parent=parent)
        self._id = os.urandom(8).hex()

        self._result: Optional[Any] = None
        self._exception: Optional[BaseException] = None

        self._cond = PythonicQWaitCondition()
        self._state: "FutureStatus" = FutureStatus.PENDING

    @property
    def future_id(self) -> str:
        return self._id

    def cancel(self) -> bool:
        with self._cond:
            if self._state in [FutureStatus.FINISHED, FutureStatus.RUNNING]:
                return False
            elif self._state in [FutureStatus.CANCELLED, FutureStatus.CANCELLED_AND_NOTIFIED]:
                return True

            self._state = FutureStatus.CANCELLED
            self._cond.notify_all()

    def cancelled(self) -> bool:
        with self._cond:
            return self._state in [FutureStatus.CANCELLED, FutureStatus.CANCELLED_AND_NOTIFIED]

    def running(self) -> bool:
        with self._cond:
            return self._state == FutureStatus.RUNNING

    def done(self) -> bool:
        with self._cond:
            return self._state in [FutureStatus.CANCELLED, FutureStatus.CANCELLED_AND_NOTIFIED, FutureStatus.FINISHED]

    def __get_result(self):
        if self._exception:
            raise self._exception
        else:
            return self._result

    def result(self, timeout: Optional[PYTHON_TIME] = None):
        with self._cond:
            if self._state in [FutureStatus.CANCELLED, FutureStatus.CANCELLED_AND_NOTIFIED]:
                raise CancelledError()
            elif self._state == FutureStatus.FINISHED:
                return self.__get_result()

            if not self._cond.wait(timeout=timeout):
                raise FutureTimeoutError

            if self._state in [FutureStatus.CANCELLED, FutureStatus.CANCELLED_AND_NOTIFIED]:
                raise CancelledError()
            elif self._state == FutureStatus.FINISHED:
                return self.__get_result()
            else:
                raise FutureTimeoutError()

    def exception(self, timeout: Optional[PYTHON_TIME] = None) -> Optional[BaseException]:
        if not self._cond.wait(timeout=timeout):
            raise FutureTimeoutError
        with self._cond:
            if self._state in [FutureStatus.CANCELLED, FutureStatus.CANCELLED_AND_NOTIFIED]:
                raise CancelledError
            return self._exception

    def add_done_callback(self, fn: Callable[["PythonicQFuture"], Any]) -> None:
        with self._cond:
            if not self._state in [FutureStatus.CANCELLED, FutureStatus.CANCELLED_AND_NOTIFIED, FutureStatus.FINISHED]:
                self.finished.connect(fn)
                return

        try:
            fn(self)
        except:  # noqa: E722
            log.exception("Error when calling PythonicQFuture done callback")

    # Testing and Executor usage
    def set_running_or_notify_cancel(self) -> bool:
        with self._cond:
            if self._state == FutureStatus.CANCELLED:
                self._state = FutureStatus.CANCELLED_AND_NOTIFIED
                return False
            elif self._state == FutureStatus.PENDING:
                self._state = FutureStatus.RUNNING
                return True
            else:
                log.critical("Future %s in unexpected state: %s", id(self), self._state)
                raise RuntimeError("Future in unexpected state")

    def set_result(self, result) -> None:
        with self._cond:
            if self._state in (FutureStatus.CANCELLED, FutureStatus.CANCELLED_AND_NOTIFIED, FutureStatus.FINISHED):
                raise InvalidStateError("{}: {!r}".format(self._state, self))
            self._result = result
            self._state = FutureStatus.FINISHED
            self._cond.notify_all()

    def set_exception(self, exception: Optional[BaseException]) -> None:
        with self._cond:
            if self._state in (FutureStatus.CANCELLED, FutureStatus.CANCELLED_AND_NOTIFIED, FutureStatus.FINISHED):
                raise InvalidStateError("{}: {!r}".format(self._state, self))
            self._exception = exception
            self._state = FutureStatus.FINISHED
            self._cond.notify_all()


class QThreadPoolExecutor(Executor):
    def __init__(self, qthread_pool: "QThreadPool" = None):
        self._pool = qthread_pool or QThreadPool.globalInstance()
        self._shutdown_mutex = PythonicQMutex()
        self._is_shutdown = False

    def submit(self, fn, /, *args, **kwargs) -> PythonicQFuture:
        with self._shutdown_mutex:
            if self._is_shutdown:
                raise RuntimeError
            future = PythonicQFuture(parent=self._pool)
            runnable = _QRunnable(future, fn, args, kwargs)
            self._pool.start(runnable)
            return future

    def shutdown(self, wait=True, *, cancel_futures=False):
        with self._shutdown_mutex:
            self._is_shutdown = True

        if cancel_futures:
            self._pool.clear()

        if wait:
            self._pool.waitForDone()
