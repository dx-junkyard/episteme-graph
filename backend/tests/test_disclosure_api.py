"""可視性6軸カタログ API（``routes/disclosure.py``）— TestClient テスト。

対象: ``GET /api/disclosure`` / ``GET /api/disclosure/{data_kind}``。
仕様の正本は ``docs/features/disclosure_axes_design.md``（DA1〜DA6）。
DB・ネットワークには接続しない（カタログは純宣言でルーターは DB を引かない）。

検証観点:
  1. 認証必須（未認証は 401/403）だが**ロールゲートは無い** — 学習者も宣言を読める（DA3）
  2. 値を1つも返さない（数値フィールドが存在しない）
  3. 宛先は provider だけで、実行時の設定から解決される（DA2）
  4. 未知の data_kind は 404 / 書き込みメソッドは存在しない（DA5）
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
# 1. 認証（DA3: ロールゲートは掛けない）
# ---------------------------------------------------------------------------


class TestAuthentication:
    @pytest.mark.parametrize("path", ["/api/disclosure", "/api/disclosure/learning_chat"])
    def test_requires_authentication(self, env, path):
        response = env["client"].get(path)
        assert response.status_code in (401, 403)

    @pytest.mark.parametrize("who", ["student", "teacher", "admin"])
    def test_every_authenticated_role_reads_the_full_catalog(self, env, who):
        from core.disclosure_axes import all_data_kinds

        response = env["client"].get("/api/disclosure", headers=_auth(env, who))
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["data_kinds"]) == len(all_data_kinds())
        assert len(body["axes"]) == 6

    def test_students_read_the_teacher_facing_entries_too(self, env):
        """宣言は全当事者に開く（DA3）— 教員向けの種別も定義は全文返る。"""
        body = env["client"].get("/api/disclosure", headers=_auth(env, "student")).json()
        items = {item["data_kind"]: item for item in body["data_kinds"]}
        entry = items["teacher_reviews"]
        assert entry["axes"]["external_transfer"].strip()
        assert entry["learner_facing"] is False


# ---------------------------------------------------------------------------
# 2. 応答の中身（値を返さない）
# ---------------------------------------------------------------------------

_NUMERIC_KEY_HINTS = (
    "count", "total", "score", "weight", "confidence", "tokens", "cost", "price",
)


class TestPayload:
    def test_note_and_k_anonymity_are_returned(self, env):
        from core import privacy
        from core.disclosure_axes import DISCLOSURE_NOTE

        body = env["client"].get("/api/disclosure", headers=_auth(env, "student")).json()
        assert body["note"] == DISCLOSURE_NOTE
        assert body["k_anonymity"] == privacy.K_ANONYMITY

    def test_items_carry_only_declared_fields(self, env):
        from core.disclosure_axes import PUBLIC_VIEW_FIELDS

        body = env["client"].get("/api/disclosure", headers=_auth(env, "teacher")).json()
        for item in body["data_kinds"]:
            assert set(item) == set(PUBLIC_VIEW_FIELDS)

    def test_response_holds_no_numbers_besides_k_anonymity(self, env):
        """宣言だけ — 値らしいキー・数値を持たない（k の正本値だけが例外）。"""
        body = env["client"].get("/api/disclosure", headers=_auth(env, "student")).json()
        body.pop("k_anonymity", None)

        offenders: list[str] = []

        def walk(node, path):
            if isinstance(node, dict):
                for key, value in node.items():
                    if any(hint in str(key).lower() for hint in _NUMERIC_KEY_HINTS):
                        offenders.append(f"{path}.{key}")
                    walk(value, f"{path}.{key}")
            elif isinstance(node, list):
                for i, value in enumerate(node):
                    walk(value, f"{path}[{i}]")
            elif isinstance(node, (int, float)) and not isinstance(node, bool):
                offenders.append(f"{path}={node!r}")

        walk(body, "$")
        assert offenders == [], f"応答に値らしいものがあります: {offenders}"

    def test_axes_are_the_six_declared_axes(self, env):
        from core.disclosure_axes import AXIS_IDS

        body = env["client"].get("/api/disclosure", headers=_auth(env, "student")).json()
        assert [axis["id"] for axis in body["axes"]] == list(AXIS_IDS)
        for item in body["data_kinds"]:
            assert set(item["axes"]) == set(AXIS_IDS)

    def test_touchpoints_do_not_expose_models_or_costs(self, env):
        """DA2: 通過点が持つのは操作・送られるもの・feature キーだけ。"""
        body = env["client"].get("/api/disclosure", headers=_auth(env, "student")).json()
        for item in body["data_kinds"]:
            for touchpoint in item["ai_touchpoints"]:
                assert set(touchpoint) == {"operation", "sends", "feature"}


# ---------------------------------------------------------------------------
# 3. provider の解決（DA2）
# ---------------------------------------------------------------------------


class TestProviderResolution:
    def test_provider_is_resolved_and_injected_into_the_note(self, env):
        from core.config import get_settings
        from core.disclosure_axes import provider_label

        expected = provider_label(get_settings().llm_provider)
        body = env["client"].get("/api/disclosure", headers=_auth(env, "student")).json()
        assert body["provider"]["label"] == expected
        items = {item["data_kind"]: item for item in body["data_kinds"]}
        note = items["learning_chat"]["note"]
        assert expected in note
        assert "{provider}" not in note

    def test_entries_without_transfer_do_not_name_a_provider(self, env):
        body = env["client"].get("/api/disclosure", headers=_auth(env, "student")).json()
        items = {item["data_kind"]: item for item in body["data_kinds"]}
        account = items["account"]
        assert account["ai_touchpoints"] == []
        assert "送りません" in account["axes"]["external_transfer"]
        assert body["provider"]["label"] not in account["note"]


# ---------------------------------------------------------------------------
# 4. 1件取得・書き込み不在
# ---------------------------------------------------------------------------


class TestDetailAndReadOnly:
    def test_detail_returns_one_entry(self, env):
        response = env["client"].get(
            "/api/disclosure/learner_traces", headers=_auth(env, "student")
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["data_kind"]["data_kind"] == "learner_traces"
        assert len(body["axes"]) == 6

    def test_unknown_data_kind_is_404(self, env):
        response = env["client"].get(
            "/api/disclosure/nope", headers=_auth(env, "student")
        )
        assert response.status_code == 404

    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
    def test_write_methods_are_not_allowed(self, env, method):
        """DA5: 告知の層であって、承諾・設定を書き込む層ではない。"""
        response = env["client"].request(
            method, "/api/disclosure", headers=_auth(env, "admin")
        )
        assert response.status_code in (404, 405), response.text
