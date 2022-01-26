import logging
from typing import Optional

from QtPy.env import (
    QSemaphore,
    QMutex,
    QRecursiveMutex,
    QWaitCondition,
    QtModuleName,
    PYQT5_MODULE_NAME,
    PYQT6_MODULE_NAME,
    PYSIDE6_MODULE_NAME,
)

from QtPy.types.bound import QT_TIME, PYTHON_TIME
from QtPy.util import qt_timeout, mk_q_deadline_timer

log = logging.getLogger(__name__)


class PythonicQMutex:
    def __init__(self, default_timeout: PYTHON_TIME = 0, recursive: bool = False):
        if QtModuleName in (PYQT6_MODULE_NAME, PYSIDE6_MODULE_NAME):
            self._mutex = QRecursiveMutex() if recursive else QMutex()
        else:
            self._mutex = QMutex(QMutex.Recursive if recursive else QMutex.NonRecursive)
        self._default_timeout: QT_TIME = qt_timeout(default_timeout)

    # Python methods to match threading.Lock/RLock's interface
    def __enter__(self):
        if not self._mutex.tryLock(self._default_timeout):
            raise TimeoutError("QMutex timed out")

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._mutex.unlock()

    def acquire(self, blocking=True, timeout: PYTHON_TIME = -1.0):
        if blocking:
            # Negative timeout in a QMutex behaves the same as a Python lock
            return self._mutex.tryLock(qt_timeout(timeout))
        else:
            if timeout != -1.0:
                raise ValueError("Cannot specify a timeout for a non-blocking call")
            return self._mutex.try_lock()

    def release(self):
        self._mutex.unlock()

    @property
    def default_timeout(self) -> float:
        return self._default_timeout

    @default_timeout.setter
    def default_timeout(self, new_timeout: float):
        self._default_timeout = qt_timeout(new_timeout)

    def is_recursive(self) -> bool:
        return self._mutex.isRecursive()

    # Qt methods, pass-through to underlying mutex
    # def lock(self):
    #     return self._mutex.lock()
    #
    # def unlock(self):
    #     return self._mutex.unlock()
    #
    # def tryLock(self, timeout: QT_TIME = 0) -> bool:
    #     return self._mutex.tryLock(timeout)
    #
    # def try_lock(self) -> bool:
    #     return self._mutex.try_lock()


class PythonicQWaitCondition:
    def __init__(self):
        self._mutex = PythonicQMutex()
        self._cond = QWaitCondition()

    # Python methods to match threading.Condition
    def __enter__(self):
        self.acquire()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()

    def acquire(self):
        self._mutex.acquire()

    def release(self):
        self._mutex.release()

    def wait(self, timeout: PYTHON_TIME = None) -> bool:
        if timeout is None:
            return self._cond.wait(self._mutex._mutex)
        else:
            if QtModuleName == PYQT5_MODULE_NAME:
                # For some reason, the QDeadlineTimer does not work with PyQt5 and wait() instantly returns
                return self._cond.wait(self._mutex._mutex, msecs=qt_timeout(timeout))
            else:
                return self._cond.wait(self._mutex._mutex, mk_q_deadline_timer(timeout))

    def notify_all(self):
        self._cond.wakeAll()

    def notify(self):
        self._cond.wakeOne()


class QThreadEvent:
    def __init__(self):
        self._is_set = False
        self._flag_mutex = PythonicQMutex()
        self._cond = PythonicQWaitCondition()

    def is_set(self) -> bool:
        with self._flag_mutex:
            return self._is_set

    def set(self):
        with self._flag_mutex:
            if not self._is_set:
                self._is_set = True
                with self._cond:
                    self._cond.notify_all()

    def clear(self):
        with self._flag_mutex:
            self._is_set = False

    def wait(self, timeout: Optional[PYTHON_TIME] = None) -> bool:
        """
        This wait function is designed to emulate threading.Event.wait()

        :param timeout: The time to wait before timing out in seconds
        """
        # Lock the event flag first
        with self._flag_mutex:
            if self._is_set:
                return True

        with self._cond:
            return self._cond.wait(timeout)


class PythonicQSemaphore(QSemaphore):
    def __init__(self, value=1):
        super().__init__(n=value)

    def __enter__(self):
        self.acquire()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()

    def release(self, n=1):
        return super().release(n=n)

    def acquire(self, blocking=True, timeout: PYTHON_TIME = None) -> bool:
        if blocking:
            return self.tryAcquire(1, qt_timeout(timeout))
        else:
            return self.tryAcquire(1)
