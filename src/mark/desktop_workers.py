from PyQt6.QtCore import QTimer

from .desktop_task import Task


class WorkerMixin:
    def _task(self, operation, success, failure=None, on_state_changed=None):
        if self.closing_when_idle:
            return None
        worker = Task(operation, self)
        self.tasks.add(worker)
        worker.completed.connect(lambda value, w=worker: self._finish_task(w, success, value))
        worker.failed.connect(lambda message, w=worker: self._finish_error(w, failure, message))
        if on_state_changed is not None:
            worker.state_changed.connect(on_state_changed)
        worker.start()
        return worker

    def _finish_task(self, worker, callback, value):
        self.tasks.discard(worker)
        worker.deleteLater()
        callback(value)
        self._close_when_idle()

    def _finish_error(self, worker, callback, message):
        self.tasks.discard(worker)
        worker.deleteLater()
        if callback:
            callback(message)
        self._close_when_idle()

    def _close_when_idle(self):
        if self.closing_when_idle and not self.tasks:
            QTimer.singleShot(0, self.close)

    def closeEvent(self, event):
        self.status_timer.stop()
        self.history_timer.stop()
        if hasattr(self,'library_page') and self.library_page.worker.isRunning():
            self.library_page.shutdown()
            QTimer.singleShot(150,self.close)
            event.ignore();return
        if self.tasks:
            self.closing_when_idle = True
            self.connection_label.setText("Finalizando a tarefa antes de fechar…")
            self.centralWidget().setDisabled(True)
            event.ignore()
            self._close_when_idle()
            return
        self.closing_when_idle = True
        event.accept()

