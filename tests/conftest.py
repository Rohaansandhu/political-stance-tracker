import sys
from pathlib import Path

# Scripts run as `python src/foo.py`, so their imports are relative to src/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
