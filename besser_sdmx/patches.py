# SPDX-License-Identifier: MIT
"""Post-generation fixes for BESSER 8.1.0 output.

Self-associations (e.g. Product.parent / Product.children) are generated without
``remote_side``, so SQLAlchemy sees both ends as one-to-many and refuses to map
them. Until the upstream template is fixed, this adds ``remote_side=[Cls.<pk>]``
to the many-to-one end of every self-relationship in a generated sql_alchemy.py.
"""
from __future__ import annotations

import re
from pathlib import Path

_PK = re.compile(r"^class (\w+)\(Base\):\n(?:    .*\n)*?    (\w+): Mapped_\[\w+\] = mapped_column\([^\n]*primary_key=True", re.M)
_SELF_ONE = re.compile(
    r'^(?P<cls>\w+)\.(?P<end>\w+): Mapped_\["(?P=cls)"\] = relationship\("(?P=cls)", (?P<args>[^\n]*uselist=False[^\n]*)\)$',
    re.M,
)


def fix_self_relationships(source: str) -> str:
    pks = {m.group(1): m.group(2) for m in _PK.finditer(source)}

    def add_remote_side(m: re.Match) -> str:
        cls, args = m.group("cls"), m.group("args")
        if "remote_side" in args or cls not in pks:
            return m.group(0)
        return f'{cls}.{m.group("end")}: Mapped_["{cls}"] = relationship("{cls}", {args}, remote_side=[{cls}.{pks[cls]}])'

    return _SELF_ONE.sub(add_remote_side, source)


def patch_generated_backend(backend_dir: str | Path) -> bool:
    """Apply the fixes in place; returns True when the file changed."""
    path = Path(backend_dir) / "sql_alchemy.py"
    src = path.read_text(encoding="utf-8")
    fixed = fix_self_relationships(src)
    if fixed != src:
        path.write_text(fixed, encoding="utf-8")
        return True
    return False
