"""The tag step against the real MusicBrainz service.

Slow and needs the internet, so it only runs when MUSICMERGE_NETWORK_TESTS=1.
It builds a complete album whose titles and track lengths match a real release (the audio
itself is noise), and checks that beets matches and retags it. This guards against the
beets 2.x "data source" penalty that silently stopped every album from matching."""

import datetime
import os
import plistlib
import subprocess

import pytest

from library_factory import ffmpeg_exe

pytestmark = pytest.mark.skipif(os.environ.get('MUSICMERGE_NETWORK_TESTS') != '1',
                                reason='set MUSICMERGE_NETWORK_TESTS=1 to run')

ALBUM = [('Don’t Panic', 136866), ('Shiver', 299693), ('Spies', 318773), ('Sparks', 227093),
         ('Yellow', 269200), ('Trouble', 270906), ('Parachutes', 46200), ('High Speed', 254360),
         ('We Never Change', 249400), ('Everything’s Not Lost / Life Is for Living', 437000)]


def test_tag_step_matches_a_real_album(tmp_path):
    pytest.importorskip('beets')
    from musicmerge.core.build import build_merged
    from musicmerge.core.common import Reporter
    from musicmerge.core.music_app import history_data
    from musicmerge.core.plan import build_plan
    from musicmerge.core.project import Project
    from musicmerge.core.tags import run_tags

    ff = ffmpeg_exe()
    lib = tmp_path / 'Lib'
    album = lib / 'Coldplay' / 'Parachutes'
    album.mkdir(parents=True)
    for n, (title, ms) in enumerate(ALBUM, 1):
        name = 'Track %d.m4a' % n               # beets should rename these
        subprocess.run([ff, '-loglevel', 'error', '-f', 'lavfi', '-i',
                        'anoisesrc=d=%.3f:c=pink:a=0.1:r=22050' % (ms / 1000.0), '-ac', '1',
                        '-c:a', 'aac', '-b:a', '24k', '-metadata', 'title=' + title,
                        '-metadata', 'artist=Coldplay', '-metadata', 'album=Parachutes',
                        '-metadata', 'track=%d/10' % n, '-metadata', 'date=2000',
                        str(album / name)], check=True)
    xml = lib / 'iTunes Music Library.xml'
    with open(xml, 'wb') as fh:
        plistlib.dump({'Tracks': {'1': {'Track ID': 1, 'Play Count': 7,
                                        'Play Date UTC': datetime.datetime(2020, 1, 2, 3, 4, 5),
                                        'Location': 'file:///x/Coldplay/Parachutes/Track%205.m4a'}},
                       'Playlists': []}, fh)

    p = Project(str(tmp_path / 'Merged'))
    p.set_libraries([{'path': str(lib), 'xml': str(xml)}])
    p.use_fingerprints = False
    p.save()
    build_plan(p, Reporter())
    build_merged(p, Reporter())
    logs = []
    counts = run_tags(p, Reporter(log=logs.append))
    assert counts['albums'] == 1, '\n'.join(logs)
    assert counts['matched'] == 1, '\n'.join(logs)
    names = sorted(os.listdir(os.path.join(p.dest, 'Coldplay', 'Parachutes')))
    assert '05 Yellow.m4a' in names

    data, _ = history_data(p)
    assert [os.path.basename(t['p']) for t in data['tracks']] == ['05 Yellow.m4a']
    assert data['tracks'][0]['plays'] == 7
