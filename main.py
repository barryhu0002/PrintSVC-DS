# -*- coding: utf-8 -*-
"""PrintSVC - Entry point for direct execution."""
import sys
import os

# Ensure the parent directory is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from printsvc.main import main

if __name__ == "__main__":
    sys.exit(main())
