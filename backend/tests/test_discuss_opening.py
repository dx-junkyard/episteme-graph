"""discuss モード開幕画面（``core/discuss/opening.py``、設計書 §3.3 / §6.3）の単体テスト。

Phase 0/1（可視性フィルタ・intent_mode='discuss' の分岐）は既存テストでカバー済み。
本ファイルは Phase 2 のバックエンド投影を検証する:

- ``core/discuss/`` が FastAPI を import しない
- 純粋投影関数（``project_thesis`` / ``project_backbone`` / ``project_fragile_points``）が
  fake の artifacts / graph_json / open_assumptions dict から DTO 形状・上限・stage 順・
  main 層フィルタを正しく組み立てる
- confidence / load_score / score の生数値が出力に一切現れない
- ``routes/learning.py`` に endpoint が存在し、``get_course_data`` ゲート → 404 の配線がある
- fact_line 生成コードに禁止語彙（D層ガードレールと同じ語彙）が無い

DB 実接続は使わない（fake dict のみ）。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.deliberation import refs as refs_module  # noqa: E402
from core.discuss import opening  # noqa: E402
from tests.guardrail_helpers import (  # noqa: E402
    assert_module_tree_does_not_import,
    assert_source_forbids,
    extract_function_source,
)

_CORE_DIR = BACKEND / "core" / "discuss"
_OPENING_SRC = (BACKEND / "core" / "discuss" / "opening.py").read_text(encoding="utf-8")
_LEARNING_SRC = (BACKEND / "api" / "routes" / "learning.py").read_text(encoding="utf-8")

# D層ガードレール（test_doubt_guardrails.py）と同じ禁止語彙リスト。
_BANNED_WORDS = ("疑え", "ノーベル賞", "危険地帯", "要注意ゾーン", "崩壊させよ")


# ---------------------------------------------------------------------------
# レイヤリング（core/discuss は FastAPI を import しない）
# ---------------------------------------------------------------------------


class TestLayering:
    def test_no_fastapi_import(self):
        assert_module_tree_does_not_import(_CORE_DIR, ["fastapi"])

    def test_no_routes_or_services_import(self):
        assert_module_tree_does_not_import(_CORE_DIR, ["routes", "services"])

    def test_no_llm_import(self):
        # DM8: 開幕画面（および core/discuss/ 配下全体、observation.py 含む）は非LLM。
        # core.llm / core.llm_worker のどちらも import しないことを構造的に固定する。
        assert_module_tree_does_not_import(_CORE_DIR, ["core.llm", "core.llm_worker"])


# ---------------------------------------------------------------------------
# project_thesis（§3.3 中心命題・支持構造）
# ---------------------------------------------------------------------------


class TestProjectThesis:
    def test_none_artifact_returns_none(self):
        assert opening.project_thesis(None, {}, {}) is None
        assert opening.project_thesis({}, {}, {}) is not None  # 空dictは「artifactはある」扱い

    def test_central_claims_and_equations_resolved_and_capped(self):
        thesis = {
            "central_thesis": {
                "claim_ids": [f"c{i}" for i in range(7)],
                "equation_ids": [f"eq{i}" for i in range(7)],
            },
        }
        claim_label_index = {f"c{i}": f"claim text {i}" for i in range(7)}
        equations_by_id = {f"eq{i}": {"label": f"equation {i}"} for i in range(7)}
        dto = opening.project_thesis(thesis, claim_label_index, equations_by_id)
        assert len(dto["central_claims"]) == 5
        assert len(dto["central_equations"]) == 5
        assert dto["central_claims"][0] == {"id": "c0", "label": "claim text 0"}
        assert dto["central_equations"][0]["id"] == "eq0"

    def test_unresolved_claim_id_falls_back_to_id_string(self):
        thesis = {"central_thesis": {"claim_ids": ["unknown_claim"], "equation_ids": []}}
        dto = opening.project_thesis(thesis, {}, {})
        assert dto["central_claims"] == [{"id": "unknown_claim", "label": "unknown_claim"}]

    def test_unresolved_equation_id_falls_back_to_id_string(self):
        thesis = {"central_thesis": {"claim_ids": [], "equation_ids": ["unknown_eq"]}}
        dto = opening.project_thesis(thesis, {}, {})
        assert dto["central_equations"] == [{"id": "unknown_eq", "label": "unknown_eq"}]

    def test_support_sections_labeled_and_flattened_with_cap(self):
        thesis = {
            "central_thesis": {},
            "support_structure": {
                "assumptions": [
                    {"claim_ids": ["a1", "a2", "a3"], "equation_ids": []},
                    {"claim_ids": ["a4"], "equation_ids": ["e1", "e2", "e3"]},
                ],
                "unknown_section_key": [{"claim_ids": ["z1"], "equation_ids": []}],
            },
        }
        claim_label_index = {f"a{i}": f"assumption {i}" for i in range(1, 5)}
        dto = opening.project_thesis(thesis, claim_label_index, {})
        sections_by_key = {s["key"]: s for s in dto["support_sections"]}

        assumptions = sections_by_key["assumptions"]
        assert assumptions["label"] == "前提"
        assert len(assumptions["items"]) == 5  # 上限5で切る（a1..a4 + e1）
        assert assumptions["items"][0] == {"type": "claim", "id": "a1", "label": "assumption 1"}
        assert assumptions["items"][-1]["type"] == "equation"

        # 未知のセクション名はキーそのものを表示する（情報を落とさない）。
        assert sections_by_key["unknown_section_key"]["label"] == "unknown_section_key"

    def test_empty_entries_produce_no_section(self):
        thesis = {"central_thesis": {}, "support_structure": {"assumptions": [{"claim_ids": [], "equation_ids": []}]}}
        dto = opening.project_thesis(thesis, {}, {})
        assert dto["support_sections"] == []

    def test_thesis_is_empty_helper(self):
        assert opening._thesis_is_empty(None) is True
        assert opening._thesis_is_empty({"central_claims": [], "central_equations": [], "support_sections": []}) is True
        assert opening._thesis_is_empty({"central_claims": [{"id": "c1", "label": "x"}], "central_equations": [], "support_sections": []}) is False


# ---------------------------------------------------------------------------
# project_backbone（main 層フィルタ・stage 順・上限12・truncated）
# ---------------------------------------------------------------------------


def _main_node(node_id: str, stage: str, **extra) -> dict:
    node = {"component_id": node_id, "label": f"label-{node_id}", "graph_layer": "main", "stage": stage}
    node.update(extra)
    return node


class TestProjectBackbone:
    def test_filters_non_main_layers(self):
        nodes = [
            _main_node("n1", "theory_basis"),
            {"component_id": "d1", "graph_layer": "equation_detail", "stage": "elimination"},
            {"component_id": "g1", "graph_layer": "debug", "stage": "elimination"},
        ]
        projected, truncated = opening.project_backbone(nodes)
        assert [n["node_id"] for n in projected] == ["n1"]
        assert truncated is False

    def test_sorts_by_theory_stage_order(self):
        nodes = [
            _main_node("n_elim", "elimination"),
            _main_node("n_basis", "theory_basis"),
            _main_node("n_diag", "diagnostic_application"),
            _main_node("n_eq", "equation_system"),
        ]
        projected, _truncated = opening.project_backbone(nodes)
        assert [n["node_id"] for n in projected] == ["n_basis", "n_eq", "n_elim", "n_diag"]

    def test_unknown_stage_sorts_last(self):
        nodes = [_main_node("n_unknown", "some_new_stage"), _main_node("n_basis", "theory_basis")]
        projected, _truncated = opening.project_backbone(nodes)
        assert [n["node_id"] for n in projected] == ["n_basis", "n_unknown"]

    def test_accepts_id_or_node_id_keys(self):
        nodes = [
            {"id": "via_id", "label": "L", "graph_layer": "main", "stage": "theory_basis"},
            {"node_id": "via_node_id", "label": "L2", "graph_layer": "main", "stage": "elimination"},
        ]
        projected, _truncated = opening.project_backbone(nodes)
        assert {n["node_id"] for n in projected} == {"via_id", "via_node_id"}

    def test_caps_at_12_and_reports_truncated(self):
        nodes = [_main_node(f"n{i}", "theory_basis") for i in range(15)]
        projected, truncated = opening.project_backbone(nodes)
        assert len(projected) == 12
        assert truncated is True

    def test_no_truncation_when_within_limit(self):
        nodes = [_main_node(f"n{i}", "theory_basis") for i in range(3)]
        projected, truncated = opening.project_backbone(nodes)
        assert len(projected) == 3
        assert truncated is False

    def test_stage_label_present(self):
        """stage_label は学習者向けの日本語表示名（開幕画面は学習者が最初に見る画面で、
        A層の英語内部語彙をそのまま出すと分類名の羅列に見えるため）。stage コード自体は
        domain-neutral のまま変えない。"""
        nodes = [_main_node("n1", "observable_construction", label="")]
        projected, _truncated = opening.project_backbone(nodes)
        assert projected[0]["stage"] == "observable_construction"
        assert projected[0]["stage_label"] == "観測量の構成"

    def test_all_known_stages_have_japanese_labels(self):
        for stage in opening._STAGE_ORDER:
            label = opening._stage_label(stage)
            assert label
            assert label.isascii() is False, f"stage {stage} の表示名が日本語化されていない"

    def test_unknown_stage_degrades_to_readable_code(self):
        """日本語訳の無い stage が増えてもコードが読める形で残る（情報を落とさない）。"""
        assert opening._stage_label("some_new_stage") == "Some new stage"
        assert opening._stage_label("") == ""

    def test_review_reasons_and_backing_status_passed_through(self):
        nodes = [
            _main_node(
                "n1", "elimination",
                review_status="review_required",
                source_backing_status="inferred",
                review_reasons=["missing_atomic_claim", "missing_evidence_link"],
                description="desc text",
            )
        ]
        projected, _truncated = opening.project_backbone(nodes)
        node = projected[0]
        assert node["review_status"] == "review_required"
        assert node["source_backing_status"] == "inferred"
        assert node["review_reasons"] == ["missing_atomic_claim", "missing_evidence_link"]
        assert node["description"] == "desc text"

    def test_confidence_key_in_raw_node_is_not_projected(self):
        nodes = [_main_node("n1", "theory_basis", confidence=0.93)]
        projected, _truncated = opening.project_backbone(nodes)
        assert "confidence" not in projected[0]

    def test_empty_input(self):
        projected, truncated = opening.project_backbone([])
        assert projected == []
        assert truncated is False
        projected, truncated = opening.project_backbone(None)
        assert projected == []
        assert truncated is False


# ---------------------------------------------------------------------------
# 「最も脆い一手」= project_fragile_points
# ---------------------------------------------------------------------------


class TestProjectFragilePoints:
    def test_assumption_items_projected_with_null_document_id(self):
        items = [
            {"target_id": "t1", "statement": "前提の文", "verification_status": "untested", "scope_count_is_zero": True},
        ]
        points, truncated = opening.project_fragile_points(items, {})
        assert points == [
            {
                "kind": "assumption",
                # 主語は論文（この論文が確かめていないこと）。システムの解析状態と
                # 同じ区画に積まないための構造的な区別（投影の是正 §2/§3）。
                "subject": "paper",
                "label": "前提の文",
                "fact_line": "どの範囲で確かめたかが記録されていません。",
                "document_id": None,
            }
        ]
        assert truncated is False

    def test_backbone_node_review_required_included(self):
        backbone = {
            "doc1": [
                {
                    "node_id": "n1", "label": "Elimination", "review_status": "review_required",
                    "source_backing_status": "review_required", "review_reasons": ["missing_equation_link"],
                }
            ]
        }
        points, _truncated = opening.project_fragile_points([], backbone)
        assert len(points) == 1
        assert points[0]["kind"] == "backbone_node"
        # 主語はシステム（解析）。論文の弱点として読ませない。
        assert points[0]["subject"] == "system"
        assert points[0]["document_id"] == "doc1"
        assert "関係する式とのつながりを記録できていません" in points[0]["fact_line"]
        assert "解析" in points[0]["fact_line"]

    def test_source_backed_backbone_node_excluded(self):
        backbone = {
            "doc1": [
                {"node_id": "n1", "label": "OK", "review_status": "source_backed", "source_backing_status": "source_backed", "review_reasons": []},
            ]
        }
        points, _truncated = opening.project_fragile_points([], backbone)
        assert points == []

    def test_caps_at_8_and_reports_truncated(self):
        items = [
            {"target_id": f"t{i}", "statement": f"assumption {i}", "verification_status": "untested", "scope_count_is_zero": True}
            for i in range(10)
        ]
        points, truncated = opening.project_fragile_points(items, {})
        assert len(points) == 8
        assert truncated is True

    def test_confidence_and_load_score_in_raw_items_not_projected(self):
        items = [
            {
                "target_id": "t1", "statement": "s", "verification_status": "unknown",
                "scope_count_is_zero": True, "load_score": 42.0, "confidence": 0.5,
            }
        ]
        points, _truncated = opening.project_fragile_points(items, {})
        assert "load_score" not in points[0]
        assert "confidence" not in points[0]


# ---------------------------------------------------------------------------
# 数値非漏洩（confidence / load_score / score を再帰的に除去、W8/DM6）
# ---------------------------------------------------------------------------


class TestNoNumericLeak:
    def test_strip_numeric_keys_removes_nested_confidence_and_load_score(self):
        value = {
            "a": {"confidence": 0.9, "load_score": 12.3, "score": 7, "keep": "yes"},
            "b": [{"confidence": 1.0}, {"other": 1}],
        }
        stripped = opening._strip_numeric_keys(value)
        assert stripped == {"a": {"keep": "yes"}, "b": [{}, {"other": 1}]}

    def test_build_opening_result_never_contains_forbidden_numeric_keys(self):
        # ホワイトリスト射影が正しく働けば、confidence 等が紛れ込む余地自体が無いことを
        # project_* 関数の合成結果で確認する（DB を経由しない end-to-end 相当）。
        thesis = {"central_thesis": {"claim_ids": ["c1"], "equation_ids": []}, "confidence": 0.5}
        thesis_dto = opening.project_thesis(thesis, {"c1": "text"}, {})
        backbone, _t = opening.project_backbone([_main_node("n1", "theory_basis", confidence=0.9)])
        fragile, _t2 = opening.project_fragile_points(
            [{"target_id": "t1", "statement": "s", "verification_status": "unknown", "scope_count_is_zero": True, "confidence": 0.1}],
            {"doc1": backbone},
        )
        result = opening._strip_numeric_keys(
            {
                "course_id": "course1",
                "available": True,
                "documents": [{"document_id": "doc1", "title": "t", "thesis": thesis_dto, "backbone": backbone, "truncated": False}],
                "fragile_points": fragile,
                "truncated": False,
            }
        )

        def _walk(node):
            if isinstance(node, dict):
                for key in ("confidence", "load_score", "score"):
                    assert key not in node
                for v in node.values():
                    _walk(v)
            elif isinstance(node, list):
                for v in node:
                    _walk(v)

        _walk(result)


# ---------------------------------------------------------------------------
# document_run_artifacts の二重取得防止（build_opening が1 document につき1回だけ
# document_analysis_runs.stage_outputs を読むこと）
# ---------------------------------------------------------------------------


class TestArtifactsFetchedOncePerDocument:
    def test_document_run_artifacts_not_refetched_for_equation_index(self, monkeypatch):
        """``build_opening`` が取得済み artifacts を ``_equations_index_for`` に渡し、
        ``equation_records`` 内部の ``document_run_artifacts`` 再取得を起こさないこと。

        ``opening.document_run_artifacts``（build_opening が直接呼ぶ束縛）と
        ``core.deliberation.refs.document_run_artifacts``（``equation_records`` が
        ``artifacts=None`` のときにだけ内部で呼ぶ、refs モジュール自身のグローバル参照）を
        別々のカウンタを持つフェイクにすげ替える。fix が正しく効いていれば
        ``_equations_index_for`` は取得済み artifacts をそのまま渡すため refs 側は
        1回も呼ばれない。呼び出し例外で検出しない（``build_opening`` は document 単位の
        読み出し失敗を fail-soft に握って ``equations_by_id = {}`` へ縮退させる設計
        （opening.py の docstring/コメント参照）のため、raise させる方式だと
        再取得が起きても静かに握り潰され検出できない）。
        """
        opening_calls = {"count": 0}
        refs_calls = {"count": 0}
        fake_artifacts = {
            "thesis_reconstruction": {},
            "equation_semantics": {"equations": [{"equation_id": "eq1", "label": "E=mc^2"}]},
        }

        def fake_opening_document_run_artifacts(document_id):
            opening_calls["count"] += 1
            return fake_artifacts

        def fake_refs_document_run_artifacts(document_id):
            refs_calls["count"] += 1
            return fake_artifacts

        monkeypatch.setattr(opening, "document_run_artifacts", fake_opening_document_run_artifacts)
        monkeypatch.setattr(refs_module, "document_run_artifacts", fake_refs_document_run_artifacts)
        monkeypatch.setattr(opening, "_document_titles", lambda ids: {})
        monkeypatch.setattr(opening, "_load_graph_nodes", lambda document_id: [])
        monkeypatch.setattr(opening, "_claim_label_index", lambda document_id, artifacts: {})
        monkeypatch.setattr(opening, "_compile_open_assumptions", lambda course_id: [])
        # Phase 3（承認済み素材の配信）の DB 読み出しもここでは無効化する（この test の
        # 対象は artifacts の再取得回数だけ）。
        monkeypatch.setattr(opening, "_load_approved_discussion_seeds", lambda document_id: [])

        result = opening.build_opening("course1", ["doc1"])

        assert opening_calls["count"] == 1
        assert refs_calls["count"] == 0, (
            "equation_records must reuse the artifacts already fetched by build_opening "
            "instead of re-invoking core.deliberation.refs.document_run_artifacts"
        )
        assert result["documents"][0]["document_id"] == "doc1"


# ---------------------------------------------------------------------------
# available フラグ
# ---------------------------------------------------------------------------


class TestIsAvailable:
    def test_false_when_everything_empty(self):
        assert opening._is_available([{"thesis": None, "backbone": []}], []) is False

    def test_true_when_thesis_present(self):
        thesis_dto = {"central_claims": [{"id": "c1", "label": "x"}], "central_equations": [], "support_sections": []}
        assert opening._is_available([{"thesis": thesis_dto, "backbone": []}], []) is True

    def test_true_when_backbone_present(self):
        assert opening._is_available([{"thesis": None, "backbone": [{"node_id": "n1"}]}], []) is True

    def test_true_when_only_fragile_points_present(self):
        assert opening._is_available([{"thesis": None, "backbone": []}], [{"kind": "assumption"}]) is True


# ---------------------------------------------------------------------------
# ルート配線（routes/learning.py の静的検査）
# ---------------------------------------------------------------------------


class TestRouteWiring:
    def test_endpoint_registered(self):
        assert '@router.get("/courses/{course_id}/discuss/opening")' in _LEARNING_SRC
        assert "def get_discussion_opening" in _LEARNING_SRC

    def test_imports_build_opening(self):
        assert "from core.discuss.opening import build_opening" in _LEARNING_SRC

    def test_gate_is_get_course_data_then_404(self):
        fn_src = extract_function_source(_LEARNING_SRC, "get_discussion_opening")
        assert 'get_course_data(current_user["id"], course_id)' in fn_src
        assert 'raise HTTPException(status_code=404, detail="Course not found")' in fn_src

    def test_uses_course_source_document_ids_for_scope(self):
        fn_src = extract_function_source(_LEARNING_SRC, "get_discussion_opening")
        assert "list_course_source_document_ids(course_data)" in fn_src


# ---------------------------------------------------------------------------
# 文言リント（D層ガードレールと同じ禁止語彙が fact_line 生成コードに無いこと）
# ---------------------------------------------------------------------------


class TestNoHypeLanguage:
    def test_no_banned_words_in_opening_module(self):
        assert_source_forbids(_OPENING_SRC, _BANNED_WORDS)
