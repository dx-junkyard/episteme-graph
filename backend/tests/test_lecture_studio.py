"""Tests for Lecture Script Studio (Issue #70).

スキーマのバリデーションとコアロジックの単体テストを行う。
"""

from __future__ import annotations

import json
import sys
import os
from unittest.mock import MagicMock, patch

# Ensure api/ is on the path for schema/route imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))


# ---------------------------------------------------------------------------
# Schema validation tests
# ---------------------------------------------------------------------------


class TestLectureStudioSchemas:
    """Issue #70 で追加した Pydantic スキーマのバリデーション。"""

    def test_lecture_script_chunk_out_defaults(self):
        from schemas import LectureScriptChunkOut

        chunk = LectureScriptChunkOut(chunk_id="abc", chunk_index=0, text="hello")
        assert chunk.spoken_text == ""
        assert chunk.formulas == []
        assert chunk.status == "ungenerated"

    def test_lecture_script_chunk_out_with_data(self):
        from schemas import LectureScriptChunkOut, LectureFormulaItem

        formula = LectureFormulaItem(id="formula_0", latex="E=mc^2", spoken="Eイコールmcの二乗")
        chunk = LectureScriptChunkOut(
            chunk_id="abc",
            chunk_index=1,
            text="source",
            spoken_text="spoken version",
            formulas=[formula],
            status="edited",
        )
        assert chunk.spoken_text == "spoken version"
        assert chunk.status == "edited"
        assert len(chunk.formulas) == 1
        assert chunk.formulas[0].latex == "E=mc^2"

    def test_lecture_script_generate_request_defaults(self):
        from schemas import LectureScriptGenerateRequest

        req = LectureScriptGenerateRequest()
        assert req.override is False

    def test_lecture_script_generate_request_override(self):
        from schemas import LectureScriptGenerateRequest

        req = LectureScriptGenerateRequest(override=True)
        assert req.override is True

    def test_lecture_script_generate_request_auto_audio_default(self):
        """Issue #139: auto_audio デフォルトは False。"""
        from schemas import LectureScriptGenerateRequest

        req = LectureScriptGenerateRequest()
        assert req.auto_audio is False

    def test_lecture_script_generate_request_auto_audio_true(self):
        """Issue #139: auto_audio=True でパイプライン連鎖モードを指定可能。"""
        from schemas import LectureScriptGenerateRequest

        req = LectureScriptGenerateRequest(override=True, auto_audio=True)
        assert req.auto_audio is True
        assert req.override is True

    def test_lecture_script_generate_response(self):
        from schemas import LectureScriptGenerateResponse

        resp = LectureScriptGenerateResponse(
            course_id="c1",
            total_chunks=10,
            generated=7,
            skipped=3,
        )
        assert resp.course_id == "c1"
        assert resp.total_chunks == 10
        assert resp.generated == 7
        assert resp.skipped == 3
        assert resp.chunks == []

    def test_lecture_script_generate_start_response(self):
        from schemas import LectureScriptGenerateStartResponse

        resp = LectureScriptGenerateStartResponse(
            task_id="abc123",
            course_id="c1",
            total_chunks=20,
        )
        assert resp.task_id == "abc123"
        assert resp.course_id == "c1"
        assert resp.total_chunks == 20
        assert resp.status == "pending"

    def test_lecture_script_generate_start_response_custom_status(self):
        from schemas import LectureScriptGenerateStartResponse

        resp = LectureScriptGenerateStartResponse(
            task_id="xyz",
            course_id="c2",
            total_chunks=5,
            status="processing",
        )
        assert resp.status == "processing"

    def test_lecture_script_save_request(self):
        from schemas import LectureScriptSaveRequest

        req = LectureScriptSaveRequest(spoken_text="edited text")
        assert req.spoken_text == "edited text"
        assert req.formulas == []

    def test_lecture_script_save_response(self):
        from schemas import LectureScriptSaveResponse

        resp = LectureScriptSaveResponse(chunk_id="chunk1")
        assert resp.status == "edited"

    def test_lecture_script_rewrite_request(self):
        from schemas import LectureScriptRewriteRequest

        req = LectureScriptRewriteRequest(prompt="前提知識を追加して")
        assert req.prompt == "前提知識を追加して"
        assert req.narration_persona is None

    def test_lecture_script_rewrite_request_with_persona(self):
        from schemas import LectureScriptRewriteRequest

        req = LectureScriptRewriteRequest(prompt="解説モードを反映して", narration_persona="general_friendly")
        assert req.narration_persona == "general_friendly"

    def test_lecture_script_rewrite_response(self):
        from schemas import LectureScriptRewriteResponse, LectureFormulaItem

        resp = LectureScriptRewriteResponse(
            chunk_id="c1",
            spoken_text="rewritten",
            formulas=[LectureFormulaItem(id="f0", latex="x^2", spoken="xの二乗")],
        )
        assert resp.spoken_text == "rewritten"
        assert len(resp.formulas) == 1

    def test_lecture_audio_generate_response(self):
        from schemas import LectureAudioGenerateResponse

        resp = LectureAudioGenerateResponse(
            course_id="c1",
            total_chunks=5,
            generated=3,
            skipped=1,
            errors=1,
        )
        assert resp.generated == 3
        assert resp.errors == 1

    def test_lecture_audio_generate_response_defaults(self):
        from schemas import LectureAudioGenerateResponse

        resp = LectureAudioGenerateResponse(course_id="c1")
        assert resp.total_chunks == 0
        assert resp.generated == 0
        assert resp.skipped == 0
        assert resp.errors == 0

    def test_lecture_studio_settings_defaults(self):
        from schemas import LectureStudioSettings

        settings = LectureStudioSettings()
        assert settings.narration_persona == ""
        assert settings.response_persona == ""

    def test_lecture_studio_settings_with_personas(self):
        from schemas import LectureStudioSettings

        settings = LectureStudioSettings(narration_persona="expert_friendly", response_persona="general_formal")
        assert settings.narration_persona == "expert_friendly"
        assert settings.response_persona == "general_formal"

    def test_learning_check_question_response_details(self):
        from schemas import LearningCheckQuestionResponse

        resp = LearningCheckQuestionResponse(
            passed=False,
            feedback="不足があります。",
            model_answer="模範解答",
            answer_requirements=["要素A"],
            explanation="要素Aが必要な理由。",
        )
        assert resp.answer_requirements == ["要素A"]
        assert resp.explanation == "要素Aが必要な理由。"


