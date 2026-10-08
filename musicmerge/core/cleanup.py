"""Step 6: what can be deleted afterwards, and how much space that gives back.

MusicMerge never deletes anything itself. This step reports:
  - the original library folders, which are no longer needed
  - files in them that are not music (videos, PDFs, ...) and were not carried over,
    with a way to save them first
  - an honest estimate of the space freed: when the merged files were cloned, they share
    disk space with the originals, so deleting the originals frees much less than their size
"""

import csv
import os
from collections import Counter, defaultdict

from .build import place
from .common import Reporter, human, plural

SAVE_KINDS = ('video', 'document', 'other')


def _others(project):
    path = project.path('others.csv')
    if not os.path.exists(path):
        return []
    with open(path, newline='', encoding='utf-8') as fh:
        return [dict(r, bytes=int(r['bytes'] or 0), lib=int(r['lib'])) for r in csv.DictReader(fh)]


def cleanup_report(project):
    plan = project.step_info('plan')
    build = project.step_info('build')
    others = _others(project)
    by_kind = defaultdict(lambda: [0, 0])
    for o in others:
        by_kind[o['kind']][0] += 1
        by_kind[o['kind']][1] += o['bytes']
    libs = []
    for lib in plan.get('libraries', []):
        n = lib['number']
        o_bytes = sum(o['bytes'] for o in others if o['lib'] == n)
        libs.append(dict(number=n, name=lib['name'], path=lib['path'],
                         exists=os.path.isdir(lib['path']), bytes=lib['bytes'] + o_bytes))
    total = sum(l['bytes'] for l in libs)
    cloned = build.get('cloned', 0) > 0 and build.get('copied', 0) == 0
    if cloned:
        estimate = plan.get('drop_bytes', 0) + sum(o['bytes'] for o in others)
    else:
        estimate = total
    return dict(libraries=libs, total=total, cloned=cloned, estimate=estimate,
                kinds={k: dict(count=v[0], bytes=v[1]) for k, v in sorted(by_kind.items())},
                to_save=sum(1 for o in others if o['kind'] in SAVE_KINDS),
                to_save_bytes=sum(o['bytes'] for o in others if o['kind'] in SAVE_KINDS))


def cleanup_text(rep):
    L = ['The original library folders are no longer needed:']
    for l in rep['libraries']:
        L.append('  %d. %s  (%s%s)' % (l['number'], l['path'], human(l['bytes']),
                                      '' if l['exists'] else ', not connected'))
    if rep['cloned']:
        L.append('Deleting them frees about %s, not their full %s: the merged files are clones '
                 'that share disk space with the originals. Only the duplicate copies and the '
                 'non-music files truly use extra space.' % (human(rep['estimate']), human(rep['total'])))
    else:
        L.append('Deleting them frees about %s.' % human(rep['estimate']))
    names = {'video': ('video', None, ''), 'document': ('document', None, ' (PDF, books)'),
             'other': ('other file', None, ''), 'image': ('loose image', None, ' (album art)'),
             'library file': ('old library or artwork-cache file', None, '')}
    if rep['kinds']:
        L.append('')
        L.append('Files in the old folders that are not in the merged library:')
        for k, v in rep['kinds'].items():
            one, many, note = names.get(k, (k, None, ''))
            L.append('  %s%s (%s)' % (plural(v['count'], one, many), note, human(v['bytes'])))
        if rep['to_save']:
            L.append('Save the videos, documents and other files first if you want them.')
    return '\n'.join(L) + '\n'


def save_extras(project, target, reporter=None, kinds=SAVE_KINDS):
    """Clone or copy the non-music files into target/<library name>/<same sub-folders>."""
    reporter = reporter or Reporter()
    plan = project.step_info('plan')
    roots = {l['number']: (l['path'], l['name']) for l in plan.get('libraries', [])}
    items = [o for o in _others(project) if o['kind'] in kinds]
    done = Counter()
    for n, o in enumerate(items, 1):
        reporter.progress('Saving files', n, len(items))
        reporter.check()
        root, name = roots[o['lib']]
        rel = os.path.relpath(o['path'], root)
        dst = os.path.join(target, name, rel)
        if os.path.exists(dst) or not os.path.exists(o['path']):
            done['skipped'] += 1
            continue
        done[place(o['path'], dst)] += 1
    return dict(saved=done['clone'] + done['copy'], skipped=done['skipped'], files=len(items))
