"""The wizard's pages, one per step."""

import os
import subprocess

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
                               QPushButton, QVBoxLayout, QWizard, QWizardPage)

from ..core import cleanup, music_app
from ..core.build import build_merged, build_text
from ..core.common import WORK_DIR_NAME, human, is_mac, plural
from ..core.fingerprint import download_fpcalc, find_fpcalc
from ..core.plan import build_plan, summary_text
from ..core.project import Project, find_xml
from ..core.tags import album_folders, beets_available, run_tags, tags_text
from .jobs import JobPanel

SECONDS_PER_ALBUM = 12      # measured on a real library: MusicBrainz + AcoustID lookups


def label(text, rich=True):
    w = QLabel(text)
    w.setWordWrap(True)
    w.setTextFormat(Qt.RichText if rich else Qt.PlainText)
    w.setOpenExternalLinks(True)
    w.setTextInteractionFlags(Qt.TextBrowserInteraction)
    return w


def result_box():
    box = QPlainTextEdit()
    box.setReadOnly(True)
    font = QFont('Menlo' if is_mac() else 'Monospace')
    font.setStyleHint(QFont.Monospace)
    box.setFont(font)
    box.setMinimumHeight(150)
    box.setVisible(False)
    return box


def open_path(path):
    QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def reveal(path):
    if is_mac():
        subprocess.run(['/usr/bin/open', '-R', path])
    else:
        open_path(path if os.path.isdir(path) else os.path.dirname(path))


class StepPage(QWizardPage):
    @property
    def project(self):
        return self.wizard().project

    def show_result(self, box, text):
        box.setPlainText(text)
        box.setVisible(True)

    def lock_navigation(self, locked):
        """While a step runs, the user must not leave the page."""
        self.completeChanged.emit()
        w = self.wizard()
        for b in (QWizard.BackButton, QWizard.CancelButton):
            w.button(b).setEnabled(not locked)


# ---------------------------------------------------------------- 1. welcome
class WelcomePage(StepPage):
    def __init__(self):
        super().__init__()
        self.setTitle('Welcome to MusicMerge')
        self.setSubTitle('Merge your music libraries into one clean library with no duplicates.')
        lay = QVBoxLayout(self)
        lay.addWidget(label(
            '<p>MusicMerge takes two or more iTunes or Music libraries and builds one new '
            'library with every song once, in the best copy you have.</p>'
            '<p><b>How it works, one step at a time:</b></p>'
            '<ol>'
            '<li>Choose your library folders and a new folder for the result.</li>'
            '<li>Scan: find duplicates by their tags and by their sound. Nothing is changed.</li>'
            '<li>Build the merged folder. Your original libraries are never touched.</li>'
            '<li>Optionally clean up tags and add cover art from MusicBrainz.</li>'
            '<li>Add the merged folder to the Music app.</li>'
            '<li>Bring back play counts, ratings, loved songs and playlists.</li>'
            '<li>See what you can safely delete afterwards.</li>'
            '</ol>'
            '<p>Every step can be stopped and continued later. MusicMerge never deletes '
            'anything; the clean-up at the end is up to you.</p>'))
        lay.addStretch(1)


