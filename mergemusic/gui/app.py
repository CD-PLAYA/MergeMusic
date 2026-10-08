"""MergeMusic window."""

import os
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox, QWizard

from .. import APP_NAME, __version__
from .banner import resource_path
from .pages import (BuildPage, CleanupPage, HistoryPage, ImportPage, LibrariesPage, ScanPage,
                    TagsPage, WelcomePage)
from .jobs import JobPanel


class MergeWizard(QWizard):
    def __init__(self):
        super().__init__()
        self.project = None
        self.setWindowTitle('%s %s' % (APP_NAME, __version__))
        # The same look everywhere. The Mac wizard style draws Apple's old Setup Assistant
        # picture (a tuxedo) down the left side and keeps that column even without it.
        self.setWizardStyle(QWizard.ModernStyle)
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
    return resource_path('icon.png')


def main(args=None):
    args = list(args or [])
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    if icon_path():
        app.setWindowIcon(QIcon(icon_path()))
    w = MergeWizard()
    w.show()
    w.raise_()
    if '--selftest' in args:            # used by the build to check the bundled app opens
        from PySide6.QtCore import QTimer
        shot = args[args.index('--screenshot') + 1] if '--screenshot' in args[:-1] else None
        def finish():
            ok = (w.isVisible() and len(w.pageIds()) == 8 and icon_path() is not None
                  and w.pages[0].banner.loaded())
            if shot:                    # the Welcome page, then the Libraries page
                w.grab().save(shot)
                w.next()
                app.processEvents()
                root, ext = os.path.splitext(shot)
                w.grab().save(root + '-libraries' + ext)
            print('GUI self-test: %s (platform %s)' % ('ok' if ok else 'FAILED', app.platformName()))
            app.exit(0 if ok else 1)
        QTimer.singleShot(1500, finish)
    return app.exec()
