"""nginx リバースプロキシのルーティング網羅チェック (回帰ガード)。

背景: `/api/atlas` の location が nginx.conf に無く、SPA フォールバック
(`location /`) が index.html を **200 で**返してフロントの JSON パースが
失敗する事故が起きた (docs/features/field_atlas_binding.md)。ブラウザから
呼ばれる API prefix が増えたのに nginx.conf を更新し忘れると同じ事故が
再発するため、FastAPI アプリの全 `/api/...` prefix が nginx.conf の
location でカバーされていることを静的に検証する。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_HAS_FASTAPI = True
try:
    from fastapi.testclient import TestClient  # noqa: F401
except ImportError:
    _HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(
    not _HAS_FASTAPI, reason="FastAPI not installed (run inside Docker for full API tests)"
)

_NGINX_CONF = Path(__file__).resolve().parents[3] / "frontend" / "nginx.conf"

# ブラウザ導線が無い (nginx 経由で呼ばれない) prefix はここで明示的に除外する。
# 追加する場合は「本当にブラウザから呼ばれないか」を確認すること。
_BROWSER_EXEMPT_PREFIXES: set[str] = set()


def _api_prefixes() -> set[str]:
    """FastAPI アプリの全ルートから `/api/<第1セグメント>` prefix を集める。"""
    backend_dir = str(Path(__file__).resolve().parents[2])
    api_dir = str(Path(__file__).resolve().parents[2] / "api")
    src_dir = str(Path(__file__).resolve().parents[3] / "src")
    for p in (backend_dir, api_dir, src_dir):
        if p not in sys.path:
            sys.path.insert(0, p)
    from api.main import app

    from tests.guardrail_helpers import iter_app_routes

    prefixes: set[str] = set()
    # FastAPI 0.139 以降の遅延 include ラッパーを開いてから走査する。
    for route in iter_app_routes(app):
        path = getattr(route, "path", "")
        m = re.match(r"^/api/([^/{]+)", path)
        if m:
            prefixes.add(f"/api/{m.group(1)}")
    return prefixes


def _nginx_locations() -> list[str]:
    text = _NGINX_CONF.read_text(encoding="utf-8")
    # `location /api/xxx/ {` / `location = /api/xxx {` の両形式を拾う
    return re.findall(r"location\s+(?:=\s+)?(/\S+)", text)


@pytest.mark.skipif(not _NGINX_CONF.exists(), reason="frontend/nginx.conf not found")
def test_all_api_prefixes_are_proxied():
    locations = _nginx_locations()
    missing = []
    for prefix in sorted(_api_prefixes() - _BROWSER_EXEMPT_PREFIXES):
        covered = any(
            loc == prefix or loc == prefix + "/" or loc.startswith(prefix + "/")
            for loc in locations
        )
        if not covered:
            missing.append(prefix)
    assert not missing, (
        f"nginx.conf に location が無い API prefix: {missing} — "
        "SPA フォールバックが index.html を 200 で返し、フロントの JSON パースが"
        "失敗します。frontend/nginx.conf に proxy_pass を追加してください "
        "(末尾スラッシュなしで呼ばれる場合は `location = <prefix>` も必要)。"
    )