# ---------------------------------------------------------------- 2. libraries
class LibrariesPage(StepPage):
    def __init__(self):
        super().__init__()
        self.setTitle('Choose your libraries')
        self.setSubTitle('Add each library folder, then choose a new, empty folder for the merged library.')
        self.libs = []                          # [{'path', 'xml'}]
        self.dest = ''

        self.list = QListWidget()
        self.list.setMinimumHeight(150)
        self.list.currentRowChanged.connect(self._update_buttons)
        add = QPushButton('Add Library Folder...')
        add.clicked.connect(self._add)
        self.remove_btn = QPushButton('Remove')
        self.remove_btn.clicked.connect(self._remove)
        self.up_btn = QPushButton('Move Up')
        self.up_btn.clicked.connect(lambda: self._move(-1))
        self.down_btn = QPushButton('Move Down')
        self.down_btn.clicked.connect(lambda: self._move(1))
        self.xml_btn = QPushButton('Choose History File...')
        self.xml_btn.clicked.connect(self._choose_xml)
        self.noxml_btn = QPushButton('No History File')
        self.noxml_btn.clicked.connect(self._no_xml)

        buttons = QHBoxLayout()
        for b in (add, self.remove_btn, self.up_btn, self.down_btn):
            buttons.addWidget(b)
        buttons.addStretch(1)
        xml_row = QHBoxLayout()
        xml_row.addWidget(self.xml_btn)
        xml_row.addWidget(self.noxml_btn)
        xml_row.addStretch(1)

        self.dest_edit = QLineEdit()
        self.dest_edit.setReadOnly(True)
        self.dest_edit.setPlaceholderText('New, empty folder for the merged library')
        dest_btn = QPushButton('Choose...')
        dest_btn.clicked.connect(self._choose_dest)
        resume_btn = QPushButton('Continue an Earlier Merge...')
        resume_btn.clicked.connect(self._resume)
        dest_row = QHBoxLayout()
        dest_row.addWidget(QLabel('Merged library:'))
        dest_row.addWidget(self.dest_edit, 1)
        dest_row.addWidget(dest_btn)

        self.problems = label('')
        self.problems.setStyleSheet('color: #c0392b;')

        lay = QVBoxLayout(self)
        lay.addWidget(label(
            'Put the library whose play history matters most <b>first</b>: when two copies of a '
            'song are equally good, the first library wins. A library folder is the one that holds '
            'your music, for example <i>iTunes</i> or <i>iTunes Media</i>.'))
        lay.addWidget(self.list, 1)
        lay.addLayout(buttons)
        lay.addLayout(xml_row)
        lay.addWidget(label(
            '<small>The <b>history file</b> holds play counts, ratings and playlists. iTunes keeps '
            'one next to its media folder and MusicMerge finds it. For a newer Music library, make '
            'one in Music with <i>File &gt; Library &gt; Export Library...</i> and choose it here.</small>'))
        lay.addSpacing(8)
        lay.addLayout(dest_row)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(resume_btn)
        lay.addLayout(row)
        lay.addWidget(self.problems)
        self._refresh()

    # ---- list handling
    def _refresh(self):
        row = self.list.currentRow()
        self.list.clear()
        for i, lib in enumerate(self.libs, 1):
            xml = os.path.basename(lib['xml']) if lib['xml'] else 'none found'
            item = QListWidgetItem('%d.  %s\n      %s\n      History file: %s'
                                   % (i, os.path.basename(lib['path']) or lib['path'], lib['path'], xml))
            self.list.addItem(item)
        if self.libs:
            self.list.setCurrentRow(min(max(row, 0), len(self.libs) - 1))
        self._update_buttons()
        self._check()

    def _update_buttons(self, *_):
        row = self.list.currentRow()
        has = 0 <= row < len(self.libs)
        for b in (self.remove_btn, self.xml_btn, self.noxml_btn):
            b.setEnabled(has)
        self.up_btn.setEnabled(has and row > 0)
        self.down_btn.setEnabled(has and row < len(self.libs) - 1)

    def _add(self):
        path = QFileDialog.getExistingDirectory(self, 'Choose a library folder')
        if not path:
            return
        self.libs.append({'path': os.path.abspath(path), 'xml': find_xml(path)})
        self.list.setCurrentRow(len(self.libs) - 1)
        self._refresh()
        self.list.setCurrentRow(len(self.libs) - 1)

    def _remove(self):
        row = self.list.currentRow()
        if 0 <= row < len(self.libs):
            del self.libs[row]
            self._refresh()

    def _move(self, step):
        row = self.list.currentRow()
        new = row + step
        if 0 <= row < len(self.libs) and 0 <= new < len(self.libs):
            self.libs[row], self.libs[new] = self.libs[new], self.libs[row]
            self._refresh()
            self.list.setCurrentRow(new)

    def _choose_xml(self):
        row = self.list.currentRow()
        if not 0 <= row < len(self.libs):
            return
        start = self.libs[row]['xml'] or self.libs[row]['path']
        path, _ = QFileDialog.getOpenFileName(self, 'Choose the library history file', start,
                                              'Library XML (*.xml)')
        if path:
            self.libs[row]['xml'] = path
            self._refresh()

    def _no_xml(self):
        row = self.list.currentRow()
        if 0 <= row < len(self.libs):
            self.libs[row]['xml'] = ''
            self._refresh()

    def _choose_dest(self):
        path = QFileDialog.getExistingDirectory(
            self, 'Choose or create an empty folder for the merged library')
        if path:
            self.dest = os.path.abspath(path)
            if Project(self.dest).exists():
                self._load(self.dest)
            self.dest_edit.setText(self.dest)
            self._check()

    def _resume(self):
        path = QFileDialog.getExistingDirectory(self, 'Choose the merged library folder of an earlier merge')
        if not path:
            return
        if not Project(path).exists():
            QMessageBox.information(self, 'MusicMerge',
                                    'That folder does not hold an earlier merge (no %s folder inside).'
                                    % WORK_DIR_NAME)
            return
        self._load(path)

    def _load(self, path):
        p = Project(path)
        self.dest = p.dest
        self.libs = [{'path': l['path'], 'xml': l.get('xml', '')} for l in p.libraries]
        self.dest_edit.setText(self.dest)
        self._refresh()

    def _check(self):
        probs = []
        if self.dest:
            p = Project(self.dest)
            p.state['libraries'] = [dict(l) for l in self.libs]
            probs = p.problems()
        elif self.libs:
            probs = ['Choose a folder for the merged library.']
        self.problems.setText('<br>'.join(probs))
        self.completeChanged.emit()

    def isComplete(self):
        if not (self.libs and self.dest):
            return False
        p = Project(self.dest)
        p.state['libraries'] = [dict(l) for l in self.libs]
        return not p.problems()

    def validatePage(self):
        p = Project(self.dest)
        p.set_libraries(self.libs)
        p.save()
        self.wizard().project = p
        return True


