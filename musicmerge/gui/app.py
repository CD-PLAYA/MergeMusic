"""MusicMerge window."""

import os
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox, QWizard

from .. import APP_NAME, __version__
from .pages import (BuildPage, CleanupPage, HistoryPage, ImportPage, LibrariesPage, ScanPage,
                    TagsPage, WelcomePage)
from .jobs import JobPanel


class MergeWizard(QWizard):
    def __init__(self):
        super().__init__()
        self.project = None
        self.setWindowTitle('%s %s' % (APP_NAME, __version__))
        self.setOption(QWizard.NoBackButtonOnStartPage, True)
        self.setOption(QWizard.NoCancelButtonOnLastPage, True)
        self.setButtonText(QWizard.NextButton, 'Continue')
        self.setButtonText(QWizard.FinishButton, 'Done')
        self.resize(860, 680)
        self.pages = [WelcomePage(), LibrariesPage(), ScanPage(), BuildPage(), TagsPage(),
                      ImportPage(), HistoryPage(), CleanupPage()]
        for p in self.pages:
            self.addPage(p)

    def running_panels(self):
        return [p for p in self.findChildren(JobPanel) if p.running]

    def _confirm_stop(self):
        running = self.running_panels()
        if not running:
            return True
        if QMessageBox.question(self, APP_NAME, 'A step is still running. Stop it and quit? '
                                'Finished work is kept, and you can continue later.') != QMessageBox.Yes:
            return False
        for p in running:
            p.stop()
        for p in running:
            p.wait(120000)
        return True

    def reject(self):
        if self._confirm_stop():
            super().reject()

    def closeEvent(self, event):
        if self._confirm_stop():
            event.accept()
        else:
            event.ignore()


def icon_path():
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for p in (os.path.join(base, 'musicmerge', 'resources', 'icon.png'),
              os.path.join(base, 'resources', 'icon.png')):
        if os.path.exists(p):
            return p
    return None


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    if icon_path():
        app.setWindowIcon(QIcon(icon_path()))
    w = MergeWizard()
    w.show()
    w.raise_()
    return app.exec()
