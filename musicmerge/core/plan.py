"""Step 1: scan every library and decide, track by track, which copy to keep.

Changes nothing outside the work folder. Writes:
  plan.csv          one row per audio file: KEEP / DROP / REVIEW and why
  review.csv        things a person should look at
  other_albums.csv  the same recording on two different albums (both kept)
  playlists.json    playlists from the library XML files, as source files
  others.csv        non-audio files found in the libraries (for the clean-up step)
  xml_not_found.csv XML entries whose file is not in any library
  plan_summary.txt  the headline numbers
"""

import csv
import json
import os
import re
import time
from collections import Counter, defaultdict

from .common import (DOC_EXT, IMAGE_EXT, JUNK_EXT, VIDEO_EXT, human, plural)
from .fingerprint import (DUR_TOL, find_fpcalc, fingerprint_all, fingerprint_pairs)
from .itunes_xml import Matcher, merge_history, read_library_xml
from .scan import scan_library

PLAN_COLUMNS = ['action', 'group', 'why', 'lib', 'file', 'artist', 'album_artist', 'album',
                'disc', 'track', 'title', 'seconds', 'kbps', 'codec', 'lossless', 'protected',
                'tag_score', 'weak_tags', 'matched_by', 'fingerprint', 'plays', 'rating',
                'loved', 'date_added', 'last_played', 'full_path']


