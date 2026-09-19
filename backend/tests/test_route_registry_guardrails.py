"""ルート登録検査の正本は ``tests/guardrail_helpers.py::iter_app_routes`` / ``collect_route_pairs``。

FastAPI 0.139 以降、``app.include_router(...)`` は子ルーターを遅延ラッパー（``_IncludedRouter``）の
まま ``app.routes`` に置き、include 時の prefix もラッパー側に持つ。テストが ``app.routes`` を素朴に
歩くと、版によって「空」か「prefix 抜きのパス」を見て**静かに空振り**する（2026-09-19 の CI で
FastAPI 0.141 により登録検査 10 件が落ちた。ローカルは 0.136 で平坦なため見えなかった）。

守るもの:
- テストは ``app.routes`` を直接歩かない（ヘルパー以外）。
- ヘルパーは遅延ラッパーを開き、prefix を連結した実パスを返す（0.139 未満の平坦な形でも同じ結果）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from fastapi import APIRouter, FastAPI

from tests.guardrail_helpers import collect_route_pairs, iter_app_routes

TESTS_DIR = Path(__file__).resolve().parent
BACKEND = TESTS_DIR.parent
# api/main.py 配下のルーターは `from dependencies import ...` のように api/ 直下を起点に import する。
# 単体実行でも解決できるよう、他のルート検査（test_admin_assistant 等）と同じく両方を載せる。
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
_DIRECT_WALK_RE = re.compile(r"\bin\s+[\w.\[\]\"']*\bapp\.routes\b")
_ALLOWLIST = {"guardrail_helpers.py", Path(__file__).name}


def _strip_comments_and_docstrings(src: str) -> str:
    src = re.sub(r'"""[\s\S]*?"""', "", src)
    src = re.sub(r"'''[\s\S]*?'''", "", src)
    return "\n".join(line.split("#", 1)[0] for line in src.splitlines())


class TestNoDirectAppRoutesWalk:
    def test_tests_use_the_flattening_helper(self):
        offenders = []
        for path in sorted(TESTS_DIR.glob("test_*.py")):
            if path.name in _ALLOWLIST:
                continue
            body = _strip_comments_and_docstrings(path.read_text(encoding="utf-8"))
            for m in _DIRECT_WALK_RE.finditer(body):
                line = body.count("\n", 0, m.start()) + 1
                offenders.append(f"{path.name}:{line}")
        assert offenders == [], (
            "テストが app.routes を直接歩いている（FastAPI の版で結果が変わる）。"
            "tests/guardrail_helpers.py の iter_app_routes / collect_route_pairs を使うこと: " + ", ".join(offenders)
        )


class TestHelperComposesIncludePrefix:
    def _app(self) -> FastAPI:
        leaf = APIRouter()

        @leaf.get("/items/{item_id}")
        def _get_item(item_id: str):  # pragma: no cover - 経路の存在だけを見る
            return {}

        @leaf.delete("/items/{item_id}")
        def _del_item(item_id: str):  # pragma: no cover
            return {}

        mid = APIRouter(prefix="/mid")
        mid.include_router(leaf)

        @mid.post("/own")
        def _own():  # pragma: no cover
            return {}

        app = FastAPI()
        app.include_router(mid, prefix="/api/admin")
        app.include_router(leaf, prefix="/api/learning")
        return app

    def test_nested_include_prefixes_are_composed(self):
        pairs = collect_route_pairs(self._app())
        assert ("/api/admin/mid/items/{item_id}", "GET") in pairs
        assert ("/api/admin/mid/items/{item_id}", "DELETE") in pairs
        assert ("/api/admin/mid/own", "POST") in pairs
        assert ("/api/learning/items/{item_id}", "GET") in pairs
        assert not any(p.startswith("/items") or p.startswith("/mid") for p, _ in pairs), (
            "prefix 抜きのパスが混じっている（遅延ラッパーの中身を素で返している）"
        )

    def test_flattened_routes_keep_endpoint_attributes(self):
        routes = [r for r in iter_app_routes(self._app()) if r.path == "/api/admin/mid/own"]
        assert len(routes) == 1
        route = routes[0]
        assert "POST" in route.methods
        assert callable(route.endpoint)
        assert route.dependant is not None
        assert route.path_format == "/api/admin/mid/own"

    def test_real_app_exposes_prefixed_admin_routes(self):
        from api.main import app

        pairs = collect_route_pairs(app)
        assert ("/api/admin/discovery/search", "POST") in pairs
        assert ("/api/admin/documents/{document_id}/reference-health", "GET") in pairs
        assert ("/api/indicators", "GET") in pairs