class TestLectureStudioModeUI:
    """原稿スタジオの解説モード設定UIの静的テスト。"""

    def test_settings_button_exists(self):
        from pathlib import Path

        html = (Path(__file__).resolve().parents[2] / "frontend" / "public" / "admin.html").read_text(encoding="utf-8")
        assert 'id="ls-settings-btn"' in html

    def test_mode_settings_logic_exists(self):
        from pathlib import Path

        # Tier 3-17b: 原稿スタジオの JS は admin-lecture-studio.js に分離された。
        js = (Path(__file__).resolve().parents[2] / "frontend" / "public" / "js" / "admin-lecture-studio.js").read_text(encoding="utf-8")
        assert "lsOpenSettingsModal" in js
        assert "/lecture-studio/settings" in js
        assert "サイエンス・コミュニケーター" in js
        assert "学会発表／査読者" in js

    def test_course_topic_selection_does_not_mask_graph_view(self):
        """Course topic detail rendering must not preempt the theory graph tabs."""
        from pathlib import Path

        js = (Path(__file__).resolve().parents[2] / "frontend" / "public" / "js" / "admin-lecture-studio.js").read_text(encoding="utf-8")
        assert 'selectedScope.type === "course_topic" && !lsIsTheoryGraphView(currentView)' in js
        assert "lsScopeHasDocumentContext" in js
        assert 'lsState.view === "graph"' in js

    def test_theory_graph_top_tab_opens_graph_view(self):
        """The document-structure theory graph entry should open the graph, not an empty claims view."""
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        html = (root / "frontend" / "public" / "admin.html").read_text(encoding="utf-8")
        js = (root / "frontend" / "public" / "js" / "admin-lecture-studio.js").read_text(encoding="utf-8")
        assert '<button class="ls-work-tab" data-ls-view="graph" hidden data-ui-anchor="lecture-studio.work-tab-graph">理論グラフ</button>' in html
        assert 'if (lsState.leftTab === "document") return ["edit", "structure", "graph"];' in js
        assert 'var topView = lsIsTheoryGraphView(view) ? "graph" : view;' in js

    def test_course_draft_check_question_detail_fields_exist(self):
        from pathlib import Path

        js = (Path(__file__).resolve().parents[2] / "frontend" / "public" / "js" / "admin-lecture-studio.js").read_text(encoding="utf-8")
        assert "ls-course-check-model-answer" in js
        assert "ls-course-check-requirements" in js
        assert "ls-course-check-explanation" in js
        assert "lsCollectCheckQuestions" in js

    def test_material_pipeline_menu_closes_on_outside_click(self):
        from pathlib import Path

        js = (Path(__file__).resolve().parents[2] / "frontend" / "public" / "js" / "admin.js").read_text(encoding="utf-8")
        assert "initMaterialPipelineOutsideClick" in js
        assert 'closest(".material-pipeline-menu")' in js
        assert "closeMaterialPipelineMenus();" in js

    def test_full_document_pipeline_run_does_not_resume_failed_artifacts(self):
        from pathlib import Path

        # Tier 3-17a: document-pipeline の実行ロジックは lecture_studio パッケージの
        # pipeline.py に移設された。
        source = (
            Path(__file__).resolve().parents[1] / "api" / "routes" / "lecture_studio" / "pipeline.py"
        ).read_text(encoding="utf-8")
        assert "resume=target_stage is not None" in source


class TestMaterialPipelineSourceLoading:
    def test_load_pipeline_source_detects_tex_archive_without_filename_suffix(self, monkeypatch):
        # Tier 3-17a: _load_pipeline_source / get_storage_client は
        # routes.lecture_studio.pipeline モジュールで定義されている。
        from routes.lecture_studio import pipeline as lecture_studio_pipeline

        calls = []

        class FakeStorage:
            def get_object(self, bucket, object_name):
                calls.append((bucket, object_name))
                if object_name == "uploads/mat-1.tar.gz":
                    return b"tex-archive"
                raise FileNotFoundError(object_name)

        monkeypatch.setattr(lecture_studio_pipeline, "get_storage_client", lambda: FakeStorage())

        data, source_kind = lecture_studio_pipeline._load_pipeline_source("mat-1", "arXiv-2407.01221v2")

        assert data == b"tex-archive"
        assert source_kind == "tex_archive"
        assert calls[0] == ("raw-papers", "uploads/mat-1.tar.gz")

    def test_load_pipeline_source_keeps_pdf_when_pdf_exists(self, monkeypatch):
        from routes.lecture_studio import pipeline as lecture_studio_pipeline

        class FakeStorage:
            def get_object(self, bucket, object_name):
                if object_name == "uploads/mat-2.pdf":
                    return b"pdf"
                raise FileNotFoundError(object_name)

        monkeypatch.setattr(lecture_studio_pipeline, "get_storage_client", lambda: FakeStorage())

        data, source_kind = lecture_studio_pipeline._load_pipeline_source("mat-2", "paper.pdf")

        assert data == b"pdf"
        assert source_kind == "pdf"


