"""Load config.settings.production in a clean subprocess.

Reloading the module in-process would mutate state shared with the test settings (the DATABASES dict and
MIDDLEWARE list are the same objects), so these checks run in their own interpreter instead.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[3]
DEFAULTS = {
    "DJANGO_SECRET_KEY": "x" * 60,
    "DATABASE_URL": "postgres://u:p@localhost:5432/db",
}


def load(*names: str, **env: str) -> dict:
    """Return the named production settings for the given environment, or {"error": "<ExceptionName>"}."""
    clean = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "LANG"}}
    clean.update({"DJANGO_SETTINGS_MODULE": "config.settings.production", **DEFAULTS, **env})
    code = (
        "import json, sys\n"
        "try:\n"
        "    import config.settings.production as s\n"
        f"    print(json.dumps({{n: getattr(s, n) for n in {list(names)!r}}}))\n"
        "except Exception as exc:\n"
        "    print(json.dumps({'error': type(exc).__name__}))\n"
    )
    result = subprocess.run(  # noqa: S603 - fixed argv, test-only
        [sys.executable, "-c", code], cwd=BASE_DIR, env=clean, capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout.strip().splitlines()[-1])
