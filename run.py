# -*- coding: utf-8 -*-
"""Entry point for PyInstaller."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from printsvc.main import main

if __name__ == "__main__":
    sys.exit(main())
