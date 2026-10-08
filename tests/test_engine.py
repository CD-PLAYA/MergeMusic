"""End-to-end tests of the engine on two generated libraries."""

import csv
import json
import os
import shutil
import subprocess
import sys

import pytest

from musicmerge.core.build import build_merged
from musicmerge.core.cleanup import cleanup_report, cleanup_text, save_extras
from musicmerge.core.common import Reporter, is_mac
from musicmerge.core.music_app import HISTORY_JS, STATUS_JS, history_data, iso_utc
from musicmerge.core.plan import build_plan
from musicmerge.core.project import Project, find_xml


def plan_rows(project):
    with open(project.path('plan.csv'), newline='', encoding='utf-8') as fh:
        return {(r['lib'], r['file']): r for r in csv.DictReader(fh)}


def row(rows, lib, *parts):
    return rows[(str(lib), os.path.join(*parts))]


@pytest.fixture
def planned(project, fpcalc):
    numbers = build_plan(project, Reporter())
    return project, numbers, plan_rows(project)


def test_find_xml(libraries):
    assert find_xml(libraries['a']) == libraries['xml']
    # also found when the chosen folder is one level down (the XML sits next to the media folder)
    assert find_xml(os.path.join(libraries['a'], 'The Beatles')) == libraries['xml']
    assert find_xml(libraries['b']) == ''


def test_project_problems(libraries, tmp_path):
    p = Project(os.path.join(libraries['a'], 'Merged'))
    p.set_libraries([{'path': libraries['a']}])
    assert any('inside library 1' in x for x in p.problems())
    full = tmp_path / 'full'
    full.mkdir()
    (full / 'song.mp3').write_bytes(b'x')
    p = Project(str(full))
    p.set_libraries([{'path': libraries['a']}])
    assert any('not empty' in x for x in p.problems())
    p = Project(str(tmp_path / 'new'))
    p.set_libraries([{'path': libraries['a']}, {'path': os.path.join(libraries['a'], 'Movies')}])
    assert any('inside library 1' in x for x in p.problems())


def test_plan_decisions(planned):
    project, n, rows = planned
    assert n['files'] == 18
    assert n['groups'] == 6

    # Come Together: three copies; the full-quality AAC without " 1" wins
    assert row(rows, 1, 'The Beatles', 'Abbey Road', '01 Come Together.m4a')['action'] == 'KEEP'
    assert row(rows, 1, 'The Beatles', 'Abbey Road', '01 Come Together 1.m4a')['action'] == 'DROP'
    assert row(rows, 2, 'The Beatles', 'Abbey Road', '01 Come Together.mp3')['action'] == 'DROP'

    # Same audio, no tags: found by fingerprint; the better-quality untagged copy is kept
    t1 = row(rows, 1, 'Unknown Artist', 'Unknown Album', 'Track 01.m4a')
    assert t1['action'] == 'KEEP' and t1['matched_by'] == 'audio'
    assert row(rows, 1, 'The Beatles', 'Abbey Road', '02 Something.mp3')['action'] == 'DROP'

    # Same tags, different audio: both kept, one flagged
    assert row(rows, 1, 'The Beatles', 'Abbey Road', '03 Here Comes the Sun.m4a')['action'] == 'KEEP'
    assert row(rows, 2, 'The Beatles', 'Abbey Road', '03 Here Comes The Sun.m4a')['action'] == 'REVIEW'

    # Lossless beats AAC, even from the second library
    assert row(rows, 2, 'Miles Davis', 'Kind Of Blue', '01 So What.aif')['action'] == 'KEEP'
    assert row(rows, 1, 'Miles Davis', 'Kind of Blue', '01 So What.m4a')['action'] == 'DROP'

    # Protected purchases: matched on tags, the plain name wins
    assert row(rows, 2, 'The Beatles', 'Revolver', '01 Taxman.m4p')['action'] == 'KEEP'
    assert row(rows, 2, 'The Beatles', 'Revolver', '01 Taxman 1.m4p')['action'] == 'DROP'

    # Higher bitrate wins even when it has the " 1" name
    assert row(rows, 1, 'Artist Two', 'Alb', '01 Tune 1.m4a')['action'] == 'KEEP'

    # Unreadable file is kept and flagged; voice memo kept
    assert row(rows, 2, 'Bad', 'Bad', '01 Broken.mp3')['action'] == 'REVIEW'
    assert row(rows, 1, 'Unknown Artist', 'Unknown Album', '20190515 191243.m4a')['action'] == 'KEEP'

    assert n['keep'] == 12 and n['drop'] == 6 and n['review'] == 2
    assert n['other_albums'] >= 1          # Come Together on Abbey Road and on Best Of

    review = open(project.path('review.csv'), encoding='utf-8').read()
    assert 'copy tags' in review and 'fingerprints disagree' in review and 'unreadable' in review