# ---------------------------------------------------------------- 3. scan
class ScanPage(StepPage):
    def __init__(self):
        super().__init__()
        self.setTitle('Find duplicates')
        self.setSubTitle('Reads every song and plans the merge. Nothing is changed yet.')
        self.fp_check = QCheckBox('Compare the sound of songs too (finds duplicates even when '
                                  'their names or tags differ)')
        self.fp_note = label('')
        self.fp_btn = QPushButton('Download the Fingerprint Tool...')
        self.fp_btn.clicked.connect(self._download_fpcalc)
        self.panel = JobPanel('Scan Libraries')
        self.panel.start_btn.clicked.connect(self._start)
        self.panel.started.connect(lambda: self.lock_navigation(True))
        self.panel.succeeded.connect(self._done)
        self.panel.failed.connect(lambda m: self.lock_navigation(False))
        self.panel.stopped.connect(lambda: self.lock_navigation(False))
        self.result = result_box()
        files = QHBoxLayout()
        self.files_row = []
        for text, name in (('Open Plan', 'plan.csv'), ('Open Review List', 'review.csv'),
                           ('Show All Plan Files', None)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, n=name: self._open(n))
            files.addWidget(b)
            self.files_row.append(b)
        files.addStretch(1)

        lay = QVBoxLayout(self)
        lay.addWidget(self.fp_check)
        fp_row = QHBoxLayout()
        fp_row.addWidget(self.fp_note, 1)
        fp_row.addWidget(self.fp_btn)
        lay.addLayout(fp_row)
        lay.addWidget(self.panel)
        lay.addWidget(self.result, 1)
        lay.addLayout(files)

    def initializePage(self):
        self._fp_state()
        self.fp_check.setChecked(self.project.use_fingerprints and find_fpcalc() is not None)
        done = self.project.done('plan')
        self.panel.start_btn.setText('Scan Again' if done else 'Scan Libraries')
        if done and os.path.exists(self.project.path('plan_summary.txt')):
            with open(self.project.path('plan_summary.txt'), encoding='utf-8') as fh:
                self.show_result(self.result, fh.read())
        for b in self.files_row:
            b.setEnabled(done)

    def _fp_state(self):
        fp = find_fpcalc()
        self.fp_check.setEnabled(fp is not None)
        self.fp_btn.setVisible(fp is None)
        self.fp_note.setText(
            '' if fp else '<small>The fingerprint tool (fpcalc, from the Chromaprint project) '
            'is not installed. Without it, duplicates are found by their tags only.</small>')

    def _download_fpcalc(self):
        self._mode = 'download'
        self.panel.run(lambda rep: download_fpcalc(rep))

    def _start(self):
        p = self.project
        p.use_fingerprints = self.fp_check.isChecked()
        p.save()
        self._mode = 'scan'
        self.result.setVisible(False)
        self.panel.run(lambda rep: build_plan(p, rep))

    def _done(self, numbers):
        if getattr(self, '_mode', 'scan') == 'download':
            self.lock_navigation(False)
            self._fp_state()
            self.fp_check.setChecked(find_fpcalc() is not None)
            self.panel.status.setText('The fingerprint tool is installed: %s' % numbers)
            return
        self.show_result(self.result, summary_text(self.project, numbers))
        self.panel.start_btn.setText('Scan Again')
        for b in self.files_row:
            b.setEnabled(True)
        self.lock_navigation(False)

    def _open(self, name):
        if name:
            open_path(self.project.path(name))
        else:
            open_path(self.project.work)

    def isComplete(self):
        return self.project is not None and self.project.done('plan') and not self.panel.running


