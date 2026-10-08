"""Step 2: build the merged library folder from plan.csv.

Copies every KEEP and REVIEW file into the destination as Artist/Album/file.
  - The original libraries are never changed, moved or deleted.
  - Nothing is ever overwritten; re-running skips files already in place.
  - On a Mac, when source and destination are on the same APFS drive, files are cloned:
    instant, and they take almost no extra space until one copy is changed.

Writes copy_map.csv (every original -> its merged file) and inodes.tsv (each merged
file's identity, so it can still be found after the tag step renames it)."""

import csv
import os
import re
import shutil
import subprocess
import time
from collections import Counter, defaultdict

from .common import fold, human, is_mac, norm, Reporter

COPY_SUFFIX = re.compile(r'^(.*\S) (\d{1,2})$')


def split_rel(rel):
    parts = rel.replace('\\', '/').split('/')
    artist = parts[-3] if len(parts) >= 3 else ''
    album = parts[-2] if len(parts) >= 2 else ''
    return artist, album, parts[-1]


def place(src, dst):
    """Clone (APFS) or copy src to dst through a .partial file. Returns 'clone' or 'copy'."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + '.partial'
    if os.path.exists(tmp):
        os.remove(tmp)
    how = 'copy'
    if is_mac():
        r = subprocess.run(['/bin/cp', '-c', '-p', src, tmp],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if r.returncode == 0:
            how = 'clone'
        elif os.path.exists(tmp):
            os.remove(tmp)
    if how == 'copy':
        shutil.copy2(src, tmp)
    os.replace(tmp, dst)
    return how


def read_plan(project):
    with open(project.path('plan.csv'), newline='', encoding='utf-8') as fh:
        return list(csv.DictReader(fh))


def layout(rows):
    """Decide each kept file's place in the merged folder. Returns [(row, rel)] and counts."""
    keep = [r for r in rows if r['action'] in ('KEEP', 'REVIEW')]

    count, best_lib = Counter(), {}
    for r in keep:
        artist, album, _ = split_rel(r['file'])
        count[(artist, album)] += 1
        best_lib[(artist, album)] = min(best_lib.get((artist, album), 99), int(r['lib']))
    variants = defaultdict(list)
    for artist, album in count:
        key = (norm(artist, True), norm(album))
        if not key[0] and not key[1]:
            key = (artist, album)
        variants[key].append((artist, album))
    canon, joined = {}, 0
    for key, opts in variants.items():
        best = max(opts, key=lambda ab: (count[ab], -best_lib[ab], ab))
        joined += len(opts) > 1
        for ab in opts:
            canon[ab] = best

    names_in_group = defaultdict(set)
    for r in rows:
        if r['group']:
            names_in_group[r['group']].add(fold(split_rel(r['file'])[2]))

    used, out = set(), []
    stripped = renamed = 0
    for r in sorted(keep, key=lambda r: (int(r['lib']), r['file'])):
        artist, album, fname = split_rel(r['file'])
        artist, album = canon[(artist, album)]
        stem, ext = os.path.splitext(fname)
        m = COPY_SUFFIX.match(stem)
        if r['group'] and m and fold(m.group(1) + ext) in names_in_group[r['group']]:
            stem = m.group(1)
            stripped += 1
        rel = '/'.join(p for p in (artist, album, stem + ext) if p)
        n = 2
        while fold(rel) in used:
            rel = '/'.join(p for p in (artist, album, '%s (%d)%s' % (stem, n, ext)) if p)
            n += 1
        renamed += n > 2
        used.add(fold(rel))
        out.append((r, rel))
    return out, dict(joined=joined, stripped=stripped, renamed=renamed)


def space_needed(project, plan_out):
    """Bytes the copy will really take: nothing for clones on the same APFS drive."""
    dest_dev = os.stat(project.dest).st_dev
    need = 0
    for r, rel in plan_out:
        size = os.path.getsize(r['full_path'])
        same = is_mac() and os.stat(r['full_path']).st_dev == dest_dev
        need += 0 if same else size
    return need


