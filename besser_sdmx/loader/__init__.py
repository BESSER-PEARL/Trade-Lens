# SPDX-License-Identifier: MIT
"""Loader: SDMX data (fetched live from the API) goes into the database BESSER generated.

Ours. Uses the ``sql_alchemy.py`` BESSER generated from the imported class diagram, so the tables
always match the model; records every loaded slice so the analytics API can fetch missing detail.
"""
from .loader import Loader, Slice, build_key, fetch, import_module

__all__ = ["Loader", "Slice", "build_key", "fetch", "import_module"]
