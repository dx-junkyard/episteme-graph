"""理解サイクル Phase 2 — 式スケール ELICIT（支配項の直感道場）のテスト。

対象仕様: docs/features/understanding_cycle_design.md §6（前提: §2 不変条項）。

- `core/reconstruction/derivation_source.py`: derivation_chain artifact から
  regime / next_step probe を非LLM・決定論で生成する純ロジック（足切り3条件・
  regime の正解一意性・ディストラクタ決定論・operation_label 空スキップ・
  generic operation 非出題）。
- `core/reconstruction/worker.py`: 非LLM derivation item オーサリング
  （CostGate 非経由・冪等性・LLM オーサリングとの合算）。
- `routes/reconstruction.py::get_next_item`: 直近回答が regime/next_step のとき
  同モードを後回しにする連続回避（除外はしない）。
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

from core.reconstruction import derivation_source  # noqa: E402
from core.reconstruction.schema import CHOICE_MODES, ELICIT_MODES  # noqa: E402


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------


def _equations_fixture():
    return [
        {"equation_id": "eq_1", "label": "3.1"},
        {"equation_id": "eq_2", "label": "3.2"},
        {"equation_id": "eq_3", "label": "3.3"},
    ]


def _chain_fixture(extra_steps=None):
    steps = [
        {
            "step_id": "step_1",
            "operation": "linearize",
            "input_equation_ids": ["eq_1"],
            "output_equation_ids": ["eq_2"],
            "confidence": 0.8,
            "input_claim_ids": ["claim_a"],
            "output_claim_ids": [],
            "required_claim_ids": [],
            "assumption_ids": [],
            "source_evidence_ids": ["ev_1"],
            "eliminated_symbols": ["epsilon"],
            "retained_symbols": ["x", "y", "z", "w"],
        },
        {
            "step_id": "step_2",
            "operation": "eliminate_parameter",
            "input_equation_ids": ["eq_2"],
            "output_equation_ids": ["eq_3"],
            "confidence": 1.5,  # クランプされることを確認する
            "input_claim_ids": [],
            "output_claim_ids": ["claim_b"],
            "required_claim_ids": [],
            "assumption_ids": [],
            "source_evidence_ids": ["ev_2"],
            "eliminated_symbols": [],
            "retained_symbols": [],
        },
    ]
    if extra_steps:
        steps = steps + extra_steps
    return {
        "derivation_id": "deriv_1",
        "source_evidence_ids": [],
        "input_claim_ids": [],
        "output_claim_ids": [],
        "assumption_ids": [],
        "steps": steps,
    }


def _claim_rows_fixture():
    return [
        ("uuid-a", {"legacy_ids": ["claim_a"]}),
        ("uuid-b", {"legacy_ids": ["claim_b"]}),
    ]


class _FakeClaimLookupSession:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *a, **k):
        return self

    def fetchall(self):
        return self._rows

    def close(self):
        pass


def _patch_common(monkeypatch, *, chains, equations, claim_rows):
    monkeypatch.setattr(derivation_source, "document_run_artifacts", lambda doc_id: {})
    monkeypatch.setattr(
        derivation_source, "derivation_records", lambda doc_id, artifacts=None: chains
    )
    monkeypatch.setattr(
        derivation_source, "equation_records", lambda doc_id, artifacts=None: equations
    )
    monkeypatch.setattr(
        derivation_source, "_pg_session", lambda: _FakeClaimLookupSession(claim_rows)
    )


# ---------------------------------------------------------------------------
# 語彙
# ---------------------------------------------------------------------------


class TestVocab:
    def test_elicit_modes_include_regime_and_next_step(self):
        assert "regime" in ELICIT_MODES
        assert "next_step" in ELICIT_MODES

    def test_choice_modes(self):
        assert CHOICE_MODES == ("predict", "regime", "next_step")


# ---------------------------------------------------------------------------
# collect_derivation_probes — 足切り・生成ロジック
# ---------------------------------------------------------------------------


class TestCollectDerivationProbesBasic:
    def test_generates_next_step_and_regime_probes(self, monkeypatch):
        chain = _chain_fixture()
        _patch_common(
            monkeypatch, chains=[chain], equations=_equations_fixture(), claim_rows=_claim_rows_fixture()
        )
        probes = derivation_source.collect_derivation_probes("doc1")
        modes_by_step = {(p["expected"]["step_id"], p["elicit_mode"]) for p in probes}
        assert ("step_1", "next_step") in modes_by_step
        assert ("step_1", "regime") in modes_by_step
        assert ("step_2", "next_step") in modes_by_step
        # step_2 の eliminated_symbols が空なので regime は生成されない
        assert ("step_2", "regime") not in modes_by_step
        assert len(probes) == 3

    def test_claim_uuid_resolved_via_legacy_ids(self, monkeypatch):
        chain = _chain_fixture()
        _patch_common(
            monkeypatch, chains=[chain], equations=_equations_fixture(), claim_rows=_claim_rows_fixture()
        )
        probes = derivation_source.collect_derivation_probes("doc1")
        by_step = {p["expected"]["step_id"]: p["claim_uuid"] for p in probes if p["elicit_mode"] == "next_step"}
        assert by_step["step_1"] == "uuid-a"
        assert by_step["step_2"] == "uuid-b"

    def test_confidence_is_clamped_to_unit_interval(self, monkeypatch):
        chain = _chain_fixture()
        _patch_common(
            monkeypatch, chains=[chain], equations=_equations_fixture(), claim_rows=_claim_rows_fixture()
        )
        probes = derivation_source.collect_derivation_probes("doc1")
        step2_next = next(p for p in probes if p["expected"]["step_id"] == "step_2" and p["elicit_mode"] == "next_step")
        assert step2_next["author_confidence"] == 1.0

    def test_document_id_and_document_scope_present(self, monkeypatch):
        chain = _chain_fixture()
        _patch_common(
            monkeypatch, chains=[chain], equations=_equations_fixture(), claim_rows=_claim_rows_fixture()
        )
        probes = derivation_source.collect_derivation_probes("doc1")
        for p in probes:
            assert p["document_id"] == "doc1"
            assert p["claim_fields_used"] == ["derivation_chain"]

    def test_result_is_deterministic_across_calls(self, monkeypatch):
        chain = _chain_fixture()
        _patch_common(
            monkeypatch, chains=[chain], equations=_equations_fixture(), claim_rows=_claim_rows_fixture()
        )
        first = derivation_source.collect_derivation_probes("doc1")
        second = derivation_source.collect_derivation_probes("doc1")
        assert first == second


class TestGateOperationMembership:
    """足切り①: operation が REGIME_OPERATIONS に含まれない step は出題しない（generic operation非出題）。"""

    def test_generic_operation_is_not_gated(self):
        for op in ("substitute", "transform", "define", "relate", "apply_definition"):
            assert op not in derivation_source.REGIME_OPERATIONS

    def test_generic_operation_step_produces_no_probe(self, monkeypatch):
        generic_step = {
            "step_id": "step_generic",
            "operation": "substitute",
            "input_equation_ids": ["eq_2"],
            "output_equation_ids": ["eq_3"],
            "confidence": 0.9,
            "input_claim_ids": ["claim_a"],
            "output_claim_ids": [],
            "required_claim_ids": [],
            "assumption_ids": [],
            "source_evidence_ids": ["ev_9"],
            "eliminated_symbols": ["epsilon"],
            "retained_symbols": ["x"],
        }
        chain = _chain_fixture(extra_steps=[generic_step])
        _patch_common(
            monkeypatch, chains=[chain], equations=_equations_fixture(), claim_rows=_claim_rows_fixture()
        )
        probes = derivation_source.collect_derivation_probes("doc1")
        assert all(p["expected"]["step_id"] != "step_generic" for p in probes)
        # 恒等変形以外の3件（既存フィクスチャ分）だけが残る
        assert len(probes) == 3


class TestGateSourceEvidence:
    """足切り②: step / chain の source_evidence_ids が両方空なら出題しない。"""

    def test_step_without_evidence_and_chain_without_evidence_is_skipped(self, monkeypatch):
        no_evidence_step = {
            "step_id": "step_no_evidence",
            "operation": "normalize",
            "input_equation_ids": ["eq_1"],
            "output_equation_ids": ["eq_2"],
            "confidence": 0.5,
            "input_claim_ids": ["claim_a"],
            "output_claim_ids": [],
            "required_claim_ids": [],
            "assumption_ids": [],
            "source_evidence_ids": [],
            "eliminated_symbols": ["epsilon"],
            "retained_symbols": ["x"],
        }
        chain = {
            "derivation_id": "deriv_no_evidence",
            "source_evidence_ids": [],  # chain レベルも空
            "input_claim_ids": [],
            "output_claim_ids": [],
            "assumption_ids": [],
            "steps": [no_evidence_step],
        }
        _patch_common(
            monkeypatch, chains=[chain], equations=_equations_fixture(), claim_rows=_claim_rows_fixture()
        )
        probes = derivation_source.collect_derivation_probes("doc1")
        assert probes == []

    def test_chain_level_evidence_satisfies_the_gate(self, monkeypatch):
        """step 自体に evidence が無くても chain レベルにあれば通過する。"""
        step = {
            "step_id": "step_chain_evidence",
            "operation": "normalize",
            "input_equation_ids": ["eq_1"],
            "output_equation_ids": ["eq_2"],
            "confidence": 0.5,
            "input_claim_ids": ["claim_a"],
            "output_claim_ids": [],
            "required_claim_ids": [],
            "assumption_ids": [],
            "source_evidence_ids": [],
            "eliminated_symbols": [],
            "retained_symbols": [],
        }
        chain = {
            "derivation_id": "deriv_chain_evidence",
            "source_evidence_ids": ["ev_chain"],
            "input_claim_ids": [],
            "output_claim_ids": [],
            "assumption_ids": [],
            "steps": [step],
        }
        _patch_common(
            monkeypatch, chains=[chain], equations=_equations_fixture(), claim_rows=_claim_rows_fixture()
        )
        probes = derivation_source.collect_derivation_probes("doc1")
        assert len(probes) == 1
        assert probes[0]["elicit_mode"] == "next_step"


class TestGateClaimResolution:
    """足切り③: claim UUID が解決できない step は出題しない。"""

    def test_unresolvable_claim_reference_is_skipped(self, monkeypatch):
        step = {
            "step_id": "step_unknown_claim",
            "operation": "normalize",
            "input_equation_ids": ["eq_1"],
            "output_equation_ids": ["eq_2"],
            "confidence": 0.5,
            "input_claim_ids": ["claim_does_not_exist"],
            "output_claim_ids": [],
            "required_claim_ids": [],
            "assumption_ids": [],
            "source_evidence_ids": ["ev_1"],
            "eliminated_symbols": [],
            "retained_symbols": [],
        }
        chain = {
            "derivation_id": "deriv_unknown_claim",
            "source_evidence_ids": [],
            "input_claim_ids": [],
            "output_claim_ids": [],
            "assumption_ids": [],
            "steps": [step],
        }
        # claim_rows には claim_a/claim_b のみ登録し、claim_does_not_exist は解決不能にする
        _patch_common(
            monkeypatch, chains=[chain], equations=_equations_fixture(), claim_rows=_claim_rows_fixture()
        )
        probes = derivation_source.collect_derivation_probes("doc1")
        assert probes == []

    def test_empty_claim_lookup_short_circuits(self, monkeypatch):
        chain = _chain_fixture()
        _patch_common(monkeypatch, chains=[chain], equations=_equations_fixture(), claim_rows=[])
        assert derivation_source.collect_derivation_probes("doc1") == []

    def test_empty_document_id_returns_empty(self):
        assert derivation_source.collect_derivation_probes("") == []

    def test_no_chains_returns_empty(self, monkeypatch):
        _patch_common(monkeypatch, chains=[], equations=[], claim_rows=_claim_rows_fixture())
        assert derivation_source.collect_derivation_probes("doc1") == []


class TestRegimeProbeCorrectness:
    def test_regime_correct_option_is_unique_eliminated_symbol(self, monkeypatch):
        chain = _chain_fixture()
        _patch_common(
            monkeypatch, chains=[chain], equations=_equations_fixture(), claim_rows=_claim_rows_fixture()
        )
        probes = derivation_source.collect_derivation_probes("doc1")
        regime = next(p for p in probes if p["elicit_mode"] == "regime")
        assert regime["expected"]["symbol"] == "epsilon"
        option_ids = {opt["id"] for opt in regime["response_space"]}
        assert regime["expected"]["option_id"] in option_ids
        labels = [opt["label"] for opt in regime["response_space"]]
        # 正解(epsilon)は1回だけ、distractor は retained_symbols 由来のみ
        assert labels.count("epsilon") == 1
        assert set(labels[1:]).issubset({"x", "y", "z", "w"})
        assert len(regime["response_space"]) <= 4  # 正解1 + distractor最大3

    def test_regime_probe_absent_without_both_symbol_lists(self, monkeypatch):
        chain = _chain_fixture()
        _patch_common(
            monkeypatch, chains=[chain], equations=_equations_fixture(), claim_rows=_claim_rows_fixture()
        )
        probes = derivation_source.collect_derivation_probes("doc1")
        assert not any(p["elicit_mode"] == "regime" and p["expected"]["step_id"] == "step_2" for p in probes)


class TestNextStepProbeLabels:
    def test_prompt_uses_equation_labels_when_resolvable(self, monkeypatch):
        chain = _chain_fixture()
        _patch_common(
            monkeypatch, chains=[chain], equations=_equations_fixture(), claim_rows=_claim_rows_fixture()
        )
        probes = derivation_source.collect_derivation_probes("doc1")
        step1_next = next(p for p in probes if p["elicit_mode"] == "next_step" and p["expected"]["step_id"] == "step_1")
        assert "式 (3.1)" in step1_next["prompt"]
        assert "式 (3.2)" in step1_next["prompt"]

    def test_prompt_falls_back_to_generic_labels_when_unresolvable(self, monkeypatch):
        chain = _chain_fixture()
        _patch_common(monkeypatch, chains=[chain], equations=[], claim_rows=_claim_rows_fixture())
        probes = derivation_source.collect_derivation_probes("doc1")
        step1_next = next(p for p in probes if p["elicit_mode"] == "next_step" and p["expected"]["step_id"] == "step_1")
        assert "前の式" in step1_next["prompt"]
        assert "次の式" in step1_next["prompt"]


class TestDistractorOperations:
    def test_skips_operations_without_label(self):
        steps = [{"operation": "totally_unregistered_op"}, {"operation": "normalize"}]
        out = derivation_source._distractor_operations(steps, "linearize", 3)
        assert "totally_unregistered_op" not in out
        assert "normalize" in out

    def test_falls_back_to_fixed_order_when_no_other_steps(self):
        out = derivation_source._distractor_operations([], "linearize", 3)
        assert out
        assert "linearize" not in out
        for op in out:
            assert op in derivation_source._FALLBACK_OPERATION_ORDER

    def test_respects_limit(self):
        out = derivation_source._distractor_operations([], "linearize", 2)
        assert len(out) <= 2

    def test_never_duplicates_excluded_operation(self):
        steps = [{"operation": "linearize"}, {"operation": "normalize"}]
        out = derivation_source._distractor_operations(steps, "linearize", 3)
        assert "linearize" not in out


# ---------------------------------------------------------------------------
# worker.py — 非LLM derivation item オーサリング
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeDerivWorkerSession:
    def __init__(self, existing_keys):
        self.existing_keys = set(existing_keys)
        self.inserts = []  # list of (sql, params)

    def execute(self, stmt, params=None):
        sql = str(stmt)
        params = params or {}
        if "SELECT 1 FROM reconstruction_items" in sql:
            key = (params.get("cid"), params.get("mode"))
            return _FakeResult(("x",) if key in self.existing_keys else None)
        if "INSERT INTO reconstruction_items" in sql:
            self.inserts.append((sql, params))
            return _FakeResult(("item-" + str(len(self.inserts)),))
        raise AssertionError("unexpected SQL in fake derivation worker session: " + sql[:120])

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def _probe(claim_uuid, mode, **overrides):
    base = {
        "claim_uuid": claim_uuid,
        "document_id": "doc1",
        "elicit_mode": mode,
        "prompt": "p",
        "response_space": [{"id": "opt_a", "label": "A"}],
        "expected": {"option_id": "opt_a"},
        "author_confidence": 0.5,
        "claim_fields_used": ["derivation_chain"],
    }
    base.update(overrides)
    return base


class TestRunDerivationItemAuthoringForDocument:
    def test_creates_items_for_each_probe(self, monkeypatch):
        from core.reconstruction import worker

        probes = [_probe("uuid-a", "next_step"), _probe("uuid-b", "regime")]
        monkeypatch.setattr(worker, "collect_derivation_probes", lambda doc_id: probes)
        session = _FakeDerivWorkerSession(existing_keys=set())
        monkeypatch.setattr(worker, "_pg_session", lambda: session)
        events = []
        monkeypatch.setattr(
            worker, "_record_item_event", lambda *a, **k: events.append((a, k))
        )

        created = worker.run_derivation_item_authoring_for_document("doc1")

        assert created == 2
        assert len(session.inserts) == 2
        for sql, params in session.inserts:
            assert "'system'" in sql  # author は system 固定
            assert "'auto'" in sql  # status は auto
        assert len(events) == 2
        for args, _kwargs in events:
            metadata = args[5] if len(args) > 5 else {}
            assert metadata.get("author") == "system"

    def test_skips_existing_non_retired_item(self, monkeypatch):
        from core.reconstruction import worker

        probes = [_probe("uuid-a", "regime")]
        monkeypatch.setattr(worker, "collect_derivation_probes", lambda doc_id: probes)
        session = _FakeDerivWorkerSession(existing_keys={("uuid-a", "regime")})
        monkeypatch.setattr(worker, "_pg_session", lambda: session)
        events = []
        monkeypatch.setattr(
            worker, "_record_item_event", lambda *a, **k: events.append((a, k))
        )

        created = worker.run_derivation_item_authoring_for_document("doc1")

        assert created == 0
        assert session.inserts == []
        assert events == []

    def test_no_probes_returns_zero(self, monkeypatch):
        from core.reconstruction import worker

        monkeypatch.setattr(worker, "collect_derivation_probes", lambda doc_id: [])
        assert worker.run_derivation_item_authoring_for_document("doc1") == 0

    def test_empty_document_id_returns_zero(self):
        from core.reconstruction import worker

        assert worker.run_derivation_item_authoring_for_document("") == 0

    def test_probe_collection_failure_is_non_fatal(self, monkeypatch):
        from core.reconstruction import worker

        def _boom(doc_id):
            raise RuntimeError("boom")

        monkeypatch.setattr(worker, "collect_derivation_probes", _boom)
        assert worker.run_derivation_item_authoring_for_document("doc1") == 0


class TestRunItemAuthoringForDocumentComposition:
    """run_item_authoring_for_document は LLM 分と derivation 分を合算する。"""

    def test_sums_llm_and_derivation_counts(self, monkeypatch):
        from core.reconstruction import worker

        monkeypatch.setattr(worker, "_run_llm_item_authoring_for_document", lambda doc_id: 3)
        monkeypatch.setattr(worker, "run_derivation_item_authoring_for_document", lambda doc_id: 2)
        assert worker.run_item_authoring_for_document("doc1") == 5

    def test_survives_derivation_authoring_failure(self, monkeypatch):
        from core.reconstruction import worker

        monkeypatch.setattr(worker, "_run_llm_item_authoring_for_document", lambda doc_id: 4)

        def _boom(doc_id):
            raise RuntimeError("boom")

        monkeypatch.setattr(worker, "run_derivation_item_authoring_for_document", _boom)
        assert worker.run_item_authoring_for_document("doc1") == 4

    def test_derivation_authoring_runs_even_when_llm_authoring_finds_nothing(self, monkeypatch):
        """LLM 側が 0 件でも（累積上限到達・claims 無し等）derivation 側は独立して実行される。"""
        from core.reconstruction import worker

        monkeypatch.setattr(worker, "_run_llm_item_authoring_for_document", lambda doc_id: 0)
        monkeypatch.setattr(worker, "run_derivation_item_authoring_for_document", lambda doc_id: 1)
        assert worker.run_item_authoring_for_document("doc1") == 1


# ---------------------------------------------------------------------------
# routes/reconstruction.py::get_next_item — 連続回避
# ---------------------------------------------------------------------------


class _FakeNextItemSession:
    def __init__(self, last_mode_row):
        self.last_mode_row = last_mode_row
        self.main_queries = []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if "JOIN reconstruction_items i ON i.id = r.item_id" in sql:
            return _FakeResult(self.last_mode_row)
        self.main_queries.append(sql)
        return _FakeResult(None)

    def close(self):
        pass


def _patch_next_item_scope(monkeypatch, route_mod):
    monkeypatch.setattr(
        route_mod, "get_accessible_course_data", lambda uid, cid: {"sources": [{"material_id": "m1"}]}
    )
    monkeypatch.setattr(
        route_mod,
        "_course_scope",
        lambda session, course_data, topic_id=None: {
            "material_ids": ["m1"], "doc_refs": ["m1"], "topic_chunk_ids": [],
        },
    )


class TestGetNextItemDeprioritization:
    def test_deprioritizes_after_recent_regime_answer(self, monkeypatch):
        import routes.reconstruction as route_mod

        _patch_next_item_scope(monkeypatch, route_mod)
        fake = _FakeNextItemSession(last_mode_row=("regime",))
        monkeypatch.setattr(route_mod, "_pg_session", lambda: fake)

        result = route_mod.get_next_item("c1", "t1", current_user={"id": "u1"})

        assert result == {"item": None, "exhausted": True}
        assert fake.main_queries
        assert all(
            "CASE WHEN i.elicit_mode IN ('regime', 'next_step') THEN 1 ELSE 0 END ASC" in q
            for q in fake.main_queries
        )

    def test_deprioritizes_after_recent_next_step_answer(self, monkeypatch):
        import routes.reconstruction as route_mod

        _patch_next_item_scope(monkeypatch, route_mod)
        fake = _FakeNextItemSession(last_mode_row=("next_step",))
        monkeypatch.setattr(route_mod, "_pg_session", lambda: fake)

        route_mod.get_next_item("c1", "t1", current_user={"id": "u1"})

        assert fake.main_queries
        assert all("CASE WHEN" in q for q in fake.main_queries)

    def test_no_deprioritization_when_last_answer_was_predict(self, monkeypatch):
        import routes.reconstruction as route_mod

        _patch_next_item_scope(monkeypatch, route_mod)
        fake = _FakeNextItemSession(last_mode_row=("predict",))
        monkeypatch.setattr(route_mod, "_pg_session", lambda: fake)

        route_mod.get_next_item("c1", "t1", current_user={"id": "u1"})

        assert fake.main_queries
        assert all("CASE WHEN" not in q for q in fake.main_queries)

    def test_no_deprioritization_when_no_prior_answers(self, monkeypatch):
        import routes.reconstruction as route_mod

        _patch_next_item_scope(monkeypatch, route_mod)
        fake = _FakeNextItemSession(last_mode_row=None)
        monkeypatch.setattr(route_mod, "_pg_session", lambda: fake)

        route_mod.get_next_item("c1", "t1", current_user={"id": "u1"})

        assert fake.main_queries
        assert all("CASE WHEN" not in q for q in fake.main_queries)

    def test_does_not_exclude_derivation_items_never_only_deprioritize(self, monkeypatch):
        """既存の必須条件（symbol 除外・source_backed・承認済み）は連続回避と無関係に維持される。"""
        import routes.reconstruction as route_mod

        _patch_next_item_scope(monkeypatch, route_mod)
        fake = _FakeNextItemSession(last_mode_row=("regime",))
        monkeypatch.setattr(route_mod, "_pg_session", lambda: fake)

        route_mod.get_next_item("c1", "t1", current_user={"id": "u1"})

        for q in fake.main_queries:
            assert "i.elicit_mode <> 'symbol'" in q
            assert "c.support_status = :backed" in q
            assert "c.review_status = ANY(:approved)" in q
            # regime/next_step は WHERE から除外しない(ORDER BY のみで後回し)
            assert "i.elicit_mode NOT IN ('regime', 'next_step')" not in q


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