def build_merged(project, reporter=None, dry_run=False):
    reporter = reporter or Reporter()
    t0 = time.time()
    rows = read_plan(project)
    if not any(r['action'] in ('KEEP', 'REVIEW') for r in rows):
        raise RuntimeError('The plan has no files to keep. Run the scan first.')
    missing = [r['full_path'] for r in rows
               if r['action'] in ('KEEP', 'REVIEW') and not os.path.isfile(r['full_path'])]
    if missing:
        raise RuntimeError('%d files from the plan are missing (is the drive connected?), '
                           'for example:\n%s' % (len(missing), '\n'.join(missing[:5])))

    plan_out, counts = layout(rows)
    total = sum(os.path.getsize(r['full_path']) for r, _ in plan_out)
    os.makedirs(project.dest, exist_ok=True)
    need = space_needed(project, plan_out)
    free = shutil.disk_usage(project.dest).free
    if need and free < need * 1.02 + 200 * 1024 ** 2:
        raise RuntimeError('Not enough free space: the copy needs about %s, the destination drive '
                           'has %s free.' % (human(need), human(free)))
    reporter.log('Placing %d files (%s); %s of new space needed'
                 % (len(plan_out), human(total), human(need) if need else 'almost no'))
    if dry_run:
        return dict(files=len(plan_out), bytes=total, need=need, **counts)

    hows, problems = Counter(), []
    for n, (r, rel) in enumerate(plan_out, 1):
        if n % 25 == 0 or n == len(plan_out):
            reporter.progress('Building the merged folder', n, len(plan_out))
            reporter.check()
        src, dst = r['full_path'], os.path.join(project.dest, rel)
        try:
            if os.path.exists(dst):
                if os.path.getsize(dst) == os.path.getsize(src):
                    hows['already there'] += 1
                else:
                    problems.append('exists with a different size, left alone: ' + dst)
            else:
                hows[place(src, dst)] += 1
        except Exception as e:
            problems.append('%s: %s -> %s' % (e.__class__.__name__, src, dst))

    reporter.progress('Checking every file')
    verified = 0
    for r, rel in plan_out:
        d = os.path.join(project.dest, rel)
        if os.path.isfile(d) and os.path.getsize(d) == os.path.getsize(r['full_path']):
            verified += 1

    dest_of = {}
    for r, rel in plan_out:
        dest_of[r['full_path']] = rel
        if r['action'] == 'KEEP' and r['group']:
            dest_of['group:' + r['group']] = rel
    with open(project.path('copy_map.csv'), 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow(['action', 'group', 'lib', 'source', 'merged_file'])
        for r in rows:
            merged = dest_of.get(r['full_path']) or dest_of.get('group:' + r['group'], '')
            w.writerow([r['action'], r['group'], r['lib'], r['full_path'], merged])
    with open(project.path('inodes.tsv'), 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh, delimiter='\t')
        w.writerow(['inode', 'merged_file'])
        for r, rel in plan_out:
            d = os.path.join(project.dest, rel)
            if os.path.isfile(d):
                w.writerow([os.stat(d).st_ino, rel])

    numbers = dict(files=len(plan_out), bytes=total, verified=verified, problems=len(problems),
                   cloned=hows['clone'], copied=hows['copy'], already=hows['already there'],
                   dropped=sum(r['action'] == 'DROP' for r in rows),
                   seconds=round(time.time() - t0), **counts)
    lines = [
        'Merged library: %s' % project.dest,
        'Files placed: %d (%s): cloned %d, copied %d, already there %d'
        % (numbers['files'], human(total), numbers['cloned'], numbers['copied'], numbers['already']),
        'Checked in place at the same size as the original: %d of %d' % (verified, numbers['files']),
        'Problems: %d' % len(problems),
        'Albums joined from differently named folders: %d' % counts['joined'],
        'iTunes " 1" copy names cleaned: %d   renamed to avoid a clash: %d'
        % (counts['stripped'], counts['renamed']),
        'Duplicate copies left out: %d.  The original libraries were not touched.' % numbers['dropped'],
    ] + ['  ' + p for p in problems[:50]]
    with open(project.path('build_log.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(lines) + '\n')
    project.mark('build', **numbers)
    for p in problems[:20]:
        reporter.log(p)
    return numbers


def build_text(n):
    return ('Files placed: %d (%s)\n'
            'Checked in place: %d of %d\n'
            'Cloned (no extra space): %d   Copied: %d   Already there: %d\n'
            'Problems: %d\n'
            'Album folders joined: %d   " 1" copy names cleaned: %d   Renamed to avoid a clash: %d\n'
            'Duplicate copies left out: %d\n'
            % (n['files'], human(n['bytes']), n['verified'], n['files'], n['cloned'], n['copied'],
               n['already'], n['problems'], n['joined'], n['stripped'], n['renamed'], n['dropped']))
