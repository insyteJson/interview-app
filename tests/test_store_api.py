"""Every store method the pages call must exist, and the cached Store key must follow the storage code."""
import pathlib
import re

import storage

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_every_called_store_method_exists():
    called = set()
    for f in ["app.py", "admin.py", "review.py", "rating.py", "profile_ui.py", "autostar.py", "roles_ui.py"]:
        p = ROOT / f
        if p.exists():
            called |= set(re.findall(r"\bstore\.([a-zA-Z_]\w*)\s*\(", p.read_text()))
    missing = sorted(m for m in called if not hasattr(storage.Store, m) and not hasattr(storage.RunCache, m))
    assert not missing, missing


def test_cache_key_contains_code_fingerprint():
    src = (ROOT / "app.py").read_text()
    assert "STORE_CODE_FINGERPRINT" in src.split("def get_store")[1]
    assert len(storage.STORE_CODE_FINGERPRINT) == 12
