import sys
from pathlib import Path

# backend/ is the import root: tests import `schemas`, `pipeline`, ... directly.
BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
