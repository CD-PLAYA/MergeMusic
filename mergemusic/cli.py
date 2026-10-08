"""Command line for MergeMusic. With no arguments it opens the step-by-step window.

  mergemusic                                   open the app window
  mergemusic plan DEST --library PATH [--library PATH ...] [--xml N=FILE] [--no-fingerprints]
  mergemusic build DEST [--dry-run]
  mergemusic tags DEST [--retry-unmatched]
  mergemusic music-status DEST                 (Mac)
  mergemusic history DEST [--apply]            (Mac)
  mergemusic cleanup DEST [--save-extras FOLDER]
  mergemusic fpcalc [--download]
"""

import argparse
import sys

from . import APP_NAME, __version__


def _reporter():
    from .core.common import Reporter
    tty = sys.stdout.isatty()
    last = {'stage': None}

    def progress(stage, done, total):
        if total:
            msg = '%s: %d / %d' % (stage, done, total)
        else:
            msg = stage
        if tty:
            sys.stdout.write('\r\033[K' + msg)
            sys.stdout.flush()
        elif stage != last['stage'] or done == total:
            print(msg)
        last['stage'] = stage

    def log(text):
        if tty:
            sys.stdout.write('\r\033[K')
        print(text)

    return Reporter(progress=progress, log=log)


def _project(dest):
    from .core.project import Project
    return Project(dest)


def cmd_plan(a):
    from .core.plan import build_plan, summary_text
    from .core.project import find_xml
    p = _project(a.dest)
    xml_over = {}
    for item in a.xml or []:
        num, _, path = item.partition('=')
        xml_over[int(num)] = path
    libs = []
    for i, path in enumerate(a.library, 1):
        libs.append({'path': path, 'xml': xml_over.get(i, '' if a.no_xml else find_xml(path))})
    p.set_libraries(libs)
    p.use_fingerprints = not a.no_fingerprints
    probs = p.problems()
    if probs:
        sys.exit('\n'.join(probs))
    p.save()
    for i, lib in enumerate(p.libraries, 1):
        print('Library %d: %s\n  history file: %s' % (i, lib['path'], lib['xml'] or 'none'))
    n = build_plan(p, _reporter())
    print()
    print(summary_text(p, n))
    print('Plan files are in', p.work)


def cmd_build(a):
    from .core.build import build_merged, build_text
    p = _project(a.dest)
    n = build_merged(p, _reporter(), dry_run=a.dry_run)
    print()
    print(n if a.dry_run else build_text(n))


def cmd_tags(a):
    from .core.tags import run_tags, tags_text
    p = _project(a.dest)
    print()
    print(tags_text(run_tags(p, _reporter(), retry_unmatched=a.retry_unmatched)))


def cmd_music_status(a):
    from .core.music_app import music_status
    s = music_status(_project(a.dest))
    print('Music has %d songs; %d of them are in the merged folder.' % (s['songs'], s['inside']))


def cmd_history(a):
    from .core.music_app import restore_history, history_text
    res = restore_history(_project(a.dest), apply=a.apply, reporter=_reporter())
    print()
    print(history_text(res))


def cmd_cleanup(a):
    from .core.cleanup import cleanup_report, cleanup_text, save_extras
    p = _project(a.dest)
    if a.save_extras:
        print(save_extras(p, a.save_extras, _reporter()))
    print(cleanup_text(cleanup_report(p)))


def cmd_fpcalc(a):
    from .core.fingerprint import download_fpcalc, find_fpcalc, fpcalc_version
    path = find_fpcalc()
    if not path and a.download:
        path = download_fpcalc(_reporter())
    print(path and '%s (%s)' % (path, fpcalc_version(path)) or 'fpcalc not found (use --download)')


def run_beets(args):
    """Internal: run the bundled beets (used by the tag step)."""
    from beets.ui import main
    main(args)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == 'beets':
        return run_beets(argv[1:])
    if not argv or argv[0] == 'gui' or argv[0].startswith('-psn'):
        from .gui.app import main as gui_main
        return gui_main(argv[1:] if argv and argv[0] == 'gui' else [])

    ap = argparse.ArgumentParser(prog='mergemusic', description='%s %s' % (APP_NAME, __version__))
    ap.add_argument('--version', action='version', version=__version__)
    sub = ap.add_subparsers(dest='cmd', required=True)

    s = sub.add_parser('plan', help='scan the libraries and plan the merge (changes nothing)')
    s.add_argument('dest', help='new, empty folder for the merged library')
    s.add_argument('--library', action='append', required=True,
                   help='a library folder; repeat for each one, in order of preference')
    s.add_argument('--xml', action='append', metavar='N=FILE',
                   help="history file for library N (default: found automatically)")
    s.add_argument('--no-xml', action='store_true', help='do not look for history files')
    s.add_argument('--no-fingerprints', action='store_true', help='match on tags only')
    s.set_defaults(fn=cmd_plan)

    s = sub.add_parser('build', help='build the merged folder from the plan')
    s.add_argument('dest')
    s.add_argument('--dry-run', action='store_true')
    s.set_defaults(fn=cmd_build)

    s = sub.add_parser('tags', help='clean up tags with beets + MusicBrainz (optional, slow)')
    s.add_argument('dest')
    s.add_argument('--retry-unmatched', action='store_true')
    s.set_defaults(fn=cmd_tags)

    s = sub.add_parser('music-status', help='count songs in the Music app (Mac)')
    s.add_argument('dest')
    s.set_defaults(fn=cmd_music_status)

    s = sub.add_parser('history', help='restore play history and playlists in Music (Mac)')
    s.add_argument('dest')
    s.add_argument('--apply', action='store_true', help='write (default: check only)')
    s.set_defaults(fn=cmd_history)

    s = sub.add_parser('cleanup', help='what can be deleted, and saving non-music files')
    s.add_argument('dest')
    s.add_argument('--save-extras', metavar='FOLDER')
    s.set_defaults(fn=cmd_cleanup)

    s = sub.add_parser('fpcalc', help='find or download the fingerprint tool')
    s.add_argument('--download', action='store_true')
    s.set_defaults(fn=cmd_fpcalc)

    a = ap.parse_args(argv)
    try:
        a.fn(a)
    except KeyboardInterrupt:
        sys.exit('\nStopped. Run the same command again to continue.')
    except RuntimeError as e:
        sys.exit(str(e))


if __name__ == '__main__':
    main()