class TestDocumentPipelineStageRegistry:
    """単独再実行 API の受理ステージ集合は orchestrator の PIPELINE_STAGES が正本。

    かつては表示ラベル表 (``DOCUMENT_PIPELINE_STAGE_LABELS``、18件) のキーを
    start_stage / target_stage の allow-list に兼用していたため、ラベル追補漏れの
    ステージ（apparatus_semantics / contextual_explanation / discuss_opening /
    landscape_placement / figure_image_extraction / dsl_embedding /
    persist_claims_components_graph 等）は管理UIの再実行メニューに出るのに
    400 "Unknown pipeline start stage" になっていた。ステージ表を2箇所に持たない。
    """

    @staticmethod
    def _pipeline_module():
        from routes.lecture_studio import pipeline as lecture_studio_pipeline

        return lecture_studio_pipeline

    @staticmethod
    def _stub_material_run(monkeypatch, mod):
        """DB・ストレージ・スレッドに触れずに run API のバリデーションだけ通す。"""
        from types import SimpleNamespace

        started: list = []

        class _FakeThread:
            def __init__(self, *args, **kwargs):
                self.kwargs = kwargs

            def start(self):
                started.append(self.kwargs)

        monkeypatch.setattr(mod, "threading", SimpleNamespace(Thread=_FakeThread))
        monkeypatch.setattr(mod, "create_background_task", lambda *a, **k: None)
        monkeypatch.setattr(mod, "update_background_task", lambda *a, **k: None)
        monkeypatch.setattr(mod, "_get_active_task_for_material", lambda *a, **k: None)
        monkeypatch.setattr(
            mod,
            "_get_editable_material_document",
            lambda material_id, current_user: {
                "document_id": "doc-1",
                "material_id": material_id,
                "filename": "paper.pdf",
            },
        )
        return started

    def test_accepted_stages_are_derived_from_orchestrator(self):
        from core.document_pipeline.orchestrator import PIPELINE_STAGES

        mod = self._pipeline_module()
        expected = tuple(stage for stage in PIPELINE_STAGES if stage != "completed")
        assert mod.DOCUMENT_PIPELINE_STAGES == expected
        # 終端マーカーは実行可能なステージではないので受け付けない。
        assert "completed" not in mod.DOCUMENT_PIPELINE_STAGES

    def test_every_pipeline_stage_has_a_display_label(self):
        """PIPELINE_STAGES（completed 除く）の全ステージに表示ラベルがある。"""
        mod = self._pipeline_module()
        missing = [
            stage
            for stage in mod.DOCUMENT_PIPELINE_STAGES
            if not mod.DOCUMENT_PIPELINE_STAGE_LABELS.get(stage)
        ]
        assert missing == [], f"表示ラベル未登録のステージ: {missing}"
        # ラベル表に実在しないステージ名（typo・撤去済みステージ）を残さない。
        assert set(mod.DOCUMENT_PIPELINE_STAGE_LABELS) == set(mod.DOCUMENT_PIPELINE_STAGES)

    def test_every_display_label_is_japanese(self):
        """進捗表示ラベルに英語 Agent クラス名を混ぜない。

        ラベルは教員が読む進捗表示（「〜が進行中です...」）に埋め込まれる。かつては
        18件が ``DocumentStructureAgent`` のような内部 Agent クラス名のままで、
        追補された日本語ラベル（「図画像の抽出」等）と英日まだらになっていた。
        内部ステージキー（dict のキー）は agent 実装と結び付いているため変更しない。
        """
        import re

        mod = self._pipeline_module()
        japanese = re.compile(r"[ぁ-んァ-ヶー一-龠]")
        # 「FooAgent」「FooBuilder」「FooGate」形の ASCII クラス名を値に置かない。
        class_name = re.compile(r"^[A-Za-z][A-Za-z0-9]*(Agent|Builder|Gate|Annotator)$")

        not_japanese = sorted(
            f"{stage}={label}"
            for stage, label in mod.DOCUMENT_PIPELINE_STAGE_LABELS.items()
            if not japanese.search(label)
        )
        assert not_japanese == [], f"日本語ラベルでないステージ: {not_japanese}"

        class_names = sorted(
            f"{stage}={label}"
            for stage, label in mod.DOCUMENT_PIPELINE_STAGE_LABELS.items()
            if class_name.match(label)
        )
        assert class_names == [], f"Agent クラス名がラベルに再混入: {class_names}"

    def test_stage_labels_agree_with_the_model_selection_ui(self):
        """同一ステージに2つの日本語名を持たない（進捗表示 ⇄ M層モデル選択UI）。

        ラベル日本語化の際、M層 ``llm_policy.PIPELINE_STAGE_LABELS`` と 12 共有キー中
        9 件で訳語が割れていることが表面化した（例: paper_skeleton =
        「論文アウトラインの推定」vs「論文骨格の仮説化」）。語彙ガードレールの
        重複表検出はキー集合の完全一致でグルーピングするため、この**部分重複の割れは
        機械検出されない** — 共有キーの値一致をここで固定する。
        """
        from core.llm_policy import PIPELINE_STAGE_LABELS

        mod = self._pipeline_module()
        diverged = sorted(
            f"{stage}: 進捗表示={mod.DOCUMENT_PIPELINE_STAGE_LABELS[stage]!r} "
            f"M層UI={PIPELINE_STAGE_LABELS[stage]!r}"
            for stage in set(mod.DOCUMENT_PIPELINE_STAGE_LABELS) & set(PIPELINE_STAGE_LABELS)
            if mod.DOCUMENT_PIPELINE_STAGE_LABELS[stage] != PIPELINE_STAGE_LABELS[stage]
        )
        assert diverged == [], f"同一ステージの訳語が画面間で分裂: {diverged}"

    def test_frontend_keeps_no_copy_of_the_stage_label_table(self):
        """原稿スタジオ JS に訳語表のコピーを持たない（表を2箇所に増やさない）。

        撤去した3つは同じ死クラスタ:
        - ``lsAgentStageLabels`` … バックエンド旧ラベル表の第3コピー（英語のまま
          取り残され、進捗復帰表示に「DocumentStructureAgentが進行中です...」と出ていた）
        - ``lsRunDocumentPipeline`` … 呼び出し元ゼロ
        - ``lsSetAgentStageItemState`` … 呼び出し元ゼロ。生成コードが存在しない
          ``.ls-agent-stage-btn`` を走査していた

        ステージ表示名の正本はバックエンドで、フロントは task の
        ``result_data.label`` をそのまま表示する。
        """
        from pathlib import Path

        js = (
            Path(__file__).resolve().parents[2]
            / "frontend" / "public" / "js" / "admin-lecture-studio.js"
        ).read_text(encoding="utf-8")

        assert "var lsAgentStageLabels" not in js
        assert "function lsRunDocumentPipeline" not in js
        assert "function lsSetAgentStageItemState" not in js
        assert ".ls-agent-stage-btn" not in js
        # 訳語表のコピーが復活していないこと（値としての Agent クラス名）。
        for class_name in ('"DocumentStructureAgent"', '"ComponentGraphAgent"', '"ExportValidationGate"'):
            assert class_name not in js, f"訳語表のコピーが復活: {class_name}"
        # 復帰表示はバックエンドの label を使う（フロントで訳し直さない）。
        assert 'var label = targetStage ? (rd.label || targetStage) : "パイプライン全実行";' in js

    def test_material_run_accepts_stage_absent_from_legacy_label_table(self, monkeypatch):
        """管理UIが送る start_stage=apparatus_semantics が 400 にならない。"""
        mod = self._pipeline_module()
        started = self._stub_material_run(monkeypatch, mod)

        result = mod.run_material_document_pipeline(
            material_id="mat-1",
            body={"start_stage": "apparatus_semantics"},
            current_user={"id": "u-1", "role": "TEACHER"},
        )

        assert result["start_stage"] == "apparatus_semantics"
        assert result["status"] == "pending"
        # worker スレッドへも同じ start_stage が渡る（Thread は fake なので実行しない）。
        assert started
        assert started[0]["kwargs"]["start_stage"] == "apparatus_semantics"

    def test_material_run_accepts_every_pipeline_stage(self, monkeypatch):
        """従来受理していた18件を含め、全ステージが start_stage として通る。"""
        mod = self._pipeline_module()
        self._stub_material_run(monkeypatch, mod)

        for stage in mod.DOCUMENT_PIPELINE_STAGES:
            result = mod.run_material_document_pipeline(
                material_id="mat-1",
                body={"start_stage": stage},
                current_user={"id": "u-1", "role": "TEACHER"},
            )
            assert result["start_stage"] == stage, stage

    def test_material_run_rejects_unknown_stage(self, monkeypatch):
        """存在しないステージ名は従来どおり 400 + 同一 detail 文言。"""
        from fastapi import HTTPException
        import pytest as _pytest

        mod = self._pipeline_module()
        self._stub_material_run(monkeypatch, mod)

        with _pytest.raises(HTTPException) as exc:
            mod.run_material_document_pipeline(
                material_id="mat-1",
                body={"start_stage": "no_such_stage"},
                current_user={"id": "u-1", "role": "TEACHER"},
            )
        assert exc.value.status_code == 400
        assert exc.value.detail == "Unknown pipeline start stage"

        with _pytest.raises(HTTPException) as exc:
            mod.run_material_document_pipeline(
                material_id="mat-1",
                body={"target_stage": "completed"},
                current_user={"id": "u-1", "role": "TEACHER"},
            )
        assert exc.value.status_code == 400
        assert exc.value.detail == "Unknown pipeline stage"

    def test_course_run_accepts_and_rejects_the_same_stage_set(self, monkeypatch):
        from types import SimpleNamespace

        from fastapi import HTTPException
        import pytest as _pytest

        mod = self._pipeline_module()

        class _FakeThread:
            def __init__(self, *args, **kwargs):
                self.kwargs = kwargs

            def start(self):
                pass

        monkeypatch.setattr(mod, "threading", SimpleNamespace(Thread=_FakeThread))
        monkeypatch.setattr(mod, "create_background_task", lambda *a, **k: None)
        monkeypatch.setattr(mod, "update_background_task", lambda *a, **k: None)
        monkeypatch.setattr(mod, "get_editable_course_data", lambda *a, **k: {"topics": []})
        monkeypatch.setattr(mod, "get_active_task_for_course", lambda *a, **k: None)
        monkeypatch.setattr(
            mod,
            "_course_pipeline_documents",
            lambda course_data: [
                {"document_id": "doc-1", "material_id": "mat-1", "filename": "paper.pdf"}
            ],
        )

        result = mod.run_course_document_pipeline(
            course_id="course-1",
            body={"start_stage": "landscape_placement"},
            current_user={"id": "u-1", "role": "TEACHER"},
        )
        assert result["start_stage"] == "landscape_placement"

        with _pytest.raises(HTTPException) as exc:
            mod.run_course_document_pipeline(
                course_id="course-1",
                body={"start_stage": "no_such_stage"},
                current_user={"id": "u-1", "role": "TEACHER"},
            )
        assert exc.value.status_code == 400
        assert exc.value.detail == "Unknown pipeline start stage"

    def test_admin_ui_pipeline_menu_stages_are_all_accepted(self):
        """`admin.js` の再実行メニューが送り得る全ステージがバックエンドで受理される。"""
        import re
        from pathlib import Path

        js = (
            Path(__file__).resolve().parents[2] / "frontend" / "public" / "js" / "admin.js"
        ).read_text(encoding="utf-8")
        start = js.index("var materialPipelineStageGroups = [")
        end = js.index("var materialPipelineStages =", start)
        ui_stages = re.findall(r'\["([a-z_]+)",\s*"', js[start:end])
        # 抽出が空振りして緑にならないようにする（現行メニューは19ステージ）。
        assert len(ui_stages) >= 19

        mod = self._pipeline_module()
        unknown = [stage for stage in ui_stages if stage not in mod.DOCUMENT_PIPELINE_STAGES]
        assert unknown == [], f"バックエンドが受理しないステージが管理UIにある: {unknown}"


