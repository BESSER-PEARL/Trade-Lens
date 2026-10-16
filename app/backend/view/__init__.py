# SPDX-License-Identifier: MIT
"""The view spec: a declarative page that agents edit through typed ops and browsers render.

The contract lives in contract/ at the repository root; schema/ and baseline.json here are copies
(tests/test_view_contract.py checks they are identical), so the backend stays self-contained.
"""
from .validate import (BASELINE_PATH, DEFAULT_DIMENSIONS, ENDPOINT_PARAMS, ENDPOINTS, MARK_ON, MAX_OPS, SCHEMA_DIR,
                       check_mark, check_panel, iter_queries, load_baseline, load_schema, query_url, resolve_path,
                       validate_ops, validate_spec)

__all__ = ["BASELINE_PATH", "DEFAULT_DIMENSIONS", "ENDPOINT_PARAMS", "ENDPOINTS", "MARK_ON", "MAX_OPS", "SCHEMA_DIR",
           "check_mark", "check_panel", "iter_queries", "load_baseline", "load_schema", "query_url", "resolve_path",
           "validate_ops", "validate_spec"]