def other_kind(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in VIDEO_EXT:
        return 'video'
    if ext in DOC_EXT:
        return 'document'
    if ext in JUNK_EXT or os.path.basename(path).lower() in ('desktop.ini', 'thumbs.db'):
        return 'library file'
    if ext in IMAGE_EXT:
        return 'image'
    return 'other'


def unmatched_kind(r):
    stem = os.path.splitext(os.path.basename(r.rel))[0].strip()
    if re.match(r'^\d{8}\s+\d{6}', stem):
        return 'named like a recording date/time (voice memo style)'
    if r.n_artist in ('', 'unknown artist') or r.n_album in ('', 'unknown album'):
        return 'Unknown Artist or Unknown Album'
    if not r.title_from_tag:
        return 'no title tag'
    return 'generic title (Track 01 etc.)'


def build_plan(project, reporter):
    t0 = time.time()
    os.makedirs(project.work, exist_ok=True)
    libs = project.libraries
    recs, others = [], []
    lib_stats = []
    for n, lib in enumerate(libs, 1):
        reporter.log('Reading library %d: %s' % (n, lib['path']))
        r_lib, o_lib = scan_library(n, lib['path'], reporter)
        recs += r_lib
        others += [(n, p, s) for p, s in o_lib]
        lib_stats.append(dict(number=n, name=project.library_name(n), path=lib['path'],
                              audio=len(r_lib), bytes=sum(r.size for r in r_lib),
                              protected=sum(r.protected for r in r_lib),
                              videos=sum(1 for p, s in o_lib if other_kind(p) == 'video')))
        reporter.log('  %d audio files (%s)' % (len(r_lib), human(lib_stats[-1]['bytes'])))
    for i, r in enumerate(recs):
        r.idx = i

    parent = list(range(len(recs)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    # 1) tag matches: same album + title + artist (or album artist), lengths close
    reporter.progress('Matching tags')
    keyed = defaultdict(list)
    for r in recs:
        if r.weak or r.error or not r.length or not r.n_title or not r.n_album:
            continue
        keyed[('t', r.n_album, r.n_title, r.n_artist)].append(r)
        keyed[('a', r.n_album, r.n_title, r.n_aa)].append(r)
    for members in keyed.values():
        members.sort(key=lambda r: r.length)
        for x, y in zip(members, members[1:]):
            if y.length - x.length <= DUR_TOL:
                union(x.idx, y.idx)
                x.matched = y.matched = 'tags'

    # 2) fingerprint matches
    fp_count, cross, pairs = 0, [], []
    fpcalc = find_fpcalc() if project.use_fingerprints else None
    if project.use_fingerprints and not fpcalc:
        reporter.log('fpcalc not found: matching on tags only.')
    if fpcalc:
        fp_count = fingerprint_all(recs, fpcalc, project.path('fingerprints.json'), reporter)
        pairs = fingerprint_pairs(recs, reporter)
        for i, j, ber in pairs:
            a, b = recs[i], recs[j]
            if find(i) == find(j):
                continue
            if a.weak or b.weak or (a.n_album and a.n_album == b.n_album):
                union(i, j)
                a.matched = b.matched = 'audio'
            else:
                cross.append((a, b, 'audio fingerprint'))
    else:
        for r in recs:
            r.fp = None

    groups = defaultdict(list)
    for r in recs:
        groups[find(r.idx)].append(r)

    confirmed = set()
    for i, j, ber in pairs:
        if find(i) == find(j):
            confirmed.update((i, j))
    for r in recs:
        if r.fp is None:
            r.fp_status = 'protected' if r.protected else ('none' if fpcalc else '')
        elif len(groups[find(r.idx)]) == 1:
            r.fp_status = 'ok'
        else:
            r.fp_status = 'match' if r.idx in confirmed else 'MISMATCH'

    # 3) the same song on two different albums (information only; both are kept)
    by_song = defaultdict(list)
    for r in recs:
        if not r.weak and r.length:
            by_song[(r.n_artist, r.n_title)].append(r)
    seen = set((min(a.idx, b.idx), max(a.idx, b.idx)) for a, b, _ in cross)
    for members in by_song.values():
        if len(members) > 60:             # e.g. "Intro" by Various Artists: not useful
            continue
        for x in range(len(members)):
            for y in range(x + 1, len(members)):
                a, b = members[x], members[y]
                if find(a.idx) == find(b.idx) or a.n_album == b.n_album:
                    continue
                if abs(a.length - b.length) > 2.0:
                    continue
                k = (min(a.idx, b.idx), max(a.idx, b.idx))
                if k not in seen:
                    seen.add(k)
                    cross.append((a, b, 'tags + length'))

    # 4) play history and playlists from each library's XML
    matcher = Matcher(recs)
    xml_results = []
    for n, lib in enumerate(libs, 1):
        if not lib.get('xml'):
            continue
        reporter.progress('Reading history file of library %d' % n)
        try:
            res = read_library_xml(lib['xml'], n, matcher)
        except Exception as e:
            reporter.log('Could not read history file %s: %s' % (lib['xml'], e))
            xml_results.append(dict(number=n, error=str(e)))
            continue
        res['number'] = n
        xml_results.append(res)
        for idx, h in res['history'].items():
            recs[idx].hist = merge_history([recs[idx].hist, h])

    # 5) choose the copy to keep in every group
    def rank(r):
        stem = os.path.splitext(os.path.basename(r.rel))[0]
        not_itunes_copy = not re.search(r' \d+$', stem)
        return (r.lossless, not r.protected, round(r.kbps / 32.0), r.tag_score,
                -r.lib, not_itunes_copy, r.size)

    review, stats, gnum = [], Counter(), 0
    for root, members in sorted(groups.items()):
        if len(members) == 1:
            r = members[0]
            r.action, r.why = 'KEEP', 'only copy'
            if r.error:
                r.action, r.why = 'REVIEW', r.error
                review.append(('unreadable', r.error, r.label, ''))
            elif r.weak:
                review.append(('unidentified', 'no usable tags and no audio match', r.label, ''))
            continue
        gnum += 1
        members.sort(key=rank, reverse=True)
        best = members[0]
        stats['groups_across' if len(set(m.lib for m in members)) > 1 else 'groups_within'] += 1
        mismatch = any(m.fp_status == 'MISMATCH' for m in members)
        by_audio = any(m.matched == 'audio' for m in members)
        stats['groups_audio'] += by_audio
        note = ' (audio match)' if by_audio else ''
        for m in members:
            m.group = gnum
        best.action, best.why = 'KEEP', 'best of %d copies%s' % (len(members), note)
        for m in members[1:]:
            if mismatch:
                m.action, m.why = 'REVIEW', 'tags match but audio may differ'
            else:
                m.action, m.why = 'DROP', 'lower-ranked copy' + note
        if mismatch:
            stats['mismatch_groups'] += 1
            review.append(('check group %d' % gnum, 'tags say same track, fingerprints disagree',
                           best.label, ' | '.join(m.label for m in members[1:])))
        if best.weak and not mismatch:
            donor = max(members[1:], key=lambda m: m.tag_score)
            if not donor.weak:
                review.append(('copy tags', 'best audio copy has weak tags; the other copy has better ones',
                               best.label, donor.label))
        keep_hist = [m.hist for m in members if m.action != 'REVIEW' or m is best]
        best.hist = merge_history(keep_hist)
        for m in members[1:]:
            if m.action == 'DROP':
                m.hist = None

    # ---------------------------------------------------------------- outputs
    reporter.progress('Writing the plan')
    order = sorted(recs, key=lambda r: (r.n_aa or '', r.n_album or '', r.disc or 0, r.track or 0,
                                        str(r.group), r.action != 'KEEP', r.lib))
    with open(project.path('plan.csv'), 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow(PLAN_COLUMNS)
        for r in order:
            h = r.hist or {}
            w.writerow([r.action, r.group, r.why, r.lib, r.rel, r.artist, r.albumartist, r.album,
                        r.disc or '', r.track or '', r.title, '%.1f' % (r.length or 0),
                        int(round(r.kbps)), r.codec, 'Y' if r.lossless else '',
                        'Y' if r.protected else '', r.tag_score, 'Y' if r.weak else '',
                        r.matched if r.group else '', r.fp_status,
                        h.get('plays', ''), h.get('rating', ''), 'Y' if h.get('loved') else '',
                        h.get('added', ''), h.get('last', ''), r.path])

    with open(project.path('review.csv'), 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow(['issue', 'detail', 'file', 'other copies'])
        w.writerows(review)

    def kept_of(r):
        return next((m for m in groups[find(r.idx)] if m.action == 'KEEP'), r)
    pairs_out = {}
    for a, b, how in cross:
        ka, kb = kept_of(a), kept_of(b)
        if ka is kb:
            continue
        k = (min(ka.idx, kb.idx), max(ka.idx, kb.idx))
        if k not in pairs_out or how == 'audio fingerprint':
            pairs_out[k] = (ka, kb, how)
    cross = list(pairs_out.values())
    with open(project.path('other_albums.csv'), 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow(['matched by', 'artist', 'title', 'album 1', 'file 1', 'album 2', 'file 2'])
        for a, b, how in sorted(cross, key=lambda x: (x[0].n_artist, x[0].n_title)):
            w.writerow([how, a.artist, a.title, a.album, a.label, b.album, b.label])

    with open(project.path('others.csv'), 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow(['lib', 'kind', 'bytes', 'path'])
        for n, p, s in others:
            w.writerow([n, other_kind(p), s, p])

    playlists, used_names = [], Counter()
    missing_rows = []
    for res in xml_results:
        if 'error' in res:
            continue
        for p in res['playlists']:
            name = p['name']
            used_names[name] += 1
            if used_names[name] > 1:
                name = '%s (%s)' % (name, project.library_name(res['number']))
            sources, seen_src = [], set()
            for idx in p['recs']:
                src = kept_of(recs[idx]).path
                if src not in seen_src:
                    seen_src.add(src)
                    sources.append(src)
            playlists.append(dict(name=name, library=res['number'], sources=sources,
                                  missing=p['missing']))
        missing_rows += [(res['number'],) + m for m in res['missing']]
    with open(project.path('playlists.json'), 'w', encoding='utf-8') as fh:
        json.dump(playlists, fh, indent=1, ensure_ascii=False)
    with open(project.path('xml_not_found.csv'), 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow(['library', 'plays', 'artist', 'album', 'name', 'path'])
        w.writerows(missing_rows)

    # ---------------------------------------------------------------- summary
    keep = [r for r in recs if r.action in ('KEEP', 'REVIEW')]
    drop = [r for r in recs if r.action == 'DROP']
    unmatched = [r for r in recs if r.weak and not r.group and not r.error]
    numbers = dict(
        libraries=lib_stats, files=len(recs),
        groups=gnum, groups_within=stats['groups_within'], groups_across=stats['groups_across'],
        keep=len(keep), keep_bytes=sum(r.size for r in keep),
        drop=len(drop), drop_bytes=sum(r.size for r in drop),
        review=sum(r.action == 'REVIEW' for r in recs),
        kept_by_lib={str(n): sum(r.lib == n for r in keep) for n in range(1, len(libs) + 1)},
        protected_kept=sum(r.protected for r in keep),
        fingerprints=bool(fpcalc), fingerprinted=fp_count, groups_audio=stats['groups_audio'],
        mismatch_groups=stats['mismatch_groups'],
        weak=sum(r.weak for r in recs), unmatched=len(unmatched),
        unmatched_kinds=dict(Counter(unmatched_kind(r) for r in unmatched).most_common()),
        unreadable=sum(1 for r in recs if r.error), other_albums=len(cross),
        history_songs=sum(1 for r in keep if r.hist and (r.hist['plays'] or r.hist['rating']
                                                          or r.hist['loved'])),
        playlists=len(playlists),
        smart_playlists=sum(res['stats']['smart_playlists'] for res in xml_results if 'stats' in res),
        xml=[dict(number=res['number'], error=res.get('error', ''),
                  **({k: res['stats'][k] for k in ('entries', 'found_path', 'found_tags',
                                                   'not_found', 'no_file', 'played',
                                                   'played_not_found', 'rated')}
                     if 'stats' in res else {}),
                  **res.get('info', {})) for res in xml_results],
        seconds=round(time.time() - t0),
    )
    text = summary_text(project, numbers)
    with open(project.path('plan_summary.txt'), 'w', encoding='utf-8') as fh:
        fh.write(text)
    project.mark('plan', **numbers)
    for step in ('build', 'tags', 'history'):      # a new plan means later steps start over
        project.state['steps'].pop(step, None)
    project.save()
    reporter.log('Plan finished in %d s' % numbers['seconds'])
    return numbers


def summary_text(project, n):
    L = []
    for lib in n['libraries']:
        L.append('Library %d (%s): %s, %s%s' % (
            lib['number'], lib['name'], plural(lib['audio'], 'audio file'), human(lib['bytes']),
            ', %d copy-protected' % lib['protected'] if lib['protected'] else ''))
    L.append('')
    L.append('Duplicate groups: %d (inside one library %d, across libraries %d)'
             % (n['groups'], n['groups_within'], n['groups_across']))
    L.append('Keep %s (%s). Leave out %s (%s).'
             % (plural(n['keep'], 'file'), human(n['keep_bytes']),
                plural(n['drop'], 'duplicate copy', 'duplicate copies'), human(n['drop_bytes'])))
    if n['review']:
        L.append('%s kept but flagged for a look (review.csv).'
                 % plural(n['review'], 'file is', 'files are'))
    if len(n['libraries']) > 1:
        L.append('Kept from each library: ' + ', '.join(
            '%s %s' % (lib['name'], n['kept_by_lib'][str(lib['number'])]) for lib in n['libraries']))
    if n['fingerprints']:
        L.append('Audio fingerprints: %s compared; %s found by sound alone.'
                 % (plural(n['fingerprinted'], 'file'),
                    plural(n['groups_audio'], 'group was', 'groups were')))
    else:
        L.append('Audio fingerprints: not used (matching on tags only).')
    if n['unmatched']:
        L.append('%s weak tags and no match; kept as they are:'
                 % plural(n['unmatched'], 'file has', 'files have'))
        for k, v in n['unmatched_kinds'].items():
            L.append('    %5d  %s' % (v, k))
    if n['unreadable']:
        L.append('%s not be read; kept and listed in review.csv.'
                 % plural(n['unreadable'], 'file could', 'files could'))
    if n['other_albums']:
        L.append('%s on two different albums; both copies are kept (other_albums.csv).'
                 % plural(n['other_albums'], 'song appears', 'songs appear'))
    L.append('')
    if not n['xml']:
        L.append('Play history: no library history file was given.')
    for x in n['xml']:
        if x.get('error'):
            L.append('History file of library %d could not be read: %s' % (x['number'], x['error']))
            continue
        found = x.get('found_path', 0) + x.get('found_tags', 0)
        L.append('History file of library %d: %s, %d found on disk, %d not found%s.'
                 % (x['number'], plural(x.get('entries', 0), 'song'), found, x.get('not_found', 0),
                    ' (%d of them had plays)' % x['played_not_found'] if x.get('played_not_found') else ''))
    if n['xml']:
        L.append('Songs carrying play counts, ratings or loves: %d.  Playlists: %d%s.'
                 % (n['history_songs'], n['playlists'],
                    ' (%s cannot be copied)' % plural(n['smart_playlists'], 'smart playlist')
                    if n['smart_playlists'] else ''))
    return '\n'.join(L) + '\n'
