from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_archived_path = Path(__file__).resolve().parent / "archive" / "2026-10-12-legacy-workflow" / "validity_analysis.py"
_module_name = _archived_path.stem
_spec = importlib.util.spec_from_file_location(_module_name, _archived_path)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Could not load archived module from {_archived_path}")
_module = importlib.util.module_from_spec(_spec)
sys.modules[_module_name] = _module
_spec.loader.exec_module(_module)

for _name in dir(_module):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_module, _name)

__all__ = [name for name in dir(_module) if not name.startswith("__")]