# ---------------------------------------------------------------- 4. build
class BuildPage(StepPage):
    def __init__(self):
        super().__init__()
        self.setTitle('Build the merged library')
        self.setSubTitle('Puts the best copy of every song into the new folder. '
                         'Your original libraries are not changed.')
        self.info = label('')
        self.panel = JobPanel('Build Merged Library')
        self.panel.start_btn.clicked.connect(self._start)
        self.panel.started.connect(lambda: self.lock_navigation(True))
        self.panel.succeeded.connect(self._done)
        self.panel.failed.connect(lambda m: self.lock_navigation(False))
        self.panel.stopped.connect(lambda: self.lock_navigation(False))
        self.result = result_box()
        show = QPushButton('Show Merged Library')
        show.clicked.connect(lambda: open_path(self.project.dest))
        row = QHBoxLayout()
        row.addWidget(show)
        row.addStretch(1)
        lay = QVBoxLayout(self)
        lay.addWidget(self.info)
        lay.addWidget(self.panel)
        lay.addWidget(self.result, 1)
        lay.addLayout(row)

    def initializePage(self):
        n = self.project.step_info('plan')
        self.info.setText(
            '<p><b>%d songs</b> (%s) will be placed in <i>%s</i>, sorted into Artist / Album '
            'folders. %d duplicate copies are left out.</p>'
            '<p><small>On a Mac, when the merged folder is on the same drive as your libraries, '
            'songs are cloned: this is fast and uses almost no extra space.</small></p>'
            % (n.get('keep', 0), human(n.get('keep_bytes', 0)), self.project.dest, n.get('drop', 0)))
        done = self.project.done('build')
        self.panel.start_btn.setText('Build Again (adds only missing files)' if done else 'Build Merged Library')
        if done:
            self.show_result(self.result, build_text(self.project.step_info('build')))

    def _start(self):
        p = self.project
        self.panel.run(lambda rep: build_merged(p, rep))

    def _done(self, numbers):
        self.show_result(self.result, build_text(numbers))
        self.lock_navigation(False)

    def isComplete(self):
        return self.project is not None and self.project.done('build') and not self.panel.running


# ---------------------------------------------------------------- 5. tags
class TagsPage(StepPage):
    def __init__(self):
        super().__init__()
        self.setTitle('Clean up tags (optional)')
        self.setSubTitle('Corrects names, years and cover art from MusicBrainz, the open music database.')
        self.info = label('')
        self.panel = JobPanel('Start Tag Clean-up')
        self.panel.start_btn.clicked.connect(self._start)
        self.panel.started.connect(lambda: self.lock_navigation(True))
        self.panel.succeeded.connect(self._done)
        self.panel.failed.connect(lambda m: self.lock_navigation(False))
        self.panel.stopped.connect(self._stopped)
        self.result = result_box()
        lay = QVBoxLayout(self)
        lay.addWidget(self.info)
        lay.addWidget(self.panel)
        lay.addWidget(self.result, 1)

    def initializePage(self):
        albums = album_folders(self.project)
        hours = albums * SECONDS_PER_ALBUM / 3600.0
        when = ('about %.0f hours' % hours) if hours >= 1.5 else \
            'about ' + plural(max(1, round(hours * 60)), 'minute')
        text = ('<p>Each album is looked up on MusicBrainz. When the match is strong, its tags and '
                'cover art are corrected and the files are renamed neatly. Otherwise the album keeps '
                'the tags it has.</p>'
                '<p>Albums you only have part of usually keep their own tags: MusicBrainz will not '
                'confirm an album with songs missing. Copy-protected iTunes purchases (.m4p) and the '
                '<i>Unknown Artist</i> folder are left alone.</p>'
                '<p><b>%s: %s.</b> The Mac is kept awake. You can stop at any time and '
                'continue later, or skip this step with Continue.</p>' % (plural(albums, 'album'), when))
        if not beets_available():
            text += '<p style="color:#c0392b">The tagging tool (beets) is not installed, so this step is not available.</p>'
            self.panel.start_btn.setEnabled(False)
        self.info.setText(text)
        info = self.project.step_info('tags')
        if info.get('running') or (info and not info.get('complete')):
            self.panel.start_btn.setText('Continue Tag Clean-up')
        elif info.get('complete'):
            self.panel.start_btn.setText('Try Unmatched Albums Again')
        if info.get('albums') is not None and 'tracks' in info:
            self.show_result(self.result, tags_text(info))

    def _start(self):
        p = self.project
        retry = bool(p.step_info('tags').get('complete'))
        self.panel.run(lambda rep: run_tags(p, rep, retry_unmatched=retry))

    def _done(self, counts):
        self.show_result(self.result, tags_text(counts))
        self.panel.start_btn.setText('Try Unmatched Albums Again')
        self.lock_navigation(False)

    def _stopped(self):
        self.panel.start_btn.setText('Continue Tag Clean-up')
        self.lock_navigation(False)

    def isComplete(self):
        return not self.panel.running


