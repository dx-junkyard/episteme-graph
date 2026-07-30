"""``discuss_opening`` ステージ本体の挙動（DB / LLM 非接続）。

正本: ``docs/features/discuss_opening_authoring_design.md`` §4.1・§5・§7.1。
静的なガードレールは ``test_discuss_opening_authoring_guardrails.py``、ここは
「素材の解決 → コスト上限 → agent → 永続化」の配線を実際に動かして確かめる
（``test_contextual_explanation_stage.py`` と同じ立場）。
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

import core.document_pipeline.orchestrator as orch  # noqa: E402
from core.discuss import authoring  # noqa: E402
from episteme_graph.agents.discuss_opening.schema import (  # noqa: E402
    DiscussOpeningInput,
    DiscussOpeningResult,
    DiscussionSeed,
)

_STATEMENT = "The detector response is linear over the full energy range."
_DOCUMENT_ID = "11111111-2222-3333-4444-555555555555"

_ARTIFACTS = {
    "paper_skeleton": {"paper_goal": {"text": "Establish a consistency relation."}},
    "thesis_reconstruction": {
        "central_question": "Can one relation describe the ratios?",
        "central_thesis": {"text": "One relation links the ratios.", "claim_ids": ["c1"]},
    },
}
_DERIVATIONS = {
    "chains": [
        {
            "steps": [
                {
                    "operation": "linearize",
                    "operation_subtype": "small amplitude",
                    "reason": "The amplitude is small compared with the separation.",
                    "input_equation_ids": ["eq_1"],
                    "output_equation_ids": ["eq_2"],
                },
                {
                    "operation": "transform",
                    "reason": "generic move",
                    "input_equation_ids": [],
                    "output_equation_ids": [],
                },
            ]
        }
    ]
}
_EQUATIONS = {
    "equations": [
        {"equation_id": "eq_1", "source_extraction": {"plain_text": "x'' + sin(x) = 0"}},
        {"equation_id": "eq_2", "source_extraction": {"plain_text": "x'' + x = 0"}},
    ]
}


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """epistemic_ledger 読み出しと element_explanations 書き込みだけを模す。"""

    def __init__(self, ledger_rows):
        self._ledger_rows = ledger_rows
        self.inserted: list[dict] = []
        self.updates = 0
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def execute(self, statement, params=None):
        sql = str(statement)
        if "epistemic_ledger" in sql:
            return _Result(self._ledger_rows)
        if sql.strip().upper().startswith("UPDATE"):
            self.updates += 1
            return _Result([])
        if "INSERT INTO element_explanations" in sql:
            self.inserted.append(dict(params or {}))
            return _Result([(
                f"id-{len(self.inserted)}", params["document_id"], params["element_type"],
                params["element_id"], params["kind"], params["body"], {},
                params["status"], params["created_by"], None, None, None, params["role"],
            )])
        return _Result([])

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class _FakeAgent:
    """DiscussOpeningAgent の差し替え（run の入力を記録し、固定の結果を返す）。"""

    last_payload: dict | None = None
    result_factory = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def run(self, payload, cartridge_id=None, config=None):
        type(self).last_payload = payload
        item = DiscussOpeningInput.from_dict(payload)
        factory = type(self).result_factory
        if factory is not None:
            return factory(item)
        return DiscussOpeningResult(
            document_id=item.document_id,
            seeds=[
                DiscussionSeed(
                    body="この前提が崩れたら結論はどう変わると考えますか？",
                    evidence_quote=_STATEMENT, reason="未検証の前提だから",
                    confidence=0.6, grounded_in="untested_assumption",
                ),
                DiscussionSeed(
                    body="別の手を選んだら何が見えなくなると思いますか？",
                    evidence_quote="The amplitude is small compared with the separation.",
                    reason="著者の選択だから", confidence=0.5,
                    grounded_in="author_choice",
                ),
            ],
            language=item.language,
            llm_call_count=1,
        )


@pytest.fixture
def stage_env(monkeypatch):
    """DB セッション・台帳ラベル解決・agent を差し替えた環境を組む。"""
    session = _FakeSession([("t1", "assumption", "untested")])
    monkeypatch.setattr("core.postgres.get_session", lambda: session)
    monkeypatch.setattr(
        "core.doubt.open_assumptions.target_label", lambda s, t, i: _STATEMENT
    )
    _FakeAgent.last_payload = None
    _FakeAgent.result_factory = None
    monkeypatch.setattr(
        "episteme_graph.agents.discuss_opening.agent.DiscussOpeningAgent", _FakeAgent
    )
    # コストゲートはプロセス寿命なのでテストごとに新品にする。
    monkeypatch.setattr(orch, "_discuss_opening_cost_gate", orch.CostGate())
    return session


def _run(**overrides) -> dict:
    kwargs = dict(
        document_id=_DOCUMENT_ID,
        cartridge_id="particle_physics",
        artifacts=_ARTIFACTS,
        derivations=_DERIVATIONS,
        equations=_EQUATIONS,
    )
    kwargs.update(overrides)
    return orch._build_discuss_opening(**kwargs)  # noqa: SLF001


class TestMaterialResolution:
    def test_agent_receives_resolved_text_only(self, stage_env):
        _run()

        payload = _FakeAgent.last_payload
        assert payload is not None
        assert [a["statement"] for a in payload["untested_assumptions"]] == [_STATEMENT]
        operations = [c["operation"] for c in payload["author_choices"]]
        # 具体的な手が先、汎用 operation は後ろ（捨てない）
        assert operations == ["linearize", "transform"]
        # 式 id ではなく式ラベルに解決されている
        assert payload["author_choices"][0]["from_label"] == "x'' + sin(x) = 0"
        assert payload["thesis"]["paper_goal"] == "Establish a consistency relation."
        assert payload["language"] == "ja"

    def test_counts_are_recorded_in_the_stage_payload(self, stage_env):
        payload = _run()
        assert payload["assumption_count"] == 1
        assert payload["author_choice_count"] == 2
        assert payload["seed_count"] == 2
        assert payload["llm_calls"] == 1
        assert payload["status"] == "completed"

    def test_ledger_read_failure_is_non_fatal(self, monkeypatch, stage_env):
        def _boom(*_args, **_kwargs):
            raise RuntimeError("ledger unavailable")

        monkeypatch.setattr(authoring, "collect_untested_assumptions", _boom)

        payload = _run()

        assert payload["assumption_lookup_failed"] is True
        # operation だけで生成は続く
        assert payload["seed_count"] == 2


class TestPersistence:
    def test_seeds_are_persisted_as_document_scoped_candidates(self, stage_env):
        payload = _run()

        assert payload["saved_candidates"] == 2
        assert stage_env.committed is True
        assert stage_env.closed is True
        # supersede はキー単位で1回（兄弟 seed を消さない）
        assert stage_env.updates == 1
        for row in stage_env.inserted:
            assert row["element_type"] == "document"
            assert row["element_id"] == _DOCUMENT_ID
            assert row["kind"] == "contextual"
            assert row["role"] == "discussion_seed"
            assert row["status"] == "candidate"
            assert row["created_by"] == "pipeline"
            assert payload["source_fingerprint"] in row["evidence"]

    def test_evidence_carries_quote_reason_confidence_and_fingerprint(self, stage_env):
        import json

        payload = _run()
        evidence = json.loads(stage_env.inserted[0]["evidence"])

        assert evidence["evidence_quote"] == _STATEMENT
        assert evidence["reason"]
        assert evidence["confidence"] == 0.6
        assert evidence["grounded_in"] == "untested_assumption"
        assert evidence["language"] == "ja"
        assert evidence["source_fingerprint"] == payload["source_fingerprint"]
        assert evidence["opening_version"] == "v1"

    def test_fingerprint_matches_the_standalone_helper(self, stage_env):
        payload = _run()
        assert payload["source_fingerprint"] == authoring.compute_source_fingerprint(_ARTIFACTS)


class TestSkipPaths:
    def test_no_material_skips_without_persisting(self, stage_env):
        stage_env._ledger_rows = []  # noqa: SLF001
        payload = _run(derivations={"chains": []})

        assert payload["skipped_reason"] == "no_source_material"
        assert payload["seed_count"] == 0
        assert payload["saved_candidates"] == 0
        assert stage_env.inserted == []
        assert _FakeAgent.last_payload is None  # agent すら呼ばない

    def test_daily_limit_is_reported_honestly(self, monkeypatch, stage_env):
        monkeypatch.setattr(orch, "_discuss_opening_max_calls_per_day", lambda: 0)

        payload = _run()

        assert payload["skipped_by_limit"] is True
        assert payload["skipped_reason"] == "daily_call_limit_reached"
        assert payload["llm_calls"] == 0
        assert stage_env.inserted == []

    def test_zero_item_limit_disables_generation(self, monkeypatch, stage_env):
        monkeypatch.setattr(orch, "_discuss_opening_max_items_per_document", lambda: 0)

        payload = _run()

        assert payload["skipped_reason"] == "item_limit_is_zero"
        assert stage_env.inserted == []

    def test_agent_skip_reason_is_propagated(self, stage_env):
        _FakeAgent.result_factory = lambda item: DiscussOpeningResult.make_skipped(
            item, "repair_failed", "validation failed after repair attempts"
        )

        payload = _run()

        assert payload["skipped_reason"] == "repair_failed"
        assert payload["saved_candidates"] == 0
        assert payload["review_notes"]
        assert stage_env.inserted == []

    def test_persistence_failure_rolls_back_and_stays_non_fatal(self, monkeypatch, stage_env):
        def _boom(*_args, **_kwargs):
            raise RuntimeError("db down")

        monkeypatch.setattr("core.element_explanations.insert_candidates", _boom)

        payload = _run()

        assert payload["saved_candidates"] == 0
        assert payload["seed_count"] == 2  # 生成自体は記録される
        assert stage_env.rolled_back is True


class TestLanguageSetting:
    def test_language_env_is_honoured(self, monkeypatch, stage_env):
        monkeypatch.setenv("DISCUSS_OPENING_LANGUAGE", "en")

        payload = _run()

        assert payload["language"] == "en"
        assert _FakeAgent.last_payload["language"] == "en"

    def test_default_language_is_ja(self, monkeypatch, stage_env):
        monkeypatch.delenv("DISCUSS_OPENING_LANGUAGE", raising=False)
        assert orch._discuss_opening_language() == "ja"  # noqa: SLF001


class TestAuthoringHelpers:
    def test_untested_statuses_only(self):
        assert authoring.UNTESTED_VERIFICATION_STATUSES == ("untested", "unknown")

    def test_collect_untested_assumptions_drops_unresolvable_rows(self, monkeypatch):
        session = _FakeSession([("t1", "assumption", "untested"), ("t2", "claim", "unknown")])
        monkeypatch.setattr(
            "core.doubt.open_assumptions.target_label",
            lambda s, t, i: _STATEMENT if i == "t1" else "",
        )

        items = authoring.collect_untested_assumptions(session, _DOCUMENT_ID)

        assert [i["statement"] for i in items] == [_STATEMENT]
        assert items[0]["verification_status"] == "untested"

    def test_author_choices_are_deduplicated(self):
        derivations = {
            "chains": [
                {
                    "steps": [
                        {"operation": "linearize", "reason": "same", "operation_subtype": ""},
                        {"operation": "linearize", "reason": "same", "operation_subtype": ""},
                    ]
                }
            ]
        }
        choices = authoring.collect_author_choices(derivations, _EQUATIONS)
        assert len(choices) == 1

    def test_author_choices_are_capped(self):
        derivations = {
            "chains": [
                {
                    "steps": [
                        {"operation": "solve", "reason": f"reason {i}"} for i in range(30)
                    ]
                }
            ]
        }
        choices = authoring.collect_author_choices(derivations, _EQUATIONS, limit=3)
        assert len(choices) == 3
