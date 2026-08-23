"""``core/component_context.py``（コーススコープ component 文脈 API の core ロジック）の
単体テスト。

設計書: ``docs/features/component_evidence_redesign.md``（Phase 2 + Phase 3）。

DB 実接続は使わず、``get_session`` を monkeypatch した最小限のフェイクセッション
（``test_deliberation_inventory.py`` の ``_FakeSession`` と同型の方針）、および
``identity_links_mod`` / ``library_store_mod`` / ``context_lens_mod`` /
``document_run_artifacts`` / ``equation_records`` の monkeypatch で検証する。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import component_context  # noqa: E402
from core.deliberation.schema import IDENTITY_LINK_STATUS_CANDIDATE  # noqa: E402
from core.library import schema as library_schema  # noqa: E402
from tests.guardrail_helpers import assert_source_does_not_import  # noqa: E402


_DOC_A = "dddddddd-dddd-dddd-dddd-dddddddddddd"
_DOC_B = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
_COMP_UUID = "cccccccc-cccc-cccc-cccc-cccccccccccc"


# ---------------------------------------------------------------------------
# _strip_confidence: 再帰的な "confidence" キー除去
# ---------------------------------------------------------------------------


class TestStripConfidence:
    def test_removes_confidence_from_nested_dict(self):
        value = {"a": {"confidence": 0.9, "b": 1}, "list": [{"confidence": 0.1, "x": 2}]}
        stripped = component_context._strip_confidence(value)
        assert stripped == {"a": {"b": 1}, "list": [{"x": 2}]}

    def test_leaves_other_keys_untouched(self):
        value = {"confidence_label": "確度高", "score": 5}
        assert component_context._strip_confidence(value) == value


# ---------------------------------------------------------------------------
# _resolve_component_row: フェイク DB（doc_ids / raw_id / uuid_id を意味論的に評価）
# ---------------------------------------------------------------------------


class _FakeMappingsResult:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def fetchone(self):
        return dict(self._row) if self._row is not None else None


class _ComponentTableFakeSession:
    """``theory_components`` に対する ``_resolve_component_row`` のクエリを模擬する。

    実 SQL は評価せず、渡された params（``doc_ids`` / ``raw_id`` / ``uuid_id``）を
    Python 側で同じ意味論（document scope 制約・id 一致・legacy_ids 包含・
    id 一致優先の並び替え）で再現する。
    """

    def __init__(self, rows: list[dict]):
        self._rows = rows
        self.closed = False
        self.execute_calls: list[dict] = []

    def execute(self, _stmt, params):
        self.execute_calls.append(dict(params))
        doc_ids = set(params.get("doc_ids") or [])
        raw_id = str(params.get("raw_id") or "")
        uuid_id = params.get("uuid_id")
        matches = []
        for row in self._rows:
            if str(row.get("document_id")) not in doc_ids:
                continue
            legacy_ids = [str(x) for x in (row.get("source_scope") or {}).get("legacy_ids") or []]
            matched_by_legacy = raw_id in legacy_ids
            matched_by_uuid = uuid_id is not None and str(row.get("id")) == str(uuid_id)
            if matched_by_legacy or matched_by_uuid:
                matches.append(row)
        matches.sort(key=lambda r: str(r.get("id")) == raw_id, reverse=True)
        return _FakeMappingsResult(matches[0] if matches else None)

    def close(self):
        self.closed = True


def _row(**kwargs) -> dict:
    base = {
        "id": _COMP_UUID,
        "document_id": _DOC_A,
        "course_id": "course-1",
        "name": "component A",
        "component_type": "theory",
        "component_type_text": "RelationComponent",
        "summary": "summary text",
        "status": "teacher_reviewed",
        "review_status": "teacher_review_required",
        "source_scope": {"legacy_ids": ["comp_001"]},
        "thesis_context": None,
    }
    base.update(kwargs)
    return base


class TestResolveComponentRow:
    def test_resolves_by_db_uuid_within_course_scope(self, monkeypatch):
        fake = _ComponentTableFakeSession([_row()])
        monkeypatch.setattr(component_context, "get_session", lambda: fake)

        result = component_context._resolve_component_row(_COMP_UUID, {_DOC_A})

        assert result is not None
        assert result["id"] == _COMP_UUID
        assert fake.closed is True

    def test_resolves_by_agent_legacy_id_within_course_scope(self, monkeypatch):
        fake = _ComponentTableFakeSession([_row()])
        monkeypatch.setattr(component_context, "get_session", lambda: fake)

        result = component_context._resolve_component_row("comp_001", {_DOC_A})

        assert result is not None
        assert result["id"] == _COMP_UUID

    def test_component_outside_course_documents_returns_none(self, monkeypatch):
        fake = _ComponentTableFakeSession([_row(document_id=_DOC_B)])
        monkeypatch.setattr(component_context, "get_session", lambda: fake)

        result = component_context._resolve_component_row("comp_001", {_DOC_A})

        assert result is None

    def test_empty_course_document_ids_returns_none_without_touching_db(self, monkeypatch):
        def _boom():  # pragma: no cover - must not be called
            raise AssertionError("get_session should not be called when doc set is empty")

        monkeypatch.setattr(component_context, "get_session", _boom)

        result = component_context._resolve_component_row("comp_001", set())

        assert result is None

    def test_same_agent_id_in_two_documents_only_matches_in_scope_document(self, monkeypatch):
        # comp_001 という agent 側 ID は論文ごとに独立採番されるため、別文書
        # （コース外）にも同名 legacy_id を持つ行が存在しうる。document scope 制約
        # により、コース内の文書の行だけが解決されることを確認する。
        rows = [
            _row(id="other-doc-comp", document_id=_DOC_B, source_scope={"legacy_ids": ["comp_001"]}),
            _row(id=_COMP_UUID, document_id=_DOC_A, source_scope={"legacy_ids": ["comp_001"]}),
        ]
        fake = _ComponentTableFakeSession(rows)
        monkeypatch.setattr(component_context, "get_session", lambda: fake)

        result = component_context._resolve_component_row("comp_001", {_DOC_A})

        assert result is not None
        assert result["id"] == _COMP_UUID


# ---------------------------------------------------------------------------
# _agent_id_candidates
# ---------------------------------------------------------------------------


class TestAgentIdCandidates:
    def test_includes_db_id_and_legacy_ids(self):
        row = _row(source_scope={"legacy_ids": ["comp_001", "claim_abc"]})
        assert component_context._agent_id_candidates(row) == {_COMP_UUID, "comp_001", "claim_abc"}

    def test_missing_source_scope_falls_back_to_db_id_only(self):
        row = _row(source_scope=None)
        assert component_context._agent_id_candidates(row) == {_COMP_UUID}


# ---------------------------------------------------------------------------
# narrative_role: 優先順位（グラフ node_narratives → thesis_context.role_in_thesis →
# artifact teaching_takeaway → summary）
# ---------------------------------------------------------------------------


class TestNarrativeRolePriority:
    def test_prefers_graph_node_narrative(self):
        row = _row(thesis_context={"role_in_thesis": "should not be used"}, summary="fallback")
        narrative = {"node_narratives": {_COMP_UUID: {"narrative_role": "グラフ由来の役割"}}}
        role = component_context._narrative_role(_COMP_UUID, row, narrative, {"teaching_takeaway": "tt"})
        assert role == "グラフ由来の役割"

    def test_falls_back_to_thesis_context_role_in_thesis(self):
        row = _row(thesis_context={"role_in_thesis": "中心命題を支持する"}, summary="fallback")
        role = component_context._narrative_role(_COMP_UUID, row, {}, {"teaching_takeaway": "tt"})
        assert role == "中心命題を支持する"

    def test_falls_back_to_artifact_teaching_takeaway(self):
        row = _row(thesis_context=None, summary="fallback")
        role = component_context._narrative_role(_COMP_UUID, row, {}, {"teaching_takeaway": "要点テキスト"})
        assert role == "要点テキスト"

    def test_falls_back_to_summary_when_all_else_empty(self):
        row = _row(thesis_context=None, summary="要約フォールバック")
        role = component_context._narrative_role(_COMP_UUID, row, {}, {})
        assert role == "要約フォールバック"

    def test_blank_graph_role_does_not_shadow_thesis_context(self):
        row = _row(thesis_context={"role_in_thesis": "中心命題を支持する"})
        narrative = {"node_narratives": {_COMP_UUID: {"narrative_role": "   "}}}
        role = component_context._narrative_role(_COMP_UUID, row, narrative, {})
        assert role == "中心命題を支持する"


# ---------------------------------------------------------------------------
# supports のサブヘルパ: equations / claims / dependencies / field refs
# ---------------------------------------------------------------------------


class TestBuildEquations:
    def test_role_derived_from_equation_id_fields(self):
        record = {
            "input_equation_ids": ["eq_1"],
            "output_equation_ids": ["eq_2"],
            "linked_equation_ids": ["eq_1", "eq_2", "eq_3"],
        }
        equations_by_id = {
            "eq_1": {"reconstruction": {"plain_text": "E1"}},
            "eq_2": {"reconstruction": {"plain_text": "E2"}},
        }
        items = component_context._build_equations(record, equations_by_id)
        by_id = {i["id"]: i for i in items}
        assert by_id["eq_1"]["role"] == "input"
        assert by_id["eq_2"]["role"] == "output"
        assert by_id["eq_3"]["role"] == "linked"
        assert by_id["eq_3"]["label"] == "eq_3"  # 索引に無い equation は id をラベルへフォールバック


class TestBuildClaims:
    def test_excludes_bare_ids_without_resolvable_text(self):
        record = {"linked_claim_ids": ["claim_1", "claim_missing"]}
        claims_by_id = {"claim_1": {"text": "主張の本文"}}
        items = component_context._build_claims(record, claims_by_id)
        assert items == [{"id": "claim_1", "excerpt": "主張の本文"}]


class TestBuildDependencies:
    def test_resolves_target_label_via_agent_id_index(self):
        record = {
            "dependencies": [
                {"dependency_type": "requires", "component_refs": ["comp_002"], "reason": "理由X"},
            ]
        }
        components_by_agent_id = {"comp_002": {"label": "依存先"}}
        items = component_context._build_dependencies(record, components_by_agent_id)
        assert items == [{"type": "requires", "target_label": "依存先", "reason": "理由X"}]

    def test_unresolvable_ref_falls_back_to_raw_ref_as_label(self):
        record = {
            "dependencies": [
                {"dependency_type": "requires", "component_refs": ["comp_999"], "reason": ""},
            ]
        }
        items = component_context._build_dependencies(record, {})
        assert items == [{"type": "requires", "target_label": "comp_999", "reason": ""}]


class TestBuildFieldRefs:
    def test_blank_text_excluded_and_capped_at_max(self):
        record = {
            "preconditions": (
                [{"text": ""}] + [{"text": f"t{i}", "claim_ids": [], "equation_ids": []} for i in range(10)]
            )
        }
        items = component_context._build_field_refs(record, "preconditions")
        assert len(items) == component_context._MAX_FIELD_REF_ITEMS
        assert items[0] == {"text": "t0", "refs": {"claim_ids": [], "equation_ids": []}}


class TestBuildCautions:
    def test_accepts_dict_and_plain_string_forms(self):
        record = {"cautions": [{"text": "注意1"}, "注意2", {"text": ""}]}
        items = component_context._build_cautions(record)
        assert items == [{"text": "注意1"}, {"text": "注意2"}]


# ---------------------------------------------------------------------------
# shared_part: confirmed + active が揃ったときのみ、揃わなければ None
# ---------------------------------------------------------------------------


def _identity_link(*, status: str, shared_part_id: str = "lib-1", decided_by: str | None = None, evidence=None):
    return {
        "status": status,
        "shared_part_id": shared_part_id,
        "decided_by": decided_by,
        "evidence": evidence or [],
    }


class TestBuildSharedPart:
    def test_returns_none_when_no_links_at_all(self, monkeypatch):
        monkeypatch.setattr(component_context.identity_links_mod, "list_for_instance", lambda *a, **k: [])
        result = component_context._build_shared_part(_COMP_UUID, _DOC_A, {_COMP_UUID, "comp_001"})
        assert result is None

    def test_candidate_only_link_is_excluded(self, monkeypatch):
        monkeypatch.setattr(
            component_context.identity_links_mod,
            "list_for_instance",
            lambda *a, **k: [_identity_link(status=IDENTITY_LINK_STATUS_CANDIDATE)],
        )
        get_entry_called = []
        monkeypatch.setattr(
            component_context.library_store_mod, "get_entry",
            lambda eid: get_entry_called.append(eid) or {"status": library_schema.STATUS_ACTIVE},
        )
        result = component_context._build_shared_part(_COMP_UUID, _DOC_A, {_COMP_UUID})
        assert result is None
        assert get_entry_called == []  # candidate は library_entries すら引かない

    def test_retired_entry_is_excluded_even_with_confirmed_link(self, monkeypatch):
        monkeypatch.setattr(
            component_context.identity_links_mod,
            "list_for_instance",
            lambda *a, **k: [_identity_link(status="confirmed")],
        )
        monkeypatch.setattr(
            component_context.library_store_mod, "get_entry",
            lambda eid: {"status": library_schema.STATUS_RETIRED, "name": "retired entry"},
        )
        result = component_context._build_shared_part(_COMP_UUID, _DOC_A, {_COMP_UUID})
        assert result is None

    def test_confirmed_and_active_returns_entry_with_other_documents_count(self, monkeypatch):
        monkeypatch.setattr(
            component_context.identity_links_mod,
            "list_for_instance",
            lambda *a, **k: [_identity_link(status="confirmed", decided_by="user-1", evidence=[{"confidence": 0.8, "quote": "q"}])],
        )
        monkeypatch.setattr(
            component_context.library_store_mod, "get_entry",
            lambda eid: {
                "status": library_schema.STATUS_ACTIVE,
                "name": "共通部品X",
                "aliases": ["alias1"],
                "summary": "共通部品の説明",
                "standardization_status": "field_standard",
                "source_document_ids": [_DOC_A, _DOC_B, "doc-other"],
            },
        )
        monkeypatch.setattr(component_context, "_user_display_name", lambda uid: "山田先生")

        result = component_context._build_shared_part(_COMP_UUID, _DOC_A, {_COMP_UUID})

        assert result is not None
        assert result["entry"]["name"] == "共通部品X"
        assert result["entry"]["other_documents_count"] == 2  # _DOC_A 自身を除く
        assert result["link"]["confirmed_by"] == "山田先生"
        assert result["provenance"] == "identity_link_confirmed + library_entry"

    def test_missing_shared_part_id_on_link_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            component_context.identity_links_mod,
            "list_for_instance",
            lambda *a, **k: [_identity_link(status="confirmed", shared_part_id="")],
        )
        result = component_context._build_shared_part(_COMP_UUID, _DOC_A, {_COMP_UUID})
        assert result is None

    def test_identity_link_lookup_exception_is_fail_soft(self, monkeypatch):
        def _raise(*_a, **_k):
            raise RuntimeError("db down")

        monkeypatch.setattr(component_context.identity_links_mod, "list_for_instance", _raise)
        result = component_context._build_shared_part(_COMP_UUID, _DOC_A, {_COMP_UUID})
        assert result is None


# ---------------------------------------------------------------------------
# graph: context_lens の1-hop近傍を candidate 除外のうえ射影する
# ---------------------------------------------------------------------------


class TestBuildGraph:
    def test_candidate_items_are_excluded_from_both_lanes(self, monkeypatch):
        monkeypatch.setattr(
            component_context.context_lens_mod,
            "build",
            lambda ref: {
                "focus": {"label": "焦点コンポーネント"},
                "upper": [
                    {"element_id": "up-1", "element_type": "theory_claim", "label": "上位A",
                     "relation_label": "を支持する", "relation_status": "source_backed", "navigable": True},
                    {"element_id": "up-2", "element_type": "theory_component", "label": "上位B(候補)",
                     "relation_label": "に関連する", "relation_status": "candidate", "navigable": True},
                ],
                "lower": [
                    {"element_id": "low-1", "element_type": "equation", "label": "下位A",
                     "relation_label": "を用いる", "relation_status": "confirmed", "navigable": True},
                ],
            },
        )
        graph = component_context._build_graph(_COMP_UUID, _DOC_A)
        assert graph["focus"] == {"id": _COMP_UUID, "label": "焦点コンポーネント"}
        assert [i["id"] for i in graph["upper"]] == ["up-1"]
        assert [i["id"] for i in graph["lower"]] == ["low-1"]

    def test_none_result_from_context_lens_yields_none_graph(self, monkeypatch):
        monkeypatch.setattr(component_context.context_lens_mod, "build", lambda ref: None)
        assert component_context._build_graph(_COMP_UUID, _DOC_A) is None

    def test_exception_from_context_lens_is_fail_soft_none(self, monkeypatch):
        def _raise(ref):
            raise RuntimeError("boom")

        monkeypatch.setattr(component_context.context_lens_mod, "build", _raise)
        assert component_context._build_graph(_COMP_UUID, _DOC_A) is None

    def test_lane_capped_at_max(self, monkeypatch):
        many_items = [
            {"element_id": f"e{i}", "element_type": "theory_claim", "label": f"l{i}",
             "relation_label": "を支持する", "relation_status": "source_backed", "navigable": True}
            for i in range(30)
        ]
        monkeypatch.setattr(
            component_context.context_lens_mod, "build",
            lambda ref: {"focus": {"label": "x"}, "upper": many_items, "lower": []},
        )
        graph = component_context._build_graph(_COMP_UUID, _DOC_A)
        assert len(graph["upper"]) == component_context._GRAPH_LANE_MAX

    def test_item_keys_stay_the_legacy_six(self, monkeypatch):
        """graph レーンの ITEM は旧6キーのまま（``group`` を足さない）。

        ``group`` が1件でも入ると統一パーツカード（``element-card.js`` の
        ``hasGroupedItems``）が4区画描画へ切り替わり、可視の UX 変更になる。
        """
        monkeypatch.setattr(
            component_context.context_lens_mod,
            "build",
            lambda ref: {
                "focus": {"label": "焦点"},
                "upper": [
                    {"element_id": "up-1", "element_type": "theory_claim", "label": "上位A",
                     "relation_label": "を支持する", "relation_status": "source_backed",
                     "navigable": True, "group": "claim", "sublabel": "区別材料",
                     "qualifier": "", "unresolved": False, "label_source": "text",
                     "evidence_refs": ["ev_0001"], "relation": "backed_by_claim"},
                ],
                "lower": [],
            },
        )
        item = component_context._build_graph(_COMP_UUID, _DOC_A)["upper"][0]
        assert set(item) == {
            "id", "element_type", "label", "relation_label", "relation_status", "navigable"
        }

    def test_internal_id_labels_are_replaced_with_generic_labels(self, monkeypatch):
        """A-3（望ましい挙動変更）: 遮断層が component の graph レーンにも効く。

        W層 ``_build_component`` はノードラベルを引けなかったとき ``comp_003`` のような
        agent 側 ref を、``_build_claim`` は図の DB UUID をそのまま label に入れる。
        従来この経路は W層の値を素通しにしていたため、学習者に内部 ID が見えていた。
        """
        monkeypatch.setattr(
            component_context.context_lens_mod,
            "build",
            lambda ref: {
                "focus": {"label": "焦点"},
                "upper": [
                    {"element_id": None, "element_type": "theory_component", "label": "comp_003",
                     "relation_label": "に含まれる", "relation_status": "source_backed",
                     "navigable": False},
                    {"element_id": "fig-1", "element_type": "figure",
                     "label": _DOC_B,  # 図の DB UUID がそのまま label に入るケース
                     "relation_label": "を根拠とする", "relation_status": "source_backed",
                     "navigable": True},
                ],
                "lower": [
                    {"element_id": "eq_tex_b14", "element_type": "equation",
                     "label": r"\frac{\rho}{\bar{\rho}} - 1",  # 切り詰められた生 TeX
                     "relation_label": "を用いる", "relation_status": "source_backed",
                     "navigable": True},
                ],
            },
        )
        graph = component_context._build_graph(_COMP_UUID, _DOC_A)
        assert [i["label"] for i in graph["upper"]] == ["関連する論理要素", "図"]
        assert graph["lower"][0]["label"] == "関連する数式"

    def test_navigable_is_recomputed_fail_closed(self, monkeypatch):
        """A-3（望ましい挙動変更）: ``navigable`` を学習者が実際に開ける型だけに絞る。

        W層の ``navigable`` は**教員向け**の可否なので、学習者向けの文脈取得 API が
        無い型（figure / evidence / derivation / stage …）でも真になり得た。押しても
        何も起きない導線を DTO に残さない（フロントの再ゲートに頼らない）。
        """
        monkeypatch.setattr(
            component_context.context_lens_mod,
            "build",
            lambda ref: {
                "focus": {"label": "焦点"},
                "upper": [
                    {"element_id": "cl-1", "element_type": "theory_claim", "label": "主張",
                     "relation_label": "を支持する", "relation_status": "source_backed",
                     "navigable": True},
                    {"element_id": "ev-1", "element_type": "evidence", "label": "本文の一節",
                     "relation_label": "を根拠とする", "relation_status": "source_backed",
                     "navigable": True},
                ],
                "lower": [
                    {"element_id": "dv-1", "element_type": "derivation", "label": "導出の名前",
                     "relation_label": "の導出に属する", "relation_status": "source_backed",
                     "navigable": True},
                    {"element_id": "fg-1", "element_type": "figure", "label": "装置図",
                     "relation_label": "を根拠とする", "relation_status": "source_backed",
                     "navigable": True},
                ],
            },
        )
        graph = component_context._build_graph(_COMP_UUID, _DOC_A)
        assert [i["navigable"] for i in graph["upper"]] == [True, False]
        assert [i["navigable"] for i in graph["lower"]] == [False, False]


# ---------------------------------------------------------------------------
# build_component_context: end-to-end DTO 組み立て + confidence 非漏洩
# ---------------------------------------------------------------------------


class TestBuildComponentContextEndToEnd:
    def test_component_outside_course_returns_none(self, monkeypatch):
        fake = _ComponentTableFakeSession([_row(document_id=_DOC_B)])
        monkeypatch.setattr(component_context, "get_session", lambda: fake)

        result = component_context.build_component_context("comp_001", "course-1", {_DOC_A})

        assert result is None

    def test_full_dto_shape_and_no_confidence_leak(self, monkeypatch):
        row = _row(thesis_context={"role_in_thesis": "中心命題を支持する"})
        fake = _ComponentTableFakeSession([row])
        monkeypatch.setattr(component_context, "get_session", lambda: fake)
        monkeypatch.setattr(component_context, "_load_graph_narrative", lambda _doc: {})
        monkeypatch.setattr(component_context, "_document_title", lambda _doc: "論文タイトル")
        monkeypatch.setattr(
            component_context, "document_run_artifacts",
            lambda _doc: {
                "component_assembly": {
                    "components": [
                        {
                            "component_id": "comp_001",
                            "label": "コンポーネントA",
                            "summary": "要約",
                            "teaching_takeaway": "要点",
                            "linked_claim_ids": ["claim_1"],
                            "linked_equation_ids": ["eq_1"],
                            "preconditions": [{"text": "前提1", "claim_ids": [], "equation_ids": []}],
                            "cautions": [{"text": "注意1"}],
                            "dependencies": [
                                {"dependency_type": "requires", "component_refs": ["comp_002"], "reason": "理由"}
                            ],
                        },
                        {"component_id": "comp_002", "label": "依存先コンポーネント"},
                    ]
                },
                "claim_object_builder": {"claims": [{"claim_id": "claim_1", "text": "主張テキスト"}]},
            },
        )
        monkeypatch.setattr(
            component_context, "equation_records",
            lambda _doc: [{"equation_id": "eq_1", "reconstruction": {"plain_text": "E=mc^2"}}],
        )
        monkeypatch.setattr(component_context.identity_links_mod, "list_for_instance", lambda *a, **k: [])
        monkeypatch.setattr(
            component_context.context_lens_mod, "build",
            lambda ref: {
                "focus": {"label": "コンポーネントA"},
                "upper": [
                    {"element_id": "up-1", "element_type": "theory_claim", "label": "上位",
                     "relation_label": "を支持する", "relation_status": "source_backed",
                     "navigable": True, "confidence": 0.9},
                ],
                "lower": [
                    {"element_id": "low-1", "element_type": "equation", "label": "下位候補",
                     "relation_label": "を用いる", "relation_status": "candidate", "navigable": True},
                ],
            },
        )

        result = component_context.build_component_context("comp_001", "course-1", {_DOC_A})

        assert result is not None
        assert result["component_id"] == _COMP_UUID
        instance = result["instance"]
        assert instance["component"]["label"] == "component A"  # DB 行の name が最優先
        assert instance["component"]["teaching_takeaway"] == "要点"
        assert instance["in_paper"]["document"] == {"id": _DOC_A, "title": "論文タイトル", "section": ""}
        assert instance["in_paper"]["narrative_role"] == "中心命題を支持する"
        assert instance["supports"]["claims"] == [{"id": "claim_1", "excerpt": "主張テキスト"}]
        assert instance["supports"]["equations"] == [{"id": "eq_1", "label": "E=mc^2", "role": "linked"}]
        assert instance["supports"]["dependencies"] == [
            {"type": "requires", "target_label": "依存先コンポーネント", "reason": "理由"}
        ]
        assert instance["supports"]["cautions"] == [{"text": "注意1"}]
        assert instance["provenance"] == "course_freeze"
        assert "explanation" not in instance  # core は explanation を付与しない(route の責務)
        assert result["shared_part"] is None
        assert [i["id"] for i in result["graph"]["upper"]] == ["up-1"]
        assert result["graph"]["lower"] == []  # candidate 除外

        def _walk(value):
            if isinstance(value, dict):
                assert "confidence" not in value
                for v in value.values():
                    _walk(v)
            elif isinstance(value, list):
                for v in value:
                    _walk(v)

        _walk(result)


# ---------------------------------------------------------------------------
# ガードレール: core/component_context.py は FastAPI/routes を import しない
# ---------------------------------------------------------------------------


class TestGuardrails:
    def test_does_not_import_fastapi_or_routes(self):
        src = Path(component_context.__file__).read_text(encoding="utf-8")
        assert_source_does_not_import(src, ["fastapi", "routes", "api."], context="core/component_context.py")
