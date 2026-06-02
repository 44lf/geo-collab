"""Lock the worker entrypoint's driver registration.

The production worker runs as a SEPARATE process (`python -m server.worker.executor`)
from the web app. It must register BOTH the default Toutiao DOM driver and the
in-page variant, so that `GEO_TOUTIAO_DRIVER=inpage` actually resolves to the
in-page driver in the worker process.

These tests spawn a FRESH subprocess that imports ONLY `server.worker.executor`
(without calling `main()`). Doing it in-process would be polluted by other test
imports (e.g. conftest importing the app) that already register the variant,
which would falsely make a naive assertion pass. The subprocess guarantees a
genuine RED-before / GREEN-after.

Importing the worker module does NOT need a live DB (`create_engine` is lazy),
but it does require `GEO_DATA_DIR` and a `GEO_DATABASE_URL` to be set, so the
subprocess env supplies a throwaway data dir and an unused MySQL URL.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile

# Script run in a fresh interpreter. It imports ONLY the worker entrypoint
# (module-level code, NOT main()), then asks the driver registry to resolve the
# Toutiao driver and prints the resolved class name. If no driver is registered,
# `resolve_driver` raises and we print the exception so the assertion fails
# loudly instead of crashing the subprocess silently.
_SUBPROCESS_SCRIPT = """
import server.worker.executor  # noqa: F401  (must register drivers at import)

from server.app.modules.tasks.drivers import resolve_driver

try:
    print(type(resolve_driver("toutiao")).__name__)
except Exception as exc:  # pragma: no cover - exercised only on the RED path
    print(f"RESOLVE_ERROR: {type(exc).__name__}: {exc}")
"""


def _run_worker_subprocess(driver_env: str | None) -> str:
    """Import the worker entrypoint in a fresh process and return the resolved
    Toutiao driver class name (or an error marker)."""
    with tempfile.TemporaryDirectory() as data_dir:
        env = {
            "GEO_DATA_DIR": data_dir,
            "GEO_DATABASE_URL": "mysql+pymysql://u:p@127.0.0.1:3306/geo_unused_test",
            "GEO_JWT_SECRET": "test-secret-not-used-here",
        }
        if driver_env is not None:
            env["GEO_TOUTIAO_DRIVER"] = driver_env

        # Inherit PATH etc. from the current process so the interpreter and its
        # site-packages resolve correctly, but layer our config on top.
        import os

        full_env = {**os.environ, **env}
        if driver_env is None:
            full_env.pop("GEO_TOUTIAO_DRIVER", None)

        result = subprocess.run(
            [sys.executable, "-c", _SUBPROCESS_SCRIPT],
            capture_output=True,
            text=True,
            env=full_env,
            timeout=120,
        )
    assert result.returncode == 0, (
        f"subprocess failed (rc={result.returncode})\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return result.stdout.strip().splitlines()[-1]


def test_worker_import_registers_inpage_variant():
    """With GEO_TOUTIAO_DRIVER=inpage, the worker process must resolve the
    in-page driver — proving `toutiao_inpage` is registered at worker import."""
    resolved = _run_worker_subprocess("inpage")
    assert resolved == "ToutiaoInPageDriver", (
        f"expected ToutiaoInPageDriver, got {resolved!r}; "
        "the worker entrypoint did not register the in-page variant"
    )


def test_worker_import_registers_default_driver():
    """With GEO_TOUTIAO_DRIVER unset, the worker process must resolve the
    default DOM driver — proving `toutiao` is registered at worker import."""
    resolved = _run_worker_subprocess(None)
    assert resolved == "ToutiaoDriver", (
        f"expected ToutiaoDriver, got {resolved!r}; "
        "the worker entrypoint did not register the default driver"
    )
