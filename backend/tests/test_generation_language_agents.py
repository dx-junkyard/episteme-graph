"""生成言語（run options ``language``）が A層の 4 ステージへ届くこと（IK-0571）。

- ``_run_language(ctx)`` が run options を読む唯一の入口で、``_language_run_kwargs`` は
  **未指定なら空 dict**（agent.run の呼び出しは従来と 1 文字も変わらない）。
- paper_skeleton / thesis_reconstruction / component_assembly / narrative_annotator の
  run に ``language=`` が渡る。
- LLM ステージの登録（``LLM_CALLING_STAGE_NAMES`` 等）は変えない。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT / "src", ROOT / "backend"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from core.document_pipeline import orchestrator as orch  # noqa: E402


class _Stop(Exception):
    pass


def _recording_agent(seen: list):
    class _Agent:
        def run(self, *args, **kwargs):
            # 未指定の run では kwarg 自体が来ない（従来の呼び出しと同一）。
            seen.append(kwargs.get("language", "<absent>"))
            raise _Stop

    return _Agent()


def _ctx(options: dict, agent_classes: dict | None = None, **extra) -> SimpleNamespace:
    base = dict(
        document_id="doc-1", material_id="mat-1", cartridge_id=None,
        effective_options=options, agent_classes=agent_classes or {},
        structure=SimpleNamespace(document_id="doc-1", blocks=[], sections=[]),
        skeleton=None, roles=None, qualified=None, equations=None, claim_objects=None,
        thesis=None, dsl=None, evidence=None, derivations=None, apparatus_result=None,
        component_graph_result=None, narrative=None,
        artifact=lambda stage: {}, should_use_artifact=lambda stage: False,
        report_start=lambda *a, **k: None, report_done=lambda *a, **k: None,
        report_item=lambda *a, **k: None, save_artifact=lambda *a, **k: None,
        finish_target_stage=lambda *a, **k: False,
    )
    base.update(extra)
    return SimpleNamespace(**base)


class TestHelpers:
    @pytest.mark.parametrize("options,expected", [
        ({"language": "ja"}, "ja"), ({"language": "EN"}, "en"),
        ({"language": "fr"}, None), ({}, None), (None, None),
    ])
    def test_run_language(self, options, expected):
        assert orch._run_language(SimpleNamespace(effective_options=options)) == expected

    def test_unset_gives_no_kwargs(self):
        seen: list = []
        assert orch._language_run_kwargs(_recording_agent(seen), _ctx({})) == {}

    def test_set_gives_kwarg_only_when_run_accepts_it(self):
        class _NoLanguage:
            def run(self, structure, cartridge_id=None):
                return None

        class _VarKw:
            def run(self, **kwargs):
                return None

        ctx = _ctx({"language": "ja"})
        assert orch._language_run_kwargs(_NoLanguage(), ctx) == {}
        assert orch._language_run_kwargs(_VarKw(), ctx) == {"language": "ja"}
        assert orch._language_run_kwargs(_recording_agent([]), ctx) == {"language": "ja"}

    def test_vocab_matches_a_layer_helper(self):
        from episteme_graph.agents.generation_language import GENERATION_LANGUAGES

        assert tuple(orch.RUN_GENERATION_LANGUAGES) == GENERATION_LANGUAGES


@pytest.mark.parametrize("language", ["ja", None])
class TestStagesPassLanguage:
    def _options(self, language):
        return {"language": language} if language else {}

    def test_paper_skeleton(self, language):
        seen: list = []
        ctx = _ctx(self._options(language), {"PaperSkeletonAgent": _recording_agent(seen)})
        with pytest.raises(orch.PipelineStageError):
            orch._stage_paper_skeleton(ctx)
        assert seen == [language or "<absent>"]

    def test_thesis_reconstruction(self, language):
        seen: list = []
        ctx = _ctx(self._options(language), {"ThesisReconstructionAgent": _recording_agent(seen)})
        with pytest.raises(orch.PipelineStageError):
            orch._stage_thesis_reconstruction(ctx)
        assert seen == [language or "<absent>"]

    def test_component_assembly(self, language):
        seen: list = []
        ctx = _ctx(self._options(language), {"ComponentAssemblyAgent": _recording_agent(seen)})
        with pytest.raises(orch.PipelineStageError):
            orch._stage_component_assembly(ctx)
        assert seen == [language or "<absent>"]

    def test_narrative_annotator(self, language, monkeypatch):
        seen: list = []
        import episteme_graph.agents.narrative_annotator.agent as na_agent

        monkeypatch.setattr(na_agent, "NarrativeAnnotator", lambda: _recording_agent(seen))
        ctx = _ctx(self._options(language))
        orch._stage_narrative_annotator(ctx)  # 非致命ステージ: 例外は握られる
        assert seen == [language or "<absent>"]


def test_llm_stage_registry_is_unchanged():
    """言語の配線で LLM ステージの集合を変えない（呼び出し回数を増やさない）。"""
    for stage in ("paper_skeleton", "thesis_reconstruction", "component_assembly", "narrative_annotator"):
        assert stage in orch.LLM_CALLING_STAGE_NAMES
    assert "figure_table_semantics" not in orch.LLM_CALLING_STAGE_NAMES
    assert "derivation_chain" not in orch.LLM_CALLING_STAGE_NAMES


# ── IK-0546: main の英語の stage 名（#308）を表示側で日本語の段名にする ──────────

class TestStageDisplayLabel:
    def test_every_agent_stage_label_translates(self):
        from core.element_vocab import THEORY_STAGE_LABELS, theory_stage_display_label
        from episteme_graph.agents.component_graph.schema import THEORY_STAGE_LABELS as AGENT_LABELS

        for key, english in AGENT_LABELS.items():
            assert theory_stage_display_label(english) == THEORY_STAGE_LABELS[key]

    def test_old_colon_form_keeps_description_and_unknown_passes_through(self):
        from core.element_vocab import theory_stage_display_label

        assert theory_stage_display_label("Theory basis: foo") == "理論の土台: foo"
        assert theory_stage_display_label("Define the lapse") == "Define the lapse"
        assert theory_stage_display_label("") == ""
        assert theory_stage_display_label(None) == ""

    def test_graph_json_label_rule_is_not_touched(self):
        """表示側の訳であって、A層の main label 規律（#308）と validator は不変。"""
        from episteme_graph.agents.component_graph import schema as cg_schema

        assert cg_schema.THEORY_STAGE_LABELS["theory_basis"] == "Theory basis"
