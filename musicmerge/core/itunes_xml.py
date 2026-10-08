"""Reading play history and playlists from an iTunes / Music library XML file.

iTunes writes 'iTunes Music Library.xml' (or 'iTunes Library.xml') next to its media
folder. The Music app on newer macOS only writes one when asked:
File > Library > Export Library..."""

import os
import plistlib
from collections import Counter, defaultdict
from urllib.parse import unquote, urlparse

from .common import AUDIO_EXT, norm, tail_key


def location_path(loc):
    if not loc or not str(loc).startswith('file:'):
        return ''
    return unquote(urlparse(loc).path)


def load_xml(path):
    with open(path, 'rb') as fh:
        return plistlib.load(fh)


def is_song(t):
    if t.get('Has Video') or t.get('Movie') or t.get('TV Show') or t.get('Music Video'):
        return False
    p = location_path(t.get('Location'))
    return not p or os.path.splitext(p)[1].lower() in AUDIO_EXT


def history_of(t):
    return dict(plays=int(t.get('Play Count', 0) or 0),
                skips=int(t.get('Skip Count', 0) or 0),
                rating=int(t.get('Rating', 0) or 0) if not t.get('Rating Computed') else 0,
                loved=bool(t.get('Loved') or t.get('Favorited')),
                added=t.get('Date Added'), last=t.get('Play Date UTC'))


def merge_history(hs):
    hs = [h for h in hs if h]
    if not hs:
        return None
    added = [h['added'] for h in hs if h['added']]
    last = [h['last'] for h in hs if h['last']]
    return dict(plays=sum(h['plays'] for h in hs), skips=sum(h['skips'] for h in hs),
                rating=max(h['rating'] for h in hs), loved=any(h['loved'] for h in hs),
                added=min(added) if added else '', last=max(last) if last else '')


class Matcher:
    """Finds the audio file an XML entry refers to: by its path first (files that were
    moved keep their Artist/Album/file tail), then by artist, album, title and length."""

    def __init__(self, recs):
        self.by_path = defaultdict(dict)        # tail -> {lib: rec}
        self.by_tags = defaultdict(list)
        self.by_song = defaultdict(list)
        for r in recs:
            self.by_path[tail_key(r.path)].setdefault(r.lib, r)
            if r.length:
                self.by_tags[(r.n_artist, r.n_album, r.n_title)].append(r)
                self.by_tags[(r.n_aa, r.n_album, r.n_title)].append(r)
                self.by_song[(r.n_artist, r.n_title)].append(r)

    @staticmethod
    def _close(cands, secs, lib):
        hits = [c for c in cands if secs and abs(c.length - secs) <= 2.0]
        hits.sort(key=lambda c: (c.lib != lib, abs(c.length - secs)))
        return hits

    def find(self, t, lib):
        """Returns (rec or None, how) where how is 'path', 'tags' or ''."""
        path = location_path(t.get('Location'))
        if path:
            libs = self.by_path.get(tail_key(path))
            if libs:
                return (libs.get(lib) or libs[min(libs)]), 'path'
        secs = (t.get('Total Time') or 0) / 1000.0
        artist = norm(t.get('Artist') or t.get('Album Artist') or '', drop_the=True)
        title = norm(t.get('Name') or '')
        album = norm(t.get('Album') or '')
        hits = self._close(self.by_tags.get((artist, album, title), []), secs, lib)
        if not hits:
            loose = self._close(self.by_song.get((artist, title), []), secs, lib)
            hits = loose if len(set(h.n_album for h in loose)) == 1 else []
        return (hits[0], 'tags') if hits else (None, '')


def read_library_xml(xml_path, lib, matcher):
    """Match one XML file against the scanned files.

    Returns a dict with:
      history   {rec.idx: history dict}
      playlists [{'name', 'recs': [rec.idx...], 'missing': n}]
      stats     counters for the summary
      missing   [(plays, artist, album, name, path)] entries with no file found
    """
    out = dict(history={}, playlists=[], missing=[], stats=Counter(), info={})
    pl = load_xml(xml_path)
    out['info'] = dict(date=str(pl.get('Date', '')), app=str(pl.get('Application Version', '')),
                       music_folder=location_path(pl.get('Music Folder')))
    st = out['stats']
    track_rec = {}
    for tid, t in pl.get('Tracks', {}).items():
        if not is_song(t):
            continue
        h = history_of(t)
        st['played'] += h['plays'] > 0
        st['rated'] += h['rating'] > 0
        if not t.get('Location'):
            st['no_file'] += 1                  # cloud-only or never downloaded
            continue
        st['entries'] += 1
        r, how = matcher.find(t, lib)
        if r is None:
            st['not_found'] += 1
            st['played_not_found'] += h['plays'] > 0
            out['missing'].append((h['plays'], t.get('Artist', ''), t.get('Album', ''),
                                   t.get('Name', ''), location_path(t.get('Location'))))
            continue
        st['found_' + how] += 1
        track_rec[str(tid)] = r.idx
        if any((h['plays'], h['rating'], h['loved'], h['skips'], h['added'], h['last'])):
            out['history'][r.idx] = merge_history([out['history'].get(r.idx), h])

    for p in pl.get('Playlists', []):
        if p.get('Master') or p.get('Distinguished Kind') or p.get('Folder') or p.get('Visible') is False:
            continue
        if 'Smart Info' in p or 'Smart Criteria' in p:
            st['smart_playlists'] += 1
            continue
        recs, missing = [], 0
        for item in p.get('Playlist Items', []):
            tid = str(item.get('Track ID'))
            if tid in track_rec:
                recs.append(track_rec[tid])
            elif tid in pl.get('Tracks', {}) and is_song(pl['Tracks'][tid]):
                missing += 1
        out['playlists'].append(dict(name=p.get('Name') or 'Playlist', recs=recs, missing=missing))
        st['playlists'] += 1
    return out
