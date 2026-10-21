from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import Qt, QThread, pyqtSignal, pyqtSlot

from .cognition.agent_loop import CancelSignal


class Task(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)
    state_changed = pyqtSignal(str, str, str, str)

    def __init__(self, operation: Callable, parent=None):
        super().__init__(parent)
        self.operation = operation
        self._result = None
        self._error = None
        self.cancel_signal = CancelSignal()
        self.finished.connect(self._deliver, Qt.ConnectionType.QueuedConnection)

    def cancel(self) -> None:
        """Sinaliza interrupção graciosa para operações que consultam o cancel_signal."""
        self.cancel_signal.cancel()

    def notify_state(self, phase_category: str, tool_name: str, action_detail: str, step_info: str = "") -> None:
        """Emite transição de estado observável para a interface (God's Eye View)."""
        self.state_changed.emit(phase_category, tool_name, action_detail, step_info)

    def run(self):
        try:
            import inspect
            sig = inspect.signature(self.operation)
            if len(sig.parameters) >= 1:
                self._result = self.operation(self)
            else:
                self._result = self.operation()
        except Exception as exc:
            self._error = str(exc) or type(exc).__name__

    @pyqtSlot()
    def _deliver(self):
        if self._error is None:
            self.completed.emit(self._result)
        else:
            self.failed.emit(self._error)
