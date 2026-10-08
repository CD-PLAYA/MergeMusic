"""Fails when any program or library inside the app needs a newer macOS than promised.

  python packaging/check_min_macos.py dist/MusicMerge.app 13.0

pip picks wheels for the Mac doing the build, so a build on macOS 15 can quietly pull in
a library that refuses to load on macOS 13. This reads every Mach-O file's minimum
macOS version (LC_BUILD_VERSION / LC_VERSION_MIN_MACOSX) and checks it."""

import os
import struct
import sys

FAT = (0xcafebabe, 0xcafebabf)
THIN = {0xfeedface: '<', 0xfeedfacf: '<', 0xcefaedfe: '>', 0xcffaedfe: '>'}


def versions(blob):
    """Minimum macOS versions of each architecture in a Mach-O file."""
    magic = struct.unpack('>I', blob[:4])[0]
    slices = []
    if magic in FAT:
        n = struct.unpack('>I', blob[4:8])[0]
        size = 20 if magic == 0xcafebabe else 32
        for i in range(n):
            off = 8 + size * i
            if magic == 0xcafebabe:
                _, _, start, length, _ = struct.unpack('>5I', blob[off:off + 20])
            else:
                _, _, start, length, _ = struct.unpack('>2I2QI', blob[off:off + 28])
            slices.append(blob[start:start + length])
    else:
        slices.append(blob)
    out = []
    for s in slices:
        m = struct.unpack('<I', s[:4])[0]
        if m not in (0xfeedface, 0xfeedfacf):
            continue
        is64 = m == 0xfeedfacf
        ncmds = struct.unpack('<I', s[16:20])[0]
        p = 32 if is64 else 28
        for _ in range(ncmds):
            cmd, size = struct.unpack('<2I', s[p:p + 8])
            if cmd == 0x32:                                   # LC_BUILD_VERSION
                platform, minos = struct.unpack('<2I', s[p + 8:p + 16])
                if platform == 1:
                    out.append((minos >> 16, (minos >> 8) & 0xff))
            elif cmd == 0x24:                                 # LC_VERSION_MIN_MACOSX
                v = struct.unpack('<I', s[p + 8:p + 12])[0]
                out.append((v >> 16, (v >> 8) & 0xff))
            p += size
    return out


def main(app, limit):
    want = tuple(int(x) for x in limit.split('.')[:2])
    bad, checked = [], 0
    for d, _, files in os.walk(app):
        for f in files:
            path = os.path.join(d, f)
            if os.path.islink(path):
                continue
            try:
                with open(path, 'rb') as fh:
                    head = fh.read(4)
                    if len(head) < 4 or (struct.unpack('>I', head)[0] not in FAT and
                                         struct.unpack('>I', head)[0] not in THIN):
                        continue
                    blob = head + fh.read()
            except OSError:
                continue
            checked += 1
            for v in versions(blob):
                if v > want:
                    bad.append('%s needs macOS %d.%d' % (os.path.relpath(path, app), *v))
    print('Checked %d binaries against macOS %s' % (checked, limit))
    for b in bad:
        print('  TOO NEW: ' + b)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else '13.0'))