class TestCourseTopicCheckQuestions:
    def test_normalize_check_questions_accepts_detailed_objects(self):
        from routes.lecture_studio import _normalize_check_questions

        result = _normalize_check_questions([
            {
                "question": "なぜ A か？",
                "model_answer": "B だから。",
                "answer_requirements": ["Bに言及", "因果関係"],
                "explanation": "前提 C から従う。",
            }
        ])

        assert result == [{
            "question": "なぜ A か？",
            "model_answer": "B だから。",
            "answer_requirements": ["Bに言及", "因果関係"],
            "explanation": "前提 C から従う。",
        }]

    def test_normalize_check_questions_preserves_legacy_strings(self):
        from routes.lecture_studio import _normalize_check_questions

        result = _normalize_check_questions(["要点を説明してください。"])

        assert result[0]["question"] == "要点を説明してください。"
        assert result[0]["model_answer"] == ""
        assert result[0]["answer_requirements"] == []


class TestTopicFiguresForPrompt:
    """Phase 4 §7.1 (hierarchical_context_explanation_design.md): 原稿スタジオの
    トピック下書きプロンプトへ図の evidence（figure_id + caption）を供給する。
    `![[figure:id]]` の予約記法に初めて実データが供給される経路。"""

    def test_extracts_figure_id_and_caption_from_figure_evidence_links(self):
        from routes.lecture_studio.topics import _topic_figures_for_prompt

        topic = {
            "evidence_links": [
                {"kind": "component", "target_id": "comp_1", "summary": "..."},
                {
                    "kind": "figure",
                    "target_id": "fig-uuid-1",
                    "figure_id": "fig-uuid-1",
                    "figure_key": "fig_2",
                    "document_id": "doc-1",
                    "caption": "Figure 2. Apparatus overview.",
                },
            ],
        }

        figures = _topic_figures_for_prompt(topic)

        assert figures == [{"figure_id": "fig-uuid-1", "caption": "Figure 2. Apparatus overview."}]

    def test_deduplicates_and_ignores_non_figure_kinds(self):
        from routes.lecture_studio.topics import _topic_figures_for_prompt

        topic = {
            "evidence_links": [
                {"kind": "figure", "target_id": "fig-uuid-1", "caption": "Figure 1."},
                {"kind": "figure", "target_id": "fig-uuid-1", "caption": "Figure 1 (dup)."},
                {"kind": "claim", "target_id": "clm_1", "summary": "..."},
            ],
        }

        figures = _topic_figures_for_prompt(topic)

        assert len(figures) == 1
        assert figures[0]["figure_id"] == "fig-uuid-1"

    def test_no_figure_evidence_links_returns_empty_list(self):
        from routes.lecture_studio.topics import _topic_figures_for_prompt

        assert _topic_figures_for_prompt({}) == []
        assert _topic_figures_for_prompt({"evidence_links": []}) == []

    def _topic_with_figure(self, document_id="doc-1", figure_id="fig-uuid-1"):
        return {
            "evidence_links": [{
                "kind": "figure",
                "target_id": figure_id,
                "figure_id": figure_id,
                "document_id": document_id,
                "caption": "Figure 2. Apparatus overview.",
            }],
        }

    @patch("core.element_explanations.list_for_document")
    @patch("routes.lecture_studio.topics._pg_session")
    def test_approved_contextual_explanation_gets_approved_status_label(
        self, mock_session, mock_list,
    ):
        """Phase 2 §5.3 続き: 承認済み(approved) contextual があれば
        explanation_status='approved' で供給する（教員向け下書きの材料）。"""
        from routes.lecture_studio.topics import _topic_figures_for_prompt
        from core import element_explanations

        mock_session.return_value = MagicMock()
        mock_list.return_value = [{
            "element_id": "fig-uuid-1",
            "kind": element_explanations.KIND_CONTEXTUAL,
            "body": "承認済みの文脈説明。",
            "status": element_explanations.STATUS_APPROVED,
        }]

        figures = _topic_figures_for_prompt(self._topic_with_figure())

        assert figures == [{
            "figure_id": "fig-uuid-1",
            "caption": "Figure 2. Apparatus overview.",
            "explanation": "承認済みの文脈説明。",
            "explanation_status": "approved",
        }]

    @patch("core.element_explanations.list_for_document")
    @patch("routes.lecture_studio.topics._pg_session")
    def test_candidate_only_explanation_is_labeled_ai_candidate(
        self, mock_session, mock_list,
    ):
        """承認前(candidate)の contextual しかない場合は「AI候補」として
        explanation_status='ai_candidate' で供給する（教員向け下書きのみの例外的許容）。"""
        from routes.lecture_studio.topics import _topic_figures_for_prompt
        from core import element_explanations

        mock_session.return_value = MagicMock()
        mock_list.return_value = [{
            "element_id": "fig-uuid-1",
            "kind": element_explanations.KIND_CONTEXTUAL,
            "body": "AI候補の文脈説明。",
            "status": element_explanations.STATUS_CANDIDATE,
        }]

        figures = _topic_figures_for_prompt(self._topic_with_figure())

        assert figures[0]["explanation"] == "AI候補の文脈説明。"
        assert figures[0]["explanation_status"] == "ai_candidate"

    @patch("core.element_explanations.list_for_document")
    @patch("routes.lecture_studio.topics._pg_session")
    def test_approved_takes_priority_over_candidate(self, mock_session, mock_list):
        from routes.lecture_studio.topics import _topic_figures_for_prompt
        from core import element_explanations

        mock_session.return_value = MagicMock()
        mock_list.return_value = [
            {
                "element_id": "fig-uuid-1",
                "kind": element_explanations.KIND_CONTEXTUAL,
                "body": "古い候補。",
                "status": element_explanations.STATUS_CANDIDATE,
            },
            {
                "element_id": "fig-uuid-1",
                "kind": element_explanations.KIND_CONTEXTUAL,
                "body": "承認済み本文。",
                "status": element_explanations.STATUS_APPROVED,
            },
        ]

        figures = _topic_figures_for_prompt(self._topic_with_figure())

        assert figures[0]["explanation"] == "承認済み本文。"
        assert figures[0]["explanation_status"] == "approved"

    @patch("core.element_explanations.list_for_document")
    @patch("routes.lecture_studio.topics._pg_session")
    def test_dismissed_or_superseded_explanations_are_not_supplied(
        self, mock_session, mock_list,
    ):
        """P4: dismissed/superseded は履歴として保持されるだけで、下書き材料には出さない。"""
        from routes.lecture_studio.topics import _topic_figures_for_prompt
        from core import element_explanations

        mock_session.return_value = MagicMock()
        mock_list.return_value = [
            {
                "element_id": "fig-uuid-1",
                "kind": element_explanations.KIND_CONTEXTUAL,
                "body": "却下済み。",
                "status": element_explanations.STATUS_DISMISSED,
            },
            {
                "element_id": "fig-uuid-1",
                "kind": element_explanations.KIND_CONTEXTUAL,
                "body": "旧版。",
                "status": element_explanations.STATUS_SUPERSEDED,
            },
        ]

        figures = _topic_figures_for_prompt(self._topic_with_figure())

        assert figures == [{"figure_id": "fig-uuid-1", "caption": "Figure 2. Apparatus overview."}]


