"""Keep tests runnable when this folder is copied outside its parent repository."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
