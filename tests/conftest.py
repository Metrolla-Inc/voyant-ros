import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for sub in ("helpers", "legacy"):
    sys.path.insert(0, str(HERE / sub))
sys.path.insert(0, str(HERE.parent / "docker"))
