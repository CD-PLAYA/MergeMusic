"""Entry point for the bundled app (PyInstaller)."""
import multiprocessing
import sys

from mergemusic.cli import main

if __name__ == '__main__':
    multiprocessing.freeze_support()
    sys.exit(main())
