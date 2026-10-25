from __future__ import annotations

import importlib.util
from pathlib import Path

root_patch = Path(__file__).resolve().parent.parent / "sitecustomize.py"
if root_patch.exists():
    spec = importlib.util.spec_from_file_location("_repo_root_sitecustomize", root_patch)
    if spec and spec.loader:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
