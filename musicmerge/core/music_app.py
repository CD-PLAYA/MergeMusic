"""Steps 4 and 5 (Mac only): talk to the Music app.

  music_status    how many songs Music has, and how many come from the merged folder
  restore_history put play counts, ratings, loved songs, last-played dates and playlists
                  from the old library XML files into the new Music library

Uses JavaScript for Automation through osascript. The first time, macOS asks whether
MusicMerge (or Terminal) may control Music; the answer must be Allow.
Music does not let scripts change 'Date Added', so that one cannot be carried over."""

import csv
import json
import os
import subprocess
import time

from .common import Reporter, is_mac, nfc, plural, to_int, walk_files

STATUS_JS = r'''
function run(argv) {
  var root = argv[0].normalize('NFC').toLowerCase().replace(/\/?$/, '/');
  var Music = Application('Music');
  var ft = Music.libraryPlaylists[0].fileTracks;
  var ids = ft.id();
  var locs = null;
  try { locs = ft.location(); } catch (e) { locs = null; }
  if (!locs || locs.length !== ids.length) {
    locs = ids.map(function (id) { try { return ft.byId(id).location(); } catch (e) { return null; } });
  }
  var inside = 0, missing = 0;
  for (var i = 0; i < locs.length; i++) {
    if (!locs[i]) { missing++; continue; }
    if (locs[i].toString().normalize('NFC').toLowerCase().indexOf(root) === 0) inside++;
  }
  return JSON.stringify({songs: ids.length, inside: inside, missing_files: missing});
}
'''

HISTORY_JS = r'''
var DATA = __DATA__;

function run(argv) {
  var apply = argv.length > 0 && argv[0] === 'apply';
  var res = {apply: apply, songs: 0, total: DATA.tracks.length, found: 0, updated: 0,
             missing: [], errors: [], playlists: []};
  var Music = Application('Music');
  var lib = Music.libraryPlaylists[0];
  var ft = lib.fileTracks;
  var ids = ft.id();
  res.songs = ids.length;
  if (ids.length === 0) return JSON.stringify(res);
  var locs = null;
  try { locs = ft.location(); } catch (e) { locs = null; }
  if (!locs || locs.length !== ids.length) {
    locs = ids.map(function (id) { try { return ft.byId(id).location(); } catch (e) { return null; } });
  }

  var exact = {}, folded = {}, clash = {};
  for (var i = 0; i < ids.length; i++) {
    if (!locs[i]) continue;
    var p = locs[i].toString().normalize('NFC');
    exact[p] = ids[i];
    var f = p.toLowerCase();
    if (folded.hasOwnProperty(f)) clash[f] = true;
    folded[f] = ids[i];
  }
  function find(path) {
    var p = path.normalize('NFC');
    if (exact.hasOwnProperty(p)) return exact[p];
    var f = p.toLowerCase();
    if (folded.hasOwnProperty(f) && !clash[f]) return folded[f];
    return null;
  }

  DATA.tracks.forEach(function (t) {
    var id = find(t.p);
    if (id === null) { res.missing.push(t.p); return; }
    res.found++;
    if (!apply) return;
    var tr = ft.byId(id);
    try {
      if (t.plays) tr.playedCount = t.plays;
      if (t.skips) tr.skippedCount = t.skips;
      if (t.rating) tr.rating = t.rating;
      if (t.played) tr.playedDate = new Date(t.played);
      if (t.loved) {
        try { tr.loved = true; } catch (e1) { tr.favorited = true; }
      }
      res.updated++;
    } catch (e) {
      res.errors.push(t.p + '  ' + e);
    }
  });

  var existing = Music.userPlaylists.name();
  DATA.playlists.forEach(function (pl) {
    var plIds = [];
    pl.paths.forEach(function (p) {
      var id = find(p);
      if (id !== null) plIds.push(id); else res.missing.push('[' + pl.name + '] ' + p);
    });
    var out = {name: pl.name, songs: pl.paths.length, found: plIds.length, added: 0, status: ''};
    if (apply) {
      if (existing.indexOf(pl.name) >= 0) {
        out.status = 'already exists, left alone';
      } else {
        try {
          var newPl = Music.UserPlaylist({name: pl.name}).make();
          plIds.forEach(function (id) {
            try { Music.duplicate(ft.byId(id), {to: newPl}); out.added++; }
            catch (e) { res.errors.push('[' + pl.name + '] ' + e); }
          });
          out.status = 'created';
        } catch (e) {
          out.status = 'could not create';
          res.errors.push('[' + pl.name + '] could not create: ' + e);
        }
      }
    }
    res.playlists.push(out);
  });
  return JSON.stringify(res);
}
'''


def run_jxa(script_path, args, timeout=3600):
    if not is_mac():
        raise RuntimeError('This step needs the Music app on a Mac.')
    res = subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', script_path] + list(args),
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                         timeout=timeout)
    if res.returncode != 0:
        msg = (res.stderr or res.stdout).strip()
        if '-1743' in msg or 'Not authorized' in msg:
            msg = ('macOS did not allow MusicMerge to control Music. Open System Settings > '
                   'Privacy & Security > Automation and allow it to control Music, then try again.')
        raise RuntimeError(msg or 'osascript failed (code %d)' % res.returncode)
    return json.loads(res.stdout.strip())


