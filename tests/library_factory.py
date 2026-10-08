"""Builds two small but realistic music libraries for the tests.

Audio is synthesized (no copyrighted music) and encoded with ffmpeg in the formats real
libraries contain. Every tricky case the merge has to get right is in here."""

import datetime
import os
import plistlib
import shutil
import subprocess
import wave

import numpy as np

SR = 44100


def ffmpeg_exe():
    exe = shutil.which('ffmpeg')
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def song(seed, secs):
    """A few seconds of 'music': random notes with harmonics plus a little noise."""
    rng = np.random.default_rng(seed)
    out, total = [], 0.0
    while total < secs:
        d = rng.choice([0.25, 0.5, 0.75, 1.0])
        t = np.arange(int(SR * d)) / SR
        f = 110 * 2 ** (rng.integers(0, 36) / 12)
        x = sum((0.5 / h) * np.sin(2 * np.pi * f * h * t) for h in range(1, 6))
        x += 0.3 * np.sin(2 * np.pi * 110 * 2 ** (rng.integers(0, 36) / 12) * t)
        out.append(x * np.minimum(1, t * 20) * np.exp(-t * 1.5))
        total += d
    y = np.concatenate(out)[:int(SR * secs)]
    y = y + 0.1 * rng.standard_normal(len(y)) * np.abs(y).max()
    y = y / np.abs(y).max() * 0.8
    return (y * 32767).astype(np.int16)


def write_wav(path, seed, secs):
    mono = song(seed, secs)
    w = wave.open(path, 'wb')
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes(np.repeat(mono[:, None], 2, axis=1).tobytes())
    w.close()


def encode(ffmpeg, src, dst, args, tags=None, strip=False):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    cmd = [ffmpeg, '-loglevel', 'error', '-y', '-i', src] + args
    if strip:
        cmd += ['-map_metadata', '-1']
    for k, v in (tags or {}).items():
        cmd += ['-metadata', '%s=%s' % (k, v)]
    subprocess.run(cmd + [dst], check=True)


AAC = ['-c:a', 'aac', '-b:a', '256k']
AAC_LOW = ['-c:a', 'aac', '-b:a', '96k']
MP3_128 = ['-c:a', 'libmp3lame', '-b:a', '128k', '-id3v2_version', '3']
MP3_192 = ['-c:a', 'libmp3lame', '-b:a', '192k', '-id3v2_version', '3']
AIFF = ['-c:a', 'pcm_s16be', '-write_id3v2', '1']


def tags(title, artist, album, track, **extra):
    t = dict(title=title, artist=artist, album=album, track=track)
    t.update(extra)
    return t


def make_libraries(base):
    """Creates base/Lib One and base/Lib Two. Returns their paths and the XML path."""
    ff = ffmpeg_exe()
    src = os.path.join(base, 'src')
    os.makedirs(src, exist_ok=True)
    seeds = {'come': (1, 45), 'something': (2, 50), 'taxman': (3, 40), 'sun_a': (4, 47),
             'sun_b': (5, 47.5), 'sowhat': (6, 55), 'memo': (7, 30), 'freddie': (8, 44),
             'tune': (9, 35), 'blue': (10, 42)}
    wav = {}
    for k, (seed, secs) in seeds.items():
        wav[k] = os.path.join(src, k + '.wav')
        write_wav(wav[k], seed, secs)

    a = os.path.join(base, 'Lib One')
    b = os.path.join(base, 'Lib Two')
    A = lambda *p: os.path.join(a, *p)
    B = lambda *p: os.path.join(b, *p)

    # Library one
    encode(ff, wav['come'], A('The Beatles', 'Abbey Road', '01 Come Together.m4a'), AAC,
           tags('Come Together', 'The Beatles', 'Abbey Road', 1, album_artist='The Beatles',
                date='1969', genre='Rock'))
    shutil.copy(A('The Beatles', 'Abbey Road', '01 Come Together.m4a'),
                A('The Beatles', 'Abbey Road', '01 Come Together 1.m4a'))
    encode(ff, wav['something'], A('The Beatles', 'Abbey Road', '02 Something.mp3'), MP3_128,
           tags('Something', 'The Beatles', 'Abbey Road', 2))
    encode(ff, wav['sun_a'], A('The Beatles', 'Abbey Road', '03 Here Comes the Sun.m4a'), AAC,
           tags('Here Comes the Sun', 'The Beatles', 'Abbey Road', 3))
    encode(ff, wav['something'], A('Unknown Artist', 'Unknown Album', 'Track 01.m4a'), AAC, strip=True)
    encode(ff, wav['come'], A('Compilations', 'Best Of', '05 Come Together.mp3'), MP3_192,
           tags('Come Together', 'The Beatles', 'Best Of', 5, compilation='1'))
    encode(ff, wav['memo'], A('Unknown Artist', 'Unknown Album', '20190515 191243.m4a'),
           ['-c:a', 'aac', '-b:a', '128k'], strip=True)
    encode(ff, wav['sowhat'], A('Miles Davis', 'Kind of Blue', '01 So What.m4a'), AAC,
           tags('So What', 'Miles Davis', 'Kind of Blue', 1))
    encode(ff, wav['blue'], A('Miles Davis', 'Kind of Blue', '03 Blue in Green.m4a'), AAC,
           tags('Blue in Green', 'Miles Davis', 'Kind of Blue', 3))
    encode(ff, wav['tune'], A('Artist Two', 'Alb', '01 Tune 1.m4a'), AAC, tags('Tune', 'Artist Two', 'Alb', 1))
    encode(ff, wav['tune'], A('Artist Two', 'Alb', '01 Tune.m4a'), AAC_LOW, tags('Tune', 'Artist Two', 'Alb', 1))
    os.makedirs(A('Movies'), exist_ok=True)
    with open(A('Movies', 'clip.m4v'), 'wb') as fh:
        fh.write(os.urandom(30000))
    with open(A('The Beatles', 'Abbey Road', 'Folder.jpg'), 'wb') as fh:
        fh.write(os.urandom(2000))

    # Library two
    encode(ff, wav['come'], B('The Beatles', 'Abbey Road', '01 Come Together.mp3'), MP3_192,
           tags('Come Together (Remastered 2009)', 'Beatles', 'Abbey Road', 1))
    encode(ff, wav['sun_b'], B('The Beatles', 'Abbey Road', '03 Here Comes The Sun.m4a'), AAC,
           tags('Here Comes The Sun', 'The Beatles', 'Abbey Road', 3))
    encode(ff, wav['taxman'], B('The Beatles', 'Revolver', '01 Taxman.m4a'), ['-c:a', 'aac', '-b:a', '128k'],
           tags('Taxman', 'The Beatles', 'Revolver', 1))
    os.rename(B('The Beatles', 'Revolver', '01 Taxman.m4a'), B('The Beatles', 'Revolver', '01 Taxman.m4p'))
    shutil.copy(B('The Beatles', 'Revolver', '01 Taxman.m4p'), B('The Beatles', 'Revolver', '01 Taxman 1.m4p'))
    encode(ff, wav['sowhat'], B('Miles Davis', 'Kind Of Blue', '01 So What.aif'), AIFF,
           tags('So What', 'Miles Davis', 'Kind of Blue', 1))
    encode(ff, wav['freddie'], B('Miles Davis', 'Kind Of Blue', '02 Freddie Freeloader.mp3'), MP3_192,
           tags('Freddie Freeloader', 'Miles Davis', 'Kind of Blue', 2))
    os.makedirs(B('Bad', 'Bad'), exist_ok=True)
    with open(B('Bad', 'Bad', '01 Broken.mp3'), 'wb') as fh:
        fh.write(os.urandom(20000))
    with open(B('Booklet.pdf'), 'wb') as fh:
        fh.write(b'%PDF-1.4\n' + os.urandom(5000))

    xml = os.path.join(a, 'iTunes Music Library.xml')
    write_xml(xml)
    return a, b, xml