class TestPersonaPromptHelpers:
    """解説モードプロンプトのヘルパーテスト。"""

    def test_persona_prompt_known_id(self):
        from core.personas import persona_prompt

        prompt = persona_prompt("general_friendly", target="narration")
        assert "サイエンス・コミュニケーター" in prompt
        assert "音声読み上げテキスト" in prompt

    def test_persona_prompt_unknown_id_returns_empty(self):
        from core.personas import persona_prompt

        assert persona_prompt("unknown", target="response") == ""

    def test_course_persona_settings_normalizes_unknown_values(self):
        from core.personas import course_persona_settings

        settings = course_persona_settings({
            "lecture_studio_settings": {
                "narration_persona": "expert_friendly",
                "response_persona": "unknown",
            },
        })
        assert settings["narration_persona"] == "expert_friendly"
        assert settings["response_persona"] == ""


class TestLectureStudioSettingsRegeneration:
    """口調設定変更後の全スクリプト再生成フラグを検証する。"""

    # Tier 3-17a: 口調設定・再生成フラグのロジックは lecture_studio パッケージの
    # scripts.py に移設された。
    SOURCE_FILE = os.path.join(
        os.path.dirname(__file__), "..", "api", "routes", "lecture_studio", "scripts.py",
    )

    def test_settings_change_marks_scripts_for_regeneration(self):
        source = open(self.SOURCE_FILE, encoding="utf-8").read()
        assert "scripts_need_regeneration" in source
        assert "settings_changed" in source

    def test_batch_generation_uses_effective_override(self):
        source = open(self.SOURCE_FILE, encoding="utf-8").read()
        assert "force_regenerate" in source
        assert "effective_override = body.override or force_regenerate" in source

    def test_successful_override_clears_regeneration_flag(self):
        source = open(self.SOURCE_FILE, encoding="utf-8").read()
        assert "_clear_script_regeneration_flag" in source
        assert "if override:" in source


# ---------------------------------------------------------------------------
# Rewrite prompt template tests
# ---------------------------------------------------------------------------


class TestRewritePromptTemplate:
    """AI書き換えプロンプトテンプレートのテスト。"""

    # _REWRITE_PROMPT は routes.lecture_studio からのインポートで jwt 依存が
    # 発生するため、テンプレート文字列を直接検証する。

    _REWRITE_PROMPT = """あなたは大学講義の音声原稿を改善するアシスタントです。

以下のソーステキストと現在の音声読み上げ原稿、そして教員からの指示に基づいて、
音声原稿を書き換えてください。

**重要:**
- 教員の指示に従い、必要に応じて一般的な物理学・数学の知識を補足してください
- ソーステキストに限定されず、教員が指示する内容を反映させてください
- LaTeX 数式は自然言語に変換してください（例: `$E = mc^2$` → 「Eイコールmcの二乗」）
- 自然な日本語の講義調で書いてください
- 数式メタデータも更新してください

## ソーステキスト:
{source_text}

## 現在の音声原稿:
{current_spoken_text}

## 教員からの指示:
{instructor_prompt}

## 出力形式 (厳密にJSON):
{{
  "spoken_text": "書き換えた音声原稿",
  "formulas": [
    {{"id": "formula_0", "latex": "E = mc^2", "spoken": "Eイコールmcの二乗"}}
  ]
}}

重要: JSON のみを出力してください。マークダウンコードフェンスは不要です。"""

    def test_rewrite_prompt_contains_placeholders(self):
        assert "{source_text}" in self._REWRITE_PROMPT
        assert "{current_spoken_text}" in self._REWRITE_PROMPT
        assert "{instructor_prompt}" in self._REWRITE_PROMPT

    def test_rewrite_prompt_format(self):
        filled = self._REWRITE_PROMPT.format(
            source_text="test source",
            current_spoken_text="current spoken",
            instructor_prompt="add prerequisite info",
        )
        assert "test source" in filled
        assert "current spoken" in filled
        assert "add prerequisite info" in filled
        assert "JSON" in filled


# ---------------------------------------------------------------------------
# Rewrite result normalization (regression for theory-tab AttributeError)
# ---------------------------------------------------------------------------


