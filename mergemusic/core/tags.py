"""Step 3 (optional): clean up tags with beets and MusicBrainz.

Each album folder is looked up on MusicBrainz (helped by audio fingerprints). When the
match is strong, the corrected tags and cover art are written and the files are renamed
to Artist/Album/01 Title. Otherwise the album keeps its own tags.

By design beets never applies a match on its own when tracks are missing from an album,
so partial albums keep their tags. Copy-protected .m4p files and the 'Unknown Artist'
folder are left alone. MusicBrainz allows about one lookup per second, so this step
takes hours on a large library; it can be stopped and resumed at any time."""

import os
import re
import sqlite3
import subprocess
import sys
import time

from .common import AUDIO_EXT, Reporter, walk_files
from .fingerprint import find_fpcalc

ALBUM_LINE = re.compile(r'^(/.+|[A-Za-z]:\\.+) \((\d+) items?\)\s*$')


def beets_available():
    try:
        import beets  # noqa: F401
        return True
    except ImportError:
        return False


def chroma_available():
    try:
        import acoustid  # noqa: F401
        return find_fpcalc() is not None
    except ImportError:
        return False


def beets_command(args):
    """Run beets through MergeMusic's own executable, so it works inside the app bundle."""
    if getattr(sys, 'frozen', False):
        return [sys.executable, 'beets'] + list(args)
    return [sys.executable, '-m', 'mergemusic', 'beets'] + list(args)


def yaml_str(s):
    return '"%s"' % s.replace('\\', '\\\\').replace('"', '\\"')


def write_config(project):
    bdir = project.path('beets')
    os.makedirs(bdir, exist_ok=True)
    plugins = ['musicbrainz', 'fetchart', 'embedart', 'inline']
    if chroma_available():
        plugins.insert(1, 'chroma')
    cfg = '''# Written by MergeMusic. Safe to read; changes are overwritten on the next run.
directory: {dest}
library: {db}
statefile: {state}
plugins: [{plugins}]
id3v23: yes
original_date: yes
ui:
  color: no
ignore: [".*", "*~", "*.partial", "System Volume Information", "lost+found",
         "Unknown Artist", "*.m4p", "*.M4P"]

import:
  write: yes
  copy: no
  move: yes
  quiet: yes
  quiet_fallback: asis
  incremental: yes
  resume: yes
  duplicate_action: keep
  languages: [en]
  log: {log}

item_fields:
  discprefix: "('%d-' % disc) if (disctotal or 0) > 1 and disc else ''"

paths:
  default: $albumartist/$album%aunique{{}}/$discprefix$track $title
  comp: Compilations/$album%aunique{{}}/$discprefix$track $title
  singleton: $artist/Non-Album/$title

# With more than one lookup source loaded, beets 2.x adds a "data source" penalty to
# every candidate, so no album could ever reach a strong match. Zero it.
musicbrainz:
  data_source_mismatch_penalty: 0.0
chroma:
  auto: yes
  data_source_mismatch_penalty: 0.0
fetchart:
  auto: yes
embedart:
  auto: yes
  ifempty: yes
'''.format(dest=yaml_str(project.dest), db=yaml_str(os.path.join(bdir, 'library.db')),
           state=yaml_str(os.path.join(bdir, 'state.pickle')), plugins=', '.join(plugins),
           log=yaml_str(os.path.join(bdir, 'import.log')))
    path = os.path.join(bdir, 'config.yaml')
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(cfg)
    return path


def album_folders(project):
    """Album folders beets will look at (for progress and time estimates)."""
    dirs = set()
    for p in walk_files(project.dest):
        ext = os.path.splitext(p)[1].lower()
        if ext in AUDIO_EXT and ext != '.m4p':
            rel = os.path.relpath(p, project.dest)
            if not rel.startswith('Unknown Artist' + os.sep):
                dirs.add(os.path.dirname(p))
    return len(dirs)


def library_counts(project):
    db = project.path('beets', 'library.db')
    if not os.path.exists(db):
        return dict(albums=0, tracks=0, matched=0)
    con = sqlite3.connect(db)
    try:
        q = lambda sql: con.execute(sql).fetchone()[0]
        return dict(albums=q('SELECT COUNT(*) FROM albums'),
                    tracks=q('SELECT COUNT(*) FROM items'),
                    matched=q("SELECT COUNT(*) FROM albums WHERE mb_albumid IS NOT NULL AND mb_albumid != ''"))
    except sqlite3.Error:
        return dict(albums=0, tracks=0, matched=0)
    finally:
        con.close()


def run_tags(project, reporter=None, retry_unmatched=False):
    """Run (or resume) the tag clean-up. Returns counts from beets' library."""
    reporter = reporter or Reporter()
    if not beets_available():
        raise RuntimeError('beets is not installed. Install it with: pip install "mergemusic[tags]"')
    if not project.done('build'):
        raise RuntimeError('Build the merged folder first.')
    cfg = write_config(project)
    total = album_folders(project)
    info = project.step_info('tags')
    tagpass = int(info.get('pass', 1))
    if retry_unmatched and info.get('complete'):
        tagpass += 1
    if tagpass == 1:
        args = ['-c', cfg, 'import', project.dest]
    else:
        args = ['-c', cfg, 'import', '-L', '--set', 'retagpass=%d' % tagpass,
                'mb_albumid::^$', '^retagpass:%d' % tagpass]
        total = max(0, library_counts(project)['albums'] - library_counts(project)['matched'])
    project.mark('tags', **dict(info, running=True, complete=False, **{'pass': tagpass}))

    env = dict(os.environ, PYTHONUNBUFFERED='1', BEETSDIR=project.path('beets'))
    fpcalc = find_fpcalc()
    if fpcalc:
        env['FPCALC'] = fpcalc
        env['PATH'] = os.path.dirname(fpcalc) + os.pathsep + env.get('PATH', '')
    cmd = beets_command(args)
    if sys.platform == 'darwin':
        cmd = ['/usr/bin/caffeinate', '-i'] + cmd       # keep the Mac awake
    reporter.log('Running: beets ' + ' '.join(args))
    t0, done = time.time(), 0
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                            universal_newlines=True, bufsize=1, encoding='utf-8', errors='replace')
    stopped = False
    try:
        for line in proc.stdout:
            line = line.rstrip()
            if not line or line.startswith('Created database backup'):
                continue
            m = ALBUM_LINE.match(line)
            if m:
                done += 1
                reporter.progress('Looking up albums on MusicBrainz', done, total)
                reporter.log(os.path.relpath(m.group(1), project.dest))
            elif not line.startswith('Importing as-is'):
                reporter.log('  ' + line)
            try:
                reporter.check()
            except Exception:
                stopped = True
                proc.terminate()
                raise
    finally:
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
        counts = library_counts(project)
        counts.update({'pass': tagpass, 'running': False, 'complete': (not stopped and proc.returncode == 0),
                       'albums_seen_this_run': done, 'minutes': round((time.time() - t0) / 60)})
        project.mark('tags', **counts)
    if proc.returncode not in (0, None) and not stopped:
        raise RuntimeError('beets stopped with an error (code %s). The activity log has the details.'
                           % proc.returncode)
    return counts


def tags_text(c):
    return ('Albums looked at: %d   matched on MusicBrainz and retagged: %d   kept their own tags: %d\n'
            'Songs: %d\n' % (c['albums'], c['matched'], c['albums'] - c['matched'], c['tracks']))
