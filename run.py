# run.py —— CLI 入口

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from trace.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
