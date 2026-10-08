# PyInstaller spec for MusicMerge.app. Build with packaging/build_macos.sh.
# -*- mode: python -*-
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, '..'))
sys.path.insert(0, ROOT)
from musicmerge import __version__  # noqa: E402

hidden = (collect_submodules('beets') + collect_submodules('beetsplug') +
          collect_submodules('musicmerge') + ['acoustid', 'audioread', 'mediafile', 'confuse'])
datas = (collect_data_files('beets') + collect_data_files('beetsplug') +
         collect_data_files('mediafile') + collect_data_files('confuse') +
         [(os.path.join(ROOT, 'musicmerge', 'resources'), os.path.join('musicmerge', 'resources'))])
binaries = []
fpcalc = os.path.join(ROOT, 'build', 'fpcalc')
if os.path.exists(fpcalc):
    binaries.append((fpcalc, '.'))
    datas.append((os.path.join(ROOT, 'packaging', 'THIRD_PARTY.md'), '.'))

a = Analysis([os.path.join(ROOT, 'packaging', 'launcher.py')],
             pathex=[ROOT], binaries=binaries, datas=datas, hiddenimports=hidden,
             excludes=['tkinter', 'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
                       'PySide6.Qt3DCore', 'PySide6.QtQuick', 'PySide6.QtQml', 'PySide6.QtMultimedia',
                       'PySide6.QtCharts', 'PySide6.QtDataVisualization', 'PySide6.QtPdf'],
             noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='MusicMerge', console=False,
          argv_emulation=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name='MusicMerge', upx=False)
app = BUNDLE(
    coll,
    name='MusicMerge.app',
    icon=os.path.join(ROOT, 'musicmerge', 'resources', 'icon.png'),
    bundle_identifier='io.github.cd-playa.musicmerge',
    version=__version__,
    info_plist={
        'CFBundleName': 'MusicMerge',
        'CFBundleDisplayName': 'MusicMerge',
        'CFBundleShortVersionString': __version__,
        'CFBundleVersion': __version__,
        'LSMinimumSystemVersion': '13.0',
        'NSHighResolutionCapable': True,
        'NSAppleEventsUsageDescription':
            'MusicMerge uses the Music app to count your songs and to restore play counts, '
            'ratings, loved songs and playlists.',
        'NSHumanReadableCopyright': 'GPL-3.0-or-later. https://github.com/CD-PLAYA/music-merge',
    },
)
