# Third-party software in MusicMerge.app

MusicMerge is free software under the GNU General Public License, version 3 or later.
The app bundles the following projects, each under its own license:

| Project | Used for | License | Source |
|---|---|---|---|
| Python | runtime | PSF License | https://www.python.org |
| Qt for Python (PySide6) | the window | LGPL-3.0 | https://wiki.qt.io/Qt_for_Python |
| Mutagen | reading tags | GPL-2.0-or-later | https://github.com/quodlibet/mutagen |
| Chromaprint fpcalc | audio fingerprints | LGPL-2.1 (includes FFmpeg code) | https://github.com/acoustid/chromaprint |
| beets | tag clean-up | MIT | https://github.com/beetbox/beets |
| pyacoustid | AcoustID lookups | MIT | https://github.com/beetbox/pyacoustid |
| NumPy and other beets dependencies | | BSD / MIT | see each package |

Data comes from MusicBrainz (https://musicbrainz.org, CC0 core data) and AcoustID
(https://acoustid.org). Cover art comes from the Cover Art Archive.
