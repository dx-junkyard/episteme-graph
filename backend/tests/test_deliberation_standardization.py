"""標準化判定 worker（Phase S、知識ネットワークビジョン §3 修正③・§6・§7）のテスト。

設計書:
- `docs/features/knowledge_network_vision.md`（§3 修正③・§6・§7 Phase S）
- `docs/features/element_deliberation_workspace_design.md`（§5.5 標準化判定）

実 LLM・実 DB は使わない（docker 停止・API キー無し）。純粋関数（``aggregate.decide()``・
``validator.validate_output()``）とロジック配線（``worker.py`` の冪等性チェック・force・
コスト上限、``annotations._commit_standardization`` のガード節）を、monkeypatch した
フェイクで検証する。
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

from core.deliberation import annotations as delib_annotations  # noqa: E402
from core.deliberation.schema import (  # noqa: E402
    ANNOTATION_KIND_STANDARDIZATION,
    ANNOTATION_KINDS_COMMIT_UNSUPPORTED,
    ANNOTATION_STATUS_CANDIDATE,
    ANNOTATION_STATUS_COMMITTED,
    ANNOTATION_STATUS_DISMISSED,
    ELEMENT_SHARED_PART,
    SCOPE_DOCUMENT,
    SCOPE_DOMAIN,
)
from core.deliberation.standardization import (  # noqa: E402
    aggregate,
    input_builder,
    prompt as stdpart_prompt,
    validator as stdpart_validator,
    worker as stdpart_worker,
)
from core.deliberation.standardization.schema import (  # noqa: E402
    BREADTH_FIELD,
    BREADTH_TEXTBOOK,
    BREADTH_UNKNOWN,
    BREADTHS,
    CorpusRepetitionEvidence,
    LibrarySimilarityEvidence,
    LLMPriorKnowledge,
    STANDARDIZATION_STATUS_EMERGING_COMMON,
    STANDARDIZATION_STATUS_FIELD_STANDARD,
    STANDARDIZATION_STATUS_NOVEL,
    STANDARDIZATION_STATUS_STANDARD,
    STANDARDIZATION_STATUS_UNKNOWN,
    STANDARDIZATION_STATUSES,
)
from core.library.schema import STANDARDIZATION_STATUSES as LIBRARY_STANDARDIZATION_STATUSES
from tests.guardrail_helpers import (  # noqa: E402
    assert_module_tree_does_not_import,
    assert_module_tree_forbids,
    read_migration_sql,
)

_STANDARDIZATION_DIR = BACKEND / "core" / "deliberation" / "standardization"
_ROUTE_SRC = (BACKEND / "api" / "routes" / "deliberation.py").read_text(encoding="utf-8")
_ANNOTATIONS_SRC = (BACKEND / "core" / "deliberation" / "annotations.py").read_text(encoding="utf-8")
_WORKER_SRC = (_STANDARDIZATION_DIR / "worker.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# レイヤリング・ガードレール
# ---------------------------------------------------------------------------


class TestLayering:
    def test_no_fastapi_import(self):
        assert_module_tree_does_not_import(_STANDARDIZATION_DIR, ["fastapi"])

    def test_no_routes_or_services_import(self):
        assert_module_tree_does_not_import(_STANDARDIZATION_DIR, ["routes", "services"])

    def test_no_a_layer_agents_import(self):
        assert_module_tree_does_not_import(_STANDARDIZATION_DIR, ["episteme_graph.agents"])

    def test_does_not_call_library_write_functions(self):
        # L層ガードレール維持: 標準化判定 worker は library の書き込み関数を一切呼ばない
        # （retrieval・get_entry・list_entries のような読み取りのみ）。LLM がライブラリへ
        # 直接書き込む経路を作らない、という既存原則をここでも構造的に守る。
        assert_module_tree_forbids(
            _STANDARDIZATION_DIR,
            [
                "library_store.create_entry",
                "library_store.update_entry",
                "library_store.freeze_entry",
                "library_store.retire_entry",
                "library_store.restore_entry",
                "library.store.create_entry",
                "library.store.update_entry",
                "library.store.freeze_entry",
            ],
        )


# ---------------------------------------------------------------------------
# 語彙: 5値が知識ネットワークビジョン §3 の表と一致する
# ---------------------------------------------------------------------------


class TestVocabulary:
    def test_five_statuses_match_the_vision_table(self):
        assert STANDARDIZATION_STATUSES == (
            "standard",
            "field_standard",
            "emerging_common",
            "novel",
            "unknown",
        )

    def test_standardization_schema_reexports_library_schema_as_single_source(self):
        # core.library.schema が正本（library_entries.standardization_status の CHECK と
        # 対応するため）。core.deliberation.standardization.schema は読み取り専用の再エクスポート。
        assert STANDARDIZATION_STATUSES == LIBRARY_STANDARDIZATION_STATUSES

    def test_breadth_vocabulary(self):
        assert BREADTHS == (BREADTH_TEXTBOOK, BREADTH_FIELD, BREADTH_UNKNOWN)


# ---------------------------------------------------------------------------
# aggregate.decide(): 決定論的合成（純粋関数）— 5語彙すべてに到達する分岐を網羅する
# ---------------------------------------------------------------------------


def _llm(recognized: bool, breadth: str = BREADTH_UNKNOWN, *, repair_failed: bool = False) -> LLMPriorKnowledge:
    return LLMPriorKnowledge(
        recognized=recognized, breadth=breadth, reason="stub reason", confidence=0.8,
        repair_failed=repair_failed,
    )


def _library(hit: bool) -> LibrarySimilarityEvidence:
    return LibrarySimilarityEvidence(hit=hit, matched_entry_ids=["e-1"] if hit else [])


def _corpus(repeated: bool, count: int = 0) -> CorpusRepetitionEvidence:
    return CorpusRepetitionEvidence(repeated=repeated, document_count=count)


class TestAggregateDecide:
    def test_all_three_converge_on_standard(self):
        result = aggregate.decide(_llm(True, BREADTH_TEXTBOOK), _library(True), _corpus(True, 3))
        assert result.standardization_status == STANDARDIZATION_STATUS_STANDARD

    def test_repeated_but_no_external_standard_is_emerging_common(self):
        # ビジョン §3 修正③の発見的価値の在り処: コーパス内反復のみ、LLM は非認知・類似無し。
        result = aggregate.decide(_llm(False), _library(False), _corpus(True, 2))
        assert result.standardization_status == STANDARDIZATION_STATUS_EMERGING_COMMON

    @pytest.mark.parametrize(
        "llm_recognized,breadth,library_hit,repeated",
        [
            (False, BREADTH_UNKNOWN, True, False),   # L層類似のみ
            (False, BREADTH_UNKNOWN, True, True),    # L層類似+反復（LLM非認知）
            (True, BREADTH_FIELD, False, True),      # LLM(field)+反復、類似なし
            (True, BREADTH_TEXTBOOK, True, False),   # LLM(textbook)+類似、反復なし
            (True, BREADTH_TEXTBOOK, False, True),   # LLM(textbook)+反復、類似なし
        ],
    )
    def test_partial_support_is_field_standard(self, llm_recognized, breadth, library_hit, repeated):
        result = aggregate.decide(
            _llm(llm_recognized, breadth), _library(library_hit), _corpus(repeated, 2 if repeated else 0),
        )
        assert result.standardization_status == STANDARDIZATION_STATUS_FIELD_STANDARD

    def test_single_occurrence_no_corroboration_is_novel(self):
        result = aggregate.decide(_llm(False), _library(False), _corpus(False))
        assert result.standardization_status == STANDARDIZATION_STATUS_NOVEL

    def test_llm_alone_claims_recognition_without_corroboration_is_unknown(self):
        # 幻覚対策: LLM だけが textbook 級だと主張しても、他の証拠が皆無なら standard/
        # field_standard に自動昇格させない（KN-3）。
        result = aggregate.decide(_llm(True, BREADTH_TEXTBOOK), _library(False), _corpus(False))
        assert result.standardization_status == STANDARDIZATION_STATUS_UNKNOWN

    def test_repair_failed_llm_still_yields_a_valid_five_vocab_status(self):
        # 修復失敗(repair_failed=True)は evidence①=unknown 相当として続行し、他の2証拠だけで
        # 判定を続けられる(P4)。単独で "unknown" を強制するわけではない。
        result = aggregate.decide(
            _llm(False, repair_failed=True), _library(False), _corpus(True, 2),
        )
        assert result.standardization_status == STANDARDIZATION_STATUS_EMERGING_COMMON
        assert result.confidence is None  # repair_failed のときは生の confidence を返さない

    def test_evidence_summary_is_factual_and_carries_all_three_evidences(self):
        result = aggregate.decide(_llm(True, BREADTH_TEXTBOOK), _library(True), _corpus(True, 4))
        summary = result.evidence_summary
        assert "llm_prior_knowledge" in summary
        assert "library_similarity" in summary
        assert "corpus_repetition" in summary
        assert summary["corpus_repetition"]["document_count"] == 4

    def test_all_five_statuses_are_reachable(self):
        # 決定表が5語彙すべてに到達できることを網羅的に確認する。
        reached = set()
        for recognized in (False, True):
            for breadth in BREADTHS:
                for hit in (False, True):
                    for repeated in (False, True):
                        r = aggregate.decide(
                            _llm(recognized, breadth), _library(hit), _corpus(repeated, 2 if repeated else 0),
                        )
                        reached.add(r.standardization_status)
        assert reached == set(STANDARDIZATION_STATUSES)


# ---------------------------------------------------------------------------
# validator.validate_output: hard error / warning coercion
# ---------------------------------------------------------------------------


class TestValidateOutput:
    def _well_formed(self, **overrides) -> dict:
        item = {
            "recognized": True,
            "claimed_canonical_name": "Lock-in amplifier",
            "breadth": "textbook",
            "reason": "教科書に頻出する装置のため",
            "confidence": 0.9,
        }
        item.update(overrides)
        return item

    def test_accepts_well_formed_output(self):
        result, errors, warnings = stdpart_validator.validate_output(self._well_formed())
        assert errors == []
        assert result is not None
        assert result.recognized is True
        assert result.breadth == "textbook"

    def test_rejects_non_bool_recognized(self):
        result, errors, _ = stdpart_validator.validate_output(self._well_formed(recognized="yes"))
        assert result is None
        assert any("recognized" in e for e in errors)

    def test_rejects_missing_reason(self):
        result, errors, _ = stdpart_validator.validate_output(self._well_formed(reason="  "))
        assert result is None
        assert any("reason" in e for e in errors)

    def test_invalid_breadth_defaults_to_unknown_with_warning_not_hard_error(self):
        result, errors, warnings = stdpart_validator.validate_output(self._well_formed(breadth="galactic"))
        assert errors == []
        assert result is not None
        assert result.breadth == BREADTH_UNKNOWN
        assert any("breadth" in w for w in warnings)

    def test_confidence_clamped_into_unit_interval(self):
        result, _, warnings = stdpart_validator.validate_output(self._well_formed(confidence=5.0))
        assert result.confidence == 1.0
        assert any("clamped" in w for w in warnings)

    def test_non_numeric_confidence_defaults_to_zero_with_warning(self):
        result, _, warnings = stdpart_validator.validate_output(self._well_formed(confidence="high"))
        assert result.confidence == 0.0
        assert any("confidence" in w for w in warnings)

    def test_non_dict_input_is_a_hard_error(self):
        result, errors, _ = stdpart_validator.validate_output("not a dict")  # type: ignore[arg-type]
        assert result is None
        assert errors


# ---------------------------------------------------------------------------
# repair: 2回失敗しても repair_failed=True の LLMPriorKnowledge を返す(P4)
# ---------------------------------------------------------------------------


class _AlwaysBrokenLLMClient:
    def complete_json(self, content: str) -> dict:
        return {"recognized": "not-a-bool"}  # 常に検証失敗


class TestRepairFailure:
    def test_repair_failure_falls_back_to_non_recognized_unknown(self):
        from core.deliberation.standardization import repair as stdpart_repair

        result = stdpart_repair.run_with_repair(_AlwaysBrokenLLMClient(), "content")
        assert result.repair_failed is True
        assert result.recognized is False
        assert result.breadth == BREADTH_UNKNOWN


# ---------------------------------------------------------------------------
# input_builder: 純粋なライブラリ類似・コーパス反復の計算ロジック
# ---------------------------------------------------------------------------


class TestInputBuilder:
    def test_library_similarity_excludes_self_and_reports_hit(self, monkeypatch):
        monkeypatch.setattr(
            input_builder.library_search, "search_frozen_entries",
            lambda **kwargs: [{"entry_id": "self-id"}, {"entry_id": "other-id"}],
        )
        entry = {"id": "self-id", "domain_key": "d", "entry_type": "apparatus", "name": "X"}
        evidence = input_builder._library_similarity(entry)
        assert evidence.hit is True
        assert evidence.matched_entry_ids == ["other-id"]

    def test_library_similarity_no_hits_when_only_self_returned(self, monkeypatch):
        monkeypatch.setattr(
            input_builder.library_search, "search_frozen_entries",
            lambda **kwargs: [{"entry_id": "self-id"}],
        )
        entry = {"id": "self-id", "domain_key": "d", "entry_type": "apparatus", "name": "X"}
        evidence = input_builder._library_similarity(entry)
        assert evidence.hit is False
        assert evidence.matched_entry_ids == []

    def test_library_similarity_skips_search_when_no_query_text(self, monkeypatch):
        called = []
        monkeypatch.setattr(
            input_builder.library_search, "search_frozen_entries",
            lambda **kwargs: called.append(kwargs) or [],
        )
        evidence = input_builder._library_similarity({"id": "e1", "name": ""})
        assert evidence.hit is False
        assert called == []

    def test_corpus_repetition_unions_source_document_ids_and_confirmed_links(self, monkeypatch):
        monkeypatch.setattr(
            input_builder.identity_links, "list_for_shared_part",
            lambda shared_part_id: [
                {"status": "confirmed", "instance_document_id": "doc-b"},
                {"status": "candidate", "instance_document_id": "doc-c"},  # candidate は数えない
                {"status": "rejected", "instance_document_id": "doc-d"},  # rejected も数えない
            ],
        )
        entry = {"id": "e1", "source_document_ids": ["doc-a"]}
        evidence = input_builder._corpus_repetition(entry)
        assert evidence.repeated is True
        assert evidence.document_count == 2
        assert evidence.document_ids == ["doc-a", "doc-b"]

    def test_corpus_repetition_single_document_is_not_repeated(self, monkeypatch):
        monkeypatch.setattr(input_builder.identity_links, "list_for_shared_part", lambda shared_part_id: [])
        evidence = input_builder._corpus_repetition({"id": "e1", "source_document_ids": ["doc-a"]})
        assert evidence.repeated is False
        assert evidence.document_count == 1


class TestPrompt:
    def test_build_content_includes_target_fields(self):
        context = input_builder.StandardizationTargetContext(
            entry_id="e1", domain_key="d1", entry_type="apparatus", name="Lock-in amplifier",
            aliases=["LIA"], summary="synchronous detection device",
        )
        content = stdpart_prompt.build_content(context)
        assert "domain: d1" in content
        assert "name: Lock-in amplifier" in content
        assert "LIA" in content

    def test_build_repair_prompt_includes_errors(self):
        text = stdpart_prompt.build_repair_prompt("{}", ["reason is required"])
        assert "reason is required" in text

    def test_instruction_tells_the_llm_not_to_fabricate(self):
        # ビジョン §3 修正③の失敗モード1（LLM が標準だと幻覚する）への直接的な対抗策。
        assert "捏造" in stdpart_prompt.build_instruction()


# ---------------------------------------------------------------------------
# worker: 冪等性チェック・force・コスト上限
# ---------------------------------------------------------------------------


class TestIdempotencyCheck:
    def test_true_when_candidate_annotation_exists(self, monkeypatch):
        monkeypatch.setattr(
            stdpart_worker.delib_store, "list_annotations_for_element",
            lambda *a, **k: [{"kind": ANNOTATION_KIND_STANDARDIZATION, "status": ANNOTATION_STATUS_CANDIDATE}],
        )
        assert stdpart_worker.has_pending_or_committed_assessment("e1", "d1") is True

    def test_true_when_committed_annotation_exists(self, monkeypatch):
        monkeypatch.setattr(
            stdpart_worker.delib_store, "list_annotations_for_element",
            lambda *a, **k: [{"kind": ANNOTATION_KIND_STANDARDIZATION, "status": ANNOTATION_STATUS_COMMITTED}],
        )
        assert stdpart_worker.has_pending_or_committed_assessment("e1", "d1") is True

    def test_false_when_only_dismissed_annotation_exists(self, monkeypatch):
        monkeypatch.setattr(
            stdpart_worker.delib_store, "list_annotations_for_element",
            lambda *a, **k: [{"kind": ANNOTATION_KIND_STANDARDIZATION, "status": ANNOTATION_STATUS_DISMISSED}],
        )
        assert stdpart_worker.has_pending_or_committed_assessment("e1", "d1") is False

    def test_false_when_no_standardization_annotation_exists(self, monkeypatch):
        monkeypatch.setattr(
            stdpart_worker.delib_store, "list_annotations_for_element",
            lambda *a, **k: [{"kind": "identity", "status": ANNOTATION_STATUS_CANDIDATE}],
        )
        assert stdpart_worker.has_pending_or_committed_assessment("e1", "d1") is False

    def test_false_when_no_annotations_at_all(self, monkeypatch):
        monkeypatch.setattr(stdpart_worker.delib_store, "list_annotations_for_element", lambda *a, **k: [])
        assert stdpart_worker.has_pending_or_committed_assessment("e1", "d1") is False


class TestRunAssessSharedPartSkipAndForce:
    def test_skips_when_entry_missing(self, monkeypatch):
        monkeypatch.setattr(stdpart_worker.library_store, "get_entry", lambda entry_id: None)
        result = stdpart_worker.run_assess_shared_part("missing")
        assert result == {"assessed": 0}

    def test_skips_retired_entry(self, monkeypatch):
        monkeypatch.setattr(
            stdpart_worker.library_store, "get_entry",
            lambda entry_id: {"id": entry_id, "status": "retired", "domain_key": "d"},
        )
        result = stdpart_worker.run_assess_shared_part("e1")
        assert result["assessed"] == 0
        assert result.get("skipped") is True

    def test_skips_when_pending_assessment_exists_and_not_forced(self, monkeypatch):
        monkeypatch.setattr(
            stdpart_worker.library_store, "get_entry",
            lambda entry_id: {"id": entry_id, "status": "active", "domain_key": "d"},
        )
        monkeypatch.setattr(stdpart_worker, "has_pending_or_committed_assessment", lambda *a, **k: True)
        called = []
        monkeypatch.setattr(stdpart_worker, "_assess_entry", lambda entry: called.append(entry) or {"id": "a1"})
        result = stdpart_worker.run_assess_shared_part("e1", force=False)
        assert result["assessed"] == 0
        assert called == []

    def test_force_bypasses_pending_assessment_skip(self, monkeypatch):
        monkeypatch.setattr(
            stdpart_worker.library_store, "get_entry",
            lambda entry_id: {"id": entry_id, "status": "active", "domain_key": "d"},
        )
        monkeypatch.setattr(stdpart_worker, "has_pending_or_committed_assessment", lambda *a, **k: True)
        called = []
        monkeypatch.setattr(stdpart_worker, "_assess_entry", lambda entry: called.append(entry) or {"id": "a1"})
        result = stdpart_worker.run_assess_shared_part("e1", force=True)
        assert result["assessed"] == 1
        assert len(called) == 1


class TestRunAssessDomainSkipAndForce:
    def test_skips_entries_with_pending_assessment_unless_forced(self, monkeypatch):
        entries = [
            {"id": "e1", "domain_key": "d", "status": "active"},
            {"id": "e2", "domain_key": "d", "status": "active"},
        ]
        monkeypatch.setattr(stdpart_worker.library_store, "list_entries", lambda **k: entries)
        monkeypatch.setattr(
            stdpart_worker, "has_pending_or_committed_assessment",
            lambda entry_id, domain_key: entry_id == "e1",
        )
        assessed_entries = []
        monkeypatch.setattr(
            stdpart_worker, "_assess_entry", lambda entry: assessed_entries.append(entry["id"]) or {"id": "a"},
        )
        result = stdpart_worker.run_assess_domain("d", force=False)
        assert assessed_entries == ["e2"]
        assert result["assessed"] == 1

    def test_force_reassesses_every_active_entry(self, monkeypatch):
        entries = [
            {"id": "e1", "domain_key": "d", "status": "active"},
            {"id": "e2", "domain_key": "d", "status": "active"},
        ]
        monkeypatch.setattr(stdpart_worker.library_store, "list_entries", lambda **k: entries)
        monkeypatch.setattr(stdpart_worker, "has_pending_or_committed_assessment", lambda *a, **k: True)
        assessed_entries = []
        monkeypatch.setattr(
            stdpart_worker, "_assess_entry", lambda entry: assessed_entries.append(entry["id"]) or {"id": "a"},
        )
        result = stdpart_worker.run_assess_domain("d", force=True)
        assert assessed_entries == ["e1", "e2"]
        assert result["assessed"] == 2


class TestCostGate:
    def test_cost_gate_blocks_assess_entry_beyond_daily_limit(self, monkeypatch):
        class _FakeSettings:
            stdpart_max_calls_per_day = 0

        monkeypatch.setattr(stdpart_worker, "get_settings", lambda: _FakeSettings())
        # cost gate はプロセス内カウンタなので、他テストの影響を避けるため一時的に
        # 上限0にした新しい CostGate インスタンスへ差し替える。
        from core.llm_worker.cost_gate import CostGate

        monkeypatch.setattr(stdpart_worker, "_cost_gate", CostGate())
        result = stdpart_worker._assess_entry({"id": "e1", "domain_key": "d", "name": "X"})
        assert result is None

    def test_worker_uses_shared_cost_gate_primitive(self):
        assert "from core.llm_worker.cost_gate import CostGate" in _WORKER_SRC

    def test_worker_reads_setting_via_getattr_with_default(self):
        assert "stdpart_max_calls_per_day" in _WORKER_SRC


class TestWorkerNeverPassesExplicitStatus:
    """W2/KN-3: worker が element_annotations へ書く際、常に candidate 固定
    （store.create_annotation は status 引数を受け取らない設計・既存ガードレールと同型）。"""

    def test_worker_does_not_pass_status_kwarg_to_create_annotation(self):
        assert "status=" not in _WORKER_SRC


# ---------------------------------------------------------------------------
# annotations._commit_standardization: ガード節（DB 非接続で検証できる部分のみ）
# ---------------------------------------------------------------------------


class TestCommitStandardizationGuards:
    def _annotation(self, **overrides) -> dict:
        base = {
            "id": "a1",
            "status": ANNOTATION_STATUS_CANDIDATE,
            "kind": ANNOTATION_KIND_STANDARDIZATION,
            "scope": SCOPE_DOMAIN,
            "element_type": ELEMENT_SHARED_PART,
            "element_id": "entry-1",
            "domain_key": "d1",
            "body": {"standardization_status": STANDARDIZATION_STATUS_EMERGING_COMMON},
            "evidence": ["q"],
            "reason": "r",
            "confidence": 0.5,
        }
        base.update(overrides)
        return base

    def test_rejects_document_scoped_annotation(self):
        annotation = self._annotation(scope=SCOPE_DOCUMENT, element_type="theory_component")
        with pytest.raises(delib_annotations.CommitRoutingError) as excinfo:
            delib_annotations._commit_standardization(annotation, "u1")
        assert excinfo.value.kind == "invalid"

    def test_rejects_missing_standardization_status_in_body(self):
        annotation = self._annotation(body={})
        with pytest.raises(delib_annotations.CommitRoutingError) as excinfo:
            delib_annotations._commit_standardization(annotation, "u1")
        assert excinfo.value.kind == "invalid"

    def test_rejects_invalid_standardization_status_value(self):
        annotation = self._annotation(body={"standardization_status": "bogus-value"})
        with pytest.raises(delib_annotations.CommitRoutingError) as excinfo:
            delib_annotations._commit_standardization(annotation, "u1")
        assert excinfo.value.kind == "invalid"

    def test_standardization_no_longer_in_commit_unsupported_set(self):
        assert ANNOTATION_KIND_STANDARDIZATION not in ANNOTATION_KINDS_COMMIT_UNSUPPORTED

    def test_commit_route_registered_for_standardization(self):
        assert ANNOTATION_KIND_STANDARDIZATION in delib_annotations._COMMIT_ROUTES
        assert delib_annotations._COMMIT_ROUTES[ANNOTATION_KIND_STANDARDIZATION] is (
            delib_annotations._commit_standardization
        )

    def test_commit_standardization_does_not_touch_revision_column(self):
        # §5.5 の決定: standardization_status はガバナンス列であり draft の revision
        # （楽観ロック）とは独立に更新する。SQL 文（UPDATE ... SET 句）に revision を
        # 含めないこと（docstring での言及自体は許容するので SQL 文だけを検査する）。
        import inspect
        import re

        body_src = inspect.getsource(delib_annotations._commit_standardization)
        sql_match = re.search(r'UPDATE library_entries.*?"""', body_src, re.DOTALL)
        assert sql_match is not None, "expected an UPDATE library_entries ... SQL literal"
        assert "revision" not in sql_match.group(0)


