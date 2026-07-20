"""出典タブの D層検証状態併記（N15）を claim にも拡張するための、学習者向け
chunk→claim ID 解決 API のテスト。

対象:
  - ``backend/api/services.py::get_chunk_claim_refs``
    （チャンクが当該コースの sources 教材に属するかを検証し、属する場合のみ
    theory_claims の最小フィールド id/claim_type/label を返す。属さない・
    チャンク不在・chunk_id 不正は None＝fail-closed）
  - ``backend/api/routes/learning.py::get_chunk_claim_refs_route``
    （``GET /api/learning/courses/{course_id}/chunks/{chunk_id}/claim-refs``。
    コースアクセスゲート → チャンク所属ゲートの2段 fail-closed）

DB は test_topic_check_completion.py / test_prerequisite_routing.py と同型の
フェイクセッション（``services._pg_session`` を monkeypatch）で代替する。
"""

from __future__ import annotations

import os
import sys

# learning.py 内部は `from dependencies import ...` 等の裸 import に依存するため、
# backend/api を sys.path に載せる（test_topic_check_completion.py と同型）。
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

import pytest


# ---------------------------------------------------------------------------
# フェイクセッション
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows if rows is not None else ([row] if row is not None else [])

    def fetchone(self):
        return self._row

    def fetchall(self):
        return list(self._rows)


class _FakeChunkClaimSession:
    """chunks.material_id 解決 + theory_claims 一覧を SQL 部分一致でエミュレートする。"""

    def __init__(self, chunk_material_id=None, chunk_missing=False,
                 claim_rows=None, raise_on_chunk_lookup=False):
        self.chunk_material_id = chunk_material_id
        self.chunk_missing = chunk_missing
        self.claim_rows = claim_rows or []
        self.raise_on_chunk_lookup = raise_on_chunk_lookup
        self.closed = False
        self.calls: list[str] = []

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.calls.append(sql)
        if "FROM chunks" in sql:
            if self.raise_on_chunk_lookup:
                raise ValueError("invalid input syntax for type uuid")
            if self.chunk_missing:
                return _Result(row=None)
            return _Result(row=(self.chunk_material_id,))
        if "FROM theory_claims" in sql:
            return _Result(rows=self.claim_rows)
        return _Result(row=None)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        self.closed = True


def _course_data(material_ids):
    return {"sources": [{"material_id": mid} for mid in material_ids]}


# ===========================================================================
# services.get_chunk_claim_refs
# ===========================================================================


class TestGetChunkClaimRefsService:
    def test_returns_claims_when_chunk_belongs_to_course_material(self, monkeypatch):
        from api import services

        fake = _FakeChunkClaimSession(
            chunk_material_id="mat-1",
            claim_rows=[
                ("11111111-1111-1111-1111-111111111111", "diagnostic_claim",
                 "text body", "normalized body"),
            ],
        )
        monkeypatch.setattr(services, "_pg_session", lambda: fake)

        result = services.get_chunk_claim_refs(_course_data(["mat-1"]), "chunk-1")

        assert result == [{
            "id": "11111111-1111-1111-1111-111111111111",
            "claim_type": "diagnostic_claim",
            "label": "normalized body",
        }]
        assert fake.closed is True

    def test_prefers_normalized_text_over_text_for_label(self, monkeypatch):
        from api import services

        fake = _FakeChunkClaimSession(
            chunk_material_id="mat-1",
            claim_rows=[("id-1", "definitional_claim", "raw text", "normalized text")],
        )
        monkeypatch.setattr(services, "_pg_session", lambda: fake)

        result = services.get_chunk_claim_refs(_course_data(["mat-1"]), "chunk-1")
        assert result[0]["label"] == "normalized text"

    def test_falls_back_to_text_when_normalized_text_empty(self, monkeypatch):
        from api import services

        fake = _FakeChunkClaimSession(
            chunk_material_id="mat-1",
            claim_rows=[("id-1", "definitional_claim", "raw text only", "")],
        )
        monkeypatch.setattr(services, "_pg_session", lambda: fake)

        result = services.get_chunk_claim_refs(_course_data(["mat-1"]), "chunk-1")
        assert result[0]["label"] == "raw text only"

    def test_long_label_is_truncated_with_ellipsis(self, monkeypatch):
        from api import services

        long_text = "あ" * 90
        fake = _FakeChunkClaimSession(
            chunk_material_id="mat-1",
            claim_rows=[("id-1", "diagnostic_claim", long_text, "")],
        )
        monkeypatch.setattr(services, "_pg_session", lambda: fake)

        result = services.get_chunk_claim_refs(_course_data(["mat-1"]), "chunk-1")
        label = result[0]["label"]
        assert label.endswith("…")
        assert len(label) == 61  # 60文字 + 省略記号

    def test_returns_empty_list_when_chunk_belongs_but_has_no_claims(self, monkeypatch):
        from api import services

        fake = _FakeChunkClaimSession(chunk_material_id="mat-1", claim_rows=[])
        monkeypatch.setattr(services, "_pg_session", lambda: fake)

        result = services.get_chunk_claim_refs(_course_data(["mat-1"]), "chunk-1")
        assert result == []

    def test_returns_none_when_chunk_material_not_in_course(self, monkeypatch):
        """チャンクは実在するが、別コースの教材に属する → fail-closed で None。"""
        from api import services

        fake = _FakeChunkClaimSession(
            chunk_material_id="other-material",
            claim_rows=[("id-1", "diagnostic_claim", "text", "")],
        )
        monkeypatch.setattr(services, "_pg_session", lambda: fake)

        result = services.get_chunk_claim_refs(_course_data(["mat-1"]), "chunk-1")
        assert result is None
        # コース外チャンクと判定した時点で theory_claims へは問い合わせない。
        assert not any("theory_claims" in c for c in fake.calls)

    def test_returns_none_when_chunk_not_found(self, monkeypatch):
        from api import services

        fake = _FakeChunkClaimSession(chunk_missing=True)
        monkeypatch.setattr(services, "_pg_session", lambda: fake)

        result = services.get_chunk_claim_refs(_course_data(["mat-1"]), "chunk-missing")
        assert result is None

    def test_returns_none_on_invalid_chunk_id(self, monkeypatch):
        from api import services

        fake = _FakeChunkClaimSession(raise_on_chunk_lookup=True)
        monkeypatch.setattr(services, "_pg_session", lambda: fake)

        result = services.get_chunk_claim_refs(_course_data(["mat-1"]), "not-a-uuid")
        assert result is None
        assert fake.closed is True

    def test_no_numeric_confidence_in_output(self, monkeypatch):
        """support_status/confidence 等の数値・審査情報を漏らさない（最小フィールドのみ）。"""
        from api import services

        fake = _FakeChunkClaimSession(
            chunk_material_id="mat-1",
            claim_rows=[("id-1", "diagnostic_claim", "text", "norm")],
        )
        monkeypatch.setattr(services, "_pg_session", lambda: fake)

        result = services.get_chunk_claim_refs(_course_data(["mat-1"]), "chunk-1")
        assert set(result[0].keys()) == {"id", "claim_type", "label"}


