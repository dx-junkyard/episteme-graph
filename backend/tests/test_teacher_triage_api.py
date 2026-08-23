"""負荷順トリアージ API（教員支援 Phase 4 §2）のテスト。

対象:
- ``GET /api/admin/documents/{id}/element-explanations``（説明レビューキュー）の
  ``sort`` パラメータ — **既定は従来順・応答形も完全に不変**（TT1）、``load`` 指定時
  のみ再ソート + 段階ラベル付与 + 導出不能候補の末尾配置。
- ``GET /api/admin/reconstruction/items/review-queue``（R層 item 監査キュー）の同上。
- 確定操作（approve / dismiss / bulk / R層 PATCH）の監査 metadata への ``sort_order``
  追記（TT3。未指定は載せない — 偽装しない）。
- R層 PATCH の「status 変化が無いと記帳されない」既存分岐が保たれていること。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from fastapi.testclient import TestClient  # noqa: F401
    _HAS_FASTAPI = True
except Exception:  # pragma: no cover
    _HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not _HAS_FASTAPI, reason="FastAPI 未導入（full API は Docker 内で）")

_UID_TEACHER = "22222222-2222-2222-2222-222222222222"
DOC = "11111111-1111-1111-1111-111111111111"
CLAIM_UUID = "33333333-3333-3333-3333-333333333333"


def _headers(role: str = "TEACHER", sub: str = _UID_TEACHER):
    import jwt

    payload = {"sub": sub, "role": role, "username": "u1", "email": "u1@test.com"}
    return {"Authorization": "Bearer " + jwt.encode(payload, "test-secret-key", algorithm="HS256")}


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from api.main import app

    return TestClient(app)


class _NoopSession:
    def execute(self, *a, **k):  # pragma: no cover - 想定外の直接 SQL を検出
        raise AssertionError("unexpected direct SQL in this test")

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def _expl_row(element_type, element_id, *, row_id):
    return {
        "id": row_id,
        "document_id": DOC,
        "element_type": element_type,
        "element_id": element_id,
        "kind": "contextual",
        "role": None,
        "body": "text",
        "evidence": {"reason": "r", "confidence": 0.5},
        "status": "candidate",
        "created_by": "pipeline",
        "reviewed_by": None,
        "reviewed_at": None,
        "created_at": "2026-08-15T00:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# 説明レビューキュー: sort パラメータ
# ---------------------------------------------------------------------------


@pytest.fixture
def explanation_queue(monkeypatch):
    """GET 一覧のための最小パッチ束（DB なし）。rows は store 順を模す。"""
    import routes.element_explanations as ee_routes

    rows = [
        _expl_row("figure", "fig-1", row_id="e-figure"),
        _expl_row("equation", "eq_1", row_id="e-equation"),
        _expl_row("theory_claim", "claim_agent_1", row_id="e-claim"),
    ]
    monkeypatch.setattr(
        ee_routes, "_ensure_document_viewable", lambda doc, user: [{"document_id": DOC}]
    )
    monkeypatch.setattr(
        "core.element_explanations.list_for_document",
        lambda session, doc, **kw: [dict(r) for r in rows],
    )
    monkeypatch.setattr(ee_routes, "get_session", lambda: _NoopSession())
    monkeypatch.setattr(
        ee_routes, "_claim_id_lookup", lambda doc: {"claim_agent_1": CLAIM_UUID}
    )
    monkeypatch.setattr(ee_routes, "_component_id_lookup", lambda doc: {})
    monkeypatch.setattr(
        "core.teacher_triage.load_levels_for_targets",
        lambda session, targets: {
            ("claim", CLAIM_UUID): "highest",
            ("equation", "eq_1"): "low",
        },
    )
    return rows


class TestExplanationQueueSort:
    def test_default_sort_is_the_legacy_order_and_shape(self, client, explanation_queue):
        """TT1: 既定（sort 省略）は従来順・load 系キーも sort キーも付かない。"""
        r = client.get(
            f"/api/admin/documents/{DOC}/element-explanations", headers=_headers()
        )
        assert r.status_code == 200
        data = r.json()
        assert "sort" not in data
        assert [e["id"] for e in data["explanations"]] == ["e-figure", "e-equation", "e-claim"]
        for e in data["explanations"]:
            assert "load_level" not in e
            assert "load_level_label" not in e

    def test_sort_default_is_explicitly_accepted(self, client, explanation_queue):
        r = client.get(
            f"/api/admin/documents/{DOC}/element-explanations?sort=default",
            headers=_headers(),
        )
        assert r.status_code == 200
        assert "sort" not in r.json()

    def test_sort_load_reorders_by_load_level(self, client, explanation_queue):
        """load 指定時のみ再ソート: 最高位（claim）→ 低（equation）→ 導出不能（figure, 末尾）。"""
        r = client.get(
            f"/api/admin/documents/{DOC}/element-explanations?sort=load",
            headers=_headers(),
        )
        assert r.status_code == 200
        data = r.json()
        assert data["sort"] == "load"
        assert [e["id"] for e in data["explanations"]] == ["e-claim", "e-equation", "e-figure"]

    def test_sort_load_labels_are_graded_and_honest(self, client, explanation_queue):
        r = client.get(
            f"/api/admin/documents/{DOC}/element-explanations?sort=load",
            headers=_headers(),
        )
        by_id = {e["id"]: e for e in r.json()["explanations"]}
        assert by_id["e-claim"]["load_level_label"] == "最高位"
        assert by_id["e-equation"]["load_level_label"] == "低"
        # figure スコープは台帳対応が引けない → 正直な縮退ラベルで末尾。
        assert by_id["e-figure"]["load_level_label"] == "影響度を導出できない候補"
        assert by_id["e-figure"]["load_level"] == ""

    def test_sort_load_does_not_leak_raw_scores(self, client, explanation_queue):
        r = client.get(
            f"/api/admin/documents/{DOC}/element-explanations?sort=load",
            headers=_headers(),
        )
        body = r.text
        assert "load_score" not in body

    def test_invalid_sort_is_422(self, client, explanation_queue):
        r = client.get(
            f"/api/admin/documents/{DOC}/element-explanations?sort=bogus",
            headers=_headers(),
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# 説明レビューキュー: 確定操作の監査 metadata（sort_order, TT3）
# ---------------------------------------------------------------------------


@pytest.fixture
def approve_capture(monkeypatch):
    import routes.element_explanations as ee_routes

    existing = _expl_row("theory_claim", "claim_agent_1", row_id="e-claim")
    updated = dict(existing, status="approved", reviewed_by=_UID_TEACHER)
    events: list[dict] = []

    monkeypatch.setattr("core.element_explanations.get_by_id", lambda s, i: dict(existing))
    monkeypatch.setattr("core.element_explanations.approve", lambda s, i, u: dict(updated))
    monkeypatch.setattr("core.element_explanations.dismiss", lambda s, i, u: dict(updated, status="dismissed"))
    monkeypatch.setattr(ee_routes, "_ensure_document_editable", lambda doc, user: [{"document_id": DOC}])
    monkeypatch.setattr(ee_routes, "get_session", lambda: _NoopSession())

    def _capture(entity_type, entity_id, old, new, user_id, metadata):
        events.append({"entity_type": entity_type, "metadata": metadata})

    monkeypatch.setattr(ee_routes, "record_review_event", _capture)
    return events


class TestExplanationAuditSortOrder:
    def test_approve_records_declared_sort_order(self, client, approve_capture):
        r = client.post(
            "/api/admin/element-explanations/e-claim/approve?sort_order=load",
            headers=_headers(),
        )
        assert r.status_code == 200
        assert approve_capture[-1]["metadata"]["sort_order"] == "load"

    def test_approve_without_sort_order_does_not_fabricate_it(self, client, approve_capture):
        r = client.post(
            "/api/admin/element-explanations/e-claim/approve", headers=_headers()
        )
        assert r.status_code == 200
        assert "sort_order" not in approve_capture[-1]["metadata"]

    def test_dismiss_records_declared_sort_order(self, client, approve_capture):
        r = client.post(
            "/api/admin/element-explanations/e-claim/dismiss?sort_order=default",
            headers=_headers(),
        )
        assert r.status_code == 200
        assert approve_capture[-1]["metadata"]["sort_order"] == "default"

    def test_invalid_sort_order_is_422_and_not_recorded(self, client, approve_capture):
        r = client.post(
            "/api/admin/element-explanations/e-claim/approve?sort_order=bogus",
            headers=_headers(),
        )
        assert r.status_code == 422
        assert approve_capture == []

    def test_bulk_review_accepts_and_records_sort_order(self, client, monkeypatch, approve_capture):
        import routes.element_explanations as ee_routes

        row = _expl_row("theory_claim", "claim_agent_1", row_id="e-claim")
        monkeypatch.setattr(
            "core.element_explanations.bulk_transition",
            lambda session, doc, ids, *, new_status, user_id: {
                "updated": [dict(row, status=new_status)],
                "skipped": [],
            },
        )
        r = client.post(
            f"/api/admin/documents/{DOC}/element-explanations/bulk-review",
            headers=_headers(),
            json={"action": "approve", "explanation_ids": ["e-claim"], "sort_order": "load"},
        )
        assert r.status_code == 200
        assert approve_capture[-1]["metadata"]["sort_order"] == "load"
        assert approve_capture[-1]["metadata"]["bulk"] is True

    def test_bulk_review_rejects_invalid_sort_order(self, client, approve_capture):
        r = client.post(
            f"/api/admin/documents/{DOC}/element-explanations/bulk-review",
            headers=_headers(),
            json={"action": "approve", "explanation_ids": ["e-claim"], "sort_order": "bogus"},
        )
        assert r.status_code == 422
        assert approve_capture == []


# ---------------------------------------------------------------------------
# R層 review キュー: sort パラメータ
# ---------------------------------------------------------------------------


@pytest.fixture
def recon_queue(monkeypatch):
    import routes.reconstruction as recon_routes

    items = [
        {"item_id": "i-low", "claim_id": "c-low", "status": "auto", "rank_tier": "要確認（高）"},
        {"item_id": "i-none", "claim_id": "", "status": "auto", "rank_tier": "情報不足"},
        {"item_id": "i-high", "claim_id": "c-high", "status": "auto", "rank_tier": "要確認（低）"},
    ]
    monkeypatch.setattr(
        recon_routes,
        "get_review_queue",
        lambda document_id=None, document_ids=None: {
            "items": [dict(it) for it in items],
            "k_anonymity": 3,
        },
    )
    monkeypatch.setattr(recon_routes, "_pg_session", lambda: _NoopSession())
    monkeypatch.setattr(
        "core.teacher_triage.load_levels_for_targets",
        lambda session, targets: {
            ("claim", "c-low"): "low",
            ("claim", "c-high"): "highest",
        },
    )
    return items


class TestReconstructionQueueSort:
    URL = "/api/admin/reconstruction/items/review-queue"

    def test_default_sort_keeps_the_suspicion_order_and_shape(self, client, recon_queue):
        r = client.get(self.URL, headers=_headers())
        assert r.status_code == 200
        data = r.json()
        assert "sort" not in data
        assert [it["item_id"] for it in data["items"]] == ["i-low", "i-none", "i-high"]
        for it in data["items"]:
            assert "load_level" not in it

    def test_sort_load_reorders_and_puts_underivable_last(self, client, recon_queue):
        r = client.get(self.URL + "?sort=load", headers=_headers())
        assert r.status_code == 200
        data = r.json()
        assert data["sort"] == "load"
        assert [it["item_id"] for it in data["items"]] == ["i-high", "i-low", "i-none"]
        by_id = {it["item_id"]: it for it in data["items"]}
        assert by_id["i-high"]["load_level_label"] == "最高位"
        assert by_id["i-low"]["load_level_label"] == "低"
        assert by_id["i-none"]["load_level_label"] == "影響度を導出できない候補"

    def test_invalid_sort_is_422(self, client, recon_queue):
        assert client.get(self.URL + "?sort=bogus", headers=_headers()).status_code == 422

    def test_sort_load_does_not_leak_raw_scores(self, client, recon_queue):
        r = client.get(self.URL + "?sort=load", headers=_headers())
        assert "load_score" not in r.text


# ---------------------------------------------------------------------------
# R層 PATCH: 監査 metadata の sort_order + 「status 不変なら記帳しない」既存分岐
# ---------------------------------------------------------------------------


class FakeReconItemSession:
    def __init__(self, status="auto"):
        self._status = status

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        if sql.startswith("SELECT status, claim_id::text, document_id FROM reconstruction_items"):
            class _R:
                def fetchone(_self):
                    return (self._status, CLAIM_UUID, DOC)

            return _R()
        if sql.startswith("UPDATE reconstruction_items SET"):
            class _R:
                def fetchone(_self):
                    return None

            return _R()
        raise AssertionError(f"unhandled SQL: {sql!r}")

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture
def recon_patch_capture(monkeypatch):
    import routes.reconstruction as recon_routes

    events: list[dict] = []
    monkeypatch.setattr(recon_routes, "_pg_session", lambda: FakeReconItemSession())

    def _capture(entity_type, entity_id, old, new, user_id, metadata=None):
        events.append({
            "entity_type": entity_type,
            "old": old,
            "new": new,
            "metadata": metadata or {},
        })

    monkeypatch.setattr(recon_routes, "_record_recon_event", _capture)
    return events


class TestReconstructionPatchAudit:
    URL = "/api/admin/reconstruction/items/44444444-4444-4444-4444-444444444444"

    def test_status_transition_records_declared_sort_order(self, client, recon_patch_capture):
        r = client.patch(
            self.URL, headers=_headers(), json={"status": "confirmed", "sort_order": "load"}
        )
        assert r.status_code == 200
        assert len(recon_patch_capture) == 1
        assert recon_patch_capture[0]["metadata"]["sort_order"] == "load"

    def test_status_transition_without_sort_order_does_not_fabricate_it(
        self, client, recon_patch_capture
    ):
        r = client.patch(self.URL, headers=_headers(), json={"status": "confirmed"})
        assert r.status_code == 200
        assert "sort_order" not in recon_patch_capture[0]["metadata"]

    def test_no_status_change_still_skips_the_audit_event(self, client, recon_patch_capture):
        """既存分岐の保全: status 変化が無ければ sort_order があっても記帳しない。"""
        r = client.patch(
            self.URL, headers=_headers(), json={"prompt": "new prompt", "sort_order": "load"}
        )
        assert r.status_code == 200
        assert recon_patch_capture == []

    def test_invalid_sort_order_is_422(self, client, recon_patch_capture):
        r = client.patch(
            self.URL, headers=_headers(), json={"status": "confirmed", "sort_order": "bogus"}
        )
        assert r.status_code == 422
        assert recon_patch_capture == []


# ---------------------------------------------------------------------------
# ソース検査: 監査 metadata 追記の配線と既存分岐の保全
# ---------------------------------------------------------------------------


class TestSourceWiring:
    def test_explanation_routes_wire_sort_metadata_into_all_three_audit_paths(self):
        src = (BACKEND / "api" / "routes" / "element_explanations.py").read_text(encoding="utf-8")
        # approve / dismiss / bulk の3系統（edit は対象外でよい — 設計指示）。
        assert src.count("teacher_triage.sort_metadata(") >= 3

    def test_reconstruction_routes_wire_sort_metadata(self):
        src = (BACKEND / "api" / "routes" / "reconstruction.py").read_text(encoding="utf-8")
        assert "teacher_triage.sort_metadata(" in src

    def test_reconstruction_status_unchanged_branch_is_preserved(self):
        """「status 変化が無いと記帳されない」既存分岐（if new_status != old_status）を変えない。"""
        src = (BACKEND / "api" / "routes" / "reconstruction.py").read_text(encoding="utf-8")
        assert "if new_status != old_status:" in src

    def test_default_queue_paths_do_not_annotate(self):
        """既定（default）経路では load 付与関数を通らない（TT1 の構造的確認）。"""
        src = (BACKEND / "api" / "routes" / "element_explanations.py").read_text(encoding="utf-8")
        assert "if sort == teacher_triage.SORT_LOAD:" in src
        recon_src = (BACKEND / "api" / "routes" / "reconstruction.py").read_text(encoding="utf-8")
        assert "if sort == teacher_triage.SORT_LOAD:" in recon_src
