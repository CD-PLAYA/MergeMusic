"""A merge project: the libraries being merged, the destination folder, and the work
folder (<destination>/.mergemusic) that holds every plan, map and report.

Keeping the work folder inside the destination means a merge can be resumed later,
even from another Mac, as long as the destination folder is there."""

import json
import os
import re
import time

from .common import WORK_DIR_NAME, inside, fold

STEPS = ('plan', 'build', 'tags', 'history')
XML_NAME = re.compile(r'^(itunes (music )?library|library|music library)\.xml$', re.I)


def looks_like_itunes_xml(path):
    try:
        with open(path, 'rb') as fh:
            head = fh.read(16384)
    except OSError:
        return False
    return b'<plist' in head and b'<key>Tracks</key>' in head


def find_xml(folder):
    """The iTunes/Music library XML that belongs to a media folder, if one is nearby.

    Looks in the folder, two levels below it and in its parent (iTunes keeps the XML
    next to the 'iTunes Media' folder). Newest file wins."""
    folder = os.path.abspath(folder)
    found = []

    def look(d, depth):
        try:
            entries = list(os.scandir(d))
        except OSError:
            return
        for e in entries:
            if e.name.startswith('.'):
                continue
            if e.is_file() and XML_NAME.match(e.name) and looks_like_itunes_xml(e.path):
                found.append(e.path)
            elif e.is_dir() and depth > 0:
                look(e.path, depth - 1)

    look(folder, 2)
    parent = os.path.dirname(folder)
    if parent and parent != folder:
        look(parent, 0)
    if not found:
        return ''
    return max(found, key=lambda p: os.path.getmtime(p))


class Project:
    def __init__(self, dest):
        self.dest = os.path.abspath(os.path.expanduser(dest)).rstrip('/') or '/'
        self.work = os.path.join(self.dest, WORK_DIR_NAME)
        self.state = {'version': 1, 'libraries': [], 'use_fingerprints': True, 'steps': {}}
        if os.path.isfile(self.state_file):
            with open(self.state_file, encoding='utf-8') as fh:
                self.state.update(json.load(fh))

    # ------------------------------------------------------------ basics
    @property
    def state_file(self):
        return os.path.join(self.work, 'state.json')

    def exists(self):
        return os.path.isfile(self.state_file)

    def path(self, *names):
        return os.path.join(self.work, *names)

    def save(self):
        os.makedirs(self.work, exist_ok=True)
        tmp = self.state_file + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(self.state, fh, indent=2, default=str)
        os.replace(tmp, self.state_file)

    # ------------------------------------------------------------ libraries
    @property
    def libraries(self):
        """[{'path': ..., 'xml': ...}] in order of preference (first wins ties)."""
        return self.state['libraries']

    def library_name(self, number):
        lib = self.libraries[number - 1]
        return lib.get('name') or os.path.basename(lib['path'].rstrip('/')) or lib['path']

    def set_libraries(self, libraries):
        new = [{'path': os.path.abspath(l['path']).rstrip('/'), 'xml': l.get('xml') or ''}
               for l in libraries]
        if [(l['path'], l['xml']) for l in new] != [(l['path'], l.get('xml', '')) for l in self.libraries]:
            self.state['libraries'] = new
            for step in STEPS:                  # a different set of libraries invalidates the plan
                self.state['steps'].pop(step, None)

    @property
    def use_fingerprints(self):
        return bool(self.state.get('use_fingerprints', True))

    @use_fingerprints.setter
    def use_fingerprints(self, value):
        self.state['use_fingerprints'] = bool(value)

    def problems(self):
        """Reasons this project cannot run, in plain words. Empty list = fine."""
        out = []
        if not self.libraries:
            out.append('Add at least one library folder.')
        for i, lib in enumerate(self.libraries, 1):
            if not os.path.isdir(lib['path']):
                out.append('Library %d is not a folder (is the drive connected?): %s' % (i, lib['path']))
            if lib.get('xml') and not os.path.isfile(lib['xml']):
                out.append('History file for library %d not found: %s' % (i, lib['xml']))
            if inside(self.dest, lib['path']):
                out.append('The destination is inside library %d. Choose a folder outside it.' % i)
            if inside(lib['path'], self.dest):
                out.append('Library %d is inside the destination. Choose a different destination.' % i)
            for j, other in enumerate(self.libraries, 1):
                if i != j and inside(lib['path'], other['path']):
                    out.append('Library %d is inside library %d. Add only the outer folder.' % (i, j))
                if i < j and fold(lib['path']) == fold(other['path']):
                    out.append('Library %d and %d are the same folder.' % (i, j))
        if os.path.isdir(self.dest) and not self.exists():
            visible = [n for n in os.listdir(self.dest) if not n.startswith('.')]
            if visible:
                out.append('The destination folder is not empty. Choose a new or empty folder.')
        return out

    # ------------------------------------------------------------ steps
    def mark(self, step, **info):
        info['finished'] = time.strftime('%Y-%m-%d %H:%M:%S')
        self.state['steps'][step] = info
        self.save()

    def done(self, step):
        return step in self.state['steps']

    def step_info(self, step):
        return self.state['steps'].get(step, {})

    def clear(self, *steps):
        for s in steps:
            self.state['steps'].pop(s, None)
        self.save()