class TestNormalizeRewriteResult:
    """`_normalize_rewrite_result` の挙動を検証する。

    LLM が JSON オブジェクトではなくトップレベル配列を返すケースで
    `AttributeError: 'list' object has no attribute 'get'` が出ていた回帰を防ぐ。
    """

    def _normalize(self, parsed, studio_view):
        from routes.lecture_studio import _normalize_rewrite_result

        return _normalize_rewrite_result(parsed, studio_view)

    def test_dict_input_returned_as_is(self):
        payload = {"theory_components": [{"id": "c1"}], "spoken_text": "x"}
        assert self._normalize(payload, "theory") == payload

    def test_list_in_theory_view_wrapped_as_theory_components(self):
        payload = [{"id": "c1", "summary": "a"}, {"id": "c2"}]
        result = self._normalize(payload, "theory")
        assert result == {"theory_components": payload}

    def test_list_in_non_theory_view_returns_empty(self):
        result = self._normalize([{"id": "c1"}], "edit")
        assert result == {}

    def test_invalid_input_returns_empty(self):
        assert self._normalize("string", "theory") == {}
        assert self._normalize(None, "edit") == {}
        assert self._normalize(42, "audio") == {}

    def test_normalized_dict_supports_get(self):
        """正規化後は必ず .get() できる(本番のリグレッション再現テスト)。"""
        result = self._normalize([{"id": "c1"}], "theory")
        # 以下が AttributeError なく動くことが本テストの主旨
        assert result.get("theory_components") == [{"id": "c1"}]
        assert result.get("display_text") is None
        assert result.get("spoken_text", "fallback") == "fallback"


# ---------------------------------------------------------------------------
# Chunk status logic tests
# ---------------------------------------------------------------------------


class TestChunkStatusLogic:
    """チャンクステータス判定のテスト。"""

    def test_status_values_are_valid(self):
        """ステータス値は ungenerated, generated, edited, audio_ready のいずれかであること。"""
        valid_statuses = {"ungenerated", "generated", "edited", "audio_ready"}
        from schemas import LectureScriptChunkOut

        for status in valid_statuses:
            chunk = LectureScriptChunkOut(
                chunk_id="test", chunk_index=0, text="t", status=status,
            )
            assert chunk.status == status


# ---------------------------------------------------------------------------
# Async batch generate worker logic tests (Issue #76)
# ---------------------------------------------------------------------------


class TestBatchGenerateAsync:
    """非同期バッチスクリプト生成 (Issue #76) のロジックテスト。"""

    def test_start_response_has_task_id(self):
        """開始レスポンスに task_id が含まれること。"""
        from schemas import LectureScriptGenerateStartResponse

        resp = LectureScriptGenerateStartResponse(
            task_id="task001",
            course_id="course1",
            total_chunks=30,
        )
        assert resp.task_id == "task001"
        assert resp.status == "pending"
        assert resp.total_chunks == 30

    def test_start_response_zero_chunks(self):
        """チャンク数0でも正常に作成できること。"""
        from schemas import LectureScriptGenerateStartResponse

        resp = LectureScriptGenerateStartResponse(
            task_id="t0",
            course_id="c0",
            total_chunks=0,
        )
        assert resp.total_chunks == 0

    def test_progress_calculation(self):
        """進捗計算ロジックのテスト: (generated + skipped) / total * 100"""
        total = 10
        generated = 3
        skipped = 2
        processed = generated + skipped
        progress = int(processed * 100 / total) if total > 0 else 100
        assert progress == 50

    def test_progress_calculation_zero_total(self):
        """total=0 のとき progress=100 となること。"""
        total = 0
        progress = int(0 * 100 / total) if total > 0 else 100
        assert progress == 100

    def test_progress_calculation_all_skipped(self):
        """全チャンクスキップ時に progress=100 となること。"""
        total = 5
        skipped = 5
        processed = skipped
        progress = int(processed * 100 / total) if total > 0 else 100
        assert progress == 100

    def test_worker_task_type(self):
        """ワーカーのタスクタイプが script_generation であること。"""
        # タスクタイプは create_background_task 呼び出し時に使われる定数
        expected_task_type = "script_generation"
        assert expected_task_type == "script_generation"


# ---------------------------------------------------------------------------
# Async batch audio worker logic tests (Issue #78)
# ---------------------------------------------------------------------------


class TestBatchAudioAsync:
    """非同期バッチ音声生成 (Issue #78) のロジックテスト。"""

    def test_audio_start_response_schema(self):
        """音声生成開始レスポンスのスキーマが正しいこと。"""
        from schemas import LectureAudioGenerateStartResponse

        resp = LectureAudioGenerateStartResponse(
            task_id="audio001",
            course_id="course1",
            total_chunks=10,
        )
        assert resp.task_id == "audio001"
        assert resp.course_id == "course1"
        assert resp.total_chunks == 10
        assert resp.status == "pending"

    def test_audio_start_response_custom_status(self):
        """status フィールドが設定可能であること。"""
        from schemas import LectureAudioGenerateStartResponse

        resp = LectureAudioGenerateStartResponse(
            task_id="t1",
            course_id="c1",
            total_chunks=5,
            status="processing",
        )
        assert resp.status == "processing"

    def test_audio_start_response_zero_chunks(self):
        """チャンク数0でも正常に作成できること。"""
        from schemas import LectureAudioGenerateStartResponse

        resp = LectureAudioGenerateStartResponse(
            task_id="t0",
            course_id="c0",
            total_chunks=0,
        )
        assert resp.total_chunks == 0

    def test_audio_progress_calculation_with_errors(self):
        """エラー件数を含む進捗計算ロジックのテスト: (generated + skipped + errors) / total * 100"""
        total = 10
        generated = 3
        skipped = 2
        errors = 1
        processed = generated + skipped + errors
        progress = int(processed * 100 / total) if total > 0 else 100
        assert progress == 60

    def test_audio_progress_calculation_zero_total(self):
        """total=0 のとき progress=100 となること。"""
        total = 0
        progress = int(0 * 100 / total) if total > 0 else 100
        assert progress == 100

    def test_audio_progress_calculation_all_generated(self):
        """全チャンク生成完了時に progress=100 となること。"""
        total = 8
        generated = 8
        skipped = 0
        errors = 0
        processed = generated + skipped + errors
        progress = int(processed * 100 / total) if total > 0 else 100
        assert progress == 100

    def test_audio_worker_task_type(self):
        """音声生成ワーカーのタスクタイプが audio_generation であること。"""
        expected_task_type = "audio_generation"
        assert expected_task_type == "audio_generation"

    def test_audio_result_data_structure(self):
        """バックグラウンドタスクの result_data 構造が正しいこと。"""
        # _batch_audio_worker が update_background_task に渡す result_data の構造を検証
        result_data = {
            "course_id": "course1",
            "total_chunks": 10,
            "generated": 5,
            "skipped": 3,
            "errors": 2,
            "progress": 100,
        }
        assert "course_id" in result_data
        assert "total_chunks" in result_data
        assert "generated" in result_data
        assert "skipped" in result_data
        assert "errors" in result_data
        assert "progress" in result_data
        assert result_data["generated"] + result_data["skipped"] + result_data["errors"] == result_data["total_chunks"]

    def test_audio_generate_response_still_works(self):
        """後方互換スキーマ LectureAudioGenerateResponse が引き続き動作すること。"""
        from schemas import LectureAudioGenerateResponse

        resp = LectureAudioGenerateResponse(
            course_id="c1",
            total_chunks=5,
            generated=3,
            skipped=1,
            errors=1,
        )
        assert resp.generated == 3
        assert resp.errors == 1


