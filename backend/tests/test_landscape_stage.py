"""``landscape_placement`` ステージ本体の挙動（DB / LLM 非接続）。

正本: ``docs/features/knowledge_landscape_design.md`` §7.1・§7.2・§7.4・§8。
ここは「骨格列挙 → 素材の解決 → コスト上限 → agent → 永続化」の配線を実際に
動かして確かめる（``test_discuss_opening_stage.py`` /
``test_contextual_explanation_stage.py`` と同じ立場）。

``core/landscape/store.py`` は Wave A-1 の所有物なので、ここでは
``sys.modules`` に差し替えたフェイクで**呼ばれ方の契約**（受け取る candidate 行の
形）を固定する。DB 実挙動（supersede セマンティクス・一意制約）は
``test_landscape_store.py`` の担当。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import atlas_store  # noqa: E402
from core.document_pipeline import orchestrator as orch  # noqa: E402
from core.landscape import builder as lb  # noqa: E402
from core.llm_worker.cost_gate import CostGate  # noqa: E402
from episteme_graph.agents.landscape_placement.schema import (  # noqa: E402
    LandscapePlacementInput,
    LandscapePlacementResult,
    PlacementCandidate,
    UnplacedDomain,
)

_DOCUMENT_ID = "11111111-2222-3333-4444-555555555555"
_RUN_ID = "99999999-8888-7777-6666-555555555555"
_THESIS = "A single consistency relation links the temperature and polarization amplitudes."
_CLAIM_TEXT = "A quadratic estimator is applied to the foreground-cleaned temperature maps."

_ARTIFACTS = {
    "document_structure": {"metadata": {"title": "A consistency relation for lensing"}},
    "paper_skeleton": {
        "cartridge_id": "astrophysics",
        "paper_goal": {"text": "Establish a consistency relation between the amplitudes."},
        "central_question": {"text": "Can one relation describe both amplitudes?"},
        "headline_claim": {"text": "The two amplitudes agree under the same beam model."},
    },
    "thesis_reconstruction": {
        "central_question": "Can the two measured amplitudes be described by one relation?",
        "central_thesis": {"text": _THESIS, "claim_ids": ["c_1"]},
    },
    "claim_object_builder": {
        "claims": [
            {
                "claim_id": "c_1",
                "text": _CLAIM_TEXT,
                "normalized_text": _CLAIM_TEXT,
                "support_status": "source_backed",
                "is_atomic": True,
            },
            {
                "claim_id": "c_2",
                "text": "A compound, unsupported statement.",
                "support_status": "review_required",
                "is_atomic": True,
            },
            {
                "claim_id": "c_3",
                "text": "A non-atomic statement.",
                "support_status": "source_backed",
                "is_atomic": False,
            },
        ]
    },
}


# ---------------------------------------------------------------------------
# フェイク骨格 / セッション / store / agent
# ---------------------------------------------------------------------------


class _FakeConcept:
    def __init__(self, concept_id, label):
        self.id = concept_id
        self.label = label


class _FakeRegion:
    def __init__(self, region_id, label, concepts=()):
        self.id = region_id
        self.label = label
        self.concepts = tuple(concepts)


class _FakeSkeleton:
    def __init__(self, version="2026.1", regions=(), learner_visible=True):
        self.version = version
        self.regions = tuple(regions)
        self._visible = learner_visible

    @property
    def is_learner_visible(self):
        return self._visible


def _astro_skeleton(**kwargs) -> _FakeSkeleton:
    return _FakeSkeleton(
        regions=[
            _FakeRegion(
                "cosmology",
                "宇宙論・大規模構造",
                [_FakeConcept("cmb", "宇宙背景放射")],
            ),
            _FakeRegion("observation_methods", "観測・装置・データ解析"),
        ],
        **kwargs,
    )


class _FakeSession:
    def __init__(self):
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def execute(self, *_args, **_kwargs):  # pragma: no cover - not used here
        raise AssertionError("builder must not run raw SQL of its own")

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class _FakeStore:
    """``core.landscape.store`` の差し替え（受け取った candidate 行を記録する）。"""

    def __init__(self):
        self.calls: list[dict] = []
        self.raises: Exception | None = None
        self.stats: dict | None = None  # None = created は candidates 件数

    def supersede_and_insert_candidates(self, session, document_id, run_id, candidates):
        self.calls.append(
            {
                "session": session,
                "document_id": document_id,
                "run_id": run_id,
                "candidates": list(candidates),
            }
        )
        if self.raises is not None:
            raise self.raises
        if self.stats is not None:
            return dict(self.stats)
        return {"created": len(candidates), "superseded": 0, "skipped_existing": 0}


class _FakeAgent:
    """LandscapePlacementAgent の差し替え（run の入力を記録し固定結果を返す）。"""

    last_payload: dict | None = None
    last_kwargs: dict | None = None
    result_factory = None

    def __init__(self, **kwargs):
        type(self).last_kwargs = dict(kwargs)

    def run(self, payload, *, cartridge_id=None, config=None):
        type(self).last_payload = payload
        item = LandscapePlacementInput.from_dict(payload)
        factory = type(self).result_factory
        if factory is not None:
            return factory(item)
        return LandscapePlacementResult(
            document_id=item.document_id,
            placements=[
                PlacementCandidate(
                    domain_key="astrophysics",
                    node_id="cmb",
                    perspective="subject",
                    weight=0.85,
                    reason="論文の対象は宇宙背景放射から再構成した振幅である。",
                    evidence_quote=_THESIS,
                    claim_id=None,
                    confidence=0.7,
                ),
                PlacementCandidate(
                    domain_key="astrophysics",
                    node_id="observation_methods",
                    perspective="method",
                    weight=0.5,
                    reason="振幅の推定に quadratic estimator を用いている。",
                    evidence_quote=_CLAIM_TEXT,
                    claim_id="c_1",
                    confidence=0.6,
                ),
            ],
            unplaced_domains=[
                UnplacedDomain("particle_physics", "加速器実験の検出器を扱っていない。")
            ],
            cartridge_id=item.cartridge_id,
            llm_call_count=1,
        )


@pytest.fixture
def stage_env(monkeypatch):
    """骨格列挙・セッション・store・agent を差し替えた環境を組む。"""
    session = _FakeSession()
    monkeypatch.setattr("core.postgres.get_session", lambda: session)
    monkeypatch.setattr(atlas_store, "retired_domain_keys", lambda _s: set())
    monkeypatch.setattr(
        atlas_store,
        "list_domains",
        lambda _s: [
            {
                "domain_key": "astrophysics",
                "domain_name": "宇宙物理",
                "frozen_version": "2026.1",
                "lifecycle": "active",
            }
        ],
    )
    monkeypatch.setattr(
        atlas_store,
        "load_learner_skeleton",
        lambda key, _s=None: _astro_skeleton() if key == "astrophysics" else None,
    )

    store = _FakeStore()
    fake_module = types.ModuleType("core.landscape.store")
    fake_module.supersede_and_insert_candidates = store.supersede_and_insert_candidates
    monkeypatch.setitem(sys.modules, "core.landscape.store", fake_module)

    _FakeAgent.last_payload = None
    _FakeAgent.last_kwargs = None
    _FakeAgent.result_factory = None
    monkeypatch.setattr(
        "episteme_graph.agents.landscape_placement.agent.LandscapePlacementAgent",
        _FakeAgent,
    )
    # コストゲートはプロセス寿命なのでテストごとに新品にする。
    monkeypatch.setattr(lb, "_landscape_cost_gate", CostGate())
    # モデル解決は M層の正本経由（既定では空 = agent 側の既定に委ねる）。ここで
    # ``_landscape_model`` 自体を差し替えないのは、M層を通ることをテストで確かめる
    # ため（差し替えると経路そのものが消える）。
    monkeypatch.setattr(
        "core.llm_policy.resolve_scene_model",
        lambda feature, **_kwargs: types.SimpleNamespace(model=""),
    )

    env = types.SimpleNamespace(session=session, store=store)
    return env


def _run(**overrides) -> dict:
    kwargs = dict(document_id=_DOCUMENT_ID, artifacts=_ARTIFACTS, run_id=_RUN_ID)
    kwargs.update(overrides)
    return lb.build_and_store_placements(**kwargs)


# ---------------------------------------------------------------------------
# 1. ステージ登録位置と M層 / U層カタログ（3点同期）
# ---------------------------------------------------------------------------


class TestStageRegistration:
    def test_registered_between_discuss_opening_and_course_mapping(self):
        stages = orch.PIPELINE_STAGES
        assert "landscape_placement" in stages
        do = stages.index("discuss_opening")
        lp = stages.index("landscape_placement")
        cm = stages.index("course_mapping")
        assert do < lp < cm

    def test_pipeline_steps_has_a_step_in_the_same_position(self):
        names = [step.name for step in orch._PIPELINE_STEPS if step.name is not None]
        assert names.index("discuss_opening") < names.index("landscape_placement")
        assert names.index("landscape_placement") < names.index("course_mapping")

    def test_pipeline_steps_order_matches_pipeline_stages(self):
        names = [step.name for step in orch._PIPELINE_STEPS if step.name is not None]
        expected = [s for s in orch.PIPELINE_STAGES if s in set(names)]
        assert names == expected

    def test_registered_as_an_llm_stage(self):
        assert "landscape_placement" in orch.LLM_STAGE_NAMES

    def test_m_layer_label_exists(self):
        from core.llm_policy import PIPELINE_STAGE_LABELS

        label = PIPELINE_STAGE_LABELS.get("landscape_placement")
        assert label and label != "landscape_placement"

    def test_m_layer_env_mapping_is_fast_tier(self):
        from core import llm_policy

        env_name, tier = llm_policy._FEATURE_DIRECT_ENV[  # noqa: SLF001
            "pipeline:landscape_placement"
        ]
        assert env_name == "LANDSCAPE_PLACEMENT_LLM_MODEL"
        assert tier == "fast"

    def test_u_layer_feature_is_known(self):
        from core.llm_usage.schema import KNOWN_FEATURES

        assert "pipeline:landscape_placement" in KNOWN_FEATURES

    def test_env_example_documents_the_three_keys(self):
        text = (ROOT / ".env.example").read_text(encoding="utf-8")
        for key in (
            "LANDSCAPE_PLACEMENT_LLM_MODEL",
            "LANDSCAPE_MAX_CALLS_PER_DAY",
            "LANDSCAPE_MAX_PLACEMENTS_PER_DOCUMENT",
        ):
            assert key in text, key


# ---------------------------------------------------------------------------
# 2. 骨格列挙（凍結版のみ・retired 除外, LS6 / LS7）
# ---------------------------------------------------------------------------


class TestDomainCollection:
    def test_regions_and_concepts_become_nodes(self, stage_env):
        domains = lb.collect_placement_domains()

        assert len(domains) == 1
        assert domains[0]["domain_key"] == "astrophysics"
        assert domains[0]["skeleton_version"] == "2026.1"
        assert [(n["node_id"], n["kind"], n["region_id"]) for n in domains[0]["nodes"]] == [
            ("cosmology", "region", ""),
            ("cmb", "concept", "cosmology"),
            ("observation_methods", "region", ""),
        ]

    def test_retired_domains_are_excluded(self, monkeypatch, stage_env):
        monkeypatch.setattr(atlas_store, "retired_domain_keys", lambda _s: {"astrophysics"})
        assert lb.collect_placement_domains() == []

    def test_retired_lifecycle_flag_is_also_honoured(self, monkeypatch, stage_env):
        monkeypatch.setattr(
            atlas_store,
            "list_domains",
            lambda _s: [{"domain_key": "astrophysics", "lifecycle": "retired"}],
        )
        assert lb.collect_placement_domains() == []

    def test_draft_only_skeletons_are_not_offered(self, monkeypatch, stage_env):
        """``is_learner_visible`` が偽（draft / 未レビュー）の骨格には配置しない。"""
        monkeypatch.setattr(
            atlas_store,
            "load_learner_skeleton",
            lambda key, _s=None: _astro_skeleton(learner_visible=False),
        )
        assert lb.collect_placement_domains() == []

    def test_db_failure_degrades_to_no_domain(self, monkeypatch, stage_env):
        def _boom(*_a, **_k):
            raise RuntimeError("db down")

        monkeypatch.setattr(atlas_store, "list_domains", _boom)
        assert lb.collect_placement_domains() == []

    def test_session_is_always_closed(self, stage_env):
        lb.collect_placement_domains()
        assert stage_env.session.closed is True


# ---------------------------------------------------------------------------
# 3. 素材の解決（artifact → 解決済みテキスト, LS2）
# ---------------------------------------------------------------------------


class TestMaterialResolution:
    def test_agent_receives_resolved_text_and_node_inventory(self, stage_env):
        _run()

        payload = _FakeAgent.last_payload
        assert payload is not None
        assert payload["central_thesis"] == _THESIS
        assert payload["paper_goal"].startswith("Establish a consistency relation")
        # central_question は thesis 側が優先
        assert payload["central_question"].startswith("Can the two measured amplitudes")
        assert payload["headline_claim"].startswith("The two amplitudes agree")
        assert payload["paper_title"] == "A consistency relation for lensing"
        assert [d["domain_key"] for d in payload["domains"]] == ["astrophysics"]
        assert payload["max_placements"] == 8

    def test_only_source_backed_atomic_claims_become_material(self, stage_env):
        _run()

        claims = _FakeAgent.last_payload["claim_summaries"]
        assert [c["claim_id"] for c in claims] == ["c_1"]
        assert claims[0]["text"] == _CLAIM_TEXT

    def test_claim_summaries_are_capped(self):
        artifacts = {
            "claim_object_builder": {
                "claims": [
                    {
                        "claim_id": f"c_{i}",
                        "text": f"Statement number {i}.",
                        "support_status": "source_backed",
                        "is_atomic": True,
                    }
                    for i in range(lb.MAX_CLAIM_SUMMARIES + 10)
                ]
            }
        }
        assert len(lb.collect_claim_summaries(artifacts)) == lb.MAX_CLAIM_SUMMARIES

    def test_central_question_falls_back_to_the_skeleton(self, stage_env):
        artifacts = dict(_ARTIFACTS)
        artifacts["thesis_reconstruction"] = {"central_thesis": {"text": _THESIS}}
        _run(artifacts=artifacts)

        assert (
            _FakeAgent.last_payload["central_question"]
            == "Can one relation describe both amplitudes?"
        )

    def test_cartridge_id_is_resolved_from_artifacts(self, stage_env):
        _run()
        assert _FakeAgent.last_payload["cartridge_id"] == "astrophysics"

    def test_artifacts_are_loaded_from_the_db_when_not_supplied(self, monkeypatch, stage_env):
        calls: list[str] = []

        def _fake_artifacts(document_id):
            calls.append(document_id)
            return _ARTIFACTS

        monkeypatch.setattr(
            "core.deliberation.refs.document_run_artifacts", _fake_artifacts
        )

        payload = _run(artifacts=None)

        assert calls == [_DOCUMENT_ID]
        assert payload["placements_total"] == 2

    def test_artifact_lookup_failure_degrades_to_no_material(self, monkeypatch, stage_env):
        def _boom(_document_id):
            raise RuntimeError("db down")

        monkeypatch.setattr(
            "core.deliberation.refs.document_run_artifacts", _boom
        )

        payload = _run(artifacts=None)

        assert payload["skipped_reason"] == lb.SKIPPED_REASON_NO_MATERIAL
        assert _FakeAgent.last_payload is None


# ---------------------------------------------------------------------------
# 4. skip 経路（設計書 §7.2）
# ---------------------------------------------------------------------------


class TestSkipPaths:
    def test_no_frozen_skeleton_skips_without_calling_the_llm(self, monkeypatch, stage_env):
        monkeypatch.setattr(atlas_store, "list_domains", lambda _s: [])

        payload = _run()

        assert payload["status"] == "completed"
        assert payload["skipped_reason"] == "no_frozen_skeleton"
        assert payload["llm_calls"] == 0
        assert payload["placements_total"] == 0
        assert _FakeAgent.last_payload is None
        assert stage_env.store.calls == []

    def test_no_source_material_skips_without_calling_the_llm(self, stage_env):
        payload = _run(artifacts={"paper_skeleton": {}, "thesis_reconstruction": {}})

        assert payload["skipped_reason"] == "no_source_material"
        assert payload["llm_calls"] == 0
        assert _FakeAgent.last_payload is None
        assert stage_env.store.calls == []

    def test_daily_limit_is_reported_honestly(self, monkeypatch, stage_env):
        monkeypatch.setattr(lb, "_max_calls_per_day", lambda: 0)

        payload = _run()

        assert payload["skipped_by_limit"] is True
        assert payload["skipped_reason"] == "daily_call_limit_reached"
        assert payload["llm_calls"] == 0
        assert stage_env.store.calls == []

    def test_exhausted_budget_from_a_previous_run_skips(self, monkeypatch, stage_env):
        from core.llm_worker.cost_gate import today_str

        monkeypatch.setattr(lb, "_max_calls_per_day", lambda: 1)
        lb._landscape_cost_gate.count_extra_daily(  # noqa: SLF001
            daily_key=today_str(), amount=1
        )

        payload = _run()

        assert payload["skipped_reason"] == "daily_call_limit_reached"

    def test_zero_placement_limit_disables_generation(self, monkeypatch, stage_env):
        monkeypatch.setattr(lb, "_max_placements_per_document", lambda: 0)

        payload = _run()

        assert payload["skipped_reason"] == "placement_limit_is_zero"
        assert stage_env.store.calls == []

    def test_agent_skip_reason_is_propagated(self, stage_env):
        _FakeAgent.result_factory = lambda item: LandscapePlacementResult.make_skipped(
            item, "repair_failed", "validation failed after repair attempts"
        )

        payload = _run()

        assert payload["skipped_reason"] == "repair_failed"
        assert payload["placements_total"] == 0
        assert payload["review_notes"]
        assert stage_env.store.calls == []

    def test_unplaced_only_result_is_recorded_without_rows(self, stage_env):
        """LS10: 配置不能は失敗ではなく信号（payload に理由が残る）。"""
        _FakeAgent.result_factory = lambda item: LandscapePlacementResult(
            document_id=item.document_id,
            placements=[],
            unplaced_domains=[UnplacedDomain("astrophysics", "天体現象を扱っていない。")],
            llm_call_count=1,
        )

        payload = _run()

        assert payload["placements_total"] == 0
        assert payload["unplaced_domains"] == [
            {"domain_key": "astrophysics", "reason": "天体現象を扱っていない。"}
        ]
        assert payload.get("skipped_reason") is None
        assert stage_env.store.calls == []


# ---------------------------------------------------------------------------
# 5. モデル解決は M層の正本経由（M1 / §7.1）
# ---------------------------------------------------------------------------


class TestModelResolution:
    def test_model_is_resolved_through_resolve_scene_model(self, monkeypatch, stage_env):
        seen: list[str] = []

        def _fake_resolve(feature, **_kwargs):
            seen.append(feature)
            return types.SimpleNamespace(model="gpt-test-landscape")

        monkeypatch.setattr("core.llm_policy.resolve_scene_model", _fake_resolve)

        _run()

        assert seen == ["pipeline:landscape_placement"]
        assert _FakeAgent.last_kwargs["llm_model"] == "gpt-test-landscape"

    def test_empty_resolution_leaves_the_agent_default(self, stage_env):
        """fixture の既定（model=""）では agent に None を渡す（tier 既定に委ねる）。"""
        _run()
        assert _FakeAgent.last_kwargs["llm_model"] is None

    def test_model_resolution_failure_falls_back_to_agent_default(self, monkeypatch, stage_env):
        def _boom(*_a, **_k):
            raise RuntimeError("policy backend down")

        monkeypatch.setattr("core.llm_policy.resolve_scene_model", _boom)

        payload = _run()

        assert payload["placements_total"] == 2
        assert _FakeAgent.last_kwargs["llm_model"] is None


# ---------------------------------------------------------------------------
# 6. 永続化（LS3: AI は inferred しか書けない）
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_store_is_called_with_deterministic_candidate_rows(self, stage_env):
        payload = _run()

        assert len(stage_env.store.calls) == 1
        call = stage_env.store.calls[0]
        assert call["document_id"] == _DOCUMENT_ID
        assert call["run_id"] == _RUN_ID
        assert call["session"] is stage_env.session

        first, second = call["candidates"]
        assert first == {
            "domain_key": "astrophysics",
            "skeleton_version": "2026.1",
            "node_id": "cmb",
            "node_kind": "concept",
            "perspective": "subject",
            "weight": 0.85,
            "reason": "論文の対象は宇宙背景放射から再構成した振幅である。",
            "evidence": [{"quote": _THESIS, "claim_id": None}],
            "provenance": "llm",
            "created_by": "pipeline",
        }
        assert second["node_kind"] == "region"
        assert second["evidence"] == [{"quote": _CLAIM_TEXT, "claim_id": "c_1"}]
        assert payload["created"] == 2
        assert stage_env.session.committed is True
        assert stage_env.session.closed is True

    def test_builder_never_writes_a_status(self, stage_env):
        """LS3: status は store 既定の inferred のみ。builder は status を渡さない。"""
        _run()
        for candidate in stage_env.store.calls[0]["candidates"]:
            assert "status" not in candidate
            assert "reviewed_by" not in candidate
            assert "reviewed_at" not in candidate

    def test_created_by_is_carried_for_manual_reproposal(self, stage_env):
        _run(created_by="teacher:abc")
        for candidate in stage_env.store.calls[0]["candidates"]:
            assert candidate["created_by"] == "teacher:abc"

    def test_store_stats_are_reported(self, stage_env):
        stage_env.store.stats = {"created": 1, "superseded": 3, "skipped_existing": 2}

        payload = _run()

        assert payload["created"] == 1
        assert payload["superseded"] == 3
        assert payload["skipped_existing"] == 2

    def test_persistence_failure_rolls_back_and_stays_non_fatal(self, stage_env):
        stage_env.store.raises = RuntimeError("db down")

        payload = _run()

        assert payload["status"] == "completed"
        assert payload["persist_failed"] is True
        assert payload["placements_total"] == 2  # 生成自体は記録される
        assert stage_env.session.rolled_back is True
        assert stage_env.session.closed is True

    def test_truncation_is_reported(self, stage_env):
        _FakeAgent.result_factory = lambda item: LandscapePlacementResult(
            document_id=item.document_id,
            placements=[
                PlacementCandidate(
                    domain_key="astrophysics",
                    node_id="cmb",
                    perspective="subject",
                    weight=0.9,
                    reason="事実文。",
                    evidence_quote=_THESIS,
                )
            ],
            truncated=True,
            truncated_count=3,
            llm_call_count=1,
        )

        payload = _run()

        assert payload["truncated"] is True
        assert payload["truncated_count"] == 3


# ---------------------------------------------------------------------------
# 7. コスト会計（実測の事後計上, §7.4）
# ---------------------------------------------------------------------------


class TestCostAccounting:
    def test_actual_calls_are_counted_after_the_run(self, monkeypatch, stage_env):
        from core.llm_worker.cost_gate import today_str

        monkeypatch.setattr(lb, "_max_calls_per_day", lambda: 2)

        _run()
        remaining = lb._landscape_cost_gate.daily_remaining(  # noqa: SLF001
            daily_limit=2, daily_key=today_str()
        )
        assert remaining == 1

        # 2回目で予算を使い切り、3回目は skip になる
        _run()
        payload = _run()
        assert payload["skipped_reason"] == "daily_call_limit_reached"

    def test_skipped_runs_do_not_consume_budget(self, monkeypatch, stage_env):
        from core.llm_worker.cost_gate import today_str

        monkeypatch.setattr(lb, "_max_calls_per_day", lambda: 2)
        _run(artifacts={"paper_skeleton": {}})

        assert lb._landscape_cost_gate.daily_remaining(  # noqa: SLF001
            daily_limit=2, daily_key=today_str()
        ) == 2


# ---------------------------------------------------------------------------
# 8. env 読み（既定値と壊れた値への耐性）
# ---------------------------------------------------------------------------


class TestEnvReaders:
    def test_defaults(self, monkeypatch):
        monkeypatch.delenv("LANDSCAPE_MAX_CALLS_PER_DAY", raising=False)
        monkeypatch.delenv("LANDSCAPE_MAX_PLACEMENTS_PER_DOCUMENT", raising=False)
        assert lb._max_calls_per_day() == 20  # noqa: SLF001
        assert lb._max_placements_per_document() == 8  # noqa: SLF001

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("LANDSCAPE_MAX_CALLS_PER_DAY", "3")
        monkeypatch.setenv("LANDSCAPE_MAX_PLACEMENTS_PER_DOCUMENT", "2")
        assert lb._max_calls_per_day() == 3  # noqa: SLF001
        assert lb._max_placements_per_document() == 2  # noqa: SLF001

    def test_broken_env_falls_back_to_defaults(self, monkeypatch):
        monkeypatch.setenv("LANDSCAPE_MAX_CALLS_PER_DAY", "many")
        monkeypatch.setenv("LANDSCAPE_MAX_PLACEMENTS_PER_DOCUMENT", "")
        assert lb._max_calls_per_day() == 20  # noqa: SLF001
        assert lb._max_placements_per_document() == 8  # noqa: SLF001

    def test_negative_env_is_clamped_to_zero(self, monkeypatch):
        monkeypatch.setenv("LANDSCAPE_MAX_CALLS_PER_DAY", "-5")
        assert lb._max_calls_per_day() == 0  # noqa: SLF001


# ---------------------------------------------------------------------------
# 9. ステージ関数の非致命性と artifact resume
# ---------------------------------------------------------------------------


def _make_ctx(**overrides):
    result = orch.DocumentPipelineResult(
        document_id=_DOCUMENT_ID, material_id="mat-1", cartridge_id=None, run_id=_RUN_ID,
    )
    ctx = orch.PipelineContext(
        pdf_bytes=b"", document_id=_DOCUMENT_ID, material_id="mat-1", filename=None,
        source_kind="pdf", cartridge_id=None, course_id=None, run_id=_RUN_ID,
        agent_classes={}, effective_options={}, result=result,
    )
    reports: list[tuple] = []
    saved: dict = {}
    ctx.report_start = lambda stage, **kw: reports.append(("start", stage, kw))
    ctx.report_done = lambda stage, payload=None, **kw: reports.append(("done", stage, payload))
    ctx.save_artifact = lambda stage, value: saved.__setitem__(stage, value)
    ctx.artifact = lambda stage: saved.get(stage)
    ctx.should_use_artifact = lambda stage: False
    ctx.finish_target_stage = lambda stage, payload=None: False
    ctx.all_artifacts = lambda: dict(_ARTIFACTS)
    ctx._reports = reports  # test-only bookkeeping
    ctx._saved = saved
    for key, value in overrides.items():
        setattr(ctx, key, value)
    return ctx


class TestStageWiring:
    def test_stage_is_non_fatal_on_build_failure(self, monkeypatch):
        ctx = _make_ctx()
        monkeypatch.setattr(
            orch, "_build_landscape_placement", MagicMock(side_effect=RuntimeError("boom"))
        )

        result = orch._stage_landscape_placement(ctx)  # noqa: SLF001

        assert result is False
        assert ctx._saved["landscape_placement"]["status"] == "completed"
        assert ctx._saved["landscape_placement"]["error"] == "boom"

    def test_stage_saves_the_builder_payload(self, monkeypatch):
        ctx = _make_ctx()
        monkeypatch.setattr(
            orch,
            "_build_landscape_placement",
            lambda _ctx: {"status": "completed", "created": 2, "llm_calls": 1},
        )

        orch._stage_landscape_placement(ctx)  # noqa: SLF001

        assert ctx._saved["landscape_placement"]["created"] == 2
        assert ("start", "landscape_placement", {"total": 1, "unit": "builder"}) in ctx._reports

    def test_stage_resumes_from_existing_artifact_without_rebuilding(self, monkeypatch):
        build_spy = MagicMock()
        monkeypatch.setattr(orch, "_build_landscape_placement", build_spy)
        ctx = _make_ctx(should_use_artifact=lambda stage: True)
        ctx._saved["landscape_placement"] = {"status": "completed", "created": 4}

        result = orch._stage_landscape_placement(ctx)  # noqa: SLF001

        assert result is False
        build_spy.assert_not_called()
        assert ("done", "landscape_placement", {"status": "completed", "created": 4}) in ctx._reports

    def test_stage_delegates_to_the_builder_with_run_context(self, monkeypatch):
        captured: dict = {}

        def _fake_build(**kwargs):
            captured.update(kwargs)
            return {"status": "completed"}

        monkeypatch.setattr("core.landscape.builder.build_and_store_placements", _fake_build)
        ctx = _make_ctx()

        orch._stage_landscape_placement(ctx)  # noqa: SLF001

        assert captured["document_id"] == _DOCUMENT_ID
        assert captured["run_id"] == _RUN_ID
        assert captured["artifacts"]["paper_skeleton"]

    def test_skip_payload_is_recognised_as_an_llm_skip(self):
        """``_stage_artifact_indicates_llm_skip`` が本ステージの skip 形を認識する
        （M7 の使用モデル記録から skip run を除外するための契約）。"""
        assert orch._stage_artifact_indicates_llm_skip(  # noqa: SLF001
            {"status": "completed", "skipped_reason": "no_frozen_skeleton", "llm_calls": 0}
        )
        assert orch._stage_artifact_indicates_llm_skip(  # noqa: SLF001
            {"skipped_by_limit": True, "skipped_reason": "daily_call_limit_reached"}
        )
        assert not orch._stage_artifact_indicates_llm_skip(  # noqa: SLF001
            {"status": "completed", "llm_calls": 1, "created": 2}
        )


# ---------------------------------------------------------------------------
# 10. core/landscape が FastAPI を import しないこと（設計書 §8）
# ---------------------------------------------------------------------------


class TestVocabularyParity:
    """agent 側の複製語彙が ``core/landscape/schema.py`` の正本と一致すること。

    agent は ``backend/core`` を import しないため語彙をリテラルで持つ（設計書 §7.3）。
    その二重管理をここで構造的に固定する。
    """

    def test_perspectives_match_the_core_vocabulary(self):
        from core.landscape import schema as core_schema
        from episteme_graph.agents.landscape_placement import schema as agent_schema

        assert tuple(agent_schema.PERSPECTIVES) == tuple(core_schema.PERSPECTIVES)

    def test_node_kinds_match_the_core_vocabulary(self):
        from core.landscape import schema as core_schema
        from episteme_graph.agents.landscape_placement import schema as agent_schema

        assert tuple(agent_schema.NODE_KINDS) == tuple(core_schema.NODE_KINDS)

    def test_perspective_hints_cover_every_perspective(self):
        from episteme_graph.agents.landscape_placement import schema as agent_schema

        assert set(agent_schema.PERSPECTIVE_HINTS) == set(agent_schema.PERSPECTIVES)

    def test_default_placement_cap_matches_the_builder_default(self):
        from episteme_graph.agents.landscape_placement import schema as agent_schema

        assert (
            agent_schema.DEFAULT_MAX_PLACEMENTS
            == lb.DEFAULT_MAX_PLACEMENTS_PER_DOCUMENT
        )

    def test_candidate_rows_pass_the_real_store_validator(self):
        """builder が組む candidate 行が ``store._validate_candidate`` を素通りすること。

        ここだけは（フェイクではなく）実 store の最終ガードを通す — 生産側
        （builder）と消費側（store）の契約が片側だけ変わったら落ちるようにする。
        DB には触らない（純関数の検証のみ）。
        """
        from core.landscape import store as real_store

        result = LandscapePlacementResult(
            document_id=_DOCUMENT_ID,
            placements=[
                PlacementCandidate(
                    domain_key="astrophysics",
                    node_id="cmb",
                    perspective="subject",
                    weight=0.85,
                    reason="事実文。",
                    evidence_quote=_THESIS,
                    claim_id="c_1",
                    confidence=0.6,
                )
            ],
        )
        domains = [
            {
                "domain_key": "astrophysics",
                "skeleton_version": "2026.1",
                "nodes": [{"node_id": "cmb", "kind": "concept"}],
            }
        ]

        candidates = lb._to_store_candidates(  # noqa: SLF001
            result, domains, created_by="teacher:1"
        )
        normalized = real_store._validate_candidate(candidates[0])  # noqa: SLF001

        assert normalized["node_kind"] == "concept"
        assert normalized["skeleton_version"] == "2026.1"
        assert normalized["perspective"] == "subject"
        assert normalized["provenance"] == "llm"
        assert normalized["created_by"] == "teacher:1"
        assert normalized["evidence"] == [{"quote": _THESIS, "claim_id": "c_1"}]
        # LS5: confidence の生値は DB 行へ渡さない（evidence 経由の漏れも作らない）
        assert "confidence" not in normalized
        assert all("confidence" not in item for item in normalized["evidence"])

    def test_skip_reason_literals_match_the_builder(self):
        from episteme_graph.agents.landscape_placement import schema as agent_schema

        assert agent_schema.SKIPPED_REASON_NO_MATERIAL == lb.SKIPPED_REASON_NO_MATERIAL
        assert agent_schema.SKIPPED_REASON_NO_DOMAINS == lb.SKIPPED_REASON_NO_SKELETON


class TestModuleHygiene:
    def test_builder_does_not_import_fastapi(self):
        from tests.guardrail_helpers import assert_module_tree_does_not_import

        assert_module_tree_does_not_import(BACKEND / "core" / "landscape", ["fastapi"])

    def test_builder_does_not_write_a_confirmed_status(self):
        """LS3: AI 経路から ``confirmed`` / ``rejected`` を書けないこと。

        教員の判断を表す status リテラルがコード中に現れないことを検査する（散文の
        言及は除く。``superseded`` は store の戻り値の**件数キー**として現れるため
        対象外 — 手動遷移は store 側で禁止される）。
        """
        src = (BACKEND / "core" / "landscape" / "builder.py").read_text(encoding="utf-8")
        for status in ("confirmed", "rejected", "review_required"):
            assert f'"{status}"' not in src, status
            assert f"'{status}'" not in src, status
        assert "DELETE FROM" not in src