# ---------------------------------------------------------------------------
# migration 050: library_entries.standardization_status
# ---------------------------------------------------------------------------


class TestMigration050:
    def test_migration_050_passes_idempotency_lint(self):
        import tempfile

        from tests.test_migrations_runner import _collect_idempotency_violations

        src = read_migration_sql(BACKEND, 50)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "050_library_standardization_status.sql"
            target.write_text(src, encoding="utf-8")
            violations = _collect_idempotency_violations(Path(tmp))
        assert violations == [], violations

    def test_migration_050_adds_column_with_all_five_values(self):
        src = read_migration_sql(BACKEND, 50)
        assert "ADD COLUMN IF NOT EXISTS standardization_status" in src
        for status in STANDARDIZATION_STATUSES:
            assert f"'{status}'" in src


# ---------------------------------------------------------------------------
# U層: feature 語彙の登録
# ---------------------------------------------------------------------------


class TestUsageFeatureRegistered:
    def test_deliberation_standardization_feature_registered(self):
        from core.llm_usage.schema import KNOWN_FEATURES

        assert "deliberation:standardization" in KNOWN_FEATURES

    def test_worker_binds_usage_context_with_registered_feature(self):
        assert 'bind_usage_context(_FEATURE' in _WORKER_SRC
        assert _WORKER_SRC.count('_FEATURE = "deliberation:standardization"') == 1


