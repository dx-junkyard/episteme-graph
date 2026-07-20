"""画像パイプライン + ナレッジライブラリ（L層）ガードレールの自動テスト。

設計の正本は docs/features/image_pipeline_knowledge_library_design.md §11
「監査・ガードレール」に列挙された 8 項目を構造的に守る（test_doubt_guardrails.py /
test_next_steps_guardrails.py の流儀 — ソースコードを読み込む構造的検査 + 非DB
ユニットテストを踏襲する）。

1. LLM がライブラリへ書き込む経路が無い（原則2: 昇格は人間の操作のみ）
2. apparatus_semantics の出力が source_backed にならない
3. 例示画像は既定で含まれない・非所有者の含有リクエストは 403
4. 図画像配信API (`routes/admin.py`) が `_ensure_document_viewable` を通る
5. ライブラリに行削除APIが無い（retire のみ）・retired が retrieval に出ない
6. `analyze_images=false` の run に `skipped_by_option` が記録される
7. `backend/core/library/` が FastAPI を import しない
8. retrieval が draft を読まない（凍結版のみ、原則8）

外部サービス（PostgreSQL / MinIO / OpenAI）への実接続は行わない。
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
SRC = ROOT / "src"
for _p in (str(BACKEND), str(BACKEND / "api"), str(SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tests.guardrail_helpers import (  # noqa: E402
    assert_module_tree_does_not_import,
    assert_module_tree_forbids,
    assert_source_does_not_import,
    assert_source_forbids,
)

_DOC_PIPELINE_DIR = BACKEND / "core" / "document_pipeline"
_LIBRARY_DIR = BACKEND / "core" / "library"
_AGENTS_DIR = SRC / "episteme_graph" / "agents"
_APPARATUS_DIR = _AGENTS_DIR / "apparatus_semantics"

_FIGURE_IMAGES_SRC = (_DOC_PIPELINE_DIR / "figure_images.py").read_text(encoding="utf-8")
_ORCHESTRATOR_SRC = (_DOC_PIPELINE_DIR / "orchestrator.py").read_text(encoding="utf-8")
_ROUTE_LIBRARY_SRC = (BACKEND / "api" / "routes" / "library.py").read_text(encoding="utf-8")
_ROUTE_ADMIN_SRC = (BACKEND / "api" / "routes" / "admin.py").read_text(encoding="utf-8")
_SEARCH_SRC = (_LIBRARY_DIR / "search.py").read_text(encoding="utf-8")
_STORE_SRC = (_LIBRARY_DIR / "store.py").read_text(encoding="utf-8")
_VALIDATOR_SRC = (_APPARATUS_DIR / "validator.py").read_text(encoding="utf-8")
_REPAIR_SRC = (_APPARATUS_DIR / "repair.py").read_text(encoding="utf-8")
_AGENT_SRC = (_APPARATUS_DIR / "agent.py").read_text(encoding="utf-8")
_PERSISTENCE_SRC = (_DOC_PIPELINE_DIR / "persistence.py").read_text(encoding="utf-8")
_FIGURE_REANALYSIS_SRC = (BACKEND / "core" / "figure_reanalysis.py").read_text(encoding="utf-8")

_LIBRARY_WRITE_FUNCS = ("create_entry", "update_entry", "freeze_entry", "retire_entry", "restore_entry")

_HAS_FASTAPI = True
try:
    import fastapi  # noqa: F401
except ImportError:  # pragma: no cover
    _HAS_FASTAPI = False

_skip_no_fastapi = pytest.mark.skipif(
    not _HAS_FASTAPI, reason="FastAPI not installed (run inside Docker for full API tests)"
)


def _find_function_signature(src: str, fn_name: str) -> str:
    """``def fn_name(...):`` の宣言全体（複数行にまたがる場合も含む）を返す。"""
    match = re.search(rf"def {fn_name}\([\s\S]*?\):", src)
    assert match, f"{fn_name} not found"
    return match.group(0)


def _extract_function_body(src: str, fn_name: str) -> str:
    """``def fn_name(`` から次のトップレベル ``def`` / ``@router`` の手前までを返す。"""
    start = src.index(f"def {fn_name}(")
    rest = src[start:]
    m = re.search(r"\n(?:def |@router)", rest[1:])
    end = m.start() + 1 if m else len(rest)
    return rest[:end]


def _extract_stage_block(src: str, stage: str) -> str:
    """orchestrator.py の ``xxx_artifact = artifact("stage")`` から
    ``finish_target_stage("stage", ...)`` までのステージ本体を返す。"""
    start_match = re.search(rf'artifact\("{stage}"\)', src)
    assert start_match, f"stage {stage} not found in orchestrator.py"
    end_match = re.search(rf'finish_target_stage\("{stage}"', src)
    assert end_match, f"finish_target_stage for {stage} not found"
    return src[start_match.start():end_match.end()]


# ===========================================================================
# 1. LLM がライブラリへ書き込む経路が無い（原則2）
# ===========================================================================


class TestNoLLMWriteToLibrary:
    def test_no_library_write_function_referenced_in_pipeline_or_agents(self):
        """document_pipeline / agents のソースに書き込み関数名が一切出現しない
        （search_frozen_entries / find_similar_entries の read 系は許可リスト）。"""
        assert_module_tree_forbids((_DOC_PIPELINE_DIR, _AGENTS_DIR), _LIBRARY_WRITE_FUNCS)

    def test_orchestrator_only_imports_read_only_search_helper(self):
        assert "from core.library.search import search_frozen_entries" in _ORCHESTRATOR_SRC
        assert "core.library.store" not in _ORCHESTRATOR_SRC

    def test_library_write_endpoints_require_teacher(self):
        for fn_name in _LIBRARY_WRITE_FUNCS:
            sig = _find_function_signature(_ROUTE_LIBRARY_SRC, fn_name)
            assert "Depends(_require_teacher)" in sig, f"{fn_name} does not require _require_teacher"

    @_skip_no_fastapi
    def test_all_registered_routes_depend_on_require_teacher(self):
        """`/api/admin/library` の全ルートが `_require_teacher` に依存する（wiki 型・TEACHER 以上）。"""
        import routes.library as library_routes

        for route in library_routes.router.routes:
            dependant = getattr(route, "dependant", None)
            assert dependant is not None
            dep_names = {dep.call.__name__ for dep in dependant.dependencies if dep.call}
            assert "_require_teacher" in dep_names, f"{route.path} missing _require_teacher"


# ===========================================================================
# 2. apparatus_semantics の出力が source_backed にならない
# ===========================================================================


class TestApparatusNeverSourceBacked:
    def test_validator_hard_errors_on_source_backed(self):
        assert "apparatus_must_not_be_source_backed" in _VALIDATOR_SRC
        block = re.search(
            r'if record\.source_backing_status == "source_backed":[\s\S]+?severity="error"',
            _VALIDATOR_SRC,
        )
        assert block, "validator does not treat source_backed as a hard error"

    def test_repair_never_assigns_source_backed_literal(self):
        assert 'source_backing_status="source_backed"' not in _REPAIR_SRC
        assert 'source_backing_status = "source_backed"' not in _REPAIR_SRC
        assert '"partially_source_backed"' in _REPAIR_SRC
        assert '"inferred"' in _REPAIR_SRC

    def test_fallback_record_source_backing_status_is_inferred(self):
        assert 'source_backing_status="inferred"' in _REPAIR_SRC

    def test_validator_dynamic_rejects_source_backed_record(self):
        from episteme_graph.agents.apparatus_semantics.schema import ApparatusRecord
        from episteme_graph.agents.apparatus_semantics.validator import ApparatusSemanticsValidator

        record = ApparatusRecord(
            figure_id="f1",
            figure_key="fig_1",
            apparatus_name_candidate="vacuum chamber",
            matched_library_entry_id=None,
            matched_library_version_no=None,
            match_status="novel",
            source_backing_status="source_backed",
        )
        issues = ApparatusSemanticsValidator().validate_record(record)
        assert any(
            i.rule_id == "apparatus_must_not_be_source_backed" and i.severity == "error"
            for i in issues
        )

    def test_fallback_record_is_never_source_backed(self):
        from episteme_graph.agents.apparatus_semantics.repair import _fallback_record
        from episteme_graph.agents.apparatus_semantics.schema import FigureImageInput

        figure = FigureImageInput(
            figure_id="f1", figure_key="fig_1", figure_label=None,
            caption_text="Figure 1: apparatus", image_bytes=None,
        )
        record = _fallback_record(figure, "image_unavailable")
        assert record.source_backing_status != "source_backed"
        assert record.review_status == "review_required"

    def test_parsed_record_from_raw_llm_output_never_source_backed(self):
        """LLM が raw JSON に source_backed を書いても _parse_record は無視する（derived only）。"""
        from episteme_graph.agents.apparatus_semantics.repair import _parse_record
        from episteme_graph.agents.apparatus_semantics.schema import FigureImageInput

        figure = FigureImageInput(
            figure_id="f1", figure_key="fig_1", figure_label=None,
            caption_text="Figure 1: apparatus", image_bytes=b"fake",
        )
        raw = {
            "apparatus_name_candidate": "vacuum chamber",
            "match_status": "novel",
            "evidence_quote": "Figure 1: apparatus",
            "source_backing_status": "source_backed",  # LLM が主張しても無視される
            "review_status": "confirmed",  # 同上
        }
        record = _parse_record(raw, figure, candidates=[])
        assert record.source_backing_status != "source_backed"
        assert record.review_status == "review_required"


# ===========================================================================
# 3. 例示画像は既定で含まれない・非所有者は 403
# ===========================================================================


@_skip_no_fastapi
class TestExemplarImagesGated:
    def test_create_entry_default_exemplar_images_empty(self):
        import routes.library as library_routes

        req = library_routes.LibraryEntryCreateRequest(domain_key="d", entry_type="apparatus", name="n")
        assert req.exemplar_images == []

    def test_authorize_exemplar_images_empty_input_returns_empty(self):
        import routes.library as library_routes

        assert library_routes._authorize_exemplar_images([], "u1") == []
        assert library_routes._authorize_exemplar_images(None, "u1") == []

    def test_authorize_exemplar_images_requires_ownership(self, monkeypatch):
        import routes.library as library_routes
        from fastapi import HTTPException

        monkeypatch.setattr(library_routes.services, "user_owns_document", lambda uid, doc: False)
        img = library_routes.ExemplarImageIn(figure_id="f1", minio_key="k1", source_document_id="d1")
        with pytest.raises(HTTPException) as exc_info:
            library_routes._authorize_exemplar_images([img], "u1")
        assert exc_info.value.status_code == 403

    def test_authorize_exemplar_images_missing_source_document_id_rejected(self, monkeypatch):
        import routes.library as library_routes
        from fastapi import HTTPException

        monkeypatch.setattr(library_routes.services, "user_owns_document", lambda uid, doc: True)
        img = library_routes.ExemplarImageIn(figure_id="f1", minio_key="k1", source_document_id="")
        with pytest.raises(HTTPException) as exc_info:
            library_routes._authorize_exemplar_images([img], "u1")
        assert exc_info.value.status_code == 403

    def test_authorize_exemplar_images_owner_is_approved_server_side(self, monkeypatch):
        import core.document_pipeline.figure_images as figure_images
        import routes.library as library_routes

        monkeypatch.setattr(library_routes.services, "user_owns_document", lambda uid, doc: True)
        monkeypatch.setattr(
            figure_images,
            "load_document_figures",
            lambda doc_id: [{"id": "f1", "minio_key": "figures/d1/f1.png"}],
        )
        img = library_routes.ExemplarImageIn(
            figure_id="f1", minio_key="client-supplied-key", source_document_id="d1"
        )
        result = library_routes._authorize_exemplar_images([img], "u1")
        assert len(result) == 1
        assert result[0]["approved_by"] == "u1"
        assert result[0]["approved_at"]
        assert result[0]["figure_id"] == "f1"
        # minio_key はクライアント値ではなく document_figures の実在行から解決される
        assert result[0]["minio_key"] == "figures/d1/f1.png"

    def test_authorize_exemplar_images_rejects_foreign_figure(self, monkeypatch):
        """所有 document A を騙って他 document の figure_id を混ぜても 403（provenance 検証）。"""
        import core.document_pipeline.figure_images as figure_images
        import routes.library as library_routes
        from fastapi import HTTPException

        monkeypatch.setattr(library_routes.services, "user_owns_document", lambda uid, doc: True)
        # 所有 document "d1" の抽出画像には f_other は存在しない
        monkeypatch.setattr(
            figure_images,
            "load_document_figures",
            lambda doc_id: [{"id": "f1", "minio_key": "figures/d1/f1.png"}],
        )
        img = library_routes.ExemplarImageIn(figure_id="f_other", source_document_id="d1")
        with pytest.raises(HTTPException) as exc_info:
            library_routes._authorize_exemplar_images([img], "u1")
        assert exc_info.value.status_code == 403

    def test_client_supplied_approved_fields_are_ignored(self):
        """ExemplarImageIn には approved_by/approved_at フィールドが無いため、
        クライアントが送っても Pydantic により破棄される。"""
        import routes.library as library_routes

        img = library_routes.ExemplarImageIn.model_validate({
            "figure_id": "f1", "minio_key": "k1", "source_document_id": "d1",
            "approved_by": "attacker", "approved_at": "2020-01-01",
        })
        assert not hasattr(img, "approved_by")
        assert not hasattr(img, "approved_at")


# ===========================================================================
# 4. 図画像配信APIが _ensure_document_viewable を通る
# ===========================================================================


class TestFigureApiUsesDocumentViewableGate:
    def test_list_document_figures_calls_ensure_viewable(self):
        body = _extract_function_body(_ROUTE_ADMIN_SRC, "list_document_figures")
        assert "_ensure_document_viewable(document_id, current_user)" in body

    def test_get_document_figure_image_calls_ensure_viewable(self):
        body = _extract_function_body(_ROUTE_ADMIN_SRC, "get_document_figure_image")
        assert "_ensure_document_viewable(document_id, current_user)" in body

    def test_figures_endpoints_require_teacher(self):
        for fn_name in ("list_document_figures", "get_document_figure_image"):
            sig = _find_function_signature(_ROUTE_ADMIN_SRC, fn_name)
            assert "Depends(_require_teacher)" in sig

    def test_admin_imports_ensure_document_viewable_from_theory_components(self):
        assert "from routes.theory_components import _ensure_document_viewable" in _ROUTE_ADMIN_SRC

    def test_list_document_figures_passes_through_inner_labels_and_bbox(self):
        """Phase 2（レビューUX）: 図の bbox / inner_labels をレスポンスにそのまま
        passthrough する（別タスクが load_document_figures に実装する契約を信じ、
        ここでは .get() でのデフォルト付与のみを行う）。"""
        body = _extract_function_body(_ROUTE_ADMIN_SRC, "list_document_figures")
        assert '"bbox": row.get("bbox")' in body
        assert '"inner_labels": row.get("inner_labels") or []' in body

    def test_list_document_figures_passes_through_apparatus_part_fields(self):
        """apparatus_semantics の parts に Phase 2 で追加される
        label_ref/expanded_name/bbox/evidence_quote/reason/confidence を
        .get() 経由で passthrough する（旧 artifact にキーが無くても落ちない）。"""
        body = _extract_function_body(_ROUTE_ADMIN_SRC, "list_document_figures")
        for key in ("label_ref", "expanded_name", "bbox", "evidence_quote", "reason", "confidence"):
            assert f'"{key}": p.get("{key}"' in body, f"parts passthrough missing key {key}"


# ===========================================================================
# 5. ライブラリに行削除APIが無い・retired が retrieval に出ない
# ===========================================================================


class TestNoDeleteAndRetiredExcluded:
    def test_no_delete_route_in_library_routes_source(self):
        assert_source_forbids(_ROUTE_LIBRARY_SRC, ["@router.delete"], context="routes/library.py")

    @_skip_no_fastapi
    def test_no_delete_route_registered_dynamically(self):
        import routes.library as library_routes

        methods: set[str] = set()
        for route in library_routes.router.routes:
            methods.update(getattr(route, "methods", set()) or set())
        assert "DELETE" not in methods

    def test_store_never_deletes_rows(self):
        assert_source_forbids(
            _STORE_SRC,
            ["DELETE FROM library_entries", "DELETE FROM library_entry_versions"],
            context="library/store.py",
        )

    def test_retire_and_restore_are_status_transitions_not_deletes(self):
        assert "def retire_entry" in _STORE_SRC
        assert "def restore_entry" in _STORE_SRC
        assert "STATUS_RETIRED" in _STORE_SRC
        assert "STATUS_ACTIVE" in _STORE_SRC

    def test_list_entries_default_excludes_retired(self):
        assert "status = 'active'" in _STORE_SRC

    def test_search_frozen_entries_excludes_retired(self):
        assert "e.status = 'active'" in _SEARCH_SRC


# ===========================================================================
# 6. analyze_images=false で skipped_by_option 記録
# ===========================================================================


class TestAnalyzeImagesSkippedByOption:
    def test_orchestrator_records_skipped_by_option_in_source(self):
        block = _extract_stage_block(_ORCHESTRATOR_SRC, "apparatus_semantics")
        assert "skipped_by_option" in block
        assert 'effective_options.get("analyze_images")' in block

    def test_figure_image_extraction_stage_is_unconditional(self):
        """常時実行ステージ（§3-3）: analyze_images に条件分岐しない。"""
        block = _extract_stage_block(_ORCHESTRATOR_SRC, "figure_image_extraction")
        assert "effective_options" not in block

    def test_pipeline_run_with_analyze_images_false_marks_apparatus_stage_skipped(self):
        """run_document_pipeline を軽量モックで走らせ、stage_outputs の
        apparatus_semantics artifact に skipped_by_option: True が入ることを確認する
        （既存 test_document_pipeline.py のオーケストレータ全段モックの流儀を再利用）。
        """
        from core.document_pipeline import orchestrator

        class _Block:
            def __init__(self, block_id, page, order, text, block_type="body_paragraph", section_id=None, bbox=None):
                self.block_id = block_id
                self.page = page
                self.order = order
                self.text = text
                self.block_type = block_type
                self.section_id = section_id
                self.bbox = bbox

        class _Section:
            def __init__(self, section_id, title, order=1, page_start=1):
                self.section_id = section_id
                self.title = title
                self.order = order
                self.page_start = page_start

        @dataclass
        class _Result:
            document_id: str = "doc"
            nodes: list = field(default_factory=list)
            edges: list = field(default_factory=list)
            components: list = field(default_factory=list)
            qualified_spans: list = field(default_factory=list)
            equations: list = field(default_factory=list)
            sections: list = field(default_factory=lambda: [_Section("s1", "Intro", order=1, page_start=1)])
            blocks: list = field(default_factory=lambda: [_Block("b1", 1, 0, "Hello world body.", section_id="s1")])
            review_notes: list = field(default_factory=list)

        @dataclass
        class _CourseMappingResult:
            document_id: str = "doc"
            cartridge_id: str | None = None
            topics: list = field(default_factory=list)
            validation_issues: list = field(default_factory=list)

        @dataclass
        class _ComponentGraphResult:
            document_id: str = "doc"
            graph_schema_version: str = "0.1.0"
            cartridge_id: str | None = None
            nodes: list = field(default_factory=list)
            edges: list = field(default_factory=list)
            review_notes: list = field(default_factory=list)
            confidence: float = 0.9
            validation_issues: list = field(default_factory=list)

            def to_dict(self):
                return {
                    "nodes": [], "edges": [], "document_id": self.document_id,
                    "graph_schema_version": self.graph_schema_version,
                    "cartridge_id": self.cartridge_id, "review_notes": self.review_notes,
                    "confidence": self.confidence, "validation_issues": [],
                }

            def to_graph_payload(self):
                return {"graph_schema_version": "0.1.0", "nodes": [], "edges": []}

        class _MockAgent:
            def __init__(self, returns):
                self._returns = returns

            def run(self, *args, **kwargs):
                return self._returns

        agents = {
            "DocumentStructureAgent": _MockAgent(_Result()),
            "PaperSkeletonAgent": _MockAgent(_Result()),
            "RhetoricalRoleAgent": _MockAgent(_Result()),
            "ClaimQualificationAgent": _MockAgent(_Result()),
            "EquationSemanticsAgent": _MockAgent(_Result()),
            "ThesisReconstructionAgent": _MockAgent(_Result()),
            "DSLLinkingAgent": _MockAgent(_Result()),
            "ComponentAssemblyAgent": _MockAgent(_Result()),
            "ComponentGraphAgent": _MockAgent(_ComponentGraphResult()),
            "CourseMappingAgent": _MockAgent(_CourseMappingResult()),
        }

        saved_artifacts: dict[str, dict] = {}

        def fake_upsert(*, run_id=None, document_id, material_id, cartridge_id=None,
                        status="running", current_stage="save_pdf", stage_outputs=None,
                        error_message=None, options=None):
            if stage_outputs and "_artifacts" in stage_outputs:
                saved_artifacts.update(stage_outputs["_artifacts"])
            return run_id or "run-opt"

        fake_persistence = {
            "persist_source_chunks": MagicMock(return_value=[
                {"chunk_id": "c1", "chunk_index": 0, "section_id": "s1",
                 "block_ids": ["b1"], "page_start": 1, "page_end": 1, "text": "Hello"}
            ]),
            "persist_qualified_claims": MagicMock(return_value=[]),
            "persist_components": MagicMock(return_value={}),
            "persist_component_graph": MagicMock(return_value="graph-1"),
            "persist_document_embedding": MagicMock(return_value="emb-1"),
            "upsert_analysis_run": fake_upsert,
        }

        with patch.multiple(orchestrator, **fake_persistence):
            result = orchestrator.run_document_pipeline(
                pdf_bytes=b"%PDF-1.4 fake bytes",
                document_id="doc-opt",
                material_id="mat-opt",
                filename="paper.pdf",
                cartridge_id="particle_physics",
                agents=agents,
                options={"analyze_images": False},
            )

        assert result.final_stage == "completed"
        assert saved_artifacts.get("apparatus_semantics") == {"skipped_by_option": True}


# ===========================================================================
# 7. backend/core/library/ が FastAPI を import しない
# ===========================================================================


class TestLibraryCoreHasNoFastAPI:
    def test_no_fastapi_import_in_core_library(self):
        assert_module_tree_does_not_import(_LIBRARY_DIR, ["fastapi"])

    def test_no_fastapi_import_in_figure_images_stage(self):
        """figure_image_extraction (非LLM決定論的ステージ) も core/ 共通ルールに従う。"""
        assert_source_does_not_import(_FIGURE_IMAGES_SRC, ["fastapi"], context="figure_images.py")


# ===========================================================================
# 8. retrieval が draft を読まない（凍結版のみ）
# ===========================================================================


class TestRetrievalDoesNotReadDraft:
    def test_search_frozen_entries_selects_from_versions_table(self):
        assert "FROM library_entry_versions" in _SEARCH_SRC

    def test_search_frozen_entries_picks_latest_version_only(self):
        assert "row_number() OVER" in _SEARCH_SRC
        assert "ORDER BY v.version_no DESC" in _SEARCH_SRC
        assert "embedding IS NOT NULL" in _SEARCH_SRC

    def test_search_frozen_entries_restores_body_from_frozen_content_not_draft(self):
        """凍結版の name/summary/body は ``library_entry_versions.content`` から
        復元するべきで、``library_entries``（draft）の同名列を検索結果の本文
        ソースにしてはならない（§6-5 原則8: パイプラインは draft を解析に使わない）。
        """
        block_match = re.search(r"def _embedding_search[\s\S]+?\n\ndef ", _SEARCH_SRC)
        assert block_match, "_embedding_search not found in search.py"
        block = block_match.group(0)
        assert "e.body" not in block, (
            "search_frozen_entries selects library_entries.body (mutable draft) as the "
            "retrieval body source instead of restoring it from the frozen "
            "library_entry_versions.content snapshot (§6-5 原則8 violation)"
        )
        assert "e.summary" not in block, (
            "search_frozen_entries selects library_entries.summary (mutable draft) as the "
            "retrieval summary source instead of restoring it from the frozen "
            "library_entry_versions.content snapshot (§6-5 原則8 violation)"
        )


# ===========================================================================
# 9. 教員指示付き図再解析（guided_figure_reanalysis_design.md §1 不変条項）
# ===========================================================================

_GUIDED_FIELDS = (
    "guidance_text",
    "focus_bbox_rel",
    "focus_image_bytes",
    "focus_label_texts",
)


class TestGuidedFigureReanalysisGuardrails:
    def test_batch_pipeline_never_sets_guidance_fields(self):
        """GF7: バッチパイプライン非改変 — orchestrator.py（analyze_images 経由の
        一括解析）も persistence.py も guidance フィールドを一切設定しない。
        これらは deliberation の単図再解析（core/figure_reanalysis.py）専用。"""
        assert_source_forbids(_ORCHESTRATOR_SRC, _GUIDED_FIELDS, context="orchestrator.py")
        assert_source_forbids(_PERSISTENCE_SRC, _GUIDED_FIELDS, context="persistence.py")

    def test_figure_reanalysis_never_assigns_review_or_backing_status(self):
        """GF1: 指示は注意誘導であって確定ではない。core/figure_reanalysis.py が
        review_status / source_backing_status を書き換える代入を一切持たない
        （確定は既存の annotation commit フローのみ。agent 側の決定論的導出も
        文字列リテラル代入ではなく既存メカニズムのまま — このガードレールは
        guided re-analysis 追加によって新たな上書き経路が生まれていないことを
        構造的に検査する）。"""
        assert_source_forbids(
            _FIGURE_REANALYSIS_SRC,
            ["review_status =", "review_status=", "source_backing_status =", "source_backing_status="],
            context="figure_reanalysis.py",
        )

    def test_figure_reanalysis_never_writes_part_bbox_directly(self):
        """GF4: 決定論的グラウンディング維持 — 部品の bbox / expanded_name は
        従来どおり label_ref → inner_labels 突合（agent.py の
        _attach_label_grounding）のみが導出する。core/figure_reanalysis.py は
        record.parts や part.bbox に一切触れず、focus_bbox を部品座標として
        直接採用する経路を持たない。"""
        assert_source_forbids(
            _FIGURE_REANALYSIS_SRC,
            [".parts[", "part.bbox", "parts=[", '"bbox":'],
            context="figure_reanalysis.py",
        )

    def test_attach_label_grounding_still_runs_unconditionally_in_agent(self):
        """GF4 (agent.py 側の構造維持確認): guided/unguided で分岐せず、
        image 欠落フォールバック・LLM失敗フォールバック・通常パスの
        いずれでも record 確定の直前に _attach_label_grounding が呼ばれる
        構造が維持されていること（guidance 追加のために弱められていないか）。"""
        assert _AGENT_SRC.count("_attach_label_grounding(") >= 3

    def test_figure_reanalysis_validates_guidance_before_any_figure_lookup(self):
        """GF6/§5: guidance 検証（_normalize_guidance）は load_document_figures
        より前で行う — 不正な指示は課金境界はおろかストレージ/DB アクセスの
        前に弾かれる（構造的検査: ソース中の出現順）。"""
        normalize_pos = _FIGURE_REANALYSIS_SRC.index("norm_guidance = _normalize_guidance(guidance)")
        load_pos = _FIGURE_REANALYSIS_SRC.index("rows = load_document_figures(document_id)")
        assert normalize_pos < load_pos

    def test_no_new_cost_gate_env_var_introduced(self):
        """GF6: 新しい環境変数・上限を作らない — CostGate 呼び出しは既存の
        _consume_budget(figure_id, user_id) のみで、guidance 由来の別キー
        （例: guidance を混ぜた session_key）を作らない。"""
        assert "_consume_budget(figure_id, created_by)" in _FIGURE_REANALYSIS_SRC
        assert "guidance" not in _extract_function_body(_FIGURE_REANALYSIS_SRC, "_consume_budget")


# ===========================================================================
# 9. 凍結の同時実行（UNIQUE 競合 → 409）/ retire・restore 監査の遷移前 status
# ===========================================================================


@_skip_no_fastapi
class TestFreezeConcurrencyMapsTo409:
    """`library_entry_versions` の UNIQUE(entry_id, version_no) 競合が素の 500 で
    漏れず、draft 楽観ロック衝突と同じ流儀の 409 になる。"""

    def test_freeze_integrity_error_returns_409(self, monkeypatch):
        import routes.library as library_routes
        from fastapi import HTTPException
        from sqlalchemy.exc import IntegrityError

        def _conflict(entry_id, *, published_by=None, note=None, **_kwargs):
            raise IntegrityError(
                "INSERT INTO library_entry_versions ...",
                {},
                Exception("duplicate key value violates unique constraint"),
            )

        monkeypatch.setattr(library_routes.library_store, "freeze_entry", _conflict)
        with pytest.raises(HTTPException) as exc_info:
            library_routes.freeze_entry(
                "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
                library_routes.FreezeRequest(),
                {"id": "u1"},
            )
        assert exc_info.value.status_code == 409
        assert "再試行" in str(exc_info.value.detail)

    def test_freeze_not_found_still_maps_to_404(self, monkeypatch):
        import routes.library as library_routes
        from fastapi import HTTPException
        from core.library.store import LibraryNotFoundError

        def _missing(entry_id, *, published_by=None, note=None, **_kwargs):
            raise LibraryNotFoundError(f"library entry not found: {entry_id}")

        monkeypatch.setattr(library_routes.library_store, "freeze_entry", _missing)
        with pytest.raises(HTTPException) as exc_info:
            library_routes.freeze_entry(
                "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
                library_routes.FreezeRequest(),
                {"id": "u1"},
            )
        assert exc_info.value.status_code == 404


@_skip_no_fastapi
class TestRetiredEntryIsReadOnlyOverApi:
    """N29: retired エントリへの draft 更新・凍結は 409（事実文つき）。
    restore は従来どおり（TestRetireRestoreAuditUsesActualPreviousStatus 参照）。"""

    def test_update_retired_maps_to_409_with_factual_detail(self, monkeypatch):
        import routes.library as library_routes
        from fastapi import HTTPException
        from core.library.store import LibraryRetiredError

        monkeypatch.setattr(
            library_routes.library_store,
            "get_entry",
            lambda entry_id: {"id": entry_id, "status": "retired", "exemplar_images": []},
        )

        def _retired(entry_id, *, expected_revision, updated_by=None, **fields):
            raise LibraryRetiredError("retired のエントリは編集できません。復元してから編集してください")

        monkeypatch.setattr(library_routes.library_store, "update_entry", _retired)
        payload = library_routes.LibraryEntryUpdateRequest(expected_revision=1, name="x")
        with pytest.raises(HTTPException) as exc_info:
            library_routes.update_entry("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee", payload, {"id": "u1"})
        assert exc_info.value.status_code == 409
        assert "復元" in str(exc_info.value.detail)

    def test_freeze_retired_maps_to_409_with_factual_detail(self, monkeypatch):
        import routes.library as library_routes
        from fastapi import HTTPException
        from core.library.store import LibraryRetiredError

        def _retired(entry_id, *, published_by=None, note=None, **_kwargs):
            raise LibraryRetiredError("retired のエントリは凍結できません。復元してから凍結してください")

        monkeypatch.setattr(library_routes.library_store, "freeze_entry", _retired)
        with pytest.raises(HTTPException) as exc_info:
            library_routes.freeze_entry(
                "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
                library_routes.FreezeRequest(),
                {"id": "u1"},
            )
        assert exc_info.value.status_code == 409
        assert "復元" in str(exc_info.value.detail)

    def test_store_blocks_retired_before_revision_lock(self):
        """update_entry は楽観ロックに進む前に retired を検査する（構造検査）。
        retired なのに楽観ロック衝突（「他の教員が更新しました」）の誤った文言を
        返さないための順序保証。"""
        update_src = _STORE_SRC[_STORE_SRC.index("def update_entry("):]
        update_src = update_src[: update_src.index("\ndef freeze_entry(")]
        retired_pos = update_src.index("raise LibraryRetiredError")
        lock_pos = update_src.index("update_with_revision_lock")
        assert retired_pos < lock_pos


@_skip_no_fastapi
class TestRetireRestoreAuditUsesActualPreviousStatus:
    """retire/restore の監査 old_status はハードコード（常に active / 常に retired）
    ではなく、store が返す遷移前の実状態を記帳する（冪等呼び出しでも事実と一致）。"""

    def _capture_audit(self, monkeypatch):
        import routes.library as library_routes

        events: list[tuple] = []
        monkeypatch.setattr(
            library_routes.services,
            "record_review_event",
            lambda *args, **kwargs: events.append(args),
        )
        return library_routes, events

    def test_retire_records_actual_previous_status(self, monkeypatch):
        library_routes, events = self._capture_audit(monkeypatch)
        entry = {"id": "e1", "domain_key": "particle_physics", "status": "retired"}
        # 既に retired だった冪等呼び出し（遷移前も retired）を模す。
        monkeypatch.setattr(
            library_routes.library_store,
            "retire_entry",
            lambda entry_id, updated_by=None: (entry, "retired"),
        )

        result = library_routes.retire_entry("e1", {"id": "u1"})
        assert result == entry  # レスポンスは entry dict のまま（tuple を漏らさない）
        assert len(events) == 1
        _entity_type, _entity_id, old_status, new_status = events[0][:4]
        assert old_status == "retired"
        assert new_status == "retired"

    def test_restore_records_actual_previous_status(self, monkeypatch):
        library_routes, events = self._capture_audit(monkeypatch)
        entry = {"id": "e1", "domain_key": "particle_physics", "status": "active"}
        # 既に active だった冪等呼び出し（遷移前も active）を模す。
        monkeypatch.setattr(
            library_routes.library_store,
            "restore_entry",
            lambda entry_id, updated_by=None: (entry, "active"),
        )

        result = library_routes.restore_entry("e1", {"id": "u1"})
        assert result == entry
        assert len(events) == 1
        _entity_type, _entity_id, old_status, new_status = events[0][:4]
        assert old_status == "active"
        assert new_status == "active"

    def test_normal_transitions_record_expected_pair(self, monkeypatch):
        library_routes, events = self._capture_audit(monkeypatch)
        monkeypatch.setattr(
            library_routes.library_store,
            "retire_entry",
            lambda entry_id, updated_by=None: (
                {"id": "e1", "domain_key": "d", "status": "retired"}, "active",
            ),
        )
        monkeypatch.setattr(
            library_routes.library_store,
            "restore_entry",
            lambda entry_id, updated_by=None: (
                {"id": "e1", "domain_key": "d", "status": "active"}, "retired",
            ),
        )
        library_routes.retire_entry("e1", {"id": "u1"})
        library_routes.restore_entry("e1", {"id": "u1"})
        assert [(e[2], e[3]) for e in events] == [
            ("active", "retired"), ("retired", "active"),
        ]