# ===========================================================================
# routes.learning.get_chunk_claim_refs_route
# ===========================================================================


class TestGetChunkClaimRefsRoute:
    def test_404_when_course_not_accessible(self, monkeypatch):
        from fastapi import HTTPException
        from api.routes import learning as learning_module

        monkeypatch.setattr(learning_module, "get_course_data", lambda user_id, course_id: None)

        with pytest.raises(HTTPException) as exc_info:
            learning_module.get_chunk_claim_refs_route(
                "course-1", "chunk-1", current_user={"id": "user-1"},
            )
        assert exc_info.value.status_code == 404

    def test_404_when_chunk_not_in_course(self, monkeypatch):
        """コースにはアクセス可だが、チャンクがそのコースの教材に属さない → 404。"""
        from fastapi import HTTPException
        from api.routes import learning as learning_module

        monkeypatch.setattr(
            learning_module, "get_course_data",
            lambda user_id, course_id: {"sources": [{"material_id": "mat-1"}]},
        )
        monkeypatch.setattr(
            learning_module, "get_chunk_claim_refs",
            lambda course_data, chunk_id: None,
        )

        with pytest.raises(HTTPException) as exc_info:
            learning_module.get_chunk_claim_refs_route(
                "course-1", "chunk-1", current_user={"id": "user-1"},
            )
        assert exc_info.value.status_code == 404

    def test_returns_claims_list_when_accessible_and_in_course(self, monkeypatch):
        from api.routes import learning as learning_module

        monkeypatch.setattr(
            learning_module, "get_course_data",
            lambda user_id, course_id: {"sources": [{"material_id": "mat-1"}]},
        )
        monkeypatch.setattr(
            learning_module, "get_chunk_claim_refs",
            lambda course_data, chunk_id: [
                {"id": "id-1", "claim_type": "diagnostic_claim", "label": "短い要約"},
            ],
        )

        result = learning_module.get_chunk_claim_refs_route(
            "course-1", "chunk-1", current_user={"id": "user-1"},
        )
        assert result == {"claims": [
            {"id": "id-1", "claim_type": "diagnostic_claim", "label": "短い要約"},
        ]}

    def test_returns_empty_claims_list_without_error(self, monkeypatch):
        """claim が0件でも 200 + 空配列（404 にしない）。"""
        from api.routes import learning as learning_module

        monkeypatch.setattr(
            learning_module, "get_course_data",
            lambda user_id, course_id: {"sources": [{"material_id": "mat-1"}]},
        )
        monkeypatch.setattr(
            learning_module, "get_chunk_claim_refs",
            lambda course_data, chunk_id: [],
        )

        result = learning_module.get_chunk_claim_refs_route(
            "course-1", "chunk-1", current_user={"id": "user-1"},
        )
        assert result == {"claims": []}