def music_status(project):
    path = project.path('music_status.js')
    os.makedirs(project.work, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(STATUS_JS)
    return run_jxa(path, [project.dest], timeout=600)


def open_music():
    if is_mac():
        subprocess.run(['/usr/bin/open', '-a', 'Music'])


def _read_csv(path, **kw):
    with open(path, newline='', encoding='utf-8') as fh:
        return list(csv.DictReader(fh, **kw))


def iso_utc(s):
    """'2018-03-01 00:00:00' (UTC, from the XML) -> '2018-03-01T00:00:00Z'."""
    s = (s or '').strip()
    return s.replace(' ', 'T')[:19] + 'Z' if len(s) >= 19 else ''


def history_data(project):
    """What to write into Music, keyed by each file's current path in the merged folder."""
    now_by_inode = {}
    for p in walk_files(project.dest):
        now_by_inode[os.stat(p).st_ino] = p
    before = {}
    if os.path.exists(project.path('inodes.tsv')):
        for r in _read_csv(project.path('inodes.tsv'), delimiter='\t'):
            before[nfc(r['merged_file'])] = int(r['inode'])

    def current(merged_file):
        if not merged_file:
            return None
        ino = before.get(nfc(merged_file))
        if ino is not None and ino in now_by_inode:
            return now_by_inode[ino]
        p = os.path.join(project.dest, merged_file)          # never renamed
        return p if os.path.exists(p) else None

    cmap = {nfc(r['source']): r['merged_file'] for r in _read_csv(project.path('copy_map.csv'))}
    tracks, lost = [], 0
    for r in _read_csv(project.path('plan.csv')):
        if r['action'] not in ('KEEP', 'REVIEW'):
            continue
        plays, rating, loved = to_int(r['plays']), to_int(r['rating']), r['loved'] == 'Y'
        if not (plays or rating or loved):
            continue
        p = current(cmap.get(nfc(r['full_path'])))
        if not p:
            lost += 1
            continue
        tracks.append(dict(p=p, plays=plays, rating=rating, loved=loved,
                           played=iso_utc(r['last_played'])))

    playlists, pl_lost = [], 0
    pl_file = project.path('playlists.json')
    if os.path.exists(pl_file):
        with open(pl_file, encoding='utf-8') as fh:
            for pl in json.load(fh):
                paths, seen = [], set()
                for src in pl['sources']:
                    p = current(cmap.get(nfc(src)))
                    if not p:
                        pl_lost += 1
                    elif p not in seen:
                        seen.add(p)
                        paths.append(p)
                playlists.append(dict(name=pl['name'], paths=paths))
    return dict(tracks=tracks, playlists=playlists), dict(lost=lost, playlist_lost=pl_lost)


def restore_history(project, apply=False, reporter=None):
    reporter = reporter or Reporter()
    reporter.progress('Matching songs in the merged folder')
    data, local = history_data(project)
    path = project.path('apply_history.js')
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(HISTORY_JS.replace('__DATA__', json.dumps(data, ensure_ascii=False)))
    reporter.progress('Asking Music' + (' and writing the history' if apply else ''))
    res = run_jxa(path, ['apply' if apply else 'check'])
    res['lost'] = local['lost']
    res['playlist_lost'] = local['playlist_lost']
    text = history_text(res)
    name = 'history_report.txt'
    with open(project.path(name), 'w', encoding='utf-8') as fh:
        fh.write('%s  (%s)\n%s' % (time.strftime('%Y-%m-%d %H:%M'), 'APPLY' if apply else 'check', text))
    if apply:
        project.mark('history', **{k: v for k, v in res.items() if k not in ('missing', 'errors')})
    return res


def history_text(res):
    L = ['Music library: %d songs' % res['songs']]
    if res['songs'] == 0:
        L.append('The Music library is empty. Import the merged folder first.')
        return '\n'.join(L) + '\n'
    L.append('Songs with play counts, ratings or loves: %d   found in Music: %d   not found: %d%s'
             % (res['total'], res['found'], res['total'] - res['found'],
                ('   updated: %d' % res['updated']) if res['apply'] else ''))
    for pl in res['playlists']:
        line = 'Playlist "%s": %s, %d found in Music' % (pl['name'], plural(pl['songs'], 'song'), pl['found'])
        if res['apply']:
            line += ' - ' + (pl['status'] + (' with %d' % pl['added'] if pl['status'] == 'created' else ''))
        L.append(line)
    if res.get('lost') or res.get('playlist_lost'):
        L.append('Not in the merged folder any more: %d songs, %d playlist entries'
                 % (res['lost'], res['playlist_lost']))
    if res['missing']:
        L.append('Not found in Music (first 20):')
        L += ['  ' + m for m in res['missing'][:20]]
    if res['errors']:
        L.append('Errors (first 20):')
        L += ['  ' + m for m in res['errors'][:20]]
    if not res['apply']:
        L.append('Check only: nothing in Music was changed.')
    return '\n'.join(L) + '\n'
