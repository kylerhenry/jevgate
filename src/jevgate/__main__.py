"""Allow ``python -m jevgate`` (useful from a plain clone with PYTHONPATH=src)."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