# ---------------------------------------------------------------- 6. import
class ImportPage(StepPage):
    def __init__(self):
        super().__init__()
        self.setTitle('Add it to Music')
        self.setSubTitle('Make a fresh Music library and import the merged folder into it.')
        self.steps = label('')
        open_btn = QPushButton('Open Music')
        open_btn.clicked.connect(music_app.open_music)
        self.panel = JobPanel('Check Music', stoppable=False)
        self.panel.start_btn.clicked.connect(self._check)
        self.panel.started.connect(lambda: self.lock_navigation(True))
        self.panel.succeeded.connect(self._checked)
        self.panel.failed.connect(lambda m: self.lock_navigation(False))
        self.result = label('')
        row = QHBoxLayout()
        row.addWidget(open_btn)
        row.addStretch(1)
        lay = QVBoxLayout(self)
        lay.addWidget(self.steps)
        lay.addLayout(row)
        lay.addSpacing(6)
        lay.addWidget(label('When the import has finished, check that Music has every song:'))
        lay.addWidget(self.panel)
        lay.addWidget(self.result)
        lay.addStretch(1)
        open_btn.setVisible(is_mac())
        self.panel.setVisible(is_mac())

    def initializePage(self):
        dest = self.project.dest
        if is_mac():
            self.steps.setText(
                '<ol>'
                '<li><b>Unplug any iPhone or iPad</b> and quit Music.</li>'
                '<li>Hold the <b>Option</b> key while you open Music, then click '
                '<b>Create Library...</b> To keep everything on the same drive, save the new library '
                'there (for example next to the merged folder).</li>'
                '<li>In Music, open <b>Settings &gt; Files</b> and <b>uncheck</b> '
                '<i>Copy files to Music Media folder when adding to library</i> and '
                '<i>Keep Music Media folder organized</i>. Otherwise Music copies everything again.</li>'
                '<li>Choose <b>File &gt; Import...</b> and select <i>%s</i>. Wait until the song '
                'count stops rising.</li>'
                '</ol>'
                '<p><small>Copy-protected iTunes purchases (.m4p) only play on a Mac authorized for '
                'the Apple Account that bought them (Account &gt; Authorizations).</small></p>' % dest)
        else:
            self.steps.setText(
                '<p>Add the merged folder <i>%s</i> to your music player. The play-history step '
                'that follows needs the Music app on a Mac, so on this computer it is skipped.</p>' % dest)

    def _check(self):
        p = self.project
        self.panel.run(lambda rep: music_app.music_status(p))

    def _checked(self, s):
        expected = self.project.step_info('build').get('files', 0)
        ok = s['inside'] >= expected > 0
        self.result.setText(
            '<p>Music has <b>%d songs</b>; <b>%d</b> of them are from the merged folder '
            '(expected %d). %s</p>' % (
                s['songs'], s['inside'], expected,
                'That is all of them.' if ok else
                'If the import is still running, wait and check again.'))
        self.lock_navigation(False)

    def isComplete(self):
        return not self.panel.running


