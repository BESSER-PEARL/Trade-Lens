# SPDX-License-Identifier: MIT
"""besser-sdmx: turn any SDMX dataset into a BESSER model, a database and a dashboard."""
from ._guard import WrongBesserError, check_besser

check_besser()  # fail fast on a BESSER other than vendor/BESSER + patches/besser/ (e.g. via PYTHONPATH)

from .importer import SOURCES, SDMXClient, Source, TooLargeError  # noqa: E402
from .importer import ImportMapping, sdmx_to_buml  # noqa: E402
from .importer import DataStructure, parse_structure  # noqa: E402

__all__ = [
    "SOURCES", "SDMXClient", "Source", "TooLargeError",
    "ImportMapping", "sdmx_to_buml", "DataStructure", "parse_structure", "WrongBesserError",
]
__version__ = "0.1.0"
