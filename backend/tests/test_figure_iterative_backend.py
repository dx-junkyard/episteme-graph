"""#499 図の反復照合解析パイプライン — Wave 3 backend 統合のユニットテスト。

設計の正本は ``docs/features/contextual_figure_analysis_iterative_verification.md``
末尾「実装記録（2026-07-18 実装決定事項）」節。対象:

- migration 054（``document_figures.iterative_analysis``）の列定義
- ``core.figure_presentation``: ``persist_suggestions`` の iterative_analysis 書込 /
  ``iterative_analysis_payload`` の confidence 除去+段階ラベル+question_id 付与 /
  ``presentation_payload`` への伝播
- ``core.document_pipeline.figure_images``: 再抽出リセット・SELECT 列への追随
- ``core.document_pipeline.orchestrator._build_apparatus_semantics``:
  ``IterativeConfig`` 配線・事前フィルタ（図あたり ``1 + max_iterations`` 見積り）・
  ``done_payload`` の実測 vision_calls / convergence 集計
- ``core.figure_reanalysis``: 設定既定値・デフォルト agent への ``IterativeConfig``
  注入・unresolved_item_ids の解決/合成/監査/既存無指示経路の不変性

DB / MinIO / LLM への実接続は行わない（既存 figure 系テストの流儀を踏襲）。
"""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import core.config as config_module  # noqa: E402
import core.document_pipeline.figure_images as figure_images_module  # noqa: E402
import core.document_pipeline.orchestrator as orch  # noqa: E402
import core.figure_presentation as figure_presentation  # noqa: E402
import core.figure_reanalysis as figure_reanalysis  # noqa: E402
import core.storage as storage_module  # noqa: E402
from core.document_pipeline.figure_context import FigureContext  # noqa: E402
from core.figure_reanalysis import FigureReanalysisError  # noqa: E402


# ===========================================================================
# migration 054
# ===========================================================================


def test_migration_054_adds_iterative_analysis_column_idempotently():
    sql = (BACKEND / "db" / "054_figure_iterative_analysis.sql").read_text()
    assert "iterative_analysis" in sql
    assert "ADD COLUMN IF NOT EXISTS" in sql
    assert "jsonb" in sql.lower()


# ===========================================================================
# core.figure_presentation: persist_suggestions
# ===========================================================================


class _FakeResult:
    def __init__(self, rowcount: int = 1):
        self.rowcount = rowcount


class _FakeSession:
    def __init__(self):
        self.executed: list[tuple[str, dict]] = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def execute(self, stmt, params=None):
        self.executed.append((str(stmt), dict(params or {})))
        return _FakeResult()

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class TestPersistSuggestionsIterativeAnalysis:
    def test_writes_iterative_analysis_when_present(self, monkeypatch):
        session = _FakeSession()
        monkeypatch.setattr(figure_presentation, "get_session", lambda: session)
        record = {
            "figure_id": "fig-1",
            "suggested_mode": "functional_diagram",
            "mode_reason": "vision",
            "analysis_profile": {},
            "iterative_analysis": {"convergence_status": "converged", "vision_calls": 3},
        }
        updated = figure_presentation.persist_suggestions("doc-1", [record])
        assert updated == 1
        assert session.committed
        sql, params = session.executed[0]
        assert "iterative_analysis = CAST(:iterative_analysis AS jsonb)" in sql
        assert json.loads(params["iterative_analysis"]) == {
            "convergence_status": "converged", "vision_calls": 3,
        }

    def test_writes_empty_object_when_absent(self, monkeypatch):
        session = _FakeSession()
        monkeypatch.setattr(figure_presentation, "get_session", lambda: session)
        record = {
            "figure_id": "fig-1",
            "suggested_mode": "unknown",
            "mode_reason": "",
            "analysis_profile": {},
        }
        figure_presentation.persist_suggestions("doc-1", [record])
        _sql, params = session.executed[0]
        assert json.loads(params["iterative_analysis"]) == {}

    def test_dataclass_record_with_nested_iterative_analysis_round_trips(self, monkeypatch):
        from episteme_graph.agents.apparatus_semantics.schema import (
            ApparatusRecord,
            IterativeAnalysisRecord,
        )

        session = _FakeSession()
        monkeypatch.setattr(figure_presentation, "get_session", lambda: session)
        record = ApparatusRecord(
            figure_id="fig-1",
            figure_key="fig_1",
            apparatus_name_candidate="",
            matched_library_entry_id=None,
            matched_library_version_no=None,
            match_status="unknown",
            iterative_analysis=IterativeAnalysisRecord(
                convergence_status="converged", vision_calls=4,
            ),
        )
        figure_presentation.persist_suggestions("doc-1", [record])
        _sql, params = session.executed[0]
        payload = json.loads(params["iterative_analysis"])
        assert payload["convergence_status"] == "converged"
        assert payload["vision_calls"] == 4


# ===========================================================================
# core.figure_presentation: iterative_analysis_payload
# ===========================================================================