def test_plan_history_and_playlists(planned):
    project, n, rows = planned
    ct = row(rows, 1, 'The Beatles', 'Abbey Road', '01 Come Together.m4a')
    assert ct['plays'] == '12' and ct['rating'] == '80'           # merged from both copies
    assert ct['date_added'].startswith('2009-06-01')               # earliest
    assert ct['last_played'].startswith('2018-03-01 12:30')
    t1 = row(rows, 1, 'Unknown Artist', 'Unknown Album', 'Track 01.m4a')
    assert t1['plays'] == '6' and t1['loved'] == 'Y'
    assert row(rows, 2, 'The Beatles', 'Revolver', '01 Taxman.m4p')['plays'] == '4'   # Windows path
    assert row(rows, 2, 'Miles Davis', 'Kind Of Blue', '01 So What.aif')['rating'] == '100'  # by tags
    assert n['history_songs'] == 4

    x = n['xml'][0]
    assert x['entries'] == 9 and x['not_found'] == 2 and x['no_file'] == 1
    assert x['played_not_found'] == 1

    with open(project.path('playlists.json'), encoding='utf-8') as fh:
        pls = {p['name']: p for p in json.load(fh)}
    assert set(pls) == {'Road Trip', 'Chill'}
    names = [os.path.basename(s) for s in pls['Road Trip']['sources']]
    assert names == ['01 Come Together.m4a', 'Track 01.m4a', '01 Taxman.m4p', '01 So What.aif',
                     '05 Come Together.mp3']
    assert pls['Road Trip']['missing'] == 1
    assert n['smart_playlists'] == 1


def test_build_and_rebuild(planned):
    project, n, rows = planned
    b = build_merged(project, Reporter())
    assert b['files'] == 12 and b['verified'] == 12 and b['problems'] == 0
    assert b['stripped'] == 1 and b['renamed'] == 1 and b['joined'] == 1
    if is_mac():
        assert b['cloned'] == 12
    d = project.dest
    assert os.path.isfile(os.path.join(d, 'Artist Two', 'Alb', '01 Tune.m4a'))
    assert not os.path.exists(os.path.join(d, 'Artist Two', 'Alb', '01 Tune 1.m4a'))
    blue = sorted(os.listdir(os.path.join(d, 'Miles Davis')))
    assert blue == ['Kind Of Blue']                                  # one folder, not two
    assert len(os.listdir(os.path.join(d, 'The Beatles', 'Abbey Road'))) == 3
    # originals untouched
    assert os.path.exists(os.path.join(project.libraries[0]['path'], 'Artist Two', 'Alb', '01 Tune 1.m4a'))

    again = build_merged(project, Reporter())
    assert again['already'] == 12 and again['cloned'] + again['copied'] == 0


def test_history_follows_renamed_files(planned):
    project, n, rows = planned
    build_merged(project, Reporter())
    d = project.dest
    # what the tag step does: rename and move files inside the merged folder
    os.makedirs(os.path.join(d, 'Miles Davis', 'Kind of Blue (1959)'))
    os.rename(os.path.join(d, 'Miles Davis', 'Kind Of Blue', '01 So What.aif'),
              os.path.join(d, 'Miles Davis', 'Kind of Blue (1959)', '01 So What (Remastered).aif'))
    data, local = history_data(project)
    by_name = {os.path.basename(t['p']): t for t in data['tracks']}
    assert set(by_name) == {'01 Come Together.m4a', 'Track 01.m4a', '01 Taxman.m4p',
                            '01 So What (Remastered).aif'}
    assert by_name['01 Come Together.m4a']['played'] == '2018-03-01T12:30:00Z'
    assert by_name['Track 01.m4a']['loved'] is True
    road = next(p for p in data['playlists'] if p['name'] == 'Road Trip')
    assert os.path.basename(road['paths'][3]) == '01 So What (Remastered).aif'
    assert local == {'lost': 0, 'playlist_lost': 0}


def test_cleanup(planned, tmp_path):
    project, n, rows = planned
    build_merged(project, Reporter())
    rep = cleanup_report(project)
    assert rep['kinds']['video']['count'] == 1
    assert rep['kinds']['document']['count'] == 1
    assert rep['to_save'] == 2
    text = cleanup_text(rep)
    assert 'Lib One' in text and 'Lib Two' in text
    res = save_extras(project, str(tmp_path / 'Extras'))
    assert res['saved'] == 2
    assert os.path.isfile(tmp_path / 'Extras' / 'Lib One' / 'Movies' / 'clip.m4v')
    assert os.path.isfile(tmp_path / 'Extras' / 'Lib Two' / 'Booklet.pdf')