# ---------------------------------------------------------------------------
# コスト設定（core/config.py）
# ---------------------------------------------------------------------------


class TestSettings:
    def test_settings_expose_stdpart_cost_controls(self):
        settings = stdpart_worker.get_settings()
        assert hasattr(settings, "stdpart_max_calls_per_day")
        assert hasattr(settings, "stdpart_llm_model")


# ---------------------------------------------------------------------------
# API ルート（ソース検査。DB 接続無しで確認できる範囲）
# ---------------------------------------------------------------------------


class TestStandardizationRoutes:
    def test_shared_part_assess_route_exists(self):
        assert '@router.post("/shared-parts/{shared_part_id}/standardization/assess")' in _ROUTE_SRC

    def test_domain_assess_route_exists(self):
        assert '@router.post("/domains/{domain_key}/standardization/assess")' in _ROUTE_SRC

    def test_routes_require_teacher(self):
        from tests.guardrail_helpers import extract_function_source

        for fn_name in ("assess_shared_part_standardization", "assess_domain_standardization"):
            body = extract_function_source(_ROUTE_SRC, fn_name)
            assert "_require_teacher" in body

    def test_routes_do_not_run_llm_synchronously_but_schedule_a_thread(self):
        from tests.guardrail_helpers import extract_function_source

        body = extract_function_source(_ROUTE_SRC, "assess_shared_part_standardization")
        assert "standardization_worker.schedule_assess_shared_part(" in body

        body = extract_function_source(_ROUTE_SRC, "assess_domain_standardization")
        assert "standardization_worker.schedule_assess_domain(" in body

    def test_note_fields_avoid_raw_digits(self):
        # W8: 件数の生数字を返さない。応答の note 文言に数字を含めないこと。
        from tests.guardrail_helpers import extract_function_source
        import re

        for fn_name in ("assess_shared_part_standardization", "assess_domain_standardization"):
            body = extract_function_source(_ROUTE_SRC, fn_name)
            for match in re.finditer(r'"note":\s*"([^"]*)"', body):
                assert not any(c.isdigit() for c in match.group(1))
