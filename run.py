# -*- coding: utf-8 -*-
"""Entry point for PyInstaller."""
import sys
import os
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    try:
        from printsvc.main import main
        rc = main()
        sys.exit(rc if rc is not None else 0)
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        input("\nPress Enter to exit...")
        sys.exit(1)
