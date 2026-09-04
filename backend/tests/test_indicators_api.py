"""制度指標カタログ API（``routes/indicators.py``）— TestClient テスト。

対象: ``GET /api/indicators`` / ``GET /api/indicators/{indicator_id}``。
仕様の正本は ``docs/features/indicator_governance_design.md``（IG1）。
DB・ネットワークには接続しない（カタログは純宣言でルーターは DB を引かない）。

検証観点:
  1. 認証必須（未認証は 401/403）だが**ロールゲートは無い** — 学習者も定義を読める
  2. 値を1つも返さない（数値フィールドが存在しない）
  3. ``readable_by_me`` がロールに応じて変わる（値の宛先の投影）
  4. 未知の id は 404
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

try:
    import fastapi  # noqa: F401
    _HAS_FASTAPI = True
except ImportError:  # pragma: no cover
    _HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not _HAS_FASTAPI, reason="FastAPI not installed")

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_STUDENT = "cccccccc-cccc-cccc-cccc-cccccccccccc"
_TEACHER = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_ADMIN = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


@pytest.fixture
def env():
    from fastapi.testclient import TestClient
    from api.main import app
    from dependencies import (
        ROLE_STUDENT,
        ROLE_SYSTEM_ADMIN,
        ROLE_TEACHER,
        _create_token,
    )

    return {
        "client": TestClient(app),
        "tokens": {
            "student": _create_token(_STUDENT, "gakusei", "g@x", ROLE_STUDENT),
            "teacher": _create_token(_TEACHER, "kyoin", "k@x", ROLE_TEACHER),
            "admin": _create_token(_ADMIN, "kanri", "a@x", ROLE_SYSTEM_ADMIN),
        },
    }


def _auth(env, who):
    return {"Authorization": "Bearer " + env["tokens"][who]}


# ---------------------------------------------------------------------------
# 1. 認証（IG1: ロールゲートは掛けない）
# ---------------------------------------------------------------------------


class TestAuthentication:
    @pytest.mark.parametrize("path", ["/api/indicators", "/api/indicators/my-records"])
    def test_requires_authentication(self, env, path):
        response = env["client"].get(path)
        assert response.status_code in (401, 403)

    @pytest.mark.parametrize("who", ["student", "teacher", "admin"])
    def test_every_authenticated_role_can_read_the_catalog(self, env, who):
        response = env["client"].get("/api/indicators", headers=_auth(env, who))
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["indicators"]) >= 15

    def test_students_get_the_full_definitions_of_admin_only_indicators(self, env):
        """値は読めなくても**定義**は全文返る（IG1）。"""
        response = env["client"].get("/api/indicators", headers=_auth(env, "student"))
        items = {item["id"]: item for item in response.json()["indicators"]}
        admin_only = items["doubt-metrics"]
        assert admin_only["readable_by_me"] is False
        assert admin_only["definition"].strip()
        assert admin_only["purpose"].strip()
        assert admin_only["retention"].strip()


# ---------------------------------------------------------------------------
# 2. 応答の中身（値を返さない）
# ---------------------------------------------------------------------------


class TestPayload:
    def test_note_and_k_anonymity_are_returned(self, env):
        from core import privacy
        from core.indicator_catalog import CATALOG_NOTE

        body = env["client"].get("/api/indicators", headers=_auth(env, "teacher")).json()
        assert body["note"] == CATALOG_NOTE
        assert body["k_anonymity"] == privacy.K_ANONYMITY

    def test_items_carry_only_definitional_fields(self, env):
        from core.indicator_catalog import PUBLIC_VIEW_FIELDS

        body = env["client"].get("/api/indicators", headers=_auth(env, "teacher")).json()
        for item in body["indicators"]:
            assert set(item) == set(PUBLIC_VIEW_FIELDS) | {"readable_by_me"}

    def test_no_numeric_values_in_the_indicator_items(self, env):
        """IG1: カタログは値を持たない（真偽値以外の数値が1つも無い）。"""
        body = env["client"].get("/api/indicators", headers=_auth(env, "admin")).json()

        def _scan(node, path=""):
            if isinstance(node, bool):
                return []
            if isinstance(node, (int, float)):
                return [path]
            if isinstance(node, dict):
                out = []
                for k, v in node.items():
                    out += _scan(v, f"{path}.{k}")
                return out
            if isinstance(node, list):
                out = []
                for i, v in enumerate(node):
                    out += _scan(v, f"{path}[{i}]")
                return out
            return []

        assert _scan(body["indicators"]) == []

    def test_every_item_declares_the_four_non_uses(self, env):
        from core.indicator_catalog import NON_USES

        body = env["client"].get("/api/indicators", headers=_auth(env, "student")).json()
        for item in body["indicators"]:
            assert set(NON_USES) <= set(item["not_used_for"]), item["id"]


# ---------------------------------------------------------------------------
# 3. readable_by_me のロール別投影
# ---------------------------------------------------------------------------


class TestReadableByMe:
    @pytest.mark.parametrize(
        "who,expected",
        [
            # (learner_self, teacher, system_admin)
            ("student", (True, False, False)),
            ("teacher", (True, True, False)),
            ("admin", (True, True, True)),
        ],
    )
    def test_readability_per_role(self, env, who, expected):
        body = env["client"].get("/api/indicators", headers=_auth(env, who)).json()
        items = {item["id"]: item for item in body["indicators"]}
        assert items["my-records"]["readable_by_me"] is expected[0]
        assert items["interest-dashboard"]["readable_by_me"] is expected[1]
        assert items["llm-usage-metrics"]["readable_by_me"] is expected[2]

    def test_account_activity_is_admin_only(self, env):
        for who, expected in (("student", False), ("teacher", False), ("admin", True)):
            body = env["client"].get("/api/indicators", headers=_auth(env, who)).json()
            items = {item["id"]: item for item in body["indicators"]}
            assert items["account-activity"]["readable_by_me"] is expected, who


# ---------------------------------------------------------------------------
# 4. 単一取得
# ---------------------------------------------------------------------------


class TestDetail:
    def test_single_indicator(self, env):
        response = env["client"].get(
            "/api/indicators/interest-dashboard", headers=_auth(env, "teacher")
        )
        assert response.status_code == 200
        body = response.json()
        assert body["indicator"]["id"] == "interest-dashboard"
        assert body["indicator"]["readable_by_me"] is True
        assert body["indicator"]["k_anonymity"] is True
        assert body["note"]

    def test_unknown_indicator_is_404(self, env):
        response = env["client"].get(
            "/api/indicators/no-such-indicator", headers=_auth(env, "admin")
        )
        assert response.status_code == 404

    def test_detail_is_readable_by_a_student(self, env):
        response = env["client"].get(
            "/api/indicators/account-activity", headers=_auth(env, "student")
        )
        assert response.status_code == 200
        assert response.json()["indicator"]["readable_by_me"] is False


# ---------------------------------------------------------------------------
# 5. 書き込み経路が無い
# ---------------------------------------------------------------------------


class TestReadOnly:
    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    def test_no_write_methods(self, env, method):
        call = getattr(env["client"], method)
        response = call("/api/indicators", headers=_auth(env, "admin"))
        assert response.status_code in (404, 405)