def test_tags_only_plan(project):
    project.use_fingerprints = False
    n = build_plan(project, Reporter())
    rows = plan_rows(project)
    # without fingerprints the untagged copy cannot be matched
    assert row(rows, 1, 'Unknown Artist', 'Unknown Album', 'Track 01.m4a')['group'] == ''
    assert n['fingerprints'] is False


def test_iso_utc():
    assert iso_utc('2018-03-01 12:30:00') == '2018-03-01T12:30:00Z'
    assert iso_utc('2018-03-01 12:30:00+00:00') == '2018-03-01T12:30:00Z'
    assert iso_utc('') == ''


# ---------------------------------------------------------------- Music app scripts
MOCK = r'''
const fs = require('fs');
const tracks = JSON.parse(process.argv[2]).map((p, i) => ({id: 1000 + i,
    loc: i % 2 ? p.normalize('NFD') : p, props: {}}));
const playlists = [];
function spec(id) {
  const t = tracks.find(x => x.id === id);
  return new Proxy({}, {
    set(o, k, v) { if (k === 'loved') throw new Error('no loved'); t.props[k] = v instanceof Date ? v.toISOString() : v; return true; },
    get(o, k) { if (k === 'location') return () => ({toString: () => t.loc}); if (k === '__id') return t.id; }
  });
}
global.Application = () => ({
  libraryPlaylists: [{fileTracks: {id: () => tracks.map(t => t.id),
      location: () => tracks.map(t => ({toString: () => t.loc})), byId: spec}}],
  userPlaylists: {name: () => playlists.map(p => p.name).concat(['Already Here'])},
  UserPlaylist: (props) => ({make: () => { const p = {name: props.name, items: []}; playlists.push(p); return p; }}),
  duplicate: (s, opts) => { opts.to.items.push(s.__id); },
});
eval(fs.readFileSync(process.argv[3], 'utf8') + '\nmodule.exports = run;');
const check = JSON.parse(module.exports(['check']));
const apply = JSON.parse(module.exports(['apply']));
console.log(JSON.stringify({check, apply, tracks, playlists}));
'''


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_history_script_against_mock_music(planned, tmp_path):
    project, n, rows = planned
    build_merged(project, Reporter())
    data, _ = history_data(project)
    data['playlists'].append(dict(name='Already Here', paths=[data['tracks'][0]['p']]))
    js = tmp_path / 'h.js'
    js.write_text(HISTORY_JS.replace('__DATA__', json.dumps(data)), encoding='utf-8')
    mock = tmp_path / 'mock.js'
    mock.write_text(MOCK)
    library = [os.path.join(dp, f) for dp, _, fs in os.walk(project.dest) if '.musicmerge' not in dp
               for f in fs]
    out = subprocess.run(['node', str(mock), json.dumps(library), str(js)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    res = json.loads(out.stdout)
    assert res['check']['found'] == 4 and res['check']['updated'] == 0
    assert res['apply']['updated'] == 4 and not res['apply']['errors']
    props = {os.path.basename(t['loc']): t['props'] for t in res['tracks'] if t['props']}
    assert props['01 Come Together.m4a'] == {'playedCount': 12, 'rating': 80,
                                             'playedDate': '2018-03-01T12:30:00.000Z'}
    assert props['Track 01.m4a'] == {'playedCount': 6, 'favorited': True}   # falls back from loved
    made = {p['name']: len(p['items']) for p in res['playlists']}
    assert made == {'Road Trip': 5, 'Chill': 1}
    statuses = {p['name']: p['status'] for p in res['apply']['playlists']}
    assert statuses['Already Here'] == 'already exists, left alone'


@pytest.mark.skipif(not is_mac(), reason='osacompile is macOS only')
def test_music_scripts_compile(tmp_path):
    for name, src in (('status', STATUS_JS), ('history', HISTORY_JS.replace('__DATA__', '{"tracks":[],"playlists":[]}'))):
        f = tmp_path / (name + '.js')
        f.write_text(src)
        out = subprocess.run(['osacompile', '-l', 'JavaScript', '-o', str(tmp_path / (name + '.scpt')), str(f)],
                             capture_output=True, text=True)
        assert out.returncode == 0, out.stderr


def test_cli_plan_and_build(libraries, tmp_path, fpcalc):
    dest = str(tmp_path / 'CLI Merged')
    env = dict(os.environ, PYTHONPATH=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    out = subprocess.run([sys.executable, '-m', 'musicmerge', 'plan', dest, '--library', libraries['a'],
                          '--library', libraries['b']], capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    assert 'Keep 12 files' in out.stdout and 'iTunes Music Library.xml' in out.stdout
    out = subprocess.run([sys.executable, '-m', 'musicmerge', 'build', dest], capture_output=True,
                         text=True, env=env)
    assert out.returncode == 0, out.stderr
    assert 'Checked in place: 12 of 12' in out.stdout
