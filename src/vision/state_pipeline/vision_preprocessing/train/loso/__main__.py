import sys

from .cli import main

if __name__ == "__main__":          # guard needed: DataLoader workers re-import this module on Windows
    sys.exit(main())
