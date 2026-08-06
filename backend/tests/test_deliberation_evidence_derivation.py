"""W層の `evidence` / `derivation` 第一級要素化（設計書 §16 / migration 064）のテスト。

正本: `docs/features/element_deliberation_workspace_design.md` §16
（実施決定は `docs/architecture/admin_ux_issues_2026-08-01.md` §3.4(d) / §3.3 Phase 5）。

検証範囲:
- `refs.py` の解決（artifact 由来・document_id 必須・not_found）。
- `context_lens.py` の focus 投影（evidence / derivation の DTO 形・fail-soft）と、
  既存 ITEM の navigable 化（evidence_id / derivation_id が入ること）。
- 学習者投影（`core/element_context.py`）が両型の `navigable` を**強制 false** にすること。
- `decomposition.py` の最小内訳（確度の生値を載せない・W8）。
- overview が 500 にならず、面②は空レーンへ縮退すること。
- 共通部品化（identity link / standardization）と commit の 422（v1 の「できないこと」）。
- migration 064 の CHECK 語彙拡張と、048/056 の CHECK を触っていないこと。

DB / LLM には触らない: artifact 読み出しは monkeypatch、権限ゲートは差し替える。
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

from core.deliberation import annotations as delib_annotations  # noqa: E402
from core.deliberation import context_lens, decomposition, dialogue, identity_links, refs  # noqa: E402
from core.deliberation.schema import (  # noqa: E402
    CONTEXT_STATUS_SOURCE_BACKED,
    DOCUMENT_ELEMENT_TYPES,
    DOCUMENT_ID_REQUIRED_ELEMENT_TYPES,
    ELEMENT_DERIVATION,
    ELEMENT_EVIDENCE,
    IDENTITY_LINKABLE_ELEMENT_TYPES,
    SCOPE_DOCUMENT,
    ElementRef,
    ElementResolutionError,
    scope_for_element_type,
)

try:
    from fastapi.testclient import TestClient  # noqa: F401
    _HAS_FASTAPI = True
except Exception:  # pragma: no cover
    _HAS_FASTAPI = False

_skip_no_fastapi = pytest.mark.skipif(not _HAS_FASTAPI, reason="FastAPI 未導入")

_DOC = "doc-1"
_EVIDENCE_ID = "ev_0001"
_DERIVATION_ID = "deriv_0001"
_CLAIM_UUID = "cccccccc-0000-0000-0000-000000000001"


def _artifacts() -> dict:
    """§16 の解決・投影に必要な最小 artifact セット（実 agent 出力と同じキー構成）。"""
    return {
        "evidence_registry": {
            "records": [
                {
                    "evidence_id": _EVIDENCE_ID,
                    "document_id": _DOC,
                    "evidence_text": "The observed shift is proportional to the applied field.",
                    "evidence_role": "source_quote",
                    "source": {
                        "page": 4,
                        "section_id": "sec_2",
                        "block_id": "block_17",
                        "span_start": 0,
                        "span_end": 58,
                    },
                }
            ]
        },
        "claim_object_builder": {
            "claims": [
                {
                    "claim_id": "claim_span_007",
                    "text": "The shift scales linearly with the field.",
                    "source_evidence_ids": [_EVIDENCE_ID],
                }
            ]
        },
        "derivation_chain": {
            "chains": [
                {
                    "derivation_id": _DERIVATION_ID,
                    "document_id": _DOC,
                    "chain_type": "equation_chain",
                    "operation": "solve_linear_system",
                    "teaching_takeaway": "線形系を解いて観測量を得る",
                    "source_section_ids": ["sec_2"],
                    "input_equation_ids": ["eq_2_1"],
                    "output_equation_ids": ["eq_2_4"],
                    "output_claim_ids": ["claim_span_007"],
                    "assumption_ids": ["claim_span_001"],
                    "source_evidence_ids": [_EVIDENCE_ID],
                    "linked_component_ids": [],
                    "steps": [
                        {
                            "step_id": "step_1",
                            "operation": "substitute",
                            "input_equation_ids": ["eq_2_1"],
                            "output_equation_ids": ["eq_2_2"],
                            "review_status": "auto_accepted",
                            "confidence": 0.9,
                        }
                    ],
                }
            ]
        },
        "document_structure": {
            "sections": [{"section_id": "sec_2", "title": "Theory"}],
            "blocks": [{"block_id": "block_17", "section_id": "sec_2", "page": 4}],
        },
        "equation_semantics": {
            "equations": [
                {"equation_id": "eq_2_1", "reconstruction": {"plain_text": "A x = b"}},
                {"equation_id": "eq_2_4", "reconstruction": {"plain_text": "x = A^-1 b"}},
            ]
        },
    }


# ---------------------------------------------------------------------------
# 語彙（schema.py）
# ---------------------------------------------------------------------------


class TestVocabulary:
    def test_both_types_are_document_scoped(self):
        assert ELEMENT_EVIDENCE in DOCUMENT_ELEMENT_TYPES
        assert ELEMENT_DERIVATION in DOCUMENT_ELEMENT_TYPES
        assert scope_for_element_type(ELEMENT_EVIDENCE) == SCOPE_DOCUMENT
        assert scope_for_element_type(ELEMENT_DERIVATION) == SCOPE_DOCUMENT

    def test_both_types_require_document_id(self):
        assert ELEMENT_EVIDENCE in DOCUMENT_ID_REQUIRED_ELEMENT_TYPES
        assert ELEMENT_DERIVATION in DOCUMENT_ID_REQUIRED_ELEMENT_TYPES

    def test_neither_type_is_identity_linkable(self):
        # §16「できないこと」: 共通部品化（同一性リンク / 標準化判定）の対象外。
        assert ELEMENT_EVIDENCE not in IDENTITY_LINKABLE_ELEMENT_TYPES
        assert ELEMENT_DERIVATION not in IDENTITY_LINKABLE_ELEMENT_TYPES

    def test_identity_linkable_matches_migration_048_check(self):
        """コード側語彙と migration 048 の CHECK 集合が一致すること（二重管理の防止）。"""
        sql = (BACKEND / "db" / "048_element_identity_links.sql").read_text(encoding="utf-8")
        for element_type in IDENTITY_LINKABLE_ELEMENT_TYPES:
            assert f"'{element_type}'" in sql, element_type
        assert "'evidence'" not in sql
        assert "'derivation'" not in sql


# ---------------------------------------------------------------------------
# refs.py の解決
# ---------------------------------------------------------------------------


class TestRefsResolution:
    def test_evidence_resolves_from_artifact(self, monkeypatch):
        monkeypatch.setattr(refs, "document_run_artifacts", lambda doc: _artifacts())
        ref = refs.resolve(ELEMENT_EVIDENCE, _EVIDENCE_ID, document_id=_DOC)
        assert ref.scope == SCOPE_DOCUMENT
        assert ref.element_type == ELEMENT_EVIDENCE
        assert ref.element_id == _EVIDENCE_ID
        assert ref.document_id == _DOC
        assert ref.provenance["block_id"] == "block_17"

    def test_derivation_resolves_from_artifact(self, monkeypatch):
        monkeypatch.setattr(refs, "document_run_artifacts", lambda doc: _artifacts())
        ref = refs.resolve(ELEMENT_DERIVATION, _DERIVATION_ID, document_id=_DOC)
        assert ref.element_type == ELEMENT_DERIVATION
        assert ref.element_id == _DERIVATION_ID
        assert ref.document_id == _DOC
        assert ref.provenance["chain_type"] == "equation_chain"

    @pytest.mark.parametrize("element_type", [ELEMENT_EVIDENCE, ELEMENT_DERIVATION])
    def test_document_id_is_required(self, element_type, monkeypatch):
        def boom(_doc):
            raise AssertionError("must not read artifacts without a document scope")

        monkeypatch.setattr(refs, "document_run_artifacts", boom)
        with pytest.raises(ElementResolutionError) as excinfo:
            refs.resolve(element_type, "whatever", document_id=None)
        assert excinfo.value.kind == "not_found"

    @pytest.mark.parametrize(
        "element_type,element_id",
        [(ELEMENT_EVIDENCE, "ev_9999"), (ELEMENT_DERIVATION, "deriv_9999")],
    )
    def test_unknown_id_is_not_found(self, element_type, element_id, monkeypatch):
        monkeypatch.setattr(refs, "document_run_artifacts", lambda doc: _artifacts())
        with pytest.raises(ElementResolutionError) as excinfo:
            refs.resolve(element_type, element_id, document_id=_DOC)
        assert excinfo.value.kind == "not_found"

    @pytest.mark.parametrize("element_type", [ELEMENT_EVIDENCE, ELEMENT_DERIVATION])
    def test_missing_artifact_is_not_found(self, element_type, monkeypatch):
        # 旧 run（該当 artifact が無い）は素直に not_found（推測で作らない）。
        monkeypatch.setattr(refs, "document_run_artifacts", lambda doc: {})
        with pytest.raises(ElementResolutionError):
            refs.resolve(element_type, _EVIDENCE_ID, document_id=_DOC)

    def test_agent_id_path_delegates_for_both_types(self, monkeypatch):
        # legacy_ids 変換の対象ではない（artifact 内 ID が正準 ID）。
        monkeypatch.setattr(refs, "document_run_artifacts", lambda doc: _artifacts())
        for element_type, element_id in (
            (ELEMENT_EVIDENCE, _EVIDENCE_ID),
            (ELEMENT_DERIVATION, _DERIVATION_ID),
        ):
            ref = refs.resolve_with_agent_id(element_type, element_id, document_id=_DOC)
            assert ref.element_id == element_id

    def test_record_helpers_tolerate_broken_artifacts(self):
        assert refs.evidence_records("", artifacts={}) == []
        assert refs.derivation_records("", artifacts={"derivation_chain": "nope"}) == []
        assert refs.evidence_records("", artifacts={"evidence_registry": {"records": "nope"}}) == []


# ---------------------------------------------------------------------------
# context_lens.py の focus 投影
# ---------------------------------------------------------------------------


@pytest.fixture
def lens_artifacts(monkeypatch):
    """context_lens の DB 読みを潰し artifact だけで投影させる（DB 非依存）。"""
    monkeypatch.setattr(refs, "document_run_artifacts", lambda doc: _artifacts())
    monkeypatch.setattr(context_lens, "_claim_id_lookup", lambda doc: {"claim_span_007": _CLAIM_UUID})
    monkeypatch.setattr(context_lens, "_component_id_lookup", lambda doc: {})
    monkeypatch.setattr(
        context_lens, "_claims_by_id",
        lambda ids: {_CLAIM_UUID: {"id": _CLAIM_UUID, "text": "The shift scales linearly."}},
    )
    monkeypatch.setattr(context_lens, "_components_by_id", lambda ids: {})
    monkeypatch.setattr(context_lens, "_load_component_graph", lambda doc: {"nodes": [], "edges": []})
    monkeypatch.setattr(context_lens, "_annotations_for", lambda *a, **k: [])
    monkeypatch.setattr(context_lens, "_generic_block_for_focus", lambda ref: None)
    return _artifacts()


def _ref(element_type: str, element_id: str) -> ElementRef:
    return ElementRef(
        scope=SCOPE_DOCUMENT, element_type=element_type, element_id=element_id, document_id=_DOC
    )


class TestContextLensEvidenceFocus:
    def test_focus_contract(self, lens_artifacts):
        result = context_lens.build(_ref(ELEMENT_EVIDENCE, _EVIDENCE_ID))
        assert set(result) == {"focus", "upper", "lower", "notes"}
        focus = result["focus"]
        assert focus["element_type"] == ELEMENT_EVIDENCE
        assert focus["element_id"] == _EVIDENCE_ID
        assert focus["document_id"] == _DOC
        # label = 引用の冒頭 / intrinsic_summary = 引用全文。
        assert focus["label"].startswith("The observed shift")
        assert focus["intrinsic_summary"].endswith("applied field.")
        assert f"evidence_registry:{_EVIDENCE_ID}" in focus["provenance"]

    def test_upper_has_dependent_claim_derivation_and_section(self, lens_artifacts):
        result = context_lens.build(_ref(ELEMENT_EVIDENCE, _EVIDENCE_ID))
        by_type = {item["element_type"]: item for item in result["upper"]}
        assert by_type["theory_claim"]["element_id"] == _CLAIM_UUID
        assert by_type["theory_claim"]["relation"] == "provides_evidence_for"
        assert by_type["theory_claim"]["navigable"] is True
        assert by_type["derivation"]["element_id"] == _DERIVATION_ID
        assert by_type["derivation"]["navigable"] is True
        assert by_type["section"]["label"] == "Theory"

    def test_lower_is_empty_and_stated_as_a_fact(self, lens_artifacts):
        result = context_lens.build(_ref(ELEMENT_EVIDENCE, _EVIDENCE_ID))
        assert result["lower"] == []
        assert any("原文の引用" in note for note in result["notes"])

    def test_unknown_evidence_degrades_fail_soft(self, lens_artifacts):
        result = context_lens.build(_ref(ELEMENT_EVIDENCE, "ev_9999"))
        assert result["upper"] == [] and result["lower"] == []
        assert result["focus"]["label"] == "ev_9999"
        assert result["notes"]


class TestContextLensDerivationFocus:
    def test_focus_contract(self, lens_artifacts):
        result = context_lens.build(_ref(ELEMENT_DERIVATION, _DERIVATION_ID))
        focus = result["focus"]
        assert focus["element_type"] == ELEMENT_DERIVATION
        assert focus["element_id"] == _DERIVATION_ID
        assert _DERIVATION_ID in focus["label"]
        assert "solve_linear_system" in focus["label"]
        assert focus["intrinsic_summary"] == "線形系を解いて観測量を得る"

    def test_lower_has_navigable_equations_and_evidence(self, lens_artifacts):
        result = context_lens.build(_ref(ELEMENT_DERIVATION, _DERIVATION_ID))
        equations = [i for i in result["lower"] if i["element_type"] == "equation"]
        assert {i["element_id"] for i in equations} >= {"eq_2_1", "eq_2_4"}
        # artifact に実体がある式は navigable。実体が無い step 出力（eq_2_2）は
        # element_id=None のまま残す（情報は落とさず、開けない導線も作らない）。
        resolvable = [i for i in equations if i["element_id"] in ("eq_2_1", "eq_2_4")]
        assert resolvable and all(i["navigable"] for i in resolvable)
        assert any(i["element_id"] is None and not i["navigable"] for i in equations)
        evidences = [i for i in result["lower"] if i["element_type"] == "evidence"]
        assert evidences and evidences[0]["element_id"] == _EVIDENCE_ID
        assert evidences[0]["navigable"] is True

    def test_upper_has_output_claim_and_section(self, lens_artifacts):
        result = context_lens.build(_ref(ELEMENT_DERIVATION, _DERIVATION_ID))
        by_type = {item["element_type"]: item for item in result["upper"]}
        assert by_type["theory_claim"]["element_id"] == _CLAIM_UUID
        assert by_type["section"]["label"] == "Theory"

    def test_operation_summary_used_when_no_takeaway(self, lens_artifacts, monkeypatch):
        artifacts = _artifacts()
        artifacts["derivation_chain"]["chains"][0]["teaching_takeaway"] = ""
        monkeypatch.setattr(refs, "document_run_artifacts", lambda doc: artifacts)
        result = context_lens.build(_ref(ELEMENT_DERIVATION, _DERIVATION_ID))
        assert result["focus"]["intrinsic_summary"] == "操作: substitute"

    def test_unknown_derivation_degrades_fail_soft(self, lens_artifacts):
        result = context_lens.build(_ref(ELEMENT_DERIVATION, "deriv_9999"))
        assert result["upper"] == [] and result["lower"] == []
        assert result["notes"]


class TestContextLensNavigableVocabulary:
    def test_both_types_can_be_navigable(self):
        for element_type, element_id in (
            (ELEMENT_EVIDENCE, _EVIDENCE_ID),
            (ELEMENT_DERIVATION, _DERIVATION_ID),
        ):
            item = context_lens._item(
                element_type, element_id, _DOC, "label", "rests_on_evidence",
                CONTEXT_STATUS_SOURCE_BACKED,
            )
            assert item["navigable"] is True

    def test_display_only_types_stay_non_navigable(self):
        # section / thesis / symbol / stage / part は解決対象ではない（§16 の表）。
        for element_type in ("section", "thesis", "symbol", "stage", "part"):
            item = context_lens._item(
                element_type, "some-id", _DOC, "label", "contains", CONTEXT_STATUS_SOURCE_BACKED
            )
            assert item["navigable"] is False, element_type

    def test_claim_focus_evidence_items_are_navigable(self):
        """claim focus の下位 evidence 項目に evidence_id が入ること（§16 の既存 ITEM 化）。"""
        source = (BACKEND / "core" / "deliberation" / "context_lens.py").read_text(encoding="utf-8")
        assert '"evidence", str(evidence_id) or None, document_id,' in source


# ---------------------------------------------------------------------------
# 学習者投影: navigable を強制 false（fail-closed）
# ---------------------------------------------------------------------------


class TestLearnerProjectionForcesNonNavigable:
    def test_forced_list_covers_both_types(self):
        from core import element_context

        assert element_context._LEARNER_FORCED_NON_NAVIGABLE_ELEMENT_TYPES == (
            ELEMENT_EVIDENCE,
            ELEMENT_DERIVATION,
        )

    @pytest.mark.parametrize("element_type", [ELEMENT_EVIDENCE, ELEMENT_DERIVATION])
    def test_project_item_never_marks_them_navigable(self, element_type):
        from core import element_context

        item = context_lens._item(
            element_type, "some-id", _DOC, "引用文です", "rests_on_evidence",
            CONTEXT_STATUS_SOURCE_BACKED,
        )
        assert item["navigable"] is True  # 教員向けでは辿れる
        projected = element_context._project_item(item)
        assert projected["navigable"] is False  # 学習者向けでは辿らせない

    def test_learner_whitelist_excludes_both_types(self):
        from core import element_context

        assert ELEMENT_EVIDENCE not in element_context._LEARNER_NAVIGABLE_ELEMENT_TYPES
        assert ELEMENT_DERIVATION not in element_context._LEARNER_NAVIGABLE_ELEMENT_TYPES

    def test_learner_app_js_has_no_navigable_whitelist_for_them(self):
        """app.js（学習者）に evidence / derivation の再フェッチ導線を作っていないこと。"""
        app_js = (ROOT / "frontend" / "public" / "js" / "app.js").read_text(encoding="utf-8")
        for needle in ('"evidence": "/api/learning', "openEvidenceContext", "openDerivationContext"):
            assert needle not in app_js, needle


# ---------------------------------------------------------------------------
# decomposition.py の最小内訳
# ---------------------------------------------------------------------------


class TestDecomposition:
    def test_evidence_breakdown(self, monkeypatch):
        monkeypatch.setattr(refs, "document_run_artifacts", lambda doc: _artifacts())
        result = decomposition.build(_ref(ELEMENT_EVIDENCE, _EVIDENCE_ID))
        assert result["element_type"] == ELEMENT_EVIDENCE
        fields = result["fields"]
        assert fields["evidence_id"] == _EVIDENCE_ID
        assert fields["block_id"] == "block_17"
        assert fields["evidence_role"] == "source_quote"
        assert result["notes"]

    def test_derivation_breakdown_omits_raw_numbers(self, monkeypatch):
        monkeypatch.setattr(refs, "document_run_artifacts", lambda doc: _artifacts())
        result = decomposition.build(_ref(ELEMENT_DERIVATION, _DERIVATION_ID))
        fields = result["fields"]
        assert fields["derivation_id"] == _DERIVATION_ID
        assert fields["steps"][0]["step_id"] == "step_1"
        # W8: step の確度の生値は投影しない（artifact 側には存在する）。
        assert "confidence" not in fields["steps"][0]
        assert all("confidence" not in step for step in fields["steps"])

    @pytest.mark.parametrize(
        "element_type,element_id",
        [(ELEMENT_EVIDENCE, "ev_9999"), (ELEMENT_DERIVATION, "deriv_9999")],
    )
    def test_unknown_id_raises_resolution_error(self, element_type, element_id, monkeypatch):
        monkeypatch.setattr(refs, "document_run_artifacts", lambda doc: _artifacts())
        with pytest.raises(ElementResolutionError) as excinfo:
            decomposition.build(_ref(element_type, element_id))
        assert excinfo.value.kind == "not_found"

    def test_explanations_lookup_degrades_to_empty(self, monkeypatch):
        # element_explanations の語彙に無い型なので 0 件（例外にしない）。
        class _Session:
            def execute(self, *a, **k):
                raise AssertionError("should not query for a non-explanation element type")

            def close(self):
                pass

        monkeypatch.setattr(
            decomposition.element_explanations_store, "list_for_document", lambda *a, **k: []
        )
        monkeypatch.setattr(decomposition, "get_session", lambda: _Session())
        assert decomposition.explanations_for_element(_ref(ELEMENT_EVIDENCE, _EVIDENCE_ID)) == []


# ---------------------------------------------------------------------------
# 面②位置づけ: 対応レンズが無いので空へ縮退する（v1 の「できないこと」）
# ---------------------------------------------------------------------------


class TestPositioningDegrades:
    @pytest.mark.parametrize("element_type", [ELEMENT_EVIDENCE, ELEMENT_DERIVATION])
    def test_all_lenses_are_none(self, element_type, monkeypatch):
        from core.deliberation import positioning

        monkeypatch.setattr(positioning.refs_mod, "document_run_artifacts", lambda doc: _artifacts())
        lenses = positioning.build(_ref(element_type, _EVIDENCE_ID))
        assert set(lenses) == {"intra_document", "cross_corpus", "atlas", "endorsement", "epistemic"}
        assert all(value is None for value in lenses.values())


# ---------------------------------------------------------------------------
# 共通部品化・commit の 422（v1 の「できないこと」）
# ---------------------------------------------------------------------------


class TestNotIdentityLinkable:
    @pytest.mark.parametrize("element_type", [ELEMENT_EVIDENCE, ELEMENT_DERIVATION])
    def test_create_candidate_rejects_before_touching_db(self, element_type, monkeypatch):
        def boom():
            raise AssertionError("must not open a session for a non-linkable element type")

        monkeypatch.setattr(identity_links, "get_session", boom)
        with pytest.raises(ElementResolutionError) as excinfo:
            identity_links.create_candidate(_ref(element_type, "x"), "dddddddd-0000-0000-0000-000000000001")
        assert excinfo.value.kind == "invalid"

    @pytest.mark.parametrize("element_type", [ELEMENT_EVIDENCE, ELEMENT_DERIVATION])
    def test_commit_capability_is_false_for_identity(self, element_type):
        supported, note = delib_annotations.commit_capability(
            {
                "kind": "identity",
                "element_type": element_type,
                "scope": SCOPE_DOCUMENT,
                "body": {},
            }
        )
        assert supported is False
        assert note

    @pytest.mark.parametrize("element_type", [ELEMENT_EVIDENCE, ELEMENT_DERIVATION])
    def test_identity_commit_raises_unsupported(self, element_type):
        with pytest.raises(delib_annotations.CommitRoutingError) as excinfo:
            delib_annotations._commit_identity(
                {
                    "kind": "identity",
                    "element_type": element_type,
                    "scope": SCOPE_DOCUMENT,
                    "element_id": "x",
                    "document_id": _DOC,
                    "body": {"shared_part_id": "dddddddd-0000-0000-0000-000000000001"},
                },
                None,
            )
        assert excinfo.value.kind == "unsupported"

    @pytest.mark.parametrize("kind", ["meaning", "decomposition", "interpretation"])
    def test_other_commit_kinds_are_unsupported_too(self, kind):
        supported, note = delib_annotations.commit_capability(
            {"kind": kind, "element_type": ELEMENT_EVIDENCE, "scope": SCOPE_DOCUMENT, "body": {}}
        )
        assert supported is False
        assert note

    @pytest.mark.parametrize("element_type", [ELEMENT_EVIDENCE, ELEMENT_DERIVATION])
    def test_dialogue_does_not_offer_shared_part_candidates(self, element_type, monkeypatch):
        def boom(_document_id):
            raise AssertionError("must not resolve a domain for a non-linkable element type")

        monkeypatch.setattr(dialogue, "document_domain_key", boom)
        assert dialogue.collect_identity_candidates(_ref(element_type, "x"), {"label": "l"}) == []


# ---------------------------------------------------------------------------
# migration 064
# ---------------------------------------------------------------------------


class TestMigration064:
    @property
    def _sql(self) -> str:
        return (BACKEND / "db" / "064_deliberation_evidence_derivation.sql").read_text(
            encoding="utf-8"
        )

    def test_file_exists_and_extends_both_check_constraints(self):
        sql = self._sql
        assert "deliberation_sessions_element_type_check" in sql
        assert "element_annotations_element_type_check" in sql
        assert sql.count("'evidence', 'derivation'") >= 2

    def test_is_idempotent_via_do_guards(self):
        sql = self._sql
        assert sql.count("IF NOT EXISTS (") >= 2
        assert "DO $$" in sql

    def test_does_not_touch_identity_links_or_explanations(self):
        sql = self._sql
        assert "ALTER TABLE element_identity_links" not in sql
        assert "ALTER TABLE element_explanations" not in sql


# ---------------------------------------------------------------------------
# API: overview / context が 500 にならない
# ---------------------------------------------------------------------------


@pytest.fixture
def client_and_teacher():
    from fastapi.testclient import TestClient
    from api.main import app
    from dependencies import _create_token, ROLE_TEACHER

    client = TestClient(app)
    teacher = _create_token("22222222-2222-2222-2222-222222222222", "tea", "tea@x", ROLE_TEACHER)
    return client, {"Authorization": "Bearer " + teacher}


@_skip_no_fastapi
class TestApiEndpoints:
    @pytest.mark.parametrize(
        "element_type,element_id",
        [(ELEMENT_EVIDENCE, _EVIDENCE_ID), (ELEMENT_DERIVATION, _DERIVATION_ID)],
    )
    def test_overview_returns_200_with_all_faces(
        self, client_and_teacher, monkeypatch, element_type, element_id
    ):
        client, headers = client_and_teacher
        import routes.deliberation as route_mod

        monkeypatch.setattr(route_mod, "_ensure_document_viewable", lambda *a, **k: None)
        monkeypatch.setattr(route_mod.refs, "document_run_artifacts", lambda doc: _artifacts())
        monkeypatch.setattr(route_mod.decomposition.refs_mod, "document_run_artifacts", lambda doc: _artifacts())
        monkeypatch.setattr(route_mod.positioning, "build", lambda ref: {})
        monkeypatch.setattr(route_mod.context_lens, "build", lambda ref: None)
        monkeypatch.setattr(route_mod.decomposition, "explanations_for_element", lambda ref: [])

        response = client.get(
            f"/api/admin/deliberation/elements/{element_type}/{element_id}/overview",
            params={"document_id": _DOC},
            headers=headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ref"]["element_type"] == element_type
        assert body["decomposition"]["element_type"] == element_type

    @pytest.mark.parametrize("element_type", [ELEMENT_EVIDENCE, ELEMENT_DERIVATION])
    def test_overview_without_document_id_is_404(self, client_and_teacher, monkeypatch, element_type):
        client, headers = client_and_teacher
        import routes.deliberation as route_mod

        monkeypatch.setattr(route_mod, "_ensure_document_viewable", lambda *a, **k: None)
        response = client.get(
            f"/api/admin/deliberation/elements/{element_type}/x/overview", headers=headers
        )
        assert response.status_code == 404

    @pytest.mark.parametrize(
        "element_type,element_id",
        [(ELEMENT_EVIDENCE, _EVIDENCE_ID), (ELEMENT_DERIVATION, _DERIVATION_ID)],
    )
    def test_context_endpoint_returns_projection(
        self, client_and_teacher, monkeypatch, element_type, element_id
    ):
        client, headers = client_and_teacher
        import routes.deliberation as route_mod

        monkeypatch.setattr(route_mod, "_ensure_document_viewable", lambda *a, **k: None)
        monkeypatch.setattr(route_mod.refs, "document_run_artifacts", lambda doc: _artifacts())
        monkeypatch.setattr(
            route_mod.context_lens, "build",
            lambda ref: {"focus": {"element_type": ref.element_type}, "upper": [], "lower": [], "notes": []},
        )
        response = client.get(
            f"/api/admin/deliberation/elements/{element_type}/{element_id}/context",
            params={"document_id": _DOC},
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["available"] is True

    @pytest.mark.parametrize("element_type", [ELEMENT_EVIDENCE, ELEMENT_DERIVATION])
    def test_identity_link_routes_are_422(self, client_and_teacher, monkeypatch, element_type):
        client, headers = client_and_teacher
        import routes.deliberation as route_mod

        monkeypatch.setattr(route_mod, "_ensure_document_viewable", lambda *a, **k: None)
        monkeypatch.setattr(route_mod, "_ensure_document_editable", lambda *a, **k: None)
        monkeypatch.setattr(route_mod.refs, "document_run_artifacts", lambda doc: _artifacts())
        element_id = _EVIDENCE_ID if element_type == ELEMENT_EVIDENCE else _DERIVATION_ID

        listing = client.get(
            f"/api/admin/deliberation/elements/{element_type}/{element_id}/identity-links",
            params={"document_id": _DOC},
            headers=headers,
        )
        assert listing.status_code == 422

        candidates = client.get(
            f"/api/admin/deliberation/elements/{element_type}/{element_id}/shared-part-candidates",
            params={"document_id": _DOC},
            headers=headers,
        )
        assert candidates.status_code == 422

        created = client.post(
            "/api/admin/deliberation/identity-links",
            json={
                "instance_element_type": element_type,
                "instance_element_id": element_id,
                "document_id": _DOC,
                "shared_part_id": "dddddddd-0000-0000-0000-000000000001",
            },
            headers=headers,
        )
        assert created.status_code == 422


# ---------------------------------------------------------------------------
# フロント静的検査（admin のみ拡張 / 学習者は不変）
# ---------------------------------------------------------------------------


class TestAdminFrontendWiring:
    @property
    def _studio(self) -> str:
        return (ROOT / "frontend" / "public" / "js" / "admin-lecture-studio.js").read_text(
            encoding="utf-8"
        )

    def test_graph_deliberable_list_includes_both_types(self):
        src = self._studio
        block = src[src.index("var LS_GRAPH_DELIBERABLE_ELEMENT_TYPES = ["):]
        block = block[: block.index("]")]
        for element_type in ("theory_component", "theory_claim", "equation", "figure", "evidence", "derivation"):
            assert '"' + element_type + '"' in block, element_type

    def test_document_scoped_list_drives_local_navigability(self):
        src = self._studio
        block = src[src.index("var LS_GRAPH_DOCUMENT_SCOPED_ELEMENT_TYPES = ["):]
        block = block[: block.index("]")]
        for element_type in ("equation", "evidence", "derivation"):
            assert '"' + element_type + '"' in block, element_type
        # 解決できなかった参照は navigable にしない（押しても開けない導線を作らない）。
        assert "var deliberable = ref.resolved &&" in src

    def test_deliberation_js_knows_document_id_requirement(self):
        src = (ROOT / "frontend" / "public" / "js" / "deliberation.js").read_text(encoding="utf-8")
        assert "var DOCUMENT_ID_REQUIRED_ELEMENT_TYPES = {" in src
        assert "_needsDocumentId(elementType) && !opts.documentId" in src
