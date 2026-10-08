"""Running engine steps in the background, with a progress bar, Stop button and log."""

import traceback

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar, QPushButton,
                               QToolButton, QVBoxLayout, QWidget)

from ..core.common import Cancelled, Reporter


class Job(QObject):
    progress = Signal(str, int, int)
    log = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
    ended = Signal()

    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self._stop = False

    def stop(self):
        self._stop = True

    @Slot()
    def run(self):
        rep = Reporter(progress=self.progress.emit, log=self.log.emit, stop=lambda: self._stop)
        try:
            result = self.fn(rep)
        except Cancelled:
            self.cancelled.emit()
        except Exception as e:  # shown to the user, with details in the log
            self.log.emit(traceback.format_exc())
            self.failed.emit(str(e) or e.__class__.__name__)
        else:
            self.succeeded.emit(result)
        finally:
            self.ended.emit()


class JobPanel(QWidget):
    """Start button, progress bar, status line and a collapsible activity log."""

    started = Signal()
    succeeded = Signal(object)
    failed = Signal(str)
    stopped = Signal()

    def __init__(self, start_text, stoppable=True, parent=None):
        super().__init__(parent)
        self.thread = None
        self.job = None
        self.start_btn = QPushButton(start_text)
        self.stop_btn = QPushButton('Stop')
        self.stop_btn.setVisible(stoppable)
        self.stop_btn.setEnabled(False)
        self.bar = QProgressBar()
        self.bar.setTextVisible(True)
        self.bar.setVisible(False)
        self.status = QLabel('')
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.toggle = QToolButton()
        self.toggle.setText('Show activity')
        self.toggle.setCheckable(True)
        self.toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(Qt.RightArrow)
        self.logbox = QPlainTextEdit()
        self.logbox.setReadOnly(True)
        self.logbox.setMaximumBlockCount(5000)
        self.logbox.setVisible(False)
        self.logbox.setMinimumHeight(140)
        self.toggle.toggled.connect(self._toggle_log)
        self.stop_btn.clicked.connect(self.stop)

        row = QHBoxLayout()
        row.addWidget(self.start_btn)
        row.addWidget(self.stop_btn)
        row.addStretch(1)
        row.addWidget(self.toggle)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(row)
        lay.addWidget(self.bar)
        lay.addWidget(self.status)
        lay.addWidget(self.logbox, 1)

    def _toggle_log(self, on):
        self.logbox.setVisible(on)
        self.toggle.setArrowType(Qt.DownArrow if on else Qt.RightArrow)
        self.toggle.setText('Hide activity' if on else 'Show activity')

    @property
    def running(self):
        return self.thread is not None

    def run(self, fn):
        """Run fn(reporter) in the background."""
        if self.running:
            return
        self.job = Job(fn)
        self.thread = QThread()
        self.job.moveToThread(self.thread)
        self.thread.started.connect(self.job.run)
        self.job.progress.connect(self._on_progress)
        self.job.log.connect(self._on_log)
        self.job.succeeded.connect(self._on_success)
        self.job.failed.connect(self._on_failure)
        self.job.cancelled.connect(self._on_cancel)
        self.job.ended.connect(self.thread.quit)
        self.thread.finished.connect(self._cleanup)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.bar.setVisible(True)
        self.bar.setRange(0, 0)
        self.status.setText('Starting...')
        self._outcome = None
        self.started.emit()
        self.thread.start()

    def stop(self):
        if self.job:
            self.job.stop()
            self.stop_btn.setEnabled(False)
            self.status.setText('Stopping after the current file...')

    def wait(self, ms=60000):
        if self.thread:
            self.thread.wait(ms)

    def _on_progress(self, stage, done, total):
        if total:
            self.bar.setRange(0, total)
            self.bar.setValue(done)
            self.bar.setFormat('%v of %m')
            self.status.setText(stage)
        else:
            self.bar.setRange(0, 0)
            self.status.setText(stage + '...')

    def _on_log(self, text):
        self.logbox.appendPlainText(text)

    # Results arrive before the thread has finished; report them once it has, so that
    # "running" is already False for whoever listens.
    def _on_success(self, result):
        self._outcome = ('ok', result)

    def _on_failure(self, message):
        self._outcome = ('fail', message)

    def _on_cancel(self):
        self._outcome = ('cancel', None)

    def _cleanup(self):
        if self.thread:
            self.thread.deleteLater()
        if self.job:
            self.job.deleteLater()
        self.thread = None
        self.job = None
        kind, value = getattr(self, '_outcome', ('fail', 'the step ended unexpectedly'))
        self._outcome = None
        self.bar.setVisible(False)
        self.stop_btn.setEnabled(False)
        self.start_btn.setEnabled(True)
        if kind == 'ok':
            self.status.setText('Done.')
            self.succeeded.emit(value)
        elif kind == 'cancel':
            self.status.setText('Stopped. You can continue later; finished work is kept.')
            self.stopped.emit()
        else:
            self.status.setText('Stopped with a problem: ' + value)
            self.failed.emit(value)