# ---------------------------------------------------------------------------
# TTS プロバイダ選択ロジックのテスト
# ---------------------------------------------------------------------------


class TestGenerateTtsAudioProviderSelection:
    """generate_tts_audio のプロバイダ選択ロジックの単体テスト。

    core.tts モジュールを直接インポートして、外部 API 呼び出しはモックに置き換える。
    """

    def _patch_settings(self, monkeypatch, api_key: str, provider: str):
        """core.tts.get_settings をパッチするヘルパー。"""
        import types
        import core.tts as tts_module
        fake_settings = types.SimpleNamespace(llm_api_key=api_key, llm_provider=provider)
        monkeypatch.setattr(tts_module, "get_settings", lambda: fake_settings)

    def _register_fake_tts_module(self, monkeypatch, fake_tts_class):
        """google.cloud.texttospeech の親モジュールも含めて sys.modules に登録するヘルパー。"""
        import sys
        import types

        # google, google.cloud の親モジュールが存在しない場合は作成する
        if "google" not in sys.modules:
            monkeypatch.setitem(sys.modules, "google", types.ModuleType("google"))
        if "google.cloud" not in sys.modules:
            monkeypatch.setitem(sys.modules, "google.cloud", types.ModuleType("google.cloud"))
        monkeypatch.setitem(sys.modules, "google.cloud.texttospeech", fake_tts_class)

    def test_openai_provider_selected_when_llm_provider_openai(self, monkeypatch):
        """LLM_PROVIDER=openai のとき OpenAI TTS が選択されること。"""
        import sys
        import types
        import core.tts as tts_module

        class FakeSpeech:
            def create(self, **kwargs):
                class FakeResponse:
                    content = b"fake-mp3-data"
                return FakeResponse()

        class FakeOpenAIClient:
            audio = type("FakeAudio", (), {"speech": FakeSpeech()})()

        fake_openai_mod = types.ModuleType("openai")
        fake_openai_mod.OpenAI = lambda api_key: FakeOpenAIClient()
        monkeypatch.setitem(sys.modules, "openai", fake_openai_mod)

        self._patch_settings(monkeypatch, "sk-test-key", "openai")
        result = tts_module.generate_tts_audio("テストテキスト")
        assert result == b"fake-mp3-data"

    def test_google_provider_selected_when_llm_provider_google(self, monkeypatch):
        """LLM_PROVIDER=google のとき Google Cloud TTS が選択されること。"""
        import types
        import core.tts as tts_module

        fake_tts_response = types.SimpleNamespace(audio_content=b"fake-gcp-mp3")

        class FakeTTSClient:
            def synthesize_speech(self, **kwargs):
                return fake_tts_response

        class FakeTextToSpeech:
            TextToSpeechClient = FakeTTSClient

            class SynthesisInput:
                def __init__(self, text):
                    self.text = text

            class VoiceSelectionParams:
                def __init__(self, **kwargs):
                    pass

            class AudioConfig:
                def __init__(self, **kwargs):
                    pass

            class SsmlVoiceGender:
                NEUTRAL = "NEUTRAL"

            class AudioEncoding:
                MP3 = "MP3"

        self._register_fake_tts_module(monkeypatch, FakeTextToSpeech)
        self._patch_settings(monkeypatch, "", "google")
        result = tts_module.generate_tts_audio("テストテキスト")
        assert result == b"fake-gcp-mp3"

    def test_gemini_vertex_provider_selected(self, monkeypatch):
        """LLM_PROVIDER=gemini-vertex のとき Google Cloud TTS が選択されること。"""
        import types
        import core.tts as tts_module

        fake_tts_response = types.SimpleNamespace(audio_content=b"fake-vertex-mp3")

        class FakeTTSClient:
            def synthesize_speech(self, **kwargs):
                return fake_tts_response

        class FakeTextToSpeech:
            TextToSpeechClient = FakeTTSClient

            class SynthesisInput:
                def __init__(self, text):
                    self.text = text

            class VoiceSelectionParams:
                def __init__(self, **kwargs):
                    pass

            class AudioConfig:
                def __init__(self, **kwargs):
                    pass

            class SsmlVoiceGender:
                NEUTRAL = "NEUTRAL"

            class AudioEncoding:
                MP3 = "MP3"

        self._register_fake_tts_module(monkeypatch, FakeTextToSpeech)
        self._patch_settings(monkeypatch, "", "gemini-vertex")
        result = tts_module.generate_tts_audio("テストテキスト")
        assert result == b"fake-vertex-mp3"

    def test_gemini_provider_returns_none(self, monkeypatch):
        """provider=gemini の場合 TTS 非対応として None が返ること。"""
        import core.tts as tts_module

        self._patch_settings(monkeypatch, "", "gemini")
        result = tts_module.generate_tts_audio("テストテキスト")
        assert result is None

    def test_openai_exception_returns_none(self, monkeypatch):
        """LLM_PROVIDER=openai で OpenAI TTS が例外を送出した場合 None が返ること。"""
        import sys
        import types
        import core.tts as tts_module

        fake_openai_mod = types.ModuleType("openai")
        fake_openai_mod.OpenAI = lambda api_key: (_ for _ in ()).throw(RuntimeError("connection failed"))
        monkeypatch.setitem(sys.modules, "openai", fake_openai_mod)

        self._patch_settings(monkeypatch, "sk-broken-key", "openai")
        result = tts_module.generate_tts_audio("テストテキスト")
        assert result is None

    def test_google_tts_exception_returns_none(self, monkeypatch):
        """Google Cloud TTS が例外を送出した場合 None が返ること。"""
        import core.tts as tts_module

        class BrokenTTSClient:
            def synthesize_speech(self, **kwargs):
                raise RuntimeError("GCP TTS connection failed")

        class FakeTextToSpeech:
            TextToSpeechClient = BrokenTTSClient

            class SynthesisInput:
                def __init__(self, text):
                    self.text = text

            class VoiceSelectionParams:
                def __init__(self, **kwargs):
                    pass

            class AudioConfig:
                def __init__(self, **kwargs):
                    pass

            class SsmlVoiceGender:
                NEUTRAL = "NEUTRAL"

            class AudioEncoding:
                MP3 = "MP3"

        self._register_fake_tts_module(monkeypatch, FakeTextToSpeech)
        self._patch_settings(monkeypatch, "", "google")
        result = tts_module.generate_tts_audio("テストテキスト")
        assert result is None

    def test_text_truncated_to_5000_for_google(self, monkeypatch):
        """Google Cloud TTS へ渡すテキストが 5000 文字以内に切り詰められること。"""
        import types
        import core.tts as tts_module

        received_texts = []

        class CaptureTTSClient:
            def synthesize_speech(self, input, voice, audio_config):
                received_texts.append(input.text)
                return types.SimpleNamespace(audio_content=b"ok")

        class FakeTextToSpeech:
            TextToSpeechClient = CaptureTTSClient

            class SynthesisInput:
                def __init__(self, text):
                    self.text = text

            class VoiceSelectionParams:
                def __init__(self, **kwargs):
                    pass

            class AudioConfig:
                def __init__(self, **kwargs):
                    pass

            class SsmlVoiceGender:
                NEUTRAL = "NEUTRAL"

            class AudioEncoding:
                MP3 = "MP3"

        self._register_fake_tts_module(monkeypatch, FakeTextToSpeech)
        self._patch_settings(monkeypatch, "", "google")
        long_text = "あ" * 6000
        result = tts_module.generate_tts_audio(long_text)
        assert result == b"ok"
        assert len(received_texts) == 1
        assert len(received_texts[0]) == 5000

    def test_text_truncated_to_4096_for_openai(self, monkeypatch):
        """OpenAI TTS へ渡すテキストが 4096 文字以内に切り詰められること。"""
        import sys
        import types
        import core.tts as tts_module

        received_inputs = []

        class FakeSpeech:
            def create(self, **kwargs):
                received_inputs.append(kwargs.get("input", ""))
                class FakeResponse:
                    content = b"ok"
                return FakeResponse()

        class FakeOpenAIClient:
            audio = type("FakeAudio", (), {"speech": FakeSpeech()})()

        fake_openai_mod = types.ModuleType("openai")
        fake_openai_mod.OpenAI = lambda api_key: FakeOpenAIClient()
        monkeypatch.setitem(sys.modules, "openai", fake_openai_mod)

        self._patch_settings(monkeypatch, "any-key", "openai")
        long_text = "a" * 8000
        result = tts_module.generate_tts_audio(long_text)
        assert result == b"ok"
        assert len(received_inputs) == 1
        assert len(received_inputs[0]) == 4096

    def test_service_disabled_raises_tts_fatal_error(self, monkeypatch):
        """GCP の SERVICE_DISABLED エラー時に TtsFatalError が送出されること。"""
        import types
        import core.tts as tts_module
        from core.tts import TtsFatalError

        class FakeTTSClient:
            def synthesize_speech(self, **kwargs):
                raise RuntimeError(
                    "403 Cloud Text-to-Speech API has not been used in project foo before or it is disabled. "
                    "[reason: \"SERVICE_DISABLED\"]"
                )

        class FakeTextToSpeech:
            TextToSpeechClient = FakeTTSClient

            class SynthesisInput:
                def __init__(self, text):
                    self.text = text

            class VoiceSelectionParams:
                def __init__(self, **kwargs):
                    pass

            class AudioConfig:
                def __init__(self, **kwargs):
                    pass

            class SsmlVoiceGender:
                NEUTRAL = "NEUTRAL"

            class AudioEncoding:
                MP3 = "MP3"

        self._register_fake_tts_module(monkeypatch, FakeTextToSpeech)
        self._patch_settings(monkeypatch, "", "google")

        import pytest
        with pytest.raises(TtsFatalError, match="Cloud Text-to-Speech API"):
            tts_module.generate_tts_audio("テストテキスト")

    def test_openai_auth_error_raises_tts_fatal_error(self, monkeypatch):
        """OpenAI の 401 認証エラー時に TtsFatalError が送出されること。"""
        import sys
        import types
        import core.tts as tts_module
        from core.tts import TtsFatalError

        class FakeAuthError(Exception):
            pass

        class BrokenOpenAIClient:
            class audio:
                class speech:
                    @staticmethod
                    def create(**kwargs):
                        raise FakeAuthError("401 Unauthorized - no API key")

        fake_openai_mod = types.ModuleType("openai")
        fake_openai_mod.OpenAI = lambda api_key: BrokenOpenAIClient()
        fake_openai_mod.AuthenticationError = FakeAuthError
        monkeypatch.setitem(sys.modules, "openai", fake_openai_mod)

        self._patch_settings(monkeypatch, "any-key", "openai")

        import pytest
        with pytest.raises(TtsFatalError, match="認証エラー"):
            tts_module.generate_tts_audio("テストテキスト")


