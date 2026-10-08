"""Audio fingerprints with Chromaprint's fpcalc: finds copies of the same recording even
when their tags or file names disagree."""

import base64
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from array import array
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor

from .common import is_mac, resource_dirs, user_data_dir

FP_LENGTH = 120      # seconds of audio fingerprinted per file
FP_MIN_SHARED = 20   # shared fingerprint values before a pair is compared closely
FP_MAX_BER = 0.15    # max bit-error rate for "same audio" (0.0 = identical)
DUR_TOL = 3.0        # seconds: copies of one track must be this close in length

FPCALC_VERSION = '1.6.1'
FPCALC_URL = 'https://github.com/acoustid/chromaprint/releases/download/v{v}/chromaprint-fpcalc-{v}-{plat}.{ext}'


def find_fpcalc():
    """Path to fpcalc, or None. Looks in the app bundle, MusicMerge's own folder, then PATH."""
    exe = 'fpcalc.exe' if os.name == 'nt' else 'fpcalc'
    env = os.environ.get('FPCALC')
    if env and os.access(env, os.X_OK):
        return env
    for d in resource_dirs():
        p = os.path.join(d, exe)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return shutil.which('fpcalc')


def fpcalc_download_url():
    machine = platform.machine().lower()
    if is_mac():
        return FPCALC_URL.format(v=FPCALC_VERSION, plat='macos-universal', ext='tar.gz')
    if sys.platform.startswith('linux'):
        plat = 'linux-arm64' if machine in ('aarch64', 'arm64') else 'linux-x86_64'
        return FPCALC_URL.format(v=FPCALC_VERSION, plat=plat, ext='tar.gz')
    if os.name == 'nt':
        return FPCALC_URL.format(v=FPCALC_VERSION, plat='windows-x86_64', ext='zip')
    return None


def download_fpcalc(reporter=None):
    """Fetch fpcalc from the official Chromaprint release into MusicMerge's folder."""
    url = fpcalc_download_url()
    if not url:
        raise RuntimeError('No fpcalc download for this system; install Chromaprint yourself.')
    if reporter:
        reporter.progress('Downloading fpcalc')
        reporter.log('Downloading ' + url)
    with urllib.request.urlopen(url, timeout=120) as resp:
        data = resp.read()
    exe = 'fpcalc.exe' if os.name == 'nt' else 'fpcalc'
    target_dir = os.path.join(user_data_dir(), 'bin')
    os.makedirs(target_dir, exist_ok=True)
    target = os.path.join(target_dir, exe)
    if url.endswith('.zip'):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            member = next(n for n in z.namelist() if n.endswith('/' + exe) or n == exe)
            blob = z.read(member)
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as t:
            member = next(m for m in t.getmembers() if m.name.endswith('/' + exe) or m.name == exe)
            blob = t.extractfile(member).read()
    with open(target, 'wb') as fh:
        fh.write(blob)
    os.chmod(target, 0o755)
    return target


def fpcalc_version(fpcalc):
    try:
        out = subprocess.run([fpcalc, '-version'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             universal_newlines=True, timeout=30)
        return out.stdout.strip()
    except Exception as e:
        return 'not working (%s)' % e


def _key(r):
    return '%s|%d|%d' % (r.path, r.size, r.mtime)


def fingerprint_all(recs, fpcalc, cache_path, reporter, workers=4):
    """Fingerprint every readable, unprotected file. Results are cached by path, size
    and date, so a second run only does new or changed files. Returns the count."""
    cache = {}
    if os.path.exists(cache_path):
        try:
            with open(cache_path) as fh:
                cache = json.load(fh)
        except Exception:
            cache = {}

    todo = [r for r in recs if not r.protected and not r.error and _key(r) not in cache]
    done = [0]

    def run(r):
        reporter.check()
        try:
            out = subprocess.run([fpcalc, '-raw', '-json', '-length', str(FP_LENGTH), r.path],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 universal_newlines=True, timeout=180)
            fp = json.loads(out.stdout).get('fingerprint') if out.returncode == 0 else None
        except Exception:
            fp = None
        done[0] += 1
        if done[0] % 25 == 0 or done[0] == len(todo):
            reporter.progress('Fingerprinting audio', done[0], len(todo))
        if fp:
            return _key(r), base64.b64encode(array('I', [v & 0xFFFFFFFF for v in fp]).tobytes()).decode()
        return _key(r), ''

    def save():
        tmp = cache_path + '.tmp'
        with open(tmp, 'w') as fh:
            json.dump(cache, fh)
        os.replace(tmp, cache_path)

    if todo:
        reporter.log('Fingerprinting %d files (%d already done earlier)' % (len(todo), len(cache)))
        try:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                for k, v in ex.map(run, todo):
                    cache[k] = v
        finally:
            save()                       # keep finished work even when stopped

    n = 0
    for r in recs:
        v = cache.get(_key(r)) if not r.protected else None
        if v:
            a = array('I')
            a.frombytes(base64.b64decode(v))
            r.fp = a
            n += 1
        else:
            r.fp = None
    return n


def _first_positions(fp):
    pos = {}
    for i, v in enumerate(fp):
        if v not in pos:
            pos[v] = i
    return pos


def _bit_error(a, b, off):
    """Mean bit-error rate with b shifted so that b[i + off] lines up with a[i]."""
    if off >= 0:
        pa, pb = a, b[off:]
    else:
        pa, pb = a[-off:], b
    n = min(len(pa), len(pb))
    if n < 40:
        return 1.0
    errs = sum((x ^ y).bit_count() for x, y in zip(pa[:n], pb[:n]))
    return errs / (32.0 * n)


def fingerprint_pairs(recs, reporter):
    """All pairs of files (lengths within DUR_TOL) whose audio matches: [(i, j, ber)]."""
    have = sorted((r for r in recs if r.fp is not None and r.length), key=lambda r: r.length)
    win, pairs = deque(), []
    for n, r in enumerate(have, 1):
        if n % 200 == 0 or n == len(have):
            reporter.progress('Comparing fingerprints', n, len(have))
            reporter.check()
        while win and r.length - win[0].length > DUR_TOL:
            old = win.popleft()
            old.fpset = old.fppos = None
        rset, rpos = set(r.fp), None
        for o in win:
            shared = rset & o.fpset
            if len(shared) < FP_MIN_SHARED:
                continue
            if rpos is None:
                rpos = _first_positions(r.fp)
            off = Counter(o.fppos[v] - rpos[v] for v in shared).most_common(1)[0][0]
            ber = _bit_error(r.fp, o.fp, off)
            if ber <= FP_MAX_BER:
                pairs.append((r.idx, o.idx, ber))
        r.fpset, r.fppos = rset, (rpos or _first_positions(r.fp))
        win.append(r)
    for r in win:
        r.fpset = r.fppos = None
    return pairs
