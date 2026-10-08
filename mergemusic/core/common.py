"""Shared constants and helpers."""

import os
import re
import sys
import unicodedata

AUDIO_EXT = {'.mp3', '.m4a', '.m4p', '.m4b', '.aac', '.aif', '.aiff', '.wav',
             '.flac', '.alac', '.ogg', '.opus', '.wma', '.ape'}
VIDEO_EXT = {'.m4v', '.mp4', '.mov', '.avi', '.mkv'}
DOC_EXT = {'.pdf', '.epub', '.ibooks'}
LOSSLESS_EXT = {'.wav', '.aif', '.aiff', '.flac', '.ape'}
# Library bookkeeping that is worthless once the merge is done.
JUNK_EXT = {'.itc', '.itc2', '.itl', '.itdb', '.plist', '.strings', '.xml', '.ini',
            '.db', '.ds_store', '.tmp', '.musicdb'}
IMAGE_EXT = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tif', '.tiff', '.webp'}

WORK_DIR_NAME = '.mergemusic'

EDITION_LABEL = re.compile(
    r'[\(\[][^\)\]]*(remaster|explicit|album version|lp version|bonus track)[^\)\]]*[\)\]]', re.I)


class Cancelled(Exception):
    """Raised inside a step when the user pressed Stop."""


class Reporter:
    """How a step talks to whoever runs it (GUI or CLI).

    progress(stage, done, total)  - total 0 means "unknown"
    log(text)                     - one line for the activity log
    stop()                        - returns True when the user asked to stop
    """

    def __init__(self, progress=None, log=None, stop=None):
        self._progress = progress
        self._log = log
        self._stop = stop

    def progress(self, stage, done=0, total=0):
        if self._progress:
            self._progress(stage, done, total)

    def log(self, text):
        if self._log:
            self._log(text)

    def check(self):
        if self._stop and self._stop():
            raise Cancelled()


def norm(s, drop_the=False):
    """Comparison key: no case, accents, punctuation or edition labels."""
    if not s:
        return ''
    s = unicodedata.normalize('NFKD', str(s))
    s = ''.join(c for c in s if not unicodedata.combining(c)).lower()
    s = s.replace('&', ' and ')
    s = EDITION_LABEL.sub(' ', s)
    s = re.sub(r'[\W_]+', ' ', s).strip()
    if drop_the and s.startswith('the '):
        s = s[4:]
    return s


def nfc(s):
    return unicodedata.normalize('NFC', s or '')


def fold(path):
    """Path key that ignores Unicode normalization and case (macOS file names)."""
    return nfc(path).casefold()


def tail_key(path, n=3):
    """Last n path parts, normalized, for matching a file that has moved (Artist/Album/file)."""
    parts = nfc(path).replace('\\', '/').split('/')
    return '/'.join(p.casefold() for p in parts[-n:])


def first(v):
    if v is None:
        return ''
    if isinstance(v, (list, tuple)):
        v = v[0] if v else ''
    return str(v).strip()


def leading_int(v):
    m = re.match(r'\s*(\d+)', str(v or ''))
    return int(m.group(1)) if m else 0


def to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def human(nbytes):
    nbytes = float(nbytes or 0)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if nbytes < 1024 or unit == 'TB':
            return ('%.0f %s' if unit == 'B' else '%.1f %s') % (nbytes, unit)
        nbytes /= 1024.0


def plural(n, one, many=None):
    """'1 song', '2 songs'."""
    return '%d %s' % (n, one if n == 1 else (many or one + 's'))


def is_mac():
    return sys.platform == 'darwin'


def inside(child, parent):
    """True when path `child` is `parent` or somewhere below it."""
    c = fold(os.path.abspath(child).rstrip('/') + '/')
    p = fold(os.path.abspath(parent).rstrip('/') + '/')
    return c.startswith(p)


def walk_files(root):
    """All files below root, skipping hidden files and folders (and MergeMusic's work folder)."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith('.'))
        for fn in sorted(filenames):
            if not fn.startswith('.'):
                yield os.path.join(dirpath, fn)


def resource_dirs():
    """Folders where bundled helper programs (fpcalc) may live."""
    dirs = []
    if getattr(sys, 'frozen', False):
        dirs.append(getattr(sys, '_MEIPASS', os.path.dirname(sys.executable)))
        dirs.append(os.path.dirname(sys.executable))
    dirs.append(os.path.join(user_data_dir(), 'bin'))
    return dirs


def user_data_dir():
    if is_mac():
        base = os.path.expanduser('~/Library/Application Support')
    elif os.name == 'nt':
        base = os.environ.get('APPDATA', os.path.expanduser('~'))
    else:
        base = os.environ.get('XDG_DATA_HOME', os.path.expanduser('~/.local/share'))
    return os.path.join(base, 'MergeMusic')