class TestIterativeAnalysisPayload:
    def test_none_is_unavailable(self):
        assert figure_presentation.iterative_analysis_payload(None) == {"available": False}

    def test_empty_dict_is_unavailable(self):
        assert figure_presentation.iterative_analysis_payload({}) == {"available": False}

    def test_confidence_is_recursively_stripped_and_labeled(self):
        raw = {
            "convergence_status": "converged",
            "context_hypothesis": {
                "confidence": 0.9,
                "expected_elements": [{"confidence": 0.2, "name": "Laser"}],
            },
            "alignment_items": [{"item_id": "align_1", "confidence": 0.6, "label": "Laser"}],
        }
        payload = figure_presentation.iterative_analysis_payload(raw)
        assert payload["available"] is True
        assert "confidence" not in payload["context_hypothesis"]
        assert payload["context_hypothesis"]["confidence_label"] == "確度高"
        assert (
            payload["context_hypothesis"]["expected_elements"][0]["confidence_label"] == "暫定"
        )
        assert "confidence" not in payload["alignment_items"][0]
        assert payload["alignment_items"][0]["confidence_label"] == "参考"

    def test_review_questions_get_deterministic_question_id_when_missing(self):
        raw = {
            "review_questions": [
                {"question": "Is X real?"},
                {"question_id": "custom", "question": "Y?"},
            ],
        }
        payload = figure_presentation.iterative_analysis_payload(raw)
        assert payload["review_questions"][0]["question_id"] == "q_0"
        assert payload["review_questions"][1]["question_id"] == "custom"

    def test_never_leaks_raw_confidence_anywhere_in_output(self):
        raw = {
            "alignment_items": [{"item_id": "a", "confidence": 0.42}],
            "alternative_hypotheses": [{"hypothesis_id": "h", "confidence": 0.9}],
            "verification_iterations": [{"iteration_index": 0, "notes": "n"}],
        }
        payload = figure_presentation.iterative_analysis_payload(raw)
        dumped = json.dumps(payload)
        assert '"confidence"' not in dumped
        assert '"confidence_label"' in dumped


# ===========================================================================
# core.figure_presentation: presentation_payload propagation
# ===========================================================================


def test_presentation_payload_includes_iterative_analysis_key():
    payload = figure_presentation.presentation_payload({
        "suggested_mode": "functional_diagram",
        "reviewed_mode": None,
        "analysis_profile": {},
        "iterative_analysis": {"convergence_status": "converged"},
    })
    assert payload["iterative_analysis"]["available"] is True
    assert payload["iterative_analysis"]["convergence_status"] == "converged"


def test_presentation_payload_reports_unavailable_when_no_iterative_data():
    payload = figure_presentation.presentation_payload({
        "suggested_mode": "unknown",
        "reviewed_mode": None,
        "analysis_profile": {},
    })
    assert payload["iterative_analysis"] == {"available": False}


# ===========================================================================
# core.document_pipeline.figure_images: re-extraction reset + SELECT/decode
# ===========================================================================


def test_reextraction_upsert_resets_iterative_analysis():
    source = inspect.getsource(figure_images_module._save_figure)
    conflict_update = source.split("ON CONFLICT", 1)[1]
    assert "iterative_analysis = '{}'::jsonb" in conflict_update
    # 052/053 の既存パターン踏襲: reviewed_* 系はリセット対象に含まれない。
    assert "reviewed_mode =" not in conflict_update


class _FakeRowsSession:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *_args, **_kwargs):
        return self

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def close(self):
        pass


def _figure_row_dict(**overrides) -> dict:
    row = {
        "id": "fig-1", "document_id": "doc-1", "run_id": None, "figure_key": "fig_1",
        "figure_label": None, "page": 1, "bbox": None, "caption_block_id": None,
        "caption_text": "", "minio_key": None, "extraction_method": "embedded",
        "region_confidence": None, "status": "extracted", "created_at": None,
        "inner_labels": "[]", "suggested_mode": "unknown", "mode_reason": "",
        "analysis_profile": "{}", "reviewed_mode": None, "mode_review_status": "pending",
        "mode_reviewed_by": None, "mode_reviewed_at": None, "reviewed_analysis_mode": None,
        "reviewed_analysis_profile": "{}", "analysis_review_status": "pending",
        "analysis_reviewed_by": None, "analysis_reviewed_at": None,
        "analysis_review_source_annotation_id": None,
        "iterative_analysis": "{}",
    }
    row.update(overrides)
    return row


class TestLoadDocumentFiguresIterativeAnalysis:
    def test_decodes_json_string(self, monkeypatch):
        row = _figure_row_dict(
            iterative_analysis=json.dumps({"convergence_status": "converged"})
        )
        monkeypatch.setattr(
            figure_images_module, "_pg_session", lambda: _FakeRowsSession([row])
        )
        result = figure_images_module.load_document_figures("doc-1")
        assert result[0]["iterative_analysis"] == {"convergence_status": "converged"}

    def test_defaults_missing_or_none_to_empty_dict(self, monkeypatch):
        row = _figure_row_dict(iterative_analysis=None)
        monkeypatch.setattr(
            figure_images_module, "_pg_session", lambda: _FakeRowsSession([row])
        )
        result = figure_images_module.load_document_figures("doc-1")
        assert result[0]["iterative_analysis"] == {}

    def test_passes_through_already_decoded_dict(self, monkeypatch):
        row = _figure_row_dict(iterative_analysis={"convergence_status": "no_progress"})
        monkeypatch.setattr(
            figure_images_module, "_pg_session", lambda: _FakeRowsSession([row])
        )
        result = figure_images_module.load_document_figures("doc-1")
        assert result[0]["iterative_analysis"] == {"convergence_status": "no_progress"}


# ===========================================================================
# core.config: apparatus iterative settings defaults
# ===========================================================================


class TestApparatusIterativeSettingsDefaults:
    def test_analysis_mode_defaults_to_iterative(self):
        assert config_module.Settings.model_fields["apparatus_analysis_mode"].default == "iterative"

    def test_verify_max_iterations_defaults_to_3(self):
        assert (
            config_module.Settings.model_fields["apparatus_verify_max_iterations"].default == 3
        )

    def test_reanalyze_max_iterations_defaults_to_1(self):
        assert (
            config_module.Settings.model_fields["apparatus_reanalyze_max_iterations"].default
            == 1
        )


