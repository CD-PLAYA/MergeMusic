"""Reading a library folder: every audio file with its tags, plus the other files."""

import os
import re

from .common import (AUDIO_EXT, LOSSLESS_EXT, first, leading_int, norm, walk_files)

try:
    import mutagen
    from mutagen.mp4 import MP4
except ImportError:  # pragma: no cover
    mutagen = None

GENERIC_TITLE = re.compile(
    r'^(track\s*\d+|audio\s*track\s*\d*|\d{8}\s+\d{6}|untitled.*|new recording.*)$', re.I)


class Rec:
    """One audio file."""
    __slots__ = ('idx', 'lib', 'root', 'path', 'rel', 'ext', 'size', 'mtime',
                 'title', 'artist', 'albumartist', 'album', 'track', 'disc',
                 'year', 'genre', 'compilation', 'art', 'title_from_tag',
                 'length', 'bitrate', 'codec', 'lossless', 'protected', 'error',
                 'n_title', 'n_artist', 'n_aa', 'n_album', 'weak', 'tag_score',
                 'fp', 'fpset', 'fppos', 'fp_status', 'matched',
                 'action', 'why', 'group', 'hist')

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    @property
    def kbps(self):
        if self.bitrate:
            return self.bitrate / 1000.0
        if self.length:
            return self.size * 8 / self.length / 1000.0
        return 0.0

    @property
    def label(self):
        return '%d:%s' % (self.lib, self.rel)


def read_tags(path, ext):
    t = dict(title='', artist='', albumartist='', album='', track=0, disc=0,
             year='', genre='', compilation=False, art=False)
    info = dict(length=0.0, bitrate=0, codec=ext.lstrip('.'))
    if mutagen is None:
        return t, info, 'mutagen is not installed'
    try:
        f = mutagen.File(path)
    except Exception as e:
        return t, info, 'unreadable (%s)' % e.__class__.__name__
    if f is None:
        return t, info, 'unrecognized format'

    i = f.info
    info['length'] = float(getattr(i, 'length', 0) or 0)
    info['bitrate'] = int(getattr(i, 'bitrate', 0) or 0)
    info['codec'] = str(getattr(i, 'codec', '') or ext.lstrip('.'))
    tags = f.tags
    if tags is None:
        return t, info, ''

    if isinstance(f, MP4):
        def g(k):
            return first(tags.get(k))
        t.update(title=g('\xa9nam'), artist=g('\xa9ART'), albumartist=g('aART'),
                 album=g('\xa9alb'), year=g('\xa9day'), genre=g('\xa9gen'),
                 compilation=bool(tags.get('cpil')), art=bool(tags.get('covr')))
        trkn, disk = tags.get('trkn'), tags.get('disk')
        if trkn:
            t['track'] = int(trkn[0][0] or 0)
        if disk:
            t['disc'] = int(disk[0][0] or 0)
    elif hasattr(tags, 'getall'):                        # ID3: MP3, AIFF, WAV
        def g(k):
            fr = tags.get(k)
            return first(fr.text) if fr is not None and hasattr(fr, 'text') else ''
        comp = g('TCMP') == '1'
        if not comp:
            for fr in tags.getall('TXXX'):
                if getattr(fr, 'desc', '').lower() == 'compilation' and first(fr.text) == '1':
                    comp = True
        t.update(title=g('TIT2'), artist=g('TPE1'), albumartist=g('TPE2'),
                 album=g('TALB'), year=g('TDRC'), genre=g('TCON'),
                 track=leading_int(g('TRCK')), disc=leading_int(g('TPOS')),
                 compilation=comp, art=bool(tags.getall('APIC')))
    else:                                                # FLAC, Ogg, APE, WMA ...
        def g(*keys):
            for k in keys:
                try:
                    v = tags.get(k)
                except Exception:
                    v = None
                if v:
                    return first(v)
            return ''
        t.update(title=g('title', 'Title'), artist=g('artist', 'Artist', 'Author'),
                 albumartist=g('albumartist', 'album artist', 'Album Artist', 'WM/AlbumArtist'),
                 album=g('album', 'Album', 'WM/AlbumTitle'), year=g('date', 'year', 'Year', 'WM/Year'),
                 genre=g('genre', 'Genre', 'WM/Genre'),
                 track=leading_int(g('tracknumber', 'Track', 'WM/TrackNumber')),
                 disc=leading_int(g('discnumber', 'Disc', 'WM/PartOfSet')),
                 compilation=(g('compilation') == '1'))
    return t, info, ''


def scan_library(number, root, reporter):
    """Returns (audio records, other files as (path, size))."""
    root = os.path.abspath(root).rstrip('/')
    audio, others = [], []
    reporter.progress('Listing files in library %d' % number)
    for p in walk_files(root):
        ext = os.path.splitext(p)[1].lower()
        if ext in AUDIO_EXT:
            audio.append(p)
        else:
            try:
                others.append((p, os.path.getsize(p)))
            except OSError:
                pass
        if len(audio) % 2000 == 0:
            reporter.check()

    recs = []
    for n, p in enumerate(audio, 1):
        if n % 50 == 0 or n == len(audio):
            reporter.progress('Reading tags in library %d' % number, n, len(audio))
            reporter.check()
        ext = os.path.splitext(p)[1].lower()
        try:
            st = os.stat(p)
        except OSError:
            continue
        t, info, err = read_tags(p, ext)
        rel = os.path.relpath(p, root)
        parts = rel.split(os.sep)

        # Fall back to folder and file names where tags are empty.
        title_from_tag = bool(t['title'])
        stem = os.path.splitext(parts[-1])[0]
        if not t['title']:
            t['title'] = re.sub(r'^\d+(-\d+)?[\s._-]+', '', stem) or stem
        if not t['track']:
            t['track'] = leading_int(re.sub(r'^\d+-', '', stem))
        if not t['album'] and len(parts) >= 2:
            t['album'] = parts[-2]
        if not t['artist'] and len(parts) >= 3:
            t['artist'] = parts[-3]

        r = Rec(lib=number, root=root, path=p, rel=rel, ext=ext, size=st.st_size,
                mtime=int(st.st_mtime), error=err, title_from_tag=title_from_tag,
                length=info['length'], bitrate=info['bitrate'], codec=info['codec'],
                fp_status='', matched='', action='', why='', group='', hist=None, **t)
        r.lossless = ext in LOSSLESS_EXT or 'alac' in r.codec.lower()
        r.protected = ext == '.m4p'
        r.n_title = norm(r.title)
        r.n_artist = norm(r.artist, drop_the=True)
        r.n_aa = 'compilations' if r.compilation else norm(r.albumartist or r.artist, drop_the=True)
        r.n_album = norm(r.album)
        r.weak = (not title_from_tag or r.n_artist in ('', 'unknown artist')
                  or r.n_album in ('', 'unknown album')
                  or bool(GENERIC_TITLE.match(r.title.strip())))
        r.tag_score = sum(bool(x) for x in (title_from_tag, r.artist, r.albumartist, r.album,
                                           r.track, r.year, r.genre, r.art))
        recs.append(r)
    return recs, others
