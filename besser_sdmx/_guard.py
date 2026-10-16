# SPDX-License-Identifier: MIT
"""Fail fast when Python imports a BESSER other than the vendored, patched one.

``scripts/setup.*`` installs BESSER v8.1.0 + ``patches/besser/`` from ``vendor/BESSER``. A
``PYTHONPATH`` pointing at another BESSER checkout (or a PyPI install) silently wins over it, and
then generation, the tests and the app behave differently. Set ``BESSER_SDMX_ALLOW_ANY_BESSER=1``
to skip this check on purpose (e.g. when developing against your own patched BESSER).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parents[1]
VENDORED = REPO / "vendor" / "BESSER"
ESCAPE_HATCH = "BESSER_SDMX_ALLOW_ANY_BESSER"


class WrongBesserError(ImportError):
    """The imported ``besser`` is not the vendored, patched BESSER of this repository."""


def wrong_besser_reason() -> Optional[str]:
    """Why the imported BESSER is not the expected one, or None when it is (or the check is off)."""
    if os.environ.get(ESCAPE_HATCH, "").strip() not in ("", "0", "false", "False"):
        return None
    try:
        import besser
    except ImportError:
        return "BESSER is not installed."
    location = Path(besser.__file__).resolve().parent
    problems = []
    if VENDORED.exists() and VENDORED.resolve() not in location.parents:
        problems.append(f"it is imported from {location}, not from {VENDORED / 'besser'}")
    try:
        from besser.BUML.metamodel.gui.binding import DataBinding
        if "endpoint" not in DataBinding.__init__.__code__.co_varnames:
            raise ImportError
    except ImportError:
        problems.append("it lacks our patches (DataBinding.endpoint, patch 0005)")
    if not problems:
        return None
    hint = ""
    if os.environ.get("PYTHONPATH"):
        hint = f"\n  PYTHONPATH is set to {os.environ['PYTHONPATH']!r}: unset it for this project."
    return ("Wrong BESSER: " + "; ".join(problems) + "."
            + hint
            + "\n  Fix: unset PYTHONPATH (bash: `unset PYTHONPATH`, PowerShell: `Remove-Item Env:PYTHONPATH`)"
              " and run scripts/setup.sh (Windows: scripts\\setup.ps1), which installs vendor/BESSER"
              " with patches/besser/."
            + f"\n  To skip this check on purpose: set {ESCAPE_HATCH}=1.")


def check_besser() -> None:
    """Raise WrongBesserError with instructions when the wrong BESSER is imported."""
    reason = wrong_besser_reason()
    if reason:
        raise WrongBesserError(reason)
