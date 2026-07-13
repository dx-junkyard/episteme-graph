"""ルーター登録のガードレール（Tier 3-17c フラット化の恒久固定）。

main.py が admin 系子ルーターを prefix="/api/admin" で直接登録するフラット構成に
移行した（アーキテクチャ整理 Tier 3-17c）。このテストは、将来ルーターの二重
include（例: admin.py 側での include_router 復活 + main.py 側登録の併存）で
同一 (path, method) が複数登録される退行を構造的に検出する。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import fastapi  # noqa: F401
    _HAS_FASTAPI = True
except Exception:  # pragma: no cover
    _HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not _HAS_FASTAPI, reason="FastAPI 未導入")


def _collect_path_methods():
    from api.main import app

    pairs = []
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        for method in methods:
            if method == "HEAD":  # GET に自動付与されるため対象外
                continue
            pairs.append((path, method))
    return pairs


def test_no_duplicate_route_registration():
    pairs = _collect_path_methods()
    seen: set = set()
    duplicates = []
    for pair in pairs:
        if pair in seen:
            duplicates.append(pair)
        seen.add(pair)
    assert not duplicates, (
        "同一 (path, method) が複数登録されている（ルーター二重 include の疑い）: "
        f"{sorted(duplicates)}"
    )


def test_admin_child_routers_registered_flat():
    """admin 系子ルーターは main.py が直接登録する（admin.router に include しない）。"""
    import inspect

    from api.routes import admin as admin_module

    source = inspect.getsource(admin_module)
    lines = [
        line for line in source.splitlines()
        if "include_router" in line and not line.lstrip().startswith("#")
    ]
    assert not lines, (
        "admin.py に include_router が復活している（Tier 3-17c のフラット化に反する）: "
        f"{lines}"
    )