# ---------------------------------------------------------------- 7. history
class HistoryPage(StepPage):
    def __init__(self):
        super().__init__()
        self.setTitle('Bring back play history')
        self.setSubTitle('Play counts, ratings, loved songs, last-played dates and playlists.')
        self.info = label('')
        self.check_panel = JobPanel('Check (changes nothing)', stoppable=False)
        self.check_panel.start_btn.clicked.connect(lambda: self._run(False))
        self.apply_btn = QPushButton('Restore History in Music')
        self.apply_btn.clicked.connect(lambda: self._run(True))
        self.check_panel.started.connect(lambda: self._busy(True))
        self.check_panel.succeeded.connect(self._done)
        self.check_panel.failed.connect(lambda m: self._busy(False))
        self.result = result_box()
        row = QHBoxLayout()
        row.addWidget(self.apply_btn)
        row.addStretch(1)
        lay = QVBoxLayout(self)
        lay.addWidget(self.info)
        lay.addWidget(self.check_panel)
        lay.addLayout(row)
        lay.addWidget(self.result, 1)
        self.applying = False

    def initializePage(self):
        n = self.project.step_info('plan')
        has = n.get('history_songs', 0) or n.get('playlists', 0)
        if not is_mac():
            self.info.setText('<p>This step needs the Music app on a Mac.</p>')
        elif not has:
            self.info.setText('<p>The libraries had no play history or playlists to bring back '
                              '(or no history file was chosen). You can continue.</p>')
        else:
            self.info.setText(
                '<p>%d songs had play counts, ratings or loves, and there are %d playlists. '
                'First <b>Check</b>: it only reads Music and shows what it can find. Then '
                '<b>Restore</b> writes them into the new library.</p>'
                '<p><small>macOS will ask to let MusicMerge control Music: choose Allow. '
                '"Date Added" cannot be carried over, because Music does not allow it. '
                'Running Restore again is safe; existing playlists are left alone.</small></p>'
                % (n.get('history_songs', 0), n.get('playlists', 0)))
        usable = bool(is_mac() and has)
        self.check_panel.start_btn.setEnabled(usable)
        self.apply_btn.setEnabled(usable)
        if self.project.done('history'):
            self.apply_btn.setText('Restore Again')

    def _busy(self, on):
        self.apply_btn.setEnabled(not on)
        self.lock_navigation(on)

    def _run(self, apply):
        if apply and QMessageBox.question(
                self, 'MusicMerge', 'Write play counts, ratings, loved songs and playlists into the '
                'Music library that is open now?') != QMessageBox.Yes:
            return
        p = self.project
        self.applying = apply
        self.check_panel.run(lambda rep: music_app.restore_history(p, apply=apply, reporter=rep))

    def _done(self, res):
        self.show_result(self.result, music_app.history_text(res))
        self._busy(False)

    def isComplete(self):
        return not self.check_panel.running


# ---------------------------------------------------------------- 8. clean up
class CleanupPage(StepPage):
    def __init__(self):
        super().__init__()
        self.setTitle('Clean up')
        self.setSubTitle('What you can delete now, and how much space it gives back.')
        self.result = result_box()
        self.panel = JobPanel('Save Non-Music Files...', stoppable=True)
        self.panel.start_btn.clicked.connect(self._save)
        self.panel.started.connect(lambda: self.lock_navigation(True))
        self.panel.succeeded.connect(self._saved)
        self.panel.failed.connect(lambda m: self.lock_navigation(False))
        self.panel.stopped.connect(lambda: self.lock_navigation(False))
        self.reveal_row = QHBoxLayout()
        lay = QVBoxLayout(self)
        lay.addWidget(self.result, 1)
        lay.addWidget(self.panel)
        lay.addLayout(self.reveal_row)
        lay.addWidget(label(
            '<p><b>MusicMerge never deletes anything.</b> When you are happy with the new library, '
            'move the old library folders to the Trash yourself. Keep the merged folder and the '
            'new Music library. The <i>%s</i> folder inside the merged folder holds the plan and '
            'reports; delete it whenever you like.</p>' % WORK_DIR_NAME))

    def initializePage(self):
        rep = cleanup.cleanup_report(self.project)
        self.show_result(self.result, cleanup.cleanup_text(rep))
        self.panel.start_btn.setEnabled(rep['to_save'] > 0)
        while self.reveal_row.count():
            w = self.reveal_row.takeAt(0).widget()
            if w:
                w.deleteLater()
        for lib in rep['libraries']:
            b = QPushButton('Show Library %d' % lib['number'])
            b.setEnabled(lib['exists'])
            b.clicked.connect(lambda _=False, p=lib['path']: reveal(p))
            self.reveal_row.addWidget(b)
        self.reveal_row.addStretch(1)

    def _save(self):
        target = QFileDialog.getExistingDirectory(self, 'Choose a folder for the videos, documents and other files')
        if not target:
            return
        p = self.project
        self.panel.run(lambda rep: cleanup.save_extras(p, target, rep))

    def _saved(self, res):
        self.panel.status.setText('Saved %d files (%d were already there).' % (res['saved'], res['skipped']))
        self.lock_navigation(False)

    def isComplete(self):
        return not self.panel.running