def write_xml(path):
    base = 'file://localhost/Users/someone/Music/iTunes/iTunes%20Media/Music/'

    def t(i, loc, **kw):
        d = {'Track ID': i}
        if loc:
            d['Location'] = loc
        d.update(kw)
        return d
    D = datetime.datetime
    tracks = {
        '1': t(1, base + 'The%20Beatles/Abbey%20Road/01%20Come%20Together.m4a',
               **{'Play Count': 10, 'Rating': 80, 'Date Added': D(2010, 1, 1), 'Play Date UTC': D(2018, 3, 1, 12, 30)}),
        '2': t(2, base + 'The%20Beatles/Abbey%20Road/01%20Come%20Together%201.m4a',
               **{'Play Count': 2, 'Date Added': D(2009, 6, 1)}),
        '3': t(3, base + 'The%20Beatles/Abbey%20Road/02%20Something.mp3', **{'Play Count': 5, 'Loved': True}),
        '4': t(4, base + 'Unknown%20Artist/Unknown%20Album/Track%2001.m4a', **{'Play Count': 1}),
        '5': t(5, base + 'Gone/Gone/01%20Missing.mp3', **{'Play Count': 3}),
        '7': t(7, 'file://localhost/E:/Music/The%20Beatles/Revolver/01%20Taxman.m4p', **{'Play Count': 4}),
        '8': t(8, 'file://localhost/Volumes/OLD/Stuff/so_what_final.m4a',
               **{'Name': 'So What', 'Artist': 'Miles Davis', 'Album': 'Kind of Blue',
                  'Total Time': 55000, 'Rating': 100}),
        '9': t(9, base + 'Compilations/Best%20Of/05%20Come%20Together.mp3'),
        '10': t(10, None, **{'Name': 'Cloud Song', 'Artist': 'X', 'Play Count': 2}),
        '11': t(11, base + 'Movies/clip.m4v', **{'Has Video': True, 'Play Count': 9}),
        '12': t(12, base + 'Miles%20Davis/Kind%20of%20Blue/03%20Rated%20Computed.m4a',
                **{'Rating': 60, 'Rating Computed': True}),
    }
    playlists = [
        {'Name': 'Library', 'Master': True, 'Playlist Items': [{'Track ID': 1}]},
        {'Name': 'Music', 'Distinguished Kind': 4, 'Playlist Items': [{'Track ID': 1}]},
        {'Name': 'Road Trip', 'Playlist Items': [{'Track ID': i} for i in (1, 3, 7, 8, 5, 2, 9)]},
        {'Name': 'Top 25', 'Smart Info': b'x', 'Playlist Items': [{'Track ID': 1}]},
        {'Name': 'Chill', 'Playlist Items': [{'Track ID': 8}]},
    ]
    with open(path, 'wb') as fh:
        plistlib.dump({'Major Version': 1, 'Date': D(2026, 10, 7, 17, 12, 35),
                       'Application Version': '12.8.3.1',
                       'Music Folder': 'file://localhost/Users/someone/Music/iTunes/iTunes%20Media/',
                       'Tracks': tracks, 'Playlists': playlists}, fh)
