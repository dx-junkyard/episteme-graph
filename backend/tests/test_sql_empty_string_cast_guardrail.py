"""空文字を条件分岐で NULL に逃がしてから uuid へ CAST する SQL を禁止する（IK-0508）。

PostgreSQL は ``CASE WHEN :p = '' THEN NULL ELSE CAST(:p AS uuid) END`` の ``CAST('' AS uuid)`` を
計画時に定数畳み込みするため、分岐が NULL 側でも ``invalid input syntax for type uuid: ""`` で落ちる
（第 13 周: 再構成の初回提出が全件 500）。空文字は ``CAST(NULLIF(:p, '') AS uuid)`` で扱う。
"""
from __future__ import annotations

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
_PATTERN = re.compile(r"CASE\s+WHEN\s+:(\w+)\s*=\s*''\s+THEN\s+NULL\s+ELSE\s+CAST\(\s*:\1\s+AS\s+uuid", re.I)


def test_no_case_guarded_empty_string_uuid_cast():
    offenders = []
    for path in list((BACKEND / "api").rglob("*.py")) + list((BACKEND / "core").rglob("*.py")):
        if _PATTERN.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(BACKEND)))
    assert offenders == [], f"CAST(NULLIF(:p, '') AS uuid) を使う: {offenders}"


def test_submit_uses_nullif_for_revision_of():
    src = (BACKEND / "api" / "routes" / "reconstruction.py").read_text(encoding="utf-8")
    assert "CAST(NULLIF(:rev, '') AS uuid)" in src
