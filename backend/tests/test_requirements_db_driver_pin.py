"""requirements の DB ドライバ契約（IK-0360）。

SQLAlchemy 2.1 は ``postgresql://`` の既定ドライバを psycopg2 から psycopg（3 系）へ変えた。
requirements が psycopg2-binary だけを入れる間は SQLAlchemy を 2.0 系に固定しないと、新規ビルドの
イメージが起動時に「No module named 'psycopg'」で落ちる（開発機の venv は旧版のまま動くので気づかない）。
psycopg（3 系）へ乗り換えるときは、このテストを「psycopg が requirements にある」側に書き換える。
"""
from __future__ import annotations

import re
from pathlib import Path

REQ = Path(__file__).resolve().parents[1] / "api" / "requirements.txt"


def _requirements() -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in REQ.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)\s*(.*)$", line)
        if m:
            out[m.group(1).lower()] = m.group(2).strip()
    return out


def test_sqlalchemy_pinned_to_driver_it_ships_with() -> None:
    reqs = _requirements()
    assert "sqlalchemy" in reqs, "requirements に sqlalchemy が無い"
    spec = reqs["sqlalchemy"]
    has_psycopg3 = any(name in ("psycopg", "psycopg[binary]") for name in reqs)
    if has_psycopg3:
        return  # 3 系を同梱しているなら 2.1 の既定に乗ってよい
    assert "psycopg2-binary" in reqs or "psycopg2" in reqs, "psycopg2 も psycopg も無い"
    assert re.search(r"<\s*2\.1\b", spec), (
        f"sqlalchemy の指定 {spec!r} に上限 <2.1 が無い。psycopg2 しか同梱しない間は 2.0 系に固定する（IK-0360）"
    )
