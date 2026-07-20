"""要素インベントリ（`core/deliberation/inventory.py` + `GET .../documents/{id}/elements`）
のテスト（設計書 `docs/features/element_inventory_design.md` §11）。

core 側は DB 非依存の純粋関数 ``build_inventory`` を fake rows（dict/tuple のリスト）で
直接検証する（``core/personal_graph/derive.py`` と同じテスト戦略。§8）。DB 読み出しの
配線自体（``build()``）は最小限の FakeSession で1本カバーする。API 側は
``test_deliberation_api.py`` と同型の TestClient + monkeypatch で権限ゲートの
fail-closed・contract 形状のみを検証する（実 DB・実 LLM なし）。
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

from core.deliberation import inventory  # noqa: E402
from core.deliberation.schema import (  # noqa: E402
    ELEMENT_EQUATION,
    ELEMENT_FIGURE,
    ELEMENT_THEORY_CLAIM,
    ELEMENT_THEORY_COMPONENT,
)

try:
    from fastapi.testclient import TestClient  # noqa: F401

    _HAS_FASTAPI = True
except Exception:  # pragma: no cover
    _HAS_FASTAPI = False

pytestmark_fastapi = pytest.mark.skipif(not _HAS_FASTAPI, reason="FastAPI 未導入")


# ---------------------------------------------------------------------------
# 型別カード整形の純粋部（label/snippet/badges/location 導出）
# ---------------------------------------------------------------------------


class TestTheoryComponentCard:
    def test_basic_fields(self):
        card = inventory._theory_component_card(
            {
                "id": "comp-1",
                "name": "ラグランジアン形式",
                "component_type": "theory",
                "status": "teacher_reviewed",
                "review_status": "teacher_approved",
                "summary": "作用原理から運動方程式を導く枠組み",
                "source_scope": {"document_id": "doc-1", "section_id": "s1"},
            }
        )
        assert card["element_type"] == ELEMENT_THEORY_COMPONENT
        assert card["element_id"] == "comp-1"
        assert card["label"] == "ラグランジアン形式"
        assert card["snippet"] == "作用原理から運動方程式を導く枠組み"
        assert card["badges"] == {
            "component_type": "theory",
            "status": "teacher_reviewed",
            "review_status": "teacher_approved",
        }
        assert card["location"] == {"document_id": "doc-1", "section_id": "s1"}

    def test_missing_name_falls_back_to_component(self):
        card = inventory._theory_component_card({"id": "comp-2"})
        assert card["label"] == "component"

    def test_non_dict_source_scope_becomes_empty_location(self):
        card = inventory._theory_component_card({"id": "comp-3", "source_scope": None})
        assert card["location"] == {}

    def test_summary_truncated_to_300_chars(self):
        long_summary = "あ" * 400
        card = inventory._theory_component_card({"id": "comp-4", "summary": long_summary})
        assert len(card["snippet"]) == 300


class TestTheoryClaimCard:
    def test_basic_fields(self):
        card = inventory._theory_claim_card(
            {
                "id": "claim-1",
                "chunk_id": "chunk-1",
                "claim_type": "result",
                "text": "運動量は保存される。",
                "support_status": "source_backed",
                "evidence_text": "実験値は誤差1%以内で一致した。",
                "review_status": "teacher_review_required",
            }
        )
        assert card["element_type"] == ELEMENT_THEORY_CLAIM
        assert card["label"] == "運動量は保存される。"
        assert card["snippet"] == "運動量は保存される。 実験値は誤差1%以内で一致した。"
        assert card["badges"] == {
            "claim_type": "result",
            "support_status": "source_backed",
            "review_status": "teacher_review_required",
        }
        assert card["location"] == {"chunk_id": "chunk-1"}

    def test_label_truncated_to_80_chars(self):
        long_text = "あ" * 200
        card = inventory._theory_claim_card({"id": "claim-2", "text": long_text})
        assert len(card["label"]) == 80

    def test_missing_text_falls_back_to_claim(self):
        card = inventory._theory_claim_card({"id": "claim-3", "text": ""})
        assert card["label"] == "claim"

    def test_no_evidence_text_snippet_is_text_only(self):
        card = inventory._theory_claim_card({"id": "claim-4", "text": "定義。", "evidence_text": ""})
        assert card["snippet"] == "定義。"

    def test_missing_chunk_id_is_none(self):
        card = inventory._theory_claim_card({"id": "claim-5", "text": "x"})
        assert card["location"] == {"chunk_id": None}


class TestEquationCard:
    def test_reads_flat_export_shape(self):
        # to_equations_export() のフラット形（トップレベルに equation_type 等）を主として読む。
        record = {
            "equation_id": "eq_1",
            "label": "eq. (1)",
            "plain_text": "F = m a",
            "latex": "F = ma",
            "source_location": {"page": 3, "section_id": "s2"},
            "equation_type": "definition",
            "semantic_status": "confirmed",
            "extraction_status": "extracted",
            "confidence": 0.92,  # I3: カードに持ち出してはいけない
        }
        card = inventory._equation_card(record)
        assert card["element_type"] == ELEMENT_EQUATION
        assert card["element_id"] == "eq_1"
        assert card["label"] == "eq. (1)"
        assert card["snippet"] == "F = m a"
        assert card["badges"] == {
            "equation_type": "definition",
            "semantic_status": "confirmed",
            "extraction_status": "extracted",
        }
        assert card["location"] == {"page": 3, "section_id": "s2"}
        assert "confidence" not in card

    def test_falls_back_to_nested_shape_when_flat_fields_missing(self):
        record = {
            "equation_id": "eq_2",
            "source_extraction": {
                "source_location": {"page": 5},
                "plain_text": "E = mc^2",
                "latex": "E=mc^2",
                "extraction_status": "extracted",
            },
            "reconstruction": {"plain_text": "E = mc^2", "latex": "E=mc^2"},
            "semantics": {"equation_type": "relation", "semantic_status": "confirmed"},
        }
        card = inventory._equation_card(record)
        assert card["snippet"] == "E = mc^2"
        assert card["badges"]["equation_type"] == "relation"
        assert card["badges"]["semantic_status"] == "confirmed"
        assert card["badges"]["extraction_status"] == "extracted"
        assert card["location"] == {"page": 5}
        assert "confidence" not in card

    def test_missing_label_falls_back_to_equation_id(self):
        card = inventory._equation_card({"equation_id": "eq_3"})
        assert card["label"] == "eq_3"

    def test_snippet_falls_back_to_latex_when_no_plain_text(self):
        card = inventory._equation_card({"equation_id": "eq_4", "latex": "x^2+y^2=1"})
        assert card["snippet"] == "x^2+y^2=1"

    def test_never_includes_confidence_key_even_if_present_at_any_level(self):
        record = {
            "equation_id": "eq_5",
            "plain_text": "x",
            "confidence": 0.5,
            "semantics": {"confidence": 0.7},
        }
        card = inventory._equation_card(record)
        assert "confidence" not in card
        assert set(card.keys()) == {"element_type", "element_id", "label", "snippet", "badges", "location"}


class TestFigureCard:
    def test_basic_fields(self):
        card = inventory._figure_card(
            {
                "id": "fig-1",
                "figure_label": "Figure 3",
                "figure_key": "fig_3",
                "caption_text": "実験装置の概略図",
                "extraction_method": "embedded",
                "page": 4,
                "status": "extracted",
            }
        )
        assert card["element_type"] == ELEMENT_FIGURE
        assert card["label"] == "Figure 3"
        assert card["snippet"] == "実験装置の概略図"
        assert card["badges"] == {"extraction_method": "embedded", "status": "extracted"}
        assert card["location"] == {"page": 4}

    def test_falls_back_to_figure_key_when_label_missing(self):
        card = inventory._figure_card({"id": "fig-2", "figure_key": "fig_9"})
        assert card["label"] == "fig_9"

    def test_falls_back_to_figure_literal_when_both_missing(self):
        card = inventory._figure_card({"id": "fig-3"})
        assert card["label"] == "figure"


# ---------------------------------------------------------------------------
# 検討状況の集約合流（§7）
# ---------------------------------------------------------------------------


class TestBuildDeliberationIndex:
    def test_merges_all_three_sources_for_same_key(self):
        index = inventory._build_deliberation_index(
            annotation_rows=[("theory_claim", "c1", "candidate", 2), ("theory_claim", "c1", "committed", 1)],
            session_rows=[("theory_claim", "c1", 3)],
            identity_rows=[("theory_claim", "c1", "confirmed", 1)],
        )
        entry = index[("theory_claim", "c1")]
        assert entry["annotations"] == {"candidate": 2, "committed": 1, "dismissed": 0}
        assert entry["sessions"] == 3
        assert entry["identity_links"] == {"candidate": 0, "confirmed": 1, "rejected": 0}

    def test_unknown_status_values_are_ignored_not_crashed(self):
        # element_annotations/element_identity_links の CHECK 制約外の値が万一来ても落ちない。
        index = inventory._build_deliberation_index(
            annotation_rows=[("figure", "f1", "some_future_status", 5)],
            session_rows=[],
            identity_rows=[],
        )
        assert index[("figure", "f1")]["annotations"] == {"candidate": 0, "committed": 0, "dismissed": 0}

    def test_no_rows_returns_empty_index(self):
        assert inventory._build_deliberation_index([], [], []) == {}


# ---------------------------------------------------------------------------
# build_inventory() の外形契約（§5）— 並び順・上限・truncated_types・deliberation 合流
# ---------------------------------------------------------------------------


def _component_row(i: int) -> dict:
    return {"id": f"comp-{i}", "name": f"component {i}", "summary": ""}


def _claim_row(i: int) -> dict:
    return {"id": f"claim-{i}", "text": f"claim {i}"}


def _equation_record(i: int) -> dict:
    return {"equation_id": f"eq-{i}", "plain_text": f"eq {i}"}


def _figure_row(i: int) -> dict:
    return {"id": f"fig-{i}", "figure_label": f"Figure {i}"}


class TestBuildInventoryContract:
    def test_response_shape_has_exactly_four_top_level_keys(self):
        result = inventory.build_inventory(
            "doc-1",
            component_rows=[],
            claim_rows=[],
            equation_records=[],
            figure_rows=[],
        )
        assert set(result.keys()) == {"document_id", "elements", "counts", "truncated_types"}
        assert result["document_id"] == "doc-1"
        assert result["elements"] == []
        assert result["truncated_types"] == []
        assert result["counts"] == {
            ELEMENT_THEORY_COMPONENT: 0,
            ELEMENT_THEORY_CLAIM: 0,
            ELEMENT_EQUATION: 0,
            ELEMENT_FIGURE: 0,
        }

    def test_elements_are_grouped_in_type_order_and_preserve_input_order(self):
        result = inventory.build_inventory(
            "doc-1",
            component_rows=[_component_row(2), _component_row(1)],
            claim_rows=[_claim_row(2), _claim_row(1)],
            equation_records=[_equation_record(2), _equation_record(1)],
            figure_rows=[_figure_row(2), _figure_row(1)],
        )
        types_in_order = [el["element_type"] for el in result["elements"]]
        assert types_in_order == (
            [ELEMENT_THEORY_COMPONENT] * 2
            + [ELEMENT_THEORY_CLAIM] * 2
            + [ELEMENT_EQUATION] * 2
            + [ELEMENT_FIGURE] * 2
        )
        # 型内は呼び出し側から渡された順（SQL ORDER BY 由来）をそのまま尊重する。
        component_ids = [el["element_id"] for el in result["elements"] if el["element_type"] == ELEMENT_THEORY_COMPONENT]
        assert component_ids == ["comp-2", "comp-1"]

    def test_counts_reflect_true_totals(self):
        result = inventory.build_inventory(
            "doc-1",
            component_rows=[_component_row(i) for i in range(3)],
            claim_rows=[_claim_row(i) for i in range(5)],
            equation_records=[],
            figure_rows=[],
        )
        assert result["counts"][ELEMENT_THEORY_COMPONENT] == 3
        assert result["counts"][ELEMENT_THEORY_CLAIM] == 5

    def test_type_over_limit_is_truncated_and_reported_honestly(self):
        component_rows = [_component_row(i) for i in range(inventory._INVENTORY_MAX_PER_TYPE + 7)]
        result = inventory.build_inventory(
            "doc-1",
            component_rows=component_rows,
            claim_rows=[],
            equation_records=[],
            figure_rows=[],
        )
        component_elements = [
            el for el in result["elements"] if el["element_type"] == ELEMENT_THEORY_COMPONENT
        ]
        assert len(component_elements) == inventory._INVENTORY_MAX_PER_TYPE
        # counts は黙って欠落させず本当の総数を報告する（I4）。
        assert result["counts"][ELEMENT_THEORY_COMPONENT] == inventory._INVENTORY_MAX_PER_TYPE + 7
        assert result["truncated_types"] == [ELEMENT_THEORY_COMPONENT]

    def test_types_within_limit_are_not_reported_as_truncated(self):
        result = inventory.build_inventory(
            "doc-1",
            component_rows=[_component_row(i) for i in range(inventory._INVENTORY_MAX_PER_TYPE)],
            claim_rows=[],
            equation_records=[],
            figure_rows=[],
        )
        assert result["truncated_types"] == []

    def test_deliberation_field_defaults_to_all_zero_when_no_match(self):
        result = inventory.build_inventory(
            "doc-1",
            component_rows=[_component_row(1)],
            claim_rows=[],
            equation_records=[],
            figure_rows=[],
        )
        card = result["elements"][0]
        assert card["deliberation"] == {
            "annotations": {"candidate": 0, "committed": 0, "dismissed": 0},
            "sessions": 0,
            "identity_links": {"candidate": 0, "confirmed": 0, "rejected": 0},
        }

    def test_deliberation_field_merges_matching_activity(self):
        result = inventory.build_inventory(
            "doc-1",
            component_rows=[{"id": "comp-1", "name": "c"}],
            claim_rows=[],
            equation_records=[],
            figure_rows=[],
            annotation_rows=[(ELEMENT_THEORY_COMPONENT, "comp-1", "committed", 1)],
            session_rows=[(ELEMENT_THEORY_COMPONENT, "comp-1", 2)],
            identity_rows=[(ELEMENT_THEORY_COMPONENT, "comp-1", "confirmed", 1)],
        )
        card = result["elements"][0]
        assert card["deliberation"]["annotations"]["committed"] == 1
        assert card["deliberation"]["sessions"] == 2
        assert card["deliberation"]["identity_links"]["confirmed"] == 1

    def test_equation_confidence_never_leaks_through_full_pipeline(self):
        result = inventory.build_inventory(
            "doc-1",
            component_rows=[],
            claim_rows=[],
            equation_records=[{"equation_id": "eq-1", "plain_text": "x", "confidence": 0.9}],
            figure_rows=[],
        )
        assert "confidence" not in result["elements"][0]


# ---------------------------------------------------------------------------
# build() の DB 配線（最小限の FakeSession で1本カバー）
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def fetchall(self):
        return self._rows


class _FakeSession:
    def __init__(self, queued_results):
        self._queue = list(queued_results)
        self.closed = False

    def execute(self, _stmt, _params=None):
        return _FakeResult(self._queue.pop(0))

    def close(self):
        self.closed = True


class TestBuildDbWiring:
    def test_build_wires_rows_through_to_the_same_shape_as_build_inventory(self, monkeypatch):
        queued = [
            [{"id": "comp-1", "name": "component A", "summary": "s"}],  # component_rows
            [{"id": "claim-1", "text": "claim A"}],  # claim_rows
            [{"id": "fig-1", "figure_label": "Figure 1"}],  # figure_rows
            [],  # annotation_rows
            [],  # session_rows
            [],  # identity_rows
        ]
        fake_session = _FakeSession(queued)
        monkeypatch.setattr(inventory, "get_session", lambda: fake_session)
        monkeypatch.setattr(inventory.refs_mod, "equation_records", lambda _doc: [])

        result = inventory.build("doc-1")

        assert fake_session.closed is True
        assert result["document_id"] == "doc-1"
        assert result["counts"] == {
            ELEMENT_THEORY_COMPONENT: 1,
            ELEMENT_THEORY_CLAIM: 1,
            ELEMENT_EQUATION: 0,
            ELEMENT_FIGURE: 1,
        }
        assert [el["element_type"] for el in result["elements"]] == [
            ELEMENT_THEORY_COMPONENT,
            ELEMENT_THEORY_CLAIM,
            ELEMENT_FIGURE,
        ]

    def test_build_closes_session_even_when_equation_records_lookup_raises(self, monkeypatch):
        # get_session().close() は equation_records() 呼び出し前に既に走っているため、
        # equation_records 側の例外は build() 自体の session close を妨げない
        # （finally ブロックの外で呼ばれるため）。ここでは単純に例外がそのまま伝播することだけ
        # 確認する（fail-soft はここでは要求しない。既存 refs.equation_records は
        # best-effort 実装で例外を出さない想定のため、失敗系の握り潰しは inventory.py の
        # 責務ではない）。
        queued = [[], [], [], [], [], []]
        fake_session = _FakeSession(queued)
        monkeypatch.setattr(inventory, "get_session", lambda: fake_session)

        def _raise(_doc):
            raise RuntimeError("boom")

        monkeypatch.setattr(inventory.refs_mod, "equation_records", _raise)

        with pytest.raises(RuntimeError):
            inventory.build("doc-1")
        assert fake_session.closed is True


# ---------------------------------------------------------------------------
# API: GET /api/admin/deliberation/documents/{document_id}/elements（設計書 §5/§11）
# ---------------------------------------------------------------------------


@pytest.fixture
def client_and_tokens():
    from fastapi.testclient import TestClient
    from api.main import app
    from dependencies import _create_token, ROLE_STUDENT, ROLE_TEACHER

    client = TestClient(app)
    student = _create_token("11111111-1111-1111-1111-111111111111", "stu", "stu@x", ROLE_STUDENT)
    teacher = _create_token("22222222-2222-2222-2222-222222222222", "tea", "tea@x", ROLE_TEACHER)
    return client, student, teacher


def _auth(tok):
    return {"Authorization": "Bearer " + tok}


class _FakeAccess:
    def __init__(self, *, found: bool, can_view: bool, document_id: str = "doc-1"):
        self._found = found
        self.can_view = can_view
        self.document_id = document_id if found else None

    @property
    def found(self) -> bool:
        return self._found


@pytestmark_fastapi
class TestGetDocumentElementInventoryApi:
    def test_unauthenticated_request_is_rejected(self, client_and_tokens):
        client, _s, _t = client_and_tokens
        response = client.get("/api/admin/deliberation/documents/doc-1/elements")
        assert response.status_code in (401, 403)

    def test_student_is_rejected(self, client_and_tokens):
        client, student, _t = client_and_tokens
        response = client.get(
            "/api/admin/deliberation/documents/doc-1/elements", headers=_auth(student)
        )
        assert response.status_code == 403

    def test_document_not_found_returns_404(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        import routes.deliberation as route_mod

        monkeypatch.setattr(
            route_mod, "resolve_document_access", lambda *a, **k: _FakeAccess(found=False, can_view=False)
        )
        response = client.get(
            "/api/admin/deliberation/documents/doc-missing/elements", headers=_auth(teacher)
        )
        assert response.status_code == 404

    def test_non_viewable_document_returns_404_fail_closed(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        import routes.deliberation as route_mod

        monkeypatch.setattr(
            route_mod, "resolve_document_access",
            lambda *a, **k: _FakeAccess(found=True, can_view=False, document_id="doc-1"),
        )
        called = {"build": False}
        monkeypatch.setattr(
            route_mod.inventory, "build", lambda *a, **k: called.__setitem__("build", True) or {}
        )
        response = client.get(
            "/api/admin/deliberation/documents/doc-1/elements", headers=_auth(teacher)
        )
        assert response.status_code == 404
        # fail-closed: 権限が無ければ core.build() 自体を呼ばない。
        assert called["build"] is False

    def test_viewable_document_returns_200_with_contract_shape(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        import routes.deliberation as route_mod

        monkeypatch.setattr(
            route_mod, "resolve_document_access",
            lambda *a, **k: _FakeAccess(found=True, can_view=True, document_id="doc-1"),
        )
        seen = {}

        def _fake_build(document_id):
            seen["document_id"] = document_id
            return {
                "document_id": document_id,
                "elements": [],
                "counts": {
                    ELEMENT_THEORY_COMPONENT: 0,
                    ELEMENT_THEORY_CLAIM: 0,
                    ELEMENT_EQUATION: 0,
                    ELEMENT_FIGURE: 0,
                },
                "truncated_types": [],
            }

        monkeypatch.setattr(route_mod.inventory, "build", _fake_build)

        response = client.get(
            "/api/admin/deliberation/documents/doc-1/elements", headers=_auth(teacher)
        )
        assert response.status_code == 200
        body = response.json()
        assert set(body.keys()) == {"document_id", "elements", "counts", "truncated_types"}
        # 権限判定で正規化済みの document_id が core に渡される（原稿スタジオが material_id を
        # そのまま渡してくる経路のため、正規化前の値ではなく access.document_id を使うこと）。
        assert seen["document_id"] == "doc-1"

    def test_run_less_document_still_returns_200_with_zero_equation_count(self, client_and_tokens, monkeypatch):
        # run/artifact が無い document でも equation が0件になるだけで 200（設計書 §5）。
        client, _s, teacher = client_and_tokens
        import routes.deliberation as route_mod

        monkeypatch.setattr(
            route_mod, "resolve_document_access",
            lambda *a, **k: _FakeAccess(found=True, can_view=True, document_id="doc-no-run"),
        )
        monkeypatch.setattr(
            route_mod.inventory, "build",
            lambda document_id: inventory.build_inventory(
                document_id,
                component_rows=[{"id": "comp-1", "name": "c"}],
                claim_rows=[],
                equation_records=[],  # run/artifact 無し
                figure_rows=[],
            ),
        )
        response = client.get(
            "/api/admin/deliberation/documents/doc-no-run/elements", headers=_auth(teacher)
        )
        assert response.status_code == 200
        body = response.json()
        assert body["counts"][ELEMENT_EQUATION] == 0
        assert body["counts"][ELEMENT_THEORY_COMPONENT] == 1
