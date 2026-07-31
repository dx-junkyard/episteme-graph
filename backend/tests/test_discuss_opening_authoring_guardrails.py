"""discuss 開幕素材の**生成側**（Phase 1: `discuss_opening` ステージ + 格納庫拡張
migration 062）と**配信側**（Phase 3: `build_opening` が承認済み素材を出す）の
ガードレール。

正本: ``docs/features/discuss_opening_authoring_design.md`` §1（OA1〜OA8）・§4・§5・
§7・§7.1・§8。

検証項目:

1. 生成モジュール（agent / `core/discuss/authoring.py`）が FastAPI を import しない
2. 禁止語彙（D層と同じ denylist）を validator が弾く / プロンプトにも明記されている
3. 生成上限と truncated / skipped の正直な記録（OA8 / P4）
4. migration 062 の role・element_type CHECK とコード側語彙の整合（§5）
5. 永続化が candidate-only（OA2）で `role='discussion_seed'` /
   `element_type='document'` を使い、`source_fingerprint` を evidence に残す（§7.1）
6. `compute_source_fingerprint` の決定論性・入力依存（§7.1）
7. ステージ登録位置（contextual_explanation の後・course_mapping の前）と
   M層 / U層カタログへの登録
8. 行削除 API を作っていない（OA5 / P4）
9. **配信（§7）**: `build_opening` の経路に LLM 呼び出しが無い（OA3）／承認済み以外
   （candidate / dismissed / superseded）が DTO に混ざらない（OA2）／承認済みゼロで
   Phase 0 の DTO と完全一致する（OA4 の回帰検知）／confidence 生値が出ない（OA6）／
   `available` の判定を変えない／配信経路が書き込みを持たない（OA5）

外部サービス（PostgreSQL / LLM）への実接続は行わない。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tests.guardrail_helpers import (  # noqa: E402
    assert_module_tree_does_not_import,
    assert_source_does_not_import,
    assert_source_forbids,
    extract_function_source,
    read_migration_sql,
)

_AGENT_DIR = ROOT / "src" / "episteme_graph" / "agents" / "discuss_opening"
_AUTHORING_SRC = (BACKEND / "core" / "discuss" / "authoring.py").read_text(encoding="utf-8")
_OPENING_SRC = (BACKEND / "core" / "discuss" / "opening.py").read_text(encoding="utf-8")
_DISCUSS_JS = (ROOT / "frontend" / "public" / "js" / "discuss.js").read_text(encoding="utf-8")
_ORCH_SRC = (BACKEND / "core" / "document_pipeline" / "orchestrator.py").read_text(encoding="utf-8")
_ELEMENT_EXPL_SRC = (BACKEND / "core" / "element_explanations.py").read_text(encoding="utf-8")
_MIGRATION_062 = read_migration_sql(BACKEND, 62)
# コメント行を落とした実行 SQL のみ（設計意図の説明文と CHECK の語彙を混同しない）。
_MIGRATION_062_SQL = "\n".join(
    line for line in _MIGRATION_062.splitlines() if not line.lstrip().startswith("--")
)


# ---------------------------------------------------------------------------
# 1. 生成モジュールは FastAPI / routes / services に依存しない
# ---------------------------------------------------------------------------


class TestNoFastapiDependency:
    def test_agent_tree_does_not_import_fastapi(self):
        assert_module_tree_does_not_import(_AGENT_DIR, ["fastapi", "sqlalchemy"])

    def test_authoring_does_not_import_fastapi(self):
        assert_source_does_not_import(
            _AUTHORING_SRC, ["fastapi"], context="core/discuss/authoring.py"
        )

    def test_authoring_does_not_import_routes_or_services(self):
        assert_source_forbids(
            _AUTHORING_SRC,
            ["from routes", "import routes", "from services", "import services"],
            context="core/discuss/authoring.py",
        )

    def test_authoring_is_non_llm(self):
        """設計上、素材の解決と指紋計算に LLM は関与しない（LLM は agent 側 1コールだけ）。"""
        assert_source_forbids(
            _AUTHORING_SRC,
            ["generate_text", "generate_structured", "complete_json", "openai"],
            context="core/discuss/authoring.py",
        )

    def test_agent_delegates_repair_loop_to_llm_worker(self):
        """8系統目のアダプタ: 修復ループ・JSON クライアントをコピー実装しない。"""
        agent_src = (_AGENT_DIR / "agent.py").read_text(encoding="utf-8")
        client_src = (_AGENT_DIR / "llm_client.py").read_text(encoding="utf-8")
        assert "core.llm_worker.repair" in agent_src
        assert "run_with_repair" in agent_src
        assert "core.llm_worker.client" in client_src
        assert "BaseJSONLLMClient" in client_src


# ---------------------------------------------------------------------------
# 2. 禁止語彙（D層と同じ denylist）
# ---------------------------------------------------------------------------

# 正本の並びは backend/tests/test_doubt_guardrails.py::BANNED と一致させる。
_BANNED = ("疑え", "ノーベル賞", "危険地帯", "要注意ゾーン", "崩壊させよ")


class TestForbiddenVocabulary:
    def test_schema_denylist_matches_the_doubt_layer_list(self):
        from episteme_graph.agents.discuss_opening.schema import FORBIDDEN_PHRASES

        assert set(_BANNED) <= set(FORBIDDEN_PHRASES)

    def test_validator_rejects_each_forbidden_phrase(self):
        from episteme_graph.agents.discuss_opening.schema import (
            AssumptionFact,
            DiscussOpeningInput,
        )
        from episteme_graph.agents.discuss_opening.validator import DiscussOpeningValidator

        statement = "The detector response is linear over the full energy range."
        item = DiscussOpeningInput(
            document_id="doc-1",
            untested_assumptions=[AssumptionFact(statement=statement)],
        )
        validator = DiscussOpeningValidator()

        for phrase in _BANNED:
            raw = {
                "seeds": [
                    {
                        "body": f"{phrase}という観点で、どこが変わると考えますか？",
                        "evidence_quote": statement,
                        "reason": "未検証の前提だから",
                        "confidence": 0.5,
                        "grounded_in": "untested_assumption",
                    }
                ]
            }
            result, errors, _warnings = validator.validate(raw, item)
            assert result is None, f"{phrase!r} must not pass validation"
            assert any("forbidden_phrase" in e for e in errors)

    def test_prompt_states_the_denylist(self):
        prompt_src = (_AGENT_DIR / "prompt.py").read_text(encoding="utf-8")
        assert "FORBIDDEN_PHRASES" in prompt_src

    def test_prompt_forbids_fabrication(self):
        from episteme_graph.agents.discuss_opening.prompt import DiscussOpeningPromptFactory
        from episteme_graph.agents.discuss_opening.schema import DiscussOpeningInput

        content = DiscussOpeningPromptFactory().build_content(
            DiscussOpeningInput(document_id="doc-1")
        )
        assert "素材に無いことを書かない" in content
        assert "逐語引用" in content


# ---------------------------------------------------------------------------
# 3. 生成上限と truncated / skipped の正直な記録（OA8 / P4）
# ---------------------------------------------------------------------------


class TestHonestLimits:
    def test_seed_cap_records_truncation(self):
        from episteme_graph.agents.discuss_opening.schema import (
            DiscussOpeningInput,
            DiscussOpeningResult,
            DiscussionSeed,
        )
        from episteme_graph.agents.discuss_opening.agent import DiscussOpeningAgent

        result = DiscussOpeningResult(
            document_id="doc-1",
            seeds=[DiscussionSeed(body=f"問い{i}？") for i in range(5)],
        )
        capped = DiscussOpeningAgent._apply_seed_cap(result, 2)  # noqa: SLF001

        assert len(capped.seeds) == 2
        assert capped.truncated is True
        assert capped.truncated_count == 3
        assert capped.review_notes  # 切ったことを言う

        # 入力型が結果に影響しないこと（cap は結果側の純関数）
        assert DiscussOpeningInput(document_id="doc-1").max_seeds > 0

    def test_no_material_is_recorded_with_a_reason(self):
        from episteme_graph.agents.discuss_opening.schema import (
            DiscussOpeningInput,
            SKIPPED_REASON_NO_MATERIAL,
        )
        from episteme_graph.agents.discuss_opening.agent import DiscussOpeningAgent

        agent = DiscussOpeningAgent()

        class _NeverCalled:
            calls = 0

            def complete_json(self, content):  # pragma: no cover - must not run
                raise AssertionError("LLM must not be called without material")

        agent._llm_client = _NeverCalled()  # noqa: SLF001
        result = agent.run(DiscussOpeningInput(document_id="doc-1"))

        assert result.seeds == []
        assert result.skipped_reason == SKIPPED_REASON_NO_MATERIAL

    def test_stage_payload_records_counts_and_skip_reasons(self):
        stage_src = extract_function_source(_ORCH_SRC, "_build_discuss_opening")
        for key in (
            '"assumption_count"',
            '"author_choice_count"',
            '"llm_calls"',
            '"seed_count"',
            '"saved_candidates"',
            '"truncated"',
            '"truncated_count"',
            '"skipped_reason"',
            '"source_fingerprint"',
        ):
            assert key in stage_src, f"stage payload must record {key}"
        assert "no_source_material" in stage_src
        assert "skipped_by_limit" in stage_src

    def test_stage_uses_env_bounded_limits(self):
        assert "DISCUSS_OPENING_MAX_ITEMS_PER_DOCUMENT" in _ORCH_SRC
        assert "DISCUSS_OPENING_MAX_CALLS_PER_DAY" in _ORCH_SRC
        assert "DISCUSS_OPENING_LANGUAGE" in _ORCH_SRC

    def test_env_example_documents_the_knobs(self):
        env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
        for key in (
            "DISCUSS_OPENING_MAX_ITEMS_PER_DOCUMENT",
            "DISCUSS_OPENING_MAX_CALLS_PER_DAY",
            "DISCUSS_OPENING_LANGUAGE",
            "DISCUSS_OPENING_LLM_MODEL",
        ):
            assert key in env_example


# ---------------------------------------------------------------------------
# 4. migration 062 と コード側語彙の整合（§5）
# ---------------------------------------------------------------------------


class TestMigration062:
    def test_element_type_check_gains_document(self):
        assert "element_explanations_element_type_check" in _MIGRATION_062
        # 既存4語彙 + document
        for vocab in ("figure", "theory_component", "theory_claim", "equation", "document"):
            assert f"'{vocab}'" in _MIGRATION_062

    def test_role_column_and_check(self):
        assert re.search(
            r"ADD COLUMN IF NOT EXISTS role TEXT", _MIGRATION_062
        ), "role column must be added idempotently"
        assert "role IS NULL OR role IN ('discussion_seed')" in _MIGRATION_062

    def test_thesis_restatement_is_not_in_the_role_vocabulary(self):
        """v1 では使わない語彙を格納庫に残さない（設計書 §4.2 末尾の指針）。"""
        assert "thesis_restatement" not in _MIGRATION_062_SQL

        from core.element_explanations import ROLES

        assert "thesis_restatement" not in ROLES

    def test_kind_vocabulary_is_not_extended(self):
        """kind は既存 CHECK のまま contextual を使う（語彙を汚さない・§5-3）。"""
        assert "element_explanations_kind_check" not in _MIGRATION_062_SQL

    def test_code_vocabulary_matches_the_migration(self):
        from core.element_explanations import (
            ELEMENT_TYPE_DOCUMENT,
            ELEMENT_TYPES,
            ROLE_DISCUSSION_SEED,
            ROLES,
        )
        from episteme_graph.agents.discuss_opening.schema import ROLE_DISCUSSION_SEED as AGENT_ROLE

        assert ELEMENT_TYPE_DOCUMENT == "document"
        assert ELEMENT_TYPE_DOCUMENT in ELEMENT_TYPES
        assert ROLES == (ROLE_DISCUSSION_SEED,)
        assert f"'{ROLE_DISCUSSION_SEED}'" in _MIGRATION_062
        # agent 側リテラル（backend/core を import しないため二重に持つ）の一致
        assert AGENT_ROLE == ROLE_DISCUSSION_SEED

    def test_invalid_role_is_rejected_before_reaching_the_db(self):
        from core.element_explanations import _validate_candidate_item

        base = {
            "element_type": "document",
            "element_id": "doc-1",
            "kind": "contextual",
            "body": "問い？",
        }
        assert _validate_candidate_item(base)["role"] is None
        assert (
            _validate_candidate_item({**base, "role": "discussion_seed"})["role"]
            == "discussion_seed"
        )
        try:
            _validate_candidate_item({**base, "role": "thesis_restatement"})
        except ValueError as exc:
            assert "invalid role" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("unknown role must raise ValueError")


# ---------------------------------------------------------------------------
# 5. 永続化は candidate-only（OA2）で role / element_type / fingerprint を使う
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_stage_persists_as_document_scoped_discussion_seed_candidates(self):
        stage_src = extract_function_source(_ORCH_SRC, "_build_discuss_opening")
        assert "ELEMENT_TYPE_DOCUMENT" in stage_src
        assert "ROLE_DISCUSSION_SEED" in stage_src
        assert "KIND_CONTEXTUAL" in stage_src
        assert "insert_candidates" in stage_src
        assert "source_fingerprint" in stage_src

    def test_stage_never_approves(self):
        stage_src = extract_function_source(_ORCH_SRC, "_build_discuss_opening")
        assert_source_forbids(
            stage_src,
            ["approve(", "STATUS_APPROVED", "bulk_transition"],
            context="orchestrator._build_discuss_opening",
        )

    def test_insert_candidates_only_creates_candidate_rows(self):
        insert_src = extract_function_source(_ELEMENT_EXPL_SRC, "insert_candidates")
        assert "status=STATUS_CANDIDATE" in insert_src
        assert "STATUS_APPROVED" not in insert_src

    def test_reanalysis_supersedes_candidates_of_the_same_role_only(self):
        insert_src = extract_function_source(_ELEMENT_EXPL_SRC, "insert_candidates")
        # role を条件に含める（開幕素材と二層説明が互いを巻き込まない）
        assert "role IS NOT DISTINCT FROM :role" in insert_src
        # candidate 以外（approved / dismissed）は触らない
        assert "AND status = :candidate" in insert_src

    def test_multiple_seeds_of_one_document_do_not_supersede_each_other(self):
        """同一キーに複数行を積む使い方（1 document に 2〜3 件）で兄弟を消さない。"""
        from core.element_explanations import insert_candidates

        class _FakeSession:
            def __init__(self):
                self.updates = 0
                self.inserts = 0

            def execute(self, statement, params=None):
                sql = str(statement)
                if sql.strip().upper().startswith("UPDATE"):
                    self.updates += 1
                    return _FakeResult(None)
                self.inserts += 1
                return _FakeResult(
                    (
                        f"id-{self.inserts}",
                        params["document_id"],
                        params["element_type"],
                        params["element_id"],
                        params["kind"],
                        params["body"],
                        {},
                        params["status"],
                        params["created_by"],
                        None,
                        None,
                        None,
                        params["role"],
                    )
                )

        class _FakeResult:
            def __init__(self, row):
                self._row = row

            def fetchone(self):
                return self._row

        session = _FakeSession()
        items = [
            {
                "element_type": "document",
                "element_id": "doc-1",
                "kind": "contextual",
                "role": "discussion_seed",
                "body": f"問い{i}？",
            }
            for i in range(3)
        ]
        created = insert_candidates(session, "doc-1", items)

        assert len(created) == 3
        assert session.inserts == 3
        # supersede はキー単位で1回だけ（3回走ると兄弟行を消してしまう）
        assert session.updates == 1
        assert {row["role"] for row in created} == {"discussion_seed"}

    def test_no_delete_api_for_the_new_role(self):
        assert_source_forbids(
            _ELEMENT_EXPL_SRC + _AUTHORING_SRC,
            ["DELETE FROM element_explanations"],
            context="core (element_explanations / discuss.authoring)",
        )


# ---------------------------------------------------------------------------
# 6. source_fingerprint（§7.1）
# ---------------------------------------------------------------------------


def _artifacts(**thesis) -> dict:
    base = {
        "central_question": "Can a single relation describe the ratios?",
        "central_thesis": {"text": "A single consistency relation links the ratios.",
                           "claim_ids": ["c1", "c2"]},
        "support_structure": {"sections": [{"entries": [{"claim_ids": ["c3"]}]}]},
        "supporting_subclaim_ids": ["c2", "c4"],
    }
    base.update(thesis)
    return {"thesis_reconstruction": base}


class TestSourceFingerprint:
    def test_is_deterministic_and_hex_sha256(self):
        from core.discuss.authoring import compute_source_fingerprint

        first = compute_source_fingerprint(_artifacts())
        second = compute_source_fingerprint(_artifacts())
        assert first == second
        assert re.fullmatch(r"[0-9a-f]{64}", first)

    def test_claim_id_order_does_not_change_the_fingerprint(self):
        from core.discuss.authoring import compute_source_fingerprint

        reordered = _artifacts(
            central_thesis={"text": "A single consistency relation links the ratios.",
                            "claim_ids": ["c2", "c1"]},
        )
        assert compute_source_fingerprint(_artifacts()) == compute_source_fingerprint(reordered)

    def test_changing_the_thesis_changes_the_fingerprint(self):
        from core.discuss.authoring import compute_source_fingerprint

        changed = _artifacts(
            central_thesis={"text": "A different thesis.", "claim_ids": ["c1", "c2"]}
        )
        assert compute_source_fingerprint(_artifacts()) != compute_source_fingerprint(changed)

    def test_changing_the_question_changes_the_fingerprint(self):
        from core.discuss.authoring import compute_source_fingerprint

        changed = _artifacts(central_question="A different question?")
        assert compute_source_fingerprint(_artifacts()) != compute_source_fingerprint(changed)

    def test_adding_a_claim_changes_the_fingerprint(self):
        from core.discuss.authoring import compute_source_fingerprint

        changed = _artifacts(supporting_subclaim_ids=["c2", "c4", "c5"])
        assert compute_source_fingerprint(_artifacts()) != compute_source_fingerprint(changed)

    def test_empty_artifacts_are_not_an_error(self):
        from core.discuss.authoring import compute_source_fingerprint

        assert re.fullmatch(r"[0-9a-f]{64}", compute_source_fingerprint({}))
        assert compute_source_fingerprint({}) == compute_source_fingerprint(None)

    def test_claim_ids_are_collected_from_every_nesting_level(self):
        from core.discuss.authoring import collect_thesis_claim_ids

        assert collect_thesis_claim_ids(_artifacts()) == ["c1", "c2", "c3", "c4"]


# ---------------------------------------------------------------------------
# 7. ステージ登録位置と M層 / U層カタログ
# ---------------------------------------------------------------------------


class TestStageRegistration:
    def test_registered_between_contextual_explanation_and_course_mapping(self):
        from core.document_pipeline.orchestrator import PIPELINE_STAGES

        assert "discuss_opening" in PIPELINE_STAGES
        ce = PIPELINE_STAGES.index("contextual_explanation")
        do = PIPELINE_STAGES.index("discuss_opening")
        cm = PIPELINE_STAGES.index("course_mapping")
        assert ce < do < cm

    def test_pipeline_steps_has_a_step_in_the_same_position(self):
        from core.document_pipeline.orchestrator import _PIPELINE_STEPS

        names = [step.name for step in _PIPELINE_STEPS if step.name is not None]
        assert names.index("contextual_explanation") < names.index("discuss_opening")
        assert names.index("discuss_opening") < names.index("course_mapping")

    def test_registered_as_an_llm_stage(self):
        from core.document_pipeline.orchestrator import LLM_STAGE_NAMES

        assert "discuss_opening" in LLM_STAGE_NAMES

    def test_m_layer_label_exists(self):
        from core.llm_policy import PIPELINE_STAGE_LABELS

        label = PIPELINE_STAGE_LABELS.get("discuss_opening")
        assert label and label != "discuss_opening"

    def test_m_layer_env_mapping_is_fast_tier(self):
        from core import llm_policy

        env_name, tier = llm_policy._FEATURE_DIRECT_ENV["pipeline:discuss_opening"]  # noqa: SLF001
        assert env_name == "DISCUSS_OPENING_LLM_MODEL"
        assert tier == "fast"

    def test_u_layer_feature_is_registered(self):
        from core.llm_usage.schema import KNOWN_FEATURES

        assert "pipeline:discuss_opening" in KNOWN_FEATURES

    def test_scene_resolves_to_the_pipeline_scene(self):
        from core.llm_policy import SCENE_PIPELINE, scene_for_feature

        assert scene_for_feature("pipeline:discuss_opening") == SCENE_PIPELINE

    def test_stage_failure_is_non_fatal(self):
        stage_src = extract_function_source(_ORCH_SRC, "_stage_discuss_opening")
        assert "except Exception" in stage_src
        assert "PipelineStageError" not in stage_src

    def test_stage_reuses_artifact_on_resume(self):
        stage_src = extract_function_source(_ORCH_SRC, "_stage_discuss_opening")
        assert 'should_use_artifact("discuss_opening")' in stage_src
        assert 'save_artifact("discuss_opening"' in stage_src


# ---------------------------------------------------------------------------
# 8. 配信（Phase 3・§7）: 承認済みだけ / 非LLM / Phase 0 を劣化させない
# ---------------------------------------------------------------------------


def _seed_row(**overrides) -> dict:
    """``element_explanations`` の1行（``_row_to_dict`` と同じ形）。"""
    row = {
        "id": "row-1",
        "document_id": "doc-1",
        "element_type": "document",
        "element_id": "doc-1",
        "kind": "contextual",
        "role": "discussion_seed",
        "body": "この前提を外すと、結論はどこまで残ると考えますか？",
        "evidence": {
            "evidence_quote": "The detector response is linear over the band.",
            "reason": "未検証の前提だから",
            "confidence": 0.62,
            "grounded_in": "untested_assumption",
            "language": "ja",
            "source_fingerprint": "f" * 64,
            "opening_version": "v1",
        },
        "status": "approved",
        "created_by": "pipeline",
        "reviewed_by": "teacher-1",
        "reviewed_at": "2026-07-30T00:00:00+00:00",
        "created_at": "2026-07-30T00:00:00+00:00",
    }
    row.update(overrides)
    return row


def _patch_opening_reads(monkeypatch, opening, *, seeds_by_document=None, thesis=None):
    """``build_opening`` の DB 読み出しを全て無効化する（実 DB に触らない）。"""
    seeds_by_document = seeds_by_document or {}
    monkeypatch.setattr(opening, "_document_titles", lambda ids: {})
    monkeypatch.setattr(
        opening, "document_run_artifacts",
        lambda document_id: {"thesis_reconstruction": thesis} if thesis else {},
    )
    monkeypatch.setattr(opening, "_equations_index_for", lambda document_id, artifacts=None: {})
    monkeypatch.setattr(opening, "_load_graph_nodes", lambda document_id: [])
    monkeypatch.setattr(opening, "_claim_label_index", lambda document_id, artifacts: {})
    monkeypatch.setattr(opening, "_compile_open_assumptions", lambda course_id: [])
    monkeypatch.setattr(
        opening, "_load_approved_discussion_seeds",
        lambda document_id: list(seeds_by_document.get(document_id) or []),
    )


class TestDeliveryIsNonLlm:
    """OA3: `GET /discuss/opening` は従来どおり LLM 0 回の読み出しのみ。"""

    def test_opening_module_has_no_llm_call(self):
        assert_source_forbids(
            _OPENING_SRC,
            [
                "core.llm",
                "generate_text",
                "generate_structured",
                "generate_conversation_turn",
                "complete_json",
                "run_with_repair",
                "openai",
            ],
            context="core/discuss/opening.py",
        )

    def test_opening_module_does_not_import_the_generation_agent(self):
        """配信は格納庫を読むだけ（生成 agent を同期パスから呼ばない）。"""
        assert_source_forbids(
            _OPENING_SRC,
            ["episteme_graph.agents", "DiscussOpeningAgent"],
            context="core/discuss/opening.py",
        )


class TestDeliveryApprovedOnly:
    """OA2: 承認済み以外（candidate / dismissed / superseded）を学習者に出さない。"""

    def test_only_approved_rows_are_projected(self):
        from core.discuss import opening

        rows = [
            _seed_row(id="a", status="candidate", body="candidate の問い？"),
            _seed_row(id="b", status="dismissed", body="dismissed の問い？"),
            _seed_row(id="c", status="superseded", body="superseded の問い？"),
            _seed_row(id="d", status="approved", body="approved の問い？"),
        ]
        projected = opening.project_discussion_seeds(rows)

        assert [s["body"] for s in projected] == ["approved の問い？"]

    def test_other_roles_and_element_types_are_not_projected(self):
        """二層説明の説明本文（role IS NULL）や他要素の行が開幕画面に混ざらない。"""
        from core.discuss import opening

        rows = [
            _seed_row(id="a", role=None),
            _seed_row(id="b", role="other_role"),
            _seed_row(id="c", element_type="figure"),
            _seed_row(id="d"),
        ]
        assert len(opening.project_discussion_seeds(rows)) == 1

    def test_read_query_filters_on_approved_status_and_role(self):
        """SQL 側でも approved / role / element_type に絞る（二重の関門）。"""
        fn_src = extract_function_source(_OPENING_SRC, "_load_approved_discussion_seeds")
        assert "status=STATUS_APPROVED" in fn_src
        assert "role=ROLE_DISCUSSION_SEED" in fn_src
        assert "element_type=ELEMENT_TYPE_DOCUMENT" in fn_src
        # 状態語彙はコード側正本（core/element_explanations.py）から取る。
        assert 'status="approved"' not in fn_src

    def test_status_vocabulary_comes_from_the_store_module(self):
        from core import element_explanations
        from core.discuss import opening

        assert opening.STATUS_APPROVED is element_explanations.STATUS_APPROVED
        assert opening.ROLE_DISCUSSION_SEED is element_explanations.ROLE_DISCUSSION_SEED
        assert opening.ELEMENT_TYPE_DOCUMENT is element_explanations.ELEMENT_TYPE_DOCUMENT

    def test_delivery_path_never_writes(self):
        """配信は読み取り専用（承認・却下・削除・投入の経路を持たない・OA5）。"""
        assert_source_forbids(
            _OPENING_SRC,
            [
                "DELETE FROM",
                "INSERT INTO",
                "UPDATE element_explanations",
                "insert_candidates",
                "bulk_transition",
                "STATUS_DISMISSED",
                "session.commit",
            ],
            context="core/discuss/opening.py",
        )


class TestDeliveryDoesNotDegradePhase0:
    """OA4: 承認済みが無くても画面を殺さない・Phase 0 の DTO を変えない。"""

    _THESIS = {
        "central_question": "How does the sum rule constrain the spectrum?",
        "central_thesis": {"text": "The sum rule predicts the spectrum.", "claim_ids": ["c1"]},
    }

    def test_dto_identical_to_phase0_without_approved_material(self, monkeypatch):
        from core.discuss import opening

        _patch_opening_reads(monkeypatch, opening, thesis=self._THESIS)
        baseline = opening.build_opening("course1", ["doc-1"])

        # candidate しか無い document でも Phase 0 と完全一致（キーすら増えない）。
        _patch_opening_reads(
            monkeypatch, opening, thesis=self._THESIS,
            seeds_by_document={"doc-1": [_seed_row(status="candidate")]},
        )
        with_candidates = opening.build_opening("course1", ["doc-1"])

        assert with_candidates == baseline
        assert "discussion_seeds" not in baseline["documents"][0]

    def test_approved_material_is_added_without_removing_projection(self, monkeypatch):
        from core.discuss import opening

        _patch_opening_reads(monkeypatch, opening, thesis=self._THESIS)
        baseline = opening.build_opening("course1", ["doc-1"])

        _patch_opening_reads(
            monkeypatch, opening, thesis=self._THESIS,
            seeds_by_document={"doc-1": [_seed_row()]},
        )
        served = opening.build_opening("course1", ["doc-1"])

        doc = served["documents"][0]
        assert doc["discussion_seeds"][0]["body"] == _seed_row()["body"]
        # 投影（Phase 0 の中身）はそのまま残る = role 単位の部分適用。
        assert doc["thesis"] == baseline["documents"][0]["thesis"]
        assert doc["backbone"] == baseline["documents"][0]["backbone"]

    def test_available_is_not_changed_by_approved_material(self, monkeypatch):
        """§7: `available` の判定は変えない（承認で画面が突然出現しない）。"""
        from core.discuss import opening

        _patch_opening_reads(
            monkeypatch, opening,
            seeds_by_document={"doc-1": [_seed_row()]},
        )
        served = opening.build_opening("course1", ["doc-1"])

        assert served["available"] is False
        assert served["documents"][0]["discussion_seeds"]

    def test_read_failure_degrades_to_projection(self, monkeypatch):
        """1 document の読み出し失敗で画面全体を壊さない（fail-soft）。"""
        from core.discuss import opening

        def _boom(session, document_id, **kwargs):
            raise RuntimeError("db down")

        # 実装本体（fail-soft の対象）を差し替え前に捕まえておく。
        real_loader = opening._load_approved_discussion_seeds  # noqa: SLF001

        _patch_opening_reads(monkeypatch, opening, thesis=self._THESIS)
        baseline = opening.build_opening("course1", ["doc-1"])

        # 本体を戻したうえで、その中で使う読み出しだけを失敗させる。
        monkeypatch.setattr(opening, "_load_approved_discussion_seeds", real_loader)
        monkeypatch.setattr(opening, "list_for_document", _boom)
        served = opening.build_opening("course1", ["doc-1"])

        assert served == baseline
        assert "discussion_seeds" not in served["documents"][0]


class TestDeliveryFreshness:
    """§7.1: 再解析で解析結果が変わっても、承認済み素材を配信側で黙って落とさない。

    鮮度不一致は**レビューキュー**に事実文で並べるのが担当（=教員が判断する）。
    配信が自動で非承認扱いにすると、学習者に出ていたものが黙って消える。
    """

    def test_stale_fingerprint_is_still_delivered(self, monkeypatch):
        from core.discuss import opening

        _patch_opening_reads(
            monkeypatch, opening,
            thesis={"central_thesis": {"text": "A new thesis after re-analysis."}},
            seeds_by_document={"doc-1": [_seed_row(evidence={
                "evidence_quote": "quoted from the paper",
                "source_fingerprint": "0" * 64,  # 現在の解析結果とは不一致
            })]},
        )
        served = opening.build_opening("course1", ["doc-1"])

        assert len(served["documents"][0]["discussion_seeds"]) == 1

    def test_delivery_does_not_recompute_the_fingerprint(self):
        """配信は指紋の突合をしない（生成・レビュー側の責務）。"""
        assert_source_forbids(
            _OPENING_SRC,
            ["compute_source_fingerprint", "source_fingerprint"],
            context="core/discuss/opening.py",
        )


class TestDeliveryHidesRawNumbers:
    """OA6: confidence 等の生数値を配信しない（段階ラベルすら開幕画面には出さない）。"""

    def test_evidence_projection_is_a_whitelist(self):
        from core.discuss import opening

        seed = opening.project_discussion_seeds([_seed_row()])[0]
        assert set(seed.keys()) == {"body", "evidence_quote", "authored", "authored_by_label"}
        assert seed["evidence_quote"] == "The detector response is linear over the band."

    def test_no_numeric_keys_in_the_full_dto(self, monkeypatch):
        from core.discuss import opening

        _patch_opening_reads(
            monkeypatch, opening,
            seeds_by_document={"doc-1": [_seed_row()]},
        )
        served = opening.build_opening("course1", ["doc-1"])

        def _walk(node):
            if isinstance(node, dict):
                for key in ("confidence", "load_score", "score"):
                    assert key not in node, node
                for value in node.values():
                    _walk(value)
            elif isinstance(node, list):
                for value in node:
                    _walk(value)

        _walk(served)
        # reason / source_fingerprint 等の内部フィールドも学習者へは出さない。
        assert "source_fingerprint" not in repr(served)
        assert "grounded_in" not in repr(served)


class TestDeliveryAttribution:
    """§7 出所表示: 承認済み素材を含む区画にだけ署名を付ける。"""

    def test_label_is_the_designed_sentence_and_server_side(self):
        from core.discuss import opening

        seed = opening.project_discussion_seeds([_seed_row()])[0]
        assert seed["authored"] is True
        assert seed["authored_by_label"] == (
            "この説明は、論文の解析結果をもとに担当教員が確認したものです。"
        )

    def test_projection_only_sections_have_no_signature(self, monkeypatch):
        """投影のみの区画（別の見方 等）に教員の署名が付かない。"""
        from core.discuss import opening

        thesis = dict(TestDeliveryDoesNotDegradePhase0._THESIS)
        thesis["alternative_theses"] = [{"text": "An alternative formulation."}]
        _patch_opening_reads(monkeypatch, opening, thesis=thesis)
        served = opening.build_opening("course1", ["doc-1"])

        assert "担当教員" not in repr(served)
        alt = served["documents"][0]["thesis"]["alternatives"][0]
        assert "AI" in alt["attribution_label"]

    def test_front_end_renders_the_server_label_verbatim(self):
        """フロントで署名文を組み立てない（出所表示の正本はサーバ側の1定数）。"""
        assert "renderDiscussionSeedsSection(doc)" in _DISCUSS_JS
        assert "s.authored_by_label" in _DISCUSS_JS
        assert "この説明は、論文の解析結果をもとに担当教員が確認したものです" not in _DISCUSS_JS
