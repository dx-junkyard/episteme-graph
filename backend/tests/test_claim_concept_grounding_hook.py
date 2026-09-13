"""orchestrator の配線（claim_concept_grounding_design.md §5）。

固定するもの:

- フック ``_hook_claim_concept_grounding`` が ``_PIPELINE_STEPS`` の **``dsl_linking``
  の直後・``dsl_embedding`` の前**にあり、``name=None``（PIPELINE_STAGES に出ない）
- フックが ``claim_object_builder`` を保存し直し、``claim_concept_grounding`` artifact に
  出所と ``coverage``（P0-10 共通形式）を残す
- 前段（``_stage_claim_object_builder``）が辞書から resolver を作って builder へ渡し、
  辞書が空なら **None のまま**（従来動作）
- 例外はステージを落とさない（非致命）

DB にも LLM にも触らない。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.document_pipeline import orchestrator as orch  # noqa: E402
from core.library import concept_dictionary as cd  # noqa: E402


@dataclass
class _Node:
    node_id: str
    node_value: str
    node_type: str = "Observable"
    source_refs: dict = field(default_factory=dict)


@dataclass
class _Dsl:
    nodes: list = field(default_factory=list)
    edges: list = field(default_factory=list)


@dataclass
class _Claim:
    claim_id: str
    text: str
    concepts: list = field(default_factory=list)
    normalized_text: str = ""
    concept_assignment_status: str = "review_required"


@dataclass
class _ClaimObjects:
    claims: list = field(default_factory=list)


def _ctx(claim_objects, dsl, saved: dict, *, cartridge_id: str | None = None):
    return SimpleNamespace(
        document_id="doc-1",
        material_id="mat-1",
        cartridge_id=cartridge_id,
        claim_objects=claim_objects,
        dsl=dsl,
        save_artifact=lambda stage, value: saved.__setitem__(stage, value),
        artifact=lambda stage: saved.get(stage),
    )


@pytest.fixture
def empty_registry(monkeypatch):
    monkeypatch.setattr(cd.library_store, "list_entries", lambda **kw: [])
    monkeypatch.setattr(
        cd.registry, "labels_for_entries",
        lambda entry_ids, include_hidden=True, session=None: {},
    )
    monkeypatch.setattr(cd, "cartridge_ontology_for", lambda cartridge_id: None)


# ---------------------------------------------------------------------------
# 1. 登録位置
# ---------------------------------------------------------------------------


class TestHookPosition:
    def _names(self) -> list[str]:
        return [
            step.name or getattr(step.execute, "__name__", "?")
            for step in orch._PIPELINE_STEPS
        ]

    def test_hook_sits_between_dsl_linking_and_dsl_embedding(self):
        names = self._names()
        assert names.index("_hook_claim_concept_grounding") == names.index("dsl_linking") + 1
        assert names.index("dsl_embedding") == names.index("_hook_claim_concept_grounding") + 1

    def test_hook_is_not_a_named_stage(self):
        step = next(
            s for s in orch._PIPELINE_STEPS
            if s.execute is orch._hook_claim_concept_grounding
        )
        assert step.name is None
        assert step.llm_kind == orch.LLM_KIND_NONE
        assert step.model_policy is False
        assert "claim_concept_grounding" not in orch.PIPELINE_STAGES


# ---------------------------------------------------------------------------
# 2. フックの振る舞い
# ---------------------------------------------------------------------------


class TestHookBehaviour:
    def test_saves_both_artifacts_with_coverage(self, monkeypatch, empty_registry):
        dsl = _Dsl(nodes=[_Node("n1", "zero recoil limit", source_refs={"claim_ids": ["c1"]})])
        claims = _ClaimObjects(claims=[_Claim("c1", "A bound is derived."), _Claim("c2", "None.")])
        saved: dict = {}
        orch._hook_claim_concept_grounding(_ctx(claims, dsl, saved))

        assert "claim_object_builder" in saved
        payload = saved["claim_concept_grounding"]
        assert payload["claims"]["c1"][0]["source"] == cd.SOURCE_DSL_REFERENCE
        report = payload["coverage"]
        assert report["unit"] == "claims"
        assert report["population"] == 2 and report["processed"] == 1
        assert report["reasons"] == ["no_dictionary_match"]

    def test_no_change_still_records_the_coverage(self, empty_registry):
        claims = _ClaimObjects(claims=[_Claim("c1", "Nothing matches.")])
        saved: dict = {}
        orch._hook_claim_concept_grounding(_ctx(claims, _Dsl(), saved))
        assert "claim_object_builder" not in saved, "変化が無ければ保存し直さない"
        assert saved["claim_concept_grounding"]["coverage"]["processed"] == 0

    def test_failures_are_non_fatal(self, monkeypatch):
        def _boom(**kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(cd, "build_concept_dictionary", _boom)
        saved: dict = {}
        assert orch._hook_claim_concept_grounding(_ctx(_ClaimObjects(), _Dsl(), saved)) is False
        assert saved == {}

    def test_the_hook_returns_false_so_the_pipeline_continues(self, empty_registry):
        saved: dict = {}
        assert orch._hook_claim_concept_grounding(_ctx(_ClaimObjects(), _Dsl(), saved)) is False


# ---------------------------------------------------------------------------
# 3. 前段（builder への注入）
# ---------------------------------------------------------------------------


class TestBuilderInputs:
    def test_empty_dictionary_keeps_the_previous_behaviour(self, empty_registry):
        resolver, ontology = orch._claim_concept_inputs(
            SimpleNamespace(document_id="doc-1", cartridge_id=None)
        )
        assert resolver is None and ontology is None

    def test_resolver_and_ontology_are_passed_when_material_exists(self, monkeypatch):
        monkeypatch.setattr(
            cd.library_store, "list_entries",
            lambda **kw: [{"id": "e1", "name": "Form factor", "entry_type": "concept", "aliases": []}],
        )
        monkeypatch.setattr(
            cd.registry, "labels_for_entries",
            lambda entry_ids, include_hidden=True, session=None: {},
        )
        monkeypatch.setattr(
            cd, "cartridge_ontology_for",
            lambda cartridge_id: {"aliases": {"Standard Model": []}, "concept_types": {}},
        )
        resolver, ontology = orch._claim_concept_inputs(
            SimpleNamespace(document_id="doc-1", cartridge_id="particle_physics")
        )
        assert callable(resolver)
        assert ontology == {"aliases": {"Standard Model": []}, "concept_types": {}}
        assert resolver("the form factor", [], {})[0].normalized == "form factor"

    def test_dictionary_failure_is_non_fatal(self, monkeypatch):
        def _boom(**kwargs):
            raise RuntimeError("db down")

        monkeypatch.setattr(cd, "build_concept_dictionary", _boom)
        assert orch._claim_concept_inputs(
            SimpleNamespace(document_id="doc-1", cartridge_id="x")
        ) == (None, None)

    def test_build_claim_objects_forwards_both_injection_points(self):
        captured: dict = {}

        class _Builder:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def build(self, **kwargs):
                return SimpleNamespace(claims=[])

        def _resolver(text, roles, ontology):
            return []

        orch._build_claim_objects(
            agent_classes={"ClaimObjectBuilder": _Builder},
            document_id="doc-1",
            cartridge_id="particle_physics",
            qualified=SimpleNamespace(qualified_spans=[]),
            equations=SimpleNamespace(equations=[]),
            evidence=None,
            concept_resolver=_resolver,
            cartridge_ontology={"aliases": {}, "concept_types": {}},
        )
        assert captured["concept_resolver"] is _resolver
        assert captured["cartridge_ontology"] == {"aliases": {}, "concept_types": {}}

    def test_build_claim_objects_defaults_stay_none(self):
        captured: dict = {}

        class _Builder:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def build(self, **kwargs):
                return SimpleNamespace(claims=[])

        orch._build_claim_objects(
            agent_classes={"ClaimObjectBuilder": _Builder},
            document_id="doc-1",
            cartridge_id=None,
            qualified=SimpleNamespace(qualified_spans=[]),
            equations=SimpleNamespace(equations=[]),
            evidence=None,
        )
        assert captured["concept_resolver"] is None
        assert captured["cartridge_ontology"] is None


# ---------------------------------------------------------------------------
# 4. CG3: レジストリだけの辞書で concept_assignment_status が昇格しない
# ---------------------------------------------------------------------------


class TestRegistryOnlyOntologyKeepsInferred:
    """A層 builder の既存規則 ``_concepts_are_cartridge_backed`` は「ontology が空で
    resolver がある」と無条件に True を返す。cartridge が解決できない run では
    非空だが既知集合が空の ``REGISTRY_ONLY_ONTOLOGY`` を渡し、辞書照合で付いた概念が
    ``source_backed`` の根拠にならないことを固定する（CG3 / KR2）。"""

    def _registry_only(self, monkeypatch):
        monkeypatch.setattr(
            cd.library_store, "list_entries",
            lambda **kw: [{"id": "e1", "name": "Form factor", "entry_type": "concept", "aliases": []}],
        )
        monkeypatch.setattr(
            cd.registry, "labels_for_entries",
            lambda entry_ids, include_hidden=True, session=None: {},
        )
        monkeypatch.setattr(cd, "cartridge_ontology_for", lambda cartridge_id: None)

    def test_registry_only_run_passes_non_empty_provenance_ontology(self, monkeypatch):
        self._registry_only(monkeypatch)
        resolver, ontology = orch._claim_concept_inputs(
            SimpleNamespace(document_id="doc-1", cartridge_id=None)
        )
        assert callable(resolver)
        assert ontology  # 非空（空 dict は A層規則で「信頼」に倒れる）
        assert ontology["aliases"] == {} and ontology["concept_types"] == {}
        assert ontology["provenance"] == "concept_registry"

    def test_builder_rule_yields_inferred_not_source_backed(self, monkeypatch):
        from episteme_graph.agents.claim_object_builder.builder import ClaimObjectBuilder
        from episteme_graph.agents.claim_object_builder.schema import ClaimConcept

        self._registry_only(monkeypatch)
        resolver, ontology = orch._claim_concept_inputs(
            SimpleNamespace(document_id="doc-1", cartridge_id=None)
        )
        builder = ClaimObjectBuilder(concept_resolver=resolver, cartridge_ontology=ontology)
        concepts = [ClaimConcept(name="form factor", normalized="form factor", concept_type="concept")]
        assert builder._concepts_are_cartridge_backed(concepts) is False
        status = builder._concept_assignment_status(
            is_atomic=True, support_status="source_backed", concepts=concepts,
        )
        assert status == "inferred"

    def test_constant_is_not_mutated_between_runs(self, monkeypatch):
        self._registry_only(monkeypatch)
        _, ontology = orch._claim_concept_inputs(SimpleNamespace(document_id="d", cartridge_id=None))
        ontology["aliases"]["x"] = ["y"]
        assert cd.REGISTRY_ONLY_ONTOLOGY["aliases"] == {}
