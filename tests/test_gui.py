"""Walks through the wizard window without a screen (Qt 'offscreen' platform)."""

import os
import time

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6')
from PySide6.QtWidgets import QApplication, QWizard  # noqa: E402

from mergemusic.core.common import is_mac  # noqa: E402


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


def wait_for(panel, timeout=300):
    end = time.time() + timeout
    while panel.running and time.time() < end:
        QApplication.processEvents()
        time.sleep(0.02)
    for _ in range(20):
        QApplication.processEvents()
    assert not panel.running, 'step did not finish in time'


def setup_libraries(w, libraries, dest):
    page = w.pages[1]
    page.libs = [{'path': libraries['a'], 'xml': libraries['xml']}, {'path': libraries['b'], 'xml': ''}]
    page.dest = dest
    page.dest_edit.setText(dest)
    page._refresh()
    return page


def test_welcome_banner(qapp, tmp_path):
    from mergemusic.gui.app import MergeWizard
    from mergemusic.gui.banner import Banner
    w = MergeWizard()
    w.show()
    for _ in range(20):
        QApplication.processEvents()
    welcome = w.pages[0]
    assert welcome.banner.loaded() and welcome.banner.isVisible()
    assert welcome.title() == ''                         # the banner carries the name
    assert 0 < welcome.banner.height() <= 260
    # the banner is really drawn: its middle is not the plain window background
    img = w.grab().toImage()
    mid = welcome.banner.mapTo(w, welcome.banner.rect().center())
    assert img.pixelColor(mid) != w.palette().window().color()
    w.close()

    missing = Banner(str(tmp_path / 'no-such-banner.jpg'))
    assert not missing.loaded() and missing.heightForWidth(800) == 0


def test_wizard_walkthrough(qapp, libraries, tmp_path, fpcalc):
    from mergemusic.gui.app import MergeWizard
    w = MergeWizard()
    w.show()
    assert w.currentPage() is w.pages[0]
    w.next()

    libs = w.pages[1]
    assert w.currentPage() is libs and not libs.isComplete()
    setup_libraries(w, libraries, str(tmp_path / 'Merged'))
    assert libs.isComplete(), libs.problems.text()
    assert 'iTunes Music Library.xml' in libs.list.item(0).text()
    w.next()

    scan = w.pages[2]
    assert w.currentPage() is scan and not scan.isComplete()
    scan.panel.start_btn.click()
    assert scan.panel.running and not w.button(QWizard.BackButton).isEnabled()
    wait_for(scan.panel)
    assert scan.isComplete(), scan.panel.status.text()
    assert 'Keep 12 files' in scan.result.toPlainText()
    assert w.button(QWizard.BackButton).isEnabled()
    w.next()

    build = w.pages[3]
    assert '12 songs' in build.info.text()
    build.panel.start_btn.click()
    wait_for(build.panel)
    assert build.isComplete(), build.panel.status.text()
    assert 'Checked in place: 12 of 12' in build.result.toPlainText()
    w.next()

    tags = w.pages[4]
    assert w.currentPage() is tags and tags.isComplete()          # optional step
    assert 'albums' in tags.info.text()
    w.next()

    imp = w.pages[5]
    assert w.currentPage() is imp
    assert str(tmp_path / 'Merged') in imp.steps.text()
    w.next()

    hist = w.pages[6]
    assert w.currentPage() is hist
    assert hist.check_panel.start_btn.isEnabled() == is_mac()
    w.next()

    clean = w.pages[7]
    assert w.currentPage() is clean
    text = clean.result.toPlainText()
    assert 'Lib One' in text and 'videos' in text
    assert clean.panel.start_btn.isEnabled()
    w.close()


def test_resume_earlier_merge(qapp, libraries, tmp_path, fpcalc):
    from mergemusic.core.common import Reporter
    from mergemusic.core.plan import build_plan
    from mergemusic.core.project import Project
    from mergemusic.gui.app import MergeWizard
    dest = str(tmp_path / 'Merged')
    p = Project(dest)
    p.set_libraries([{'path': libraries['a'], 'xml': libraries['xml']}, {'path': libraries['b']}])
    p.save()
    build_plan(p, Reporter())

    w = MergeWizard()
    w.show()
    w.next()
    libs = w.pages[1]
    libs._load(dest)
    assert len(libs.libs) == 2 and libs.isComplete()
    w.next()
    scan = w.pages[2]
    assert scan.isComplete() and 'Keep 12 files' in scan.result.toPlainText()
    assert scan.panel.start_btn.text() == 'Scan Again'
    w.close()