# ===========================================================================
# core.document_pipeline.orchestrator: IterativeConfig wiring + prefilter +
# done_payload measurement
# ===========================================================================


def _figure_row(key: str) -> dict:
    return {
        "id": key,
        "figure_key": key,
        "figure_label": None,
        "caption_text": "",
        "caption_block_id": None,
        "status": "extracted",
        "minio_key": f"figures/{key}.png",
        "inner_labels": [],
    }


def _orch_settings(**overrides) -> SimpleNamespace:
    values = dict(
        apparatus_max_images_per_document=20,
        apparatus_context_max_items=12,
        apparatus_context_max_chars=6000,
        apparatus_retrieval_top_k=5,
        apparatus_max_calls_per_day=30,
        apparatus_analysis_mode="iterative",
        apparatus_verify_max_iterations=3,
        apparatus_llm_model="gpt-4o",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


class _FakeOrchStorage:
    def get_object(self, bucket, key):
        return b"\x89PNG\r\n\x1a\nrestofpngbytes"


class _CapturingApparatusAgent:
    def __init__(self, captured, records):
        self._captured = captured
        self._records = records

    def run(self, *, document_id, figures, library_candidates=None, cartridge_id=None):
        self._captured["figures"] = figures
        return SimpleNamespace(apparatus_records=self._records)


def _agent_factory(captured, records):
    def _factory(cartridge_id=None, llm_client=None, cartridge_loader=None, iterative_config=None):
        captured["cartridge_id"] = cartridge_id
        captured["iterative_config"] = iterative_config
        return _CapturingApparatusAgent(captured, records)
    return _factory


def _patch_orch_common(monkeypatch, *, settings, daily_remaining, figures, records, captured):
    import episteme_graph.agents.apparatus_semantics.agent as apparatus_agent_module

    monkeypatch.setattr(
        figure_images_module, "load_document_figures", lambda _doc: figures,
    )
    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    monkeypatch.setattr(storage_module, "get_storage_client", lambda: _FakeOrchStorage())
    monkeypatch.setattr(orch, "_apparatus_daily_remaining", lambda _settings: daily_remaining)
    monkeypatch.setattr(
        apparatus_agent_module, "ApparatusSemanticsAgent", _agent_factory(captured, records),
    )


class TestOrchestratorIterativeConfigWiring:
    def test_iterative_mode_builds_enabled_config_from_settings(self, monkeypatch):
        captured: dict = {}
        settings = _orch_settings(apparatus_verify_max_iterations=2)
        _patch_orch_common(
            monkeypatch, settings=settings, daily_remaining=10,
            figures=[_figure_row("fig_1")], records=[], captured=captured,
        )
        orch._build_apparatus_semantics(
            document_id="doc1", cartridge_id=None, fig_tbl=None, structure=None,
        )
        cfg = captured["iterative_config"]
        assert cfg.enabled is True
        assert cfg.max_iterations == 2
        assert cfg.vision_call_budget == 10
        assert cfg.model_name == "gpt-4o"

    def test_one_shot_mode_disables_iterative_config(self, monkeypatch):
        captured: dict = {}
        settings = _orch_settings(apparatus_analysis_mode="one_shot")
        _patch_orch_common(
            monkeypatch, settings=settings, daily_remaining=10,
            figures=[_figure_row("fig_1")], records=[], captured=captured,
        )
        orch._build_apparatus_semantics(
            document_id="doc1", cartridge_id=None, fig_tbl=None, structure=None,
        )
        assert captured["iterative_config"].enabled is False


class TestOrchestratorPrefilter:
    def test_at_least_one_figure_allowed_even_with_tight_budget(self, monkeypatch):
        captured: dict = {}
        settings = _orch_settings(apparatus_verify_max_iterations=3)
        figures = [_figure_row(f"fig_{i}") for i in range(3)]
        _patch_orch_common(
            monkeypatch, settings=settings, daily_remaining=1,
            figures=figures, records=[], captured=captured,
        )
        _result, done_payload = orch._build_apparatus_semantics(
            document_id="doc1", cartridge_id=None, fig_tbl=None, structure=None,
        )
        assert len(captured["figures"]) == 1
        assert done_payload["skipped_by_limit"] == ["fig_1", "fig_2"]

    def test_cost_per_figure_is_1_plus_max_iterations(self, monkeypatch):
        captured: dict = {}
        settings = _orch_settings(apparatus_verify_max_iterations=3)  # cost = 4/figure
        figures = [_figure_row(f"fig_{i}") for i in range(3)]
        _patch_orch_common(
            monkeypatch, settings=settings, daily_remaining=8,  # 8 // 4 == 2
            figures=figures, records=[], captured=captured,
        )
        _result, done_payload = orch._build_apparatus_semantics(
            document_id="doc1", cartridge_id=None, fig_tbl=None, structure=None,
        )
        assert len(captured["figures"]) == 2
        assert done_payload["skipped_by_limit"] == ["fig_2"]

    def test_one_shot_prefilter_uses_daily_remaining_directly(self, monkeypatch):
        captured: dict = {}
        settings = _orch_settings(apparatus_analysis_mode="one_shot")
        figures = [_figure_row(f"fig_{i}") for i in range(3)]
        _patch_orch_common(
            monkeypatch, settings=settings, daily_remaining=2,
            figures=figures, records=[], captured=captured,
        )
        _result, done_payload = orch._build_apparatus_semantics(
            document_id="doc1", cartridge_id=None, fig_tbl=None, structure=None,
        )
        assert len(captured["figures"]) == 2
        assert done_payload["skipped_by_limit"] == ["fig_2"]


class TestOrchestratorDonePayloadMeasurement:
    def test_reports_iterative_mode_flag(self, monkeypatch):
        captured: dict = {}
        settings = _orch_settings()
        _patch_orch_common(
            monkeypatch, settings=settings, daily_remaining=10,
            figures=[_figure_row("fig_1")], records=[], captured=captured,
        )
        _result, done_payload = orch._build_apparatus_semantics(
            document_id="doc1", cartridge_id=None, fig_tbl=None, structure=None,
        )
        assert done_payload["iterative_mode"] is True

    def test_vision_calls_and_convergence_are_measured_from_records(self, monkeypatch):
        captured: dict = {}
        settings = _orch_settings()
        figures = [_figure_row("fig_1"), _figure_row("fig_2")]
        records = [
            SimpleNamespace(
                figure_id="fig_1", suggested_mode="functional_diagram",
                iterative_analysis=SimpleNamespace(
                    vision_calls=3, convergence_status="converged",
                ),
            ),
            SimpleNamespace(
                figure_id="fig_2", suggested_mode="unknown",
                iterative_analysis=SimpleNamespace(
                    vision_calls=2, convergence_status="max_iterations_reached",
                ),
            ),
        ]
        _patch_orch_common(
            monkeypatch, settings=settings, daily_remaining=10,
            figures=figures, records=records, captured=captured,
        )
        _result, done_payload = orch._build_apparatus_semantics(
            document_id="doc1", cartridge_id=None, fig_tbl=None, structure=None,
        )
        assert done_payload["vision_calls"] == 5
        assert done_payload["convergence"] == {
            "converged": 1,
            "max_iterations_reached": 1,
            "no_progress": 0,
            "aborted_error": 0,
            "aborted_cost_limit": 0,
        }

    def test_vision_calls_falls_back_to_image_bytes_when_no_iterative_analysis(
        self, monkeypatch,
    ):
        captured: dict = {}
        settings = _orch_settings(apparatus_analysis_mode="one_shot")
        figures = [_figure_row("fig_1")]
        records = [
            SimpleNamespace(figure_id="fig_1", suggested_mode="unknown", iterative_analysis=None),
        ]
        _patch_orch_common(
            monkeypatch, settings=settings, daily_remaining=10,
            figures=figures, records=records, captured=captured,
        )
        _result, done_payload = orch._build_apparatus_semantics(
            document_id="doc1", cartridge_id=None, fig_tbl=None, structure=None,
        )
        assert done_payload["vision_calls"] == 1
        assert "convergence" not in done_payload


# ===========================================================================
# core.figure_reanalysis: default agent receives IterativeConfig
# ===========================================================================

FIGURE_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


class _IRStorage:
    def __init__(self, content: bytes = b"image"):
        self.content = content

    def get_object(self, bucket, key):
        return self.content


class _IRAgent:
    def __init__(self, result):
        self.result = result
        self.inputs = []

    def run(self, **kwargs):
        self.inputs.append(kwargs)
        return self.result


def _ir_row(**overrides) -> dict:
    row = {
        "id": FIGURE_ID,
        "figure_key": "p1_i0",
        "figure_label": None,
        "caption_text": "Optical system",
        "status": "extracted",
        "minio_key": "doc/p1_i0.png",
        "bbox": [0.0, 0.0, 100.0, 100.0],
        "inner_labels": [
            {"text": "Laser", "bbox": [0.0, 0.0, 10.0, 10.0]},
            {"text": "Detector", "bbox": [60.0, 60.0, 90.0, 90.0]},
        ],
        "iterative_analysis": {
            "alignment_items": [
                {
                    "item_id": "align_1",
                    "label": "Laser",
                    "text_evidence": "a laser diode",
                    "label_ref": "Laser",
                    "status": "text_only",
                },
            ],
            "review_questions": [
                {"question": "Is the detector a photodiode?"},
            ],
            "unresolved_conflicts": [
                {"item_id": "conflict_1", "description": "beam direction unclear"},
            ],
        },
    }
    row.update(overrides)
    return row


def _ir_result(mode: str = "functional_diagram"):
    from episteme_graph.agents.apparatus_semantics.schema import (
        ApparatusRecord,
        ApparatusSemanticsResult,
    )

    profile = {
        "overall_function": "光を検出する",
        "functions": [
            {
                "id": "laser", "name": "Laser", "role": "光を出す",
                "inputs": [], "outputs": [{"id": "laser_out", "name": "光"}],
            },
            {
                "id": "detector", "name": "Detector", "role": "光を検出する",
                "inputs": [{"id": "detector_in", "name": "光"}], "outputs": [],
            },
        ],
        "connections": [{
            "id": "beam", "from_function_id": "laser", "from_output_id": "laser_out",
            "to_function_id": "detector", "to_input_id": "detector_in", "relation": "光",
        }],
    }
    record = ApparatusRecord(
        figure_id=FIGURE_ID, figure_key="p1_i0", apparatus_name_candidate="Optical system",
        matched_library_entry_id=None, matched_library_version_no=None, match_status="novel",
        evidence_quote="Laser and Detector labels", reason="vision", confidence=0.8,
        suggested_mode=mode, mode_reason="vision", analysis_profile=profile,
    )
    return ApparatusSemanticsResult(
        document_id="doc-1", cartridge_id=None, apparatus_records=[record], validation_issues=[],
    )


def _patch_ir_dependencies(monkeypatch, *, settings_overrides=None):
    monkeypatch.setattr(figure_reanalysis, "load_document_figures", lambda _doc: [_ir_row()])
    monkeypatch.setattr(
        figure_reanalysis, "_latest_context", lambda _doc, _row: (None, None, None),
    )
    monkeypatch.setattr(
        figure_reanalysis,
        "collect_figure_context",
        lambda *_args, **_kwargs: FigureContext(nearby_text=[], abbreviations={}),
    )
    values = dict(
        apparatus_context_max_items=12,
        apparatus_context_max_chars=6000,
        apparatus_max_calls_per_day=100,
        apparatus_analysis_mode="iterative",
        apparatus_reanalyze_max_iterations=1,
        apparatus_llm_model="gpt-4o",
    )
    values.update(settings_overrides or {})
    monkeypatch.setattr(figure_reanalysis, "get_settings", lambda: SimpleNamespace(**values))
    monkeypatch.setattr(figure_reanalysis, "persist_suggestions", lambda *_a, **_k: 1)
    monkeypatch.setattr(
        figure_reanalysis.deliberation_store,
        "create_annotation",
        lambda ref, **kwargs: {"id": "candidate-1", "status": "candidate", **kwargs},
    )


class TestFigureReanalysisDefaultAgentIterativeConfig:
    def test_default_agent_receives_iterative_config_from_settings(self, monkeypatch):
        _patch_ir_dependencies(monkeypatch, settings_overrides={
            "apparatus_analysis_mode": "iterative",
            "apparatus_reanalyze_max_iterations": 1,
            "apparatus_llm_model": "gpt-4.1",
        })
        captured: dict = {}

        def _factory(cartridge_id=None, iterative_config=None, **_kwargs):
            captured["cartridge_id"] = cartridge_id
            captured["iterative_config"] = iterative_config
            return _IRAgent(_ir_result())

        monkeypatch.setattr(figure_reanalysis, "ApparatusSemanticsAgent", _factory)
        figure_reanalysis.reanalyze_figure(
            "doc-1", FIGURE_ID, created_by="user-1",
            storage=_IRStorage(), enforce_cost_gate=False,
        )
        cfg = captured["iterative_config"]
        assert cfg.enabled is True
        assert cfg.max_iterations == 1
        assert cfg.vision_call_budget is None
        assert cfg.model_name == "gpt-4.1"

    def test_one_shot_setting_disables_default_agent_iterative_config(self, monkeypatch):
        _patch_ir_dependencies(
            monkeypatch, settings_overrides={"apparatus_analysis_mode": "one_shot"},
        )
        captured: dict = {}

        def _factory(cartridge_id=None, iterative_config=None, **_kwargs):
            captured["iterative_config"] = iterative_config
            return _IRAgent(_ir_result())

        monkeypatch.setattr(figure_reanalysis, "ApparatusSemanticsAgent", _factory)
        figure_reanalysis.reanalyze_figure(
            "doc-1", FIGURE_ID, created_by="user-1",
            storage=_IRStorage(), enforce_cost_gate=False,
        )
        assert captured["iterative_config"].enabled is False

    def test_explicitly_injected_agent_is_used_as_is(self, monkeypatch):
        """An explicit ``agent=`` bypasses the default-agent construction path
        entirely (every existing guided/unguided test relies on exactly this)."""
        _patch_ir_dependencies(monkeypatch)

        def _fail_factory(*_args, **_kwargs):
            pytest.fail("ApparatusSemanticsAgent must not be constructed when agent= is given")

        monkeypatch.setattr(figure_reanalysis, "ApparatusSemanticsAgent", _fail_factory)
        agent = _IRAgent(_ir_result())
        figure_reanalysis.reanalyze_figure(
            "doc-1", FIGURE_ID, created_by="user-1",
            agent=agent, storage=_IRStorage(), enforce_cost_gate=False,
        )
        assert agent.inputs


# ===========================================================================
# core.figure_reanalysis: daily vision-call metering (#499 review P1)
#
# Prior to this fix, ``reanalyze_figure`` built the default agent's
# IterativeConfig with ``vision_call_budget=None`` (unlimited) while
# ``_consume_budget`` only ever incremented the daily counter by 1 per
# request — even though the iterative engine can spend up to 1 (observation)
# + max_iterations * (1 verification), each with up to 2 repair attempts, in
# vision calls for a single ``analyzer.run()`` call. That let one synchronous
# re-analysis request blow past ``APPARATUS_MAX_CALLS_PER_DAY`` by ~4x. The
# fix passes the live remaining daily allowance as the default agent's
# vision_call_budget and reconciles actual spend (``record.iterative_analysis
# .vision_calls``) into the shared daily counter after every run, whether the
# agent was built here or injected by the caller.
# ===========================================================================


def _ir_result_with_vision_calls(vision_calls: int, *, mode: str = "functional_diagram"):
    """Like ``_ir_result`` but with a populated ``iterative_analysis`` so the
    post-run reconciliation logic under test has real data to read. Reuses
    ``_ir_result``'s non-empty profile so ``normalize_figure_analysis_candidate``
    still accepts the record (an empty profile is rejected as insufficient)."""
    from dataclasses import replace

    from episteme_graph.agents.apparatus_semantics.schema import IterativeAnalysisRecord

    base_record = _ir_result(mode).apparatus_records[0]
    record = replace(
        base_record,
        iterative_analysis=IterativeAnalysisRecord(
            convergence_status="converged", vision_calls=vision_calls,
        ),
    )
    return _ir_result_from_record(record)


def _ir_result_from_record(record):
    from episteme_graph.agents.apparatus_semantics.schema import ApparatusSemanticsResult

    return ApparatusSemanticsResult(
        document_id="doc-1", cartridge_id=None, apparatus_records=[record], validation_issues=[],
    )


class TestFigureReanalysisCostGateMetering:
    def _fresh_gate(self, monkeypatch):
        from core.llm_worker.cost_gate import CostGate

        gate = CostGate()
        monkeypatch.setattr(figure_reanalysis, "_cost_gate", gate)
        return gate

    def test_actual_vision_calls_are_reconciled_into_daily_counter(self, monkeypatch):
        """A fake agent reporting 4 vision calls must add up to 4 total in
        the daily counter (1 from _consume_budget + 3 reconciled extra)."""
        gate = self._fresh_gate(monkeypatch)
        _patch_ir_dependencies(
            monkeypatch, settings_overrides={"apparatus_max_calls_per_day": 100}
        )
        agent = _IRAgent(_ir_result_with_vision_calls(4))
        figure_reanalysis.reanalyze_figure(
            "doc-1", FIGURE_ID, created_by="user-1",
            agent=agent, storage=_IRStorage(), enforce_cost_gate=True,
        )
        key = figure_reanalysis._daily_budget_key("user-1")
        assert gate.daily_counts[key] == 4

    def test_default_agent_receives_remaining_daily_allowance_as_vision_budget(
        self, monkeypatch,
    ):
        gate = self._fresh_gate(monkeypatch)
        _patch_ir_dependencies(
            monkeypatch, settings_overrides={"apparatus_max_calls_per_day": 5}
        )
        key = figure_reanalysis._daily_budget_key("user-1")
        gate.daily_counts[key] = 3  # already consumed 3 of today's 5 before this call

        captured: dict = {}

        def _factory(cartridge_id=None, iterative_config=None, **_kwargs):
            captured["iterative_config"] = iterative_config
            return _IRAgent(_ir_result_with_vision_calls(1))

        monkeypatch.setattr(figure_reanalysis, "ApparatusSemanticsAgent", _factory)
        figure_reanalysis.reanalyze_figure(
            "doc-1", FIGURE_ID, created_by="user-1",
            storage=_IRStorage(), enforce_cost_gate=True,
        )
        # _consume_budget takes the counter from 3 -> 4 (allowed, limit=5).
        # remaining = 5 - 4 = 1; budget = 1 (already counted) + 1 (remaining) = 2.
        assert captured["iterative_config"].vision_call_budget == 2

    def test_limit_already_reached_raises_before_default_agent_is_built(self, monkeypatch):
        gate = self._fresh_gate(monkeypatch)
        _patch_ir_dependencies(
            monkeypatch, settings_overrides={"apparatus_max_calls_per_day": 2}
        )
        key = figure_reanalysis._daily_budget_key("user-1")
        gate.daily_counts[key] = 2  # already at today's limit

        def _fail_factory(*_args, **_kwargs):
            pytest.fail(
                "ApparatusSemanticsAgent must not be constructed once the daily limit is hit"
            )

        monkeypatch.setattr(figure_reanalysis, "ApparatusSemanticsAgent", _fail_factory)
        with pytest.raises(FigureReanalysisError) as excinfo:
            figure_reanalysis.reanalyze_figure(
                "doc-1", FIGURE_ID, created_by="user-1",
                storage=_IRStorage(), enforce_cost_gate=True,
            )
        assert excinfo.value.kind == "limit"

    def test_enforce_cost_gate_false_keeps_unlimited_budget_and_no_accounting(
        self, monkeypatch,
    ):
        gate = self._fresh_gate(monkeypatch)
        _patch_ir_dependencies(monkeypatch)
        captured: dict = {}

        def _factory(cartridge_id=None, iterative_config=None, **_kwargs):
            captured["iterative_config"] = iterative_config
            return _IRAgent(_ir_result_with_vision_calls(4))

        monkeypatch.setattr(figure_reanalysis, "ApparatusSemanticsAgent", _factory)
        figure_reanalysis.reanalyze_figure(
            "doc-1", FIGURE_ID, created_by="user-1",
            storage=_IRStorage(), enforce_cost_gate=False,
        )
        assert captured["iterative_config"].vision_call_budget is None
        assert gate.daily_counts == {}

    def test_externally_injected_agent_is_still_metered(self, monkeypatch):
        """An externally-configured agent's IterativeConfig is left untouched,
        but its actual vision spend must still be reconciled into the shared
        daily counter when enforce_cost_gate=True."""
        gate = self._fresh_gate(monkeypatch)
        _patch_ir_dependencies(
            monkeypatch, settings_overrides={"apparatus_max_calls_per_day": 100}
        )
        agent = _IRAgent(_ir_result_with_vision_calls(3))
        figure_reanalysis.reanalyze_figure(
            "doc-1", FIGURE_ID, created_by="user-1",
            agent=agent, storage=_IRStorage(), enforce_cost_gate=True,
        )
        key = figure_reanalysis._daily_budget_key("user-1")
        assert gate.daily_counts[key] == 3  # 1 (consume_budget) + 2 (extra)

    def test_record_without_iterative_analysis_keeps_one_call_approximation(
        self, monkeypatch,
    ):
        gate = self._fresh_gate(monkeypatch)
        _patch_ir_dependencies(
            monkeypatch, settings_overrides={"apparatus_max_calls_per_day": 100}
        )
        agent = _IRAgent(_ir_result())  # default iterative_analysis=None (one_shot shape)
        figure_reanalysis.reanalyze_figure(
            "doc-1", FIGURE_ID, created_by="user-1",
            agent=agent, storage=_IRStorage(), enforce_cost_gate=True,
        )
        key = figure_reanalysis._daily_budget_key("user-1")
        assert gate.daily_counts[key] == 1


# ===========================================================================
# core.figure_reanalysis: unresolved_item_ids
# ===========================================================================


class TestNormalizeGuidanceUnresolvedItemIds:
    def test_accepts_unresolved_item_ids(self):
        result = figure_reanalysis._normalize_guidance(
            {"unresolved_item_ids": ["align_1", "q_0"]}
        )
        assert result == {
            "hint_text": None, "focus_bbox": None,
            "unresolved_item_ids": ["align_1", "q_0"],
        }

    def test_key_omitted_entirely_when_absent(self):
        """Back-compat: existing hint/focus-only call sites' exact-equality
        assertions must be unaffected by this additive field."""
        result = figure_reanalysis._normalize_guidance({"hint_text": "hello"})
        assert result == {"hint_text": "hello", "focus_bbox": None}
        assert "unresolved_item_ids" not in result

    def test_more_than_ten_ids_rejected(self):
        with pytest.raises(FigureReanalysisError) as excinfo:
            figure_reanalysis._normalize_guidance(
                {"unresolved_item_ids": [f"id_{i}" for i in range(11)]}
            )
        assert excinfo.value.kind == "invalid"

    def test_exactly_ten_ids_accepted(self):
        ids = [f"id_{i}" for i in range(10)]
        result = figure_reanalysis._normalize_guidance({"unresolved_item_ids": ids})
        assert result["unresolved_item_ids"] == ids

    def test_non_string_item_rejected(self):
        with pytest.raises(FigureReanalysisError) as excinfo:
            figure_reanalysis._normalize_guidance({"unresolved_item_ids": ["ok", 5]})
        assert excinfo.value.kind == "invalid"

    def test_empty_string_item_rejected(self):
        with pytest.raises(FigureReanalysisError):
            figure_reanalysis._normalize_guidance({"unresolved_item_ids": [""]})

    def test_too_long_item_rejected(self):
        with pytest.raises(FigureReanalysisError):
            figure_reanalysis._normalize_guidance({"unresolved_item_ids": ["a" * 65]})

    def test_64_char_item_accepted(self):
        item_id = "a" * 64
        result = figure_reanalysis._normalize_guidance({"unresolved_item_ids": [item_id]})
        assert result["unresolved_item_ids"] == [item_id]

    def test_not_a_list_rejected(self):
        with pytest.raises(FigureReanalysisError):
            figure_reanalysis._normalize_guidance({"unresolved_item_ids": "align_1"})

    def test_empty_list_is_treated_as_absent(self):
        assert figure_reanalysis._normalize_guidance({"unresolved_item_ids": []}) is None


class TestResolveUnresolvedItems:
    def test_raises_for_unknown_id(self):
        with pytest.raises(FigureReanalysisError) as excinfo:
            figure_reanalysis._resolve_unresolved_items(
                {"alignment_items": [{"item_id": "align_1"}]}, ["unknown_id"],
            )
        assert excinfo.value.kind == "invalid"

    def test_resolves_across_all_three_collections(self):
        data = {
            "alignment_items": [{"item_id": "align_1", "label": "L"}],
            "review_questions": [{"question": "Q?"}],
            "unresolved_conflicts": [{"item_id": "conflict_1", "description": "D"}],
        }
        resolved = figure_reanalysis._resolve_unresolved_items(
            data, ["align_1", "q_0", "conflict_1"]
        )
        assert [kind for kind, _ in resolved] == [
            "alignment_item", "review_question", "unresolved_conflict",
        ]

    def test_none_iterative_analysis_treated_as_empty(self):
        with pytest.raises(FigureReanalysisError):
            figure_reanalysis._resolve_unresolved_items(None, ["align_1"])


class TestReanalyzeFigureWithUnresolvedItemIds:
    def test_synthesizes_hint_and_focus_bbox_from_alignment_item(self, monkeypatch):
        _patch_ir_dependencies(monkeypatch)
        captured_annotation: dict = {}

        def fake_create(ref, **kwargs):
            captured_annotation.update(kwargs)
            return {"id": "candidate-1", "status": "candidate", **kwargs}

        monkeypatch.setattr(
            figure_reanalysis.deliberation_store, "create_annotation", fake_create
        )
        agent = _IRAgent(_ir_result())
        result = figure_reanalysis.reanalyze_figure(
            "doc-1", FIGURE_ID, created_by="user-1",
            guidance={"unresolved_item_ids": ["align_1"]},
            agent=agent, storage=_IRStorage(), enforce_cost_gate=False,
        )
        fig_input = agent.inputs[0]["figures"][0]
        assert "未解決箇所の再確認" in fig_input.guidance_text
        assert "Laser" in fig_input.guidance_text
        assert fig_input.focus_bbox_rel is not None
        assert len(fig_input.focus_bbox_rel) == 4
        assert all(0.0 <= v <= 1.0 for v in fig_input.focus_bbox_rel)
        assert result["guidance"]["unresolved_item_ids"] == ["align_1"]
        assert captured_annotation["body"]["guidance"]["unresolved_item_ids"] == ["align_1"]

    def test_unknown_unresolved_item_id_raises_invalid(self, monkeypatch):
        _patch_ir_dependencies(monkeypatch)
        with pytest.raises(FigureReanalysisError) as excinfo:
            figure_reanalysis.reanalyze_figure(
                "doc-1", FIGURE_ID, created_by="user-1",
                guidance={"unresolved_item_ids": ["nope"]},
                agent=_IRAgent(_ir_result()), storage=_IRStorage(), enforce_cost_gate=False,
            )
        assert excinfo.value.kind == "invalid"

    def test_user_hint_text_is_kept_first(self, monkeypatch):
        _patch_ir_dependencies(monkeypatch)
        agent = _IRAgent(_ir_result())
        figure_reanalysis.reanalyze_figure(
            "doc-1", FIGURE_ID, created_by="user-1",
            guidance={"hint_text": "ユーザーの指示", "unresolved_item_ids": ["align_1"]},
            agent=agent, storage=_IRStorage(), enforce_cost_gate=False,
        )
        fig_input = agent.inputs[0]["figures"][0]
        assert fig_input.guidance_text.startswith("ユーザーの指示")
        assert "未解決箇所の再確認" in fig_input.guidance_text

    def test_explicit_focus_bbox_is_not_overridden_by_synthesis(self, monkeypatch):
        _patch_ir_dependencies(monkeypatch)
        agent = _IRAgent(_ir_result())
        figure_reanalysis.reanalyze_figure(
            "doc-1", FIGURE_ID, created_by="user-1",
            guidance={
                "focus_bbox": [0.2, 0.2, 0.8, 0.8],
                "unresolved_item_ids": ["align_1"],
            },
            agent=agent, storage=_IRStorage(), enforce_cost_gate=False,
        )
        fig_input = agent.inputs[0]["figures"][0]
        assert fig_input.focus_bbox_rel == [0.2, 0.2, 0.8, 0.8]

    def test_no_label_ref_match_falls_back_to_hint_text_only(self, monkeypatch):
        _patch_ir_dependencies(monkeypatch)
        agent = _IRAgent(_ir_result())
        figure_reanalysis.reanalyze_figure(
            "doc-1", FIGURE_ID, created_by="user-1",
            guidance={"unresolved_item_ids": ["q_0"]},  # review_question has no label_ref
            agent=agent, storage=_IRStorage(), enforce_cost_gate=False,
        )
        fig_input = agent.inputs[0]["figures"][0]
        assert fig_input.focus_bbox_rel is None
        assert "Is the detector a photodiode" in fig_input.guidance_text

    def test_resolves_unresolved_conflict_item(self, monkeypatch):
        _patch_ir_dependencies(monkeypatch)
        agent = _IRAgent(_ir_result())
        figure_reanalysis.reanalyze_figure(
            "doc-1", FIGURE_ID, created_by="user-1",
            guidance={"unresolved_item_ids": ["conflict_1"]},
            agent=agent, storage=_IRStorage(), enforce_cost_gate=False,
        )
        fig_input = agent.inputs[0]["figures"][0]
        assert "beam direction unclear" in fig_input.guidance_text

    def test_audit_relevant_guidance_includes_unresolved_item_ids(self, monkeypatch):
        """The route layer forwards ``result['guidance']`` verbatim into the
        audit payload — this just confirms the core result carries it."""
        _patch_ir_dependencies(monkeypatch)
        agent = _IRAgent(_ir_result())
        result = figure_reanalysis.reanalyze_figure(
            "doc-1", FIGURE_ID, created_by="user-1",
            guidance={"unresolved_item_ids": ["align_1"]},
            agent=agent, storage=_IRStorage(), enforce_cost_gate=False,
        )
        assert result["guidance"]["unresolved_item_ids"] == ["align_1"]


class TestUnresolvedItemIdsExistingPathsUnchanged:
    """§ existing unguided/hint/focus paths must remain exactly as before."""

    def test_unguided_reanalysis_still_has_no_unresolved_item_ids_key(self, monkeypatch):
        _patch_ir_dependencies(monkeypatch)
        agent = _IRAgent(_ir_result())
        result = figure_reanalysis.reanalyze_figure(
            "doc-1", FIGURE_ID, created_by="user-1",
            agent=agent, storage=_IRStorage(), enforce_cost_gate=False,
        )
        assert result["guidance"] is None
        fig_input = agent.inputs[0]["figures"][0]
        assert fig_input.guidance_text == ""
        assert fig_input.focus_bbox_rel is None

    def test_hint_only_guidance_is_unaffected(self, monkeypatch):
        _patch_ir_dependencies(monkeypatch)
        agent = _IRAgent(_ir_result())
        result = figure_reanalysis.reanalyze_figure(
            "doc-1", FIGURE_ID, created_by="user-1",
            guidance={"hint_text": "左下のEOM"},
            agent=agent, storage=_IRStorage(), enforce_cost_gate=False,
        )
        assert result["guidance"] == {"hint_text": "左下のEOM", "focus_bbox": None}
        fig_input = agent.inputs[0]["figures"][0]
        assert fig_input.guidance_text == "左下のEOM"


# ===========================================================================
# Guardrails (#499)
# ===========================================================================


class TestGuardrails:
    def test_persist_suggestions_never_touches_reviewed_columns(self):
        source = inspect.getsource(figure_presentation.persist_suggestions)
        assert "reviewed_mode" not in source
        assert "reviewed_analysis_mode" not in source
        assert "reviewed_analysis_profile" not in source

    def test_strip_confidence_replaces_raw_confidence_key(self):
        source = inspect.getsource(figure_presentation._strip_confidence)
        assert 'result["confidence_label"]' in source
        assert 'key == "confidence"' in source

    def test_iterative_analysis_payload_routes_every_key_through_strip_confidence(self):
        source = inspect.getsource(figure_presentation.iterative_analysis_payload)
        assert "_strip_confidence(data.get(key))" in source

    def test_figure_reanalysis_iterative_wiring_does_not_touch_review_status(self):
        source = inspect.getsource(figure_reanalysis)
        assert "review_status =" not in source
        assert "source_backing_status =" not in source

    def test_core_modules_do_not_import_fastapi(self):
        for module in (figure_presentation, figure_images_module, orch, figure_reanalysis):
            source = inspect.getsource(module)
            assert "import fastapi" not in source
            assert "from fastapi" not in source
