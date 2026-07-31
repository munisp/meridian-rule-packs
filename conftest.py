"""Pytest bootstrap: make tools/ importable (tools.refmatch, tools.conformance)."""
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