class TestUnreachableSyncSpokenRemoved:
    """2026-09-05 是正: 到達不能だった読み上げ同期チェックボックスの撤去。

    `#ls-sync-spoken` を含む `#ls-sync-row` の表示条件は `view === "audio"` だったが、
    `lsRenderWorkspace()` は冒頭で `lsUpdateWorkTabActive()` を呼び、そこで
    `lsNormalizeViewForCurrentMode()` が "audio" を無条件に "edit" へ畳む。
    `data-ls-view="audio"` のタブも存在しないため、チェックボックスは一度も表示されず、
    既定の同期 ON のまま切り替えられなかった。**挙動（表示テキスト→読み上げ文の追随）は
    変えずに**、操作できない UI と使われないフラグだけを落とす。
    """

    @staticmethod
    def _sources():
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        return (
            (root / "frontend" / "public" / "admin.html").read_text(encoding="utf-8"),
            (root / "frontend" / "public" / "js" / "admin-lecture-studio.js").read_text(encoding="utf-8"),
            (root / "frontend" / "public" / "css" / "styles.css").read_text(encoding="utf-8"),
        )

    def test_checkbox_and_row_are_gone_from_dom(self):
        html, _js, _css = self._sources()
        assert 'id="ls-sync-spoken"' not in html
        assert 'id="ls-sync-row"' not in html

    def test_no_js_references_to_removed_elements(self):
        """getElementById("ls-sync-row") が残っていると null 参照で落ちる。"""
        _html, js, _css = self._sources()
        assert 'getElementById("ls-sync-row")' not in js
        assert 'getElementById("ls-sync-spoken")' not in js
        assert "lsState.syncSpoken" not in js
        assert "syncSpoken:" not in js

    def test_css_rules_removed(self):
        _html, _js, css = self._sources()
        assert ".ls-sync-row" not in css

    def test_display_text_still_syncs_to_spoken_text(self):
        """撤去は挙動の変更ではない（既定 ON だった同期はそのまま残る）。"""
        _html, js, _css = self._sources()
        marker = 'document.getElementById("ls-display-text").addEventListener("input", function () {'
        start = js.index(marker)
        block = js[start:start + 1200]
        assert 'document.getElementById("ls-spoken-text").value = this.value;' in block
        assert "chunk.spoken_text = this.value;" in block

    def test_audio_view_is_structurally_unreachable(self):
        """撤去の根拠（"audio" は正規化で必ず "edit" になる）を固定する。"""
        html, js, _css = self._sources()
        assert 'if (view === "audio") return "edit";' in js
        assert 'data-ls-view="audio"' not in html
