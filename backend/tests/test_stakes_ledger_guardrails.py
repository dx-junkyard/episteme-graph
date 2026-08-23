"""SL層（賭け金の台帳, Stakes Ledger）ガードレール — core スコープ分（§12）。

正本: docs/features/stakes_ledger_design.md §1（SL1〜SL10）/ §12。

設計書 §12 の着手条件「ガードレールテストを先に書いてから実装する」に従い、
このファイルは core（backend/core/doubt/support_paths.py /
backend/core/doubt/observation_targets.py /
backend/core/doubt/falsification_conditions/）スコープの検証を先に定義したもの。

route スコープ分（backend/api/routes/doubt.py への SL-1/SL-4 API 追加、
core/discuss/opening.py への結線、frontend/ の UI）は別エージェントが本ファイルへ
追記する想定（§12 項目6 の proposal 昇格422検査・項目9 の監査 action 語彙検査・
項目10 の ``_strip_numeric_keys`` 検査は route/opening 側のコードが存在しないため
このファイルでは扱わない）。クラス名は本ファイルのものと衝突しないよう
``...Core`` サフィックスで棲み分けている。
"""

from __future__ import annotations

import re
from pathlib import Path

from core import schema as core_schema
from core.doubt.falsification_conditions.schema import FalsificationTargetContext, SourceBlock
from core.doubt.falsification_conditions.validator import validate_output as fc_validate_output
from core.doubt.schema import (
    FALSIFICATION_KIND_LABELS,
    FALSIFICATION_KINDS,
    HUMAN_ONLY_FALSIFICATION_FIELDS,
    REACHABILITY_LABELS,
    REACHABILITY_LEVELS,
    SUPPORT_LINE_LEVELS,
    FalsificationCandidate,
    FalsificationCondition,
)
from tests.guardrail_helpers import (
    assert_module_tree_does_not_import,
    assert_module_tree_forbids,
    assert_paths_forbid,
    assert_source_does_not_import,
    assert_source_forbids,
    extract_function_source,
    read_migration_sql,
)

_BACKEND = Path(__file__).resolve().parents[1]
_REPO = _BACKEND.parent
_DOUBT_DIR = _BACKEND / "core" / "doubt"
_FALSIFICATION_DIR = _DOUBT_DIR / "falsification_conditions"
_SUPPORT_PATHS_SRC = (_DOUBT_DIR / "support_paths.py").read_text(encoding="utf-8")
_OBSERVATION_TARGETS_SRC = (_DOUBT_DIR / "observation_targets.py").read_text(encoding="utf-8")
_ROUTES_DOUBT = _BACKEND / "api" / "routes" / "doubt.py"
_OPEN_ASSUMPTIONS = _DOUBT_DIR / "open_assumptions.py"
_DOUBT_ATLAS_JS = _REPO / "frontend" / "public" / "js" / "doubt-atlas.js"
_MIGRATION_067_SRC = read_migration_sql(_BACKEND, 67)

# route スコープ（第2波）で追記された対象ソース。
_ROUTES_DOUBT_SRC = _ROUTES_DOUBT.read_text(encoding="utf-8")
_OPEN_ASSUMPTIONS_SRC = _OPEN_ASSUMPTIONS.read_text(encoding="utf-8")
_OPENING_MODULE = _BACKEND / "core" / "discuss" / "opening.py"
_OPENING_SRC = _OPENING_MODULE.read_text(encoding="utf-8")
_METRICS_MODULE = _DOUBT_DIR / "metrics.py"
_METRICS_SRC = _METRICS_MODULE.read_text(encoding="utf-8")

_BIND_DIRECT_CAST = re.compile(r":[a-zA-Z_][a-zA-Z0-9_]*::[a-zA-Z_]")


# ===========================================================================
# 1. SL1 閉世界語彙の固定
# ===========================================================================


class TestClosedWorldVocabulary:
    """SL1: 分野レベルの不在言明を禁止し、コーパス内不在の固定文言だけを許可する。

    設計書 §12-1 の対象ソース群を全て検査する（doubt.py / open_assumptions.py は
    route/opening スコープ側の追記者がまだ触っていないため現状は無関係語彙のみだが、
    将来 SL 語彙を書き込んだときに同じテストで検出されるよう最初から対象に含める）。
    """

    BANNED = ("この分野では未検証", "誰も検証していない", "世界初", "未踏")

    def _assets(self) -> list[Path]:
        return [
            _DOUBT_DIR / "support_paths.py",
            _DOUBT_DIR / "observation_targets.py",
            # ゼミ前ブリーフ（seminar_brief_mirroring_design.md §1）: 晴れ間の閉世界固定文を
            # 新設したモジュール。SL1 denylist の検査対象に最初から含める。
            _DOUBT_DIR / "seminar_brief.py",
            _ROUTES_DOUBT,
            _OPEN_ASSUMPTIONS,
            _DOUBT_ATLAS_JS,
        ]

    def test_no_banned_closed_world_phrases(self):
        assert_paths_forbid(self._assets(), self.BANNED)
        assert_module_tree_forbids(_FALSIFICATION_DIR, self.BANNED)

    def test_fixed_closed_world_phrase_present_in_support_paths(self):
        """「このコーパスの中では」の原文存在（肯定形の固定文言）。"""
        assert "このコーパスの中では" in _SUPPORT_PATHS_SRC


# ===========================================================================
# 2. worker の書き込み分離（AIに疑わせない, SL2）
# ===========================================================================


class TestFalsificationWorkerWriteSeparationCore:
    """falsification worker は falsification_candidates 列にのみ append する。

    falsification_conditions（確定列）・verification_status・verification_scopes・
    reachability には一切書き込まない（D層契約2〜4と同型）。
    """

    def _worker_src(self) -> str:
        return (_FALSIFICATION_DIR / "worker.py").read_text(encoding="utf-8")

    def test_worker_never_writes_confirmed_or_verification_columns(self):
        src = self._worker_src()
        writes = re.findall(r"SET\s+([\s\S]+?)WHERE", src)
        assert writes, "expected at least one SET ... WHERE block in worker.py"
        for write in writes:
            for forbidden in (
                "falsification_conditions",
                "verification_status",
                "verification_scopes",
                "reachability",
            ):
                assert forbidden not in write, (
                    f"falsification worker must never touch {forbidden!r} "
                    f"(found in SET block: {write!r})"
                )

    def test_worker_only_appends_to_candidates_column(self):
        src = self._worker_src()
        assert "falsification_candidates = falsification_candidates ||" in src


# ===========================================================================
# 3. SL3 人間専用語彙（reachability・not_formulable は人間の記帳専用）
# ===========================================================================


class TestHumanOnlyVocabulary:
    def test_falsification_candidate_has_no_reachability_field(self):
        assert "reachability" not in FalsificationCandidate.model_fields

    def test_falsification_candidate_kinds_exclude_not_formulable_by_default(self):
        assert FalsificationCandidate().kind == ""

    def test_falsification_condition_has_reachability_field(self):
        """確定側（人間専用の記帳先）は reachability を持つ（双対の非対称）。"""
        assert "reachability" in FalsificationCondition.model_fields
        assert FalsificationCondition().reachability == "unassessed"

    def test_reachability_is_human_only_declared(self):
        assert HUMAN_ONLY_FALSIFICATION_FIELDS == ("reachability",)

    def test_validator_strips_reachability_field_from_candidate(self):
        context = FalsificationTargetContext(
            target_id="c1", target_type="claim",
            source_blocks=[],
        )
        context.source_blocks = [
            SourceBlock("S1", "claim 本文", "測定値が閾値を超えれば覆る")
        ]
        data = {
            "candidates": [
                {
                    "statement": "測定値が閾値を超えれば覆る",
                    "kind": "observation_value",
                    "reachability": "reachable",  # 混入
                    "evidence_quote": "測定値が閾値を超えれば覆る",
                    "reason": "出典に明記",
                    "confidence": 0.5,
                }
            ]
        }
        result, errors, warnings = fc_validate_output(data, context)
        assert errors == []
        assert result is not None
        assert len(result.candidates) == 1
        assert "reachability" not in result.candidates[0].model_dump()
        assert any("reachability" in w for w in warnings)

    def test_validator_drops_not_formulable_candidate_with_warning(self):
        context = FalsificationTargetContext(target_id="c1", target_type="claim")
        context.source_blocks = [SourceBlock("S1", "claim 本文", "これは検証不能だと述べられている")]
        data = {
            "candidates": [
                {
                    "statement": "反証条件は定式化できない",
                    "kind": "not_formulable",
                    "evidence_quote": "これは検証不能だと述べられている",
                    "reason": "出典に明記",
                    "confidence": 0.5,
                }
            ]
        }
        result, errors, warnings = fc_validate_output(data, context)
        assert errors == []
        assert result is not None
        assert result.candidates == []
        assert any("not_formulable" in w for w in warnings)

    def test_ledger_builder_source_never_mentions_falsification(self):
        """builder（非LLM バックフィル）は反証条件を一切生成しない（SL2 継承）。"""
        src = (_DOUBT_DIR / "ledger_builder.py").read_text(encoding="utf-8")
        assert "falsification" not in src


# ===========================================================================
# 4. SL4 数値非表示（支持経路の本数・カットのサイズを出さない）
# ===========================================================================


class TestNoRawNumbersInSupportPaths:
    def test_support_line_levels_are_three_valued(self):
        assert SUPPORT_LINE_LEVELS == ("none", "single", "several")

    def test_support_paths_module_has_no_count_style_keys(self):
        assert_source_forbids(
            _SUPPORT_PATHS_SRC,
            ['"count"', '"path_count"', "'count'", "'path_count'"],
            context="core/doubt/support_paths.py",
        )

    def test_compute_support_lines_return_keys_are_dto_contract_only(self):
        """公開関数の戻り値の辞書リテラルに数値キーが混入していないことを、
        戻り値組み立て箇所（return {...}）のソース断片で検査する。
        """
        for match in re.finditer(r"return\s*\{[\s\S]*?\}\s*\n", _SUPPORT_PATHS_SRC):
            block = match.group(0)
            if '"level"' not in block:
                continue
            for forbidden in ('"count"', '"flow"', '"score"', '"path_count"'):
                assert forbidden not in block, f"forbidden numeric-ish key in {block!r}"


# ===========================================================================
# 5. P4 / DELETE FROM 禁止（配置による自動担保の明示確認）
# ===========================================================================


class TestNothingDeletedCore:
    def test_no_delete_statements_in_new_sl_modules(self):
        assert_source_forbids(_SUPPORT_PATHS_SRC, ["DELETE FROM"], context="support_paths.py")
        assert_source_forbids(
            _OBSERVATION_TARGETS_SRC, ["DELETE FROM"], context="observation_targets.py"
        )
        assert_module_tree_forbids(_FALSIFICATION_DIR, ["DELETE FROM"])

    def test_doubt_layer_wide_delete_guard_still_covers_new_modules(self):
        """core/doubt/ 配下ツリー全体の DELETE FROM 禁止（既存ガードレールと同型）。

        新モジュールを core/doubt/ 配下に置いたこと自体が SL5 の担保になっている
        ことを明示的に再確認する（配置がずれたら検出する）。
        """
        assert_module_tree_forbids(_DOUBT_DIR, ["DELETE FROM"])


# ===========================================================================
# 6. SL9 非LLM決定論モジュールの純度（fastapi・LLM・networkx 非 import）
# ===========================================================================


class TestNonLLMModulePurity:
    def test_support_paths_does_not_import_fastapi_or_networkx_or_llm(self):
        assert_source_does_not_import(
            _SUPPORT_PATHS_SRC,
            ["fastapi", "networkx", "core.llm"],
            context="core/doubt/support_paths.py",
        )

    def test_observation_targets_does_not_import_fastapi_or_networkx_or_llm(self):
        assert_source_does_not_import(
            _OBSERVATION_TARGETS_SRC,
            ["fastapi", "networkx", "core.llm"],
            context="core/doubt/observation_targets.py",
        )

    def test_falsification_conditions_package_does_not_import_fastapi_or_networkx(self):
        assert_module_tree_does_not_import(_FALSIFICATION_DIR, ["fastapi", "networkx"])


# ===========================================================================
# 7. bind-cast アンチパターン（`:name::type`）
# ===========================================================================


class TestNoBrokenBindParamCastSL:
    def test_support_paths_has_no_bind_param_direct_cast(self):
        match = _BIND_DIRECT_CAST.search(_SUPPORT_PATHS_SRC)
        assert match is None, (
            f"found `:name::type` bind-cast anti-pattern: {match.group(0)!r}"
            if match else ""
        )

    def test_observation_targets_has_no_bind_param_direct_cast(self):
        match = _BIND_DIRECT_CAST.search(_OBSERVATION_TARGETS_SRC)
        assert match is None, (
            f"found `:name::type` bind-cast anti-pattern: {match.group(0)!r}"
            if match else ""
        )

    def test_falsification_conditions_package_has_no_bind_param_direct_cast(self):
        offenders = []
        for path in sorted(_FALSIFICATION_DIR.rglob("*.py")):
            src = path.read_text(encoding="utf-8")
            match = _BIND_DIRECT_CAST.search(src)
            if match:
                offenders.append(f"{path.name}: {match.group(0)!r}")
        assert offenders == [], offenders


# ===========================================================================
# 8. migration 067 の存在・冪等性（構造検査。全体の冪等性 lint は
#    test_migrations_runner.py::TestIdempotencyLint が既にカバーするが、
#    SL層専用の内容確認としてここでも明示する）
# ===========================================================================


class TestMigration067:
    def test_migration_exists_and_targets_expected_tables(self):
        assert "epistemic_ledger" in _MIGRATION_067_SRC
        assert "verification_proposals" in _MIGRATION_067_SRC
        assert "counterfactual_sessions" in _MIGRATION_067_SRC

    def test_migration_adds_falsification_columns(self):
        assert "falsification_conditions" in _MIGRATION_067_SRC
        assert "falsification_candidates" in _MIGRATION_067_SRC
        assert "falsification_analyzed_at" in _MIGRATION_067_SRC

    def test_migration_adds_reachability_and_external_check(self):
        assert "reachability" in _MIGRATION_067_SRC
        assert "external_check" in _MIGRATION_067_SRC

    def test_migration_adds_toggled_observations(self):
        assert "toggled_observations" in _MIGRATION_067_SRC

    def test_migration_is_only_idempotent_ddl(self):
        """このファイル自体は ADD COLUMN IF NOT EXISTS / CREATE INDEX IF NOT EXISTS /
        コメントのみで構成される（CREATE TABLE・DROP は使わない — 既存テーブルへの
        列追加のみ）。全体の冪等性 lint は test_migrations_runner.py が担保するので、
        ここでは SL 固有の構成を明示的に固定する。
        """
        lines = [
            line.strip() for line in _MIGRATION_067_SRC.splitlines()
            if line.strip() and not line.strip().startswith("--")
        ]
        statement = " ".join(lines)
        assert "CREATE TABLE" not in statement
        assert "DROP " not in statement
        # 実際の DDL 文の先頭トークンはすべて ALTER TABLE か CREATE INDEX
        for stmt in [s.strip() for s in statement.split(";") if s.strip()]:
            assert stmt.startswith("ALTER TABLE") or stmt.startswith("CREATE INDEX"), stmt


# ===========================================================================
# 9. 監査語彙（AUDIT_ENTITY_TYPES 非拡張。route スコープの action 語彙検査は
#    第2波が追記する）
# ===========================================================================


class TestAuditEntityNotExtendedForSL:
    """SL-1 は新 entity_type を作らず AUDIT_ENTITY_LEDGER を流用する（設計書 §3.4）。

    ここでは core/schema.py のカタログに SL 専用の新語彙が増えていないことだけを
    固定する（監査 action 語彙自体 — metadata.action の falsification_* 4種 — の
    検査は routes/doubt.py の実装を要するため第2波が追記する）。
    """

    def test_no_new_falsification_entity_type_added(self):
        for entity_type in core_schema.AUDIT_ENTITY_TYPES:
            assert "falsification" not in entity_type
            assert "stakes" not in entity_type

    def test_ledger_entity_type_still_present(self):
        assert core_schema.AUDIT_ENTITY_LEDGER in core_schema.AUDIT_ENTITY_TYPES


# ===========================================================================
# 参考: SL 語彙の日本語ラベルが揃っていること（フロント・API 双方に必要になる
# 語彙の正本が core/doubt/schema.py にあることの確認。§2-10）
# ===========================================================================


class TestSLLabelVocabularyIsComplete:
    def test_falsification_kind_labels_cover_all_kinds(self):
        assert set(FALSIFICATION_KIND_LABELS.keys()) == set(FALSIFICATION_KINDS)

    def test_reachability_labels_cover_all_levels(self):
        assert set(REACHABILITY_LABELS.keys()) == set(REACHABILITY_LEVELS)


# ===========================================================================
# route スコープ（第2波）— backend/api/routes/doubt.py /
# backend/core/discuss/opening.py / backend/core/doubt/metrics.py の追加分。
# §12 の残り項目（6・9・10）+ SL8 の検証を、route/opening 側のコードが実在する
# ようになったこの時点で追記する（§12 冒頭の注記どおり）。
# ===========================================================================


class TestClosedWorldVocabularyRouteScope:
    """SL1: doubt.py / open_assumptions.py の新規追加分にも禁止語彙が無いことの
    実確認（§12-1 で「将来 SL 語彙を書き込んだときに同じテストで検出される」と
    予告していた対象に、実際に SL 語彙が書き込まれた後の再確認）。
    """

    def test_no_banned_phrases_in_routes_doubt(self):
        assert_source_forbids(
            _ROUTES_DOUBT_SRC, TestClosedWorldVocabulary.BANNED, context="routes/doubt.py"
        )

    def test_no_banned_phrases_in_open_assumptions(self):
        assert_source_forbids(
            _OPEN_ASSUMPTIONS_SRC, TestClosedWorldVocabulary.BANNED, context="open_assumptions.py"
        )

    def test_no_banned_phrases_in_opening_module(self):
        assert_source_forbids(
            _OPENING_SRC, TestClosedWorldVocabulary.BANNED, context="core/discuss/opening.py"
        )


class TestProposalExternalCheckRequired:
    """SL8: challenge → proposal 昇格に external_check 必須の 422 が存在する。"""

    def test_external_check_422_present(self):
        fn_src = extract_function_source(_ROUTES_DOUBT_SRC, "create_verification_proposal")
        assert "external_check" in fn_src
        assert "status_code=422" in fn_src
        assert "コーパス外の文献確認の記録が必要です" in fn_src

    def test_withdrawn_challenge_promotion_is_rejected(self):
        """取り下げ済みの疑義からの昇格を 422 に是正する（§6.2 の記帳整合性の是正）。"""
        fn_src = extract_function_source(_ROUTES_DOUBT_SRC, "create_verification_proposal")
        assert "WITHDRAWN" in fn_src
        assert "取り下げ済みの疑義から検証提案へ昇格することはできません" in fn_src

    def test_existing_proposal_text_requirement_preserved(self):
        """既存契約（proposal 本文も必須）を壊さない。"""
        fn_src = extract_function_source(_ROUTES_DOUBT_SRC, "create_verification_proposal")
        assert 'if not body.proposal.strip():' in fn_src
        assert "proposal が必要です" in fn_src


class TestProposalPatchTransitionVocabulary:
    """proposal PATCH の遷移語彙検査（新設エンドポイント + 不正遷移 422 の存在）。"""

    def test_patch_endpoint_registered(self):
        assert '@admin_router.patch("/proposals/{proposal_id}")' in _ROUTES_DOUBT_SRC
        assert "def patch_verification_proposal" in _ROUTES_DOUBT_SRC

    def test_invalid_transition_returns_422(self):
        fn_src = extract_function_source(_ROUTES_DOUBT_SRC, "patch_verification_proposal")
        assert "invalid status transition" in fn_src
        assert "status_code=422" in fn_src

    def test_allowed_transitions_constant_present(self):
        assert "_PROPOSAL_ALLOWED_TRANSITIONS" in _ROUTES_DOUBT_SRC
        assert '("proposed", "in_progress")' in _ROUTES_DOUBT_SRC
        assert '("in_progress", "completed")' in _ROUTES_DOUBT_SRC
        assert '("proposed", "withdrawn")' in _ROUTES_DOUBT_SRC


class TestFalsificationProjectionsDropNumericAndAttribution:
    """falsification_candidates の教員向け射影に confidence が出ない（射影関数のソース
    検査）+ 学習者投影に recorded_by / evidence_ids / cut_members が出ない（同）。
    """

    def test_teacher_candidate_projection_has_no_confidence(self):
        fn_src = extract_function_source(_ROUTES_DOUBT_SRC, "_falsification_candidate_out")
        assert "confidence" not in fn_src

    def test_learner_falsification_projection_drops_attribution(self):
        fn_src = extract_function_source(_ROUTES_DOUBT_SRC, "_learner_falsification_conditions")
        assert "recorded_by" not in fn_src
        assert "evidence_ids" not in fn_src
        assert "evidence_quote" not in fn_src

    def test_learner_ledger_route_does_not_leak_cut_members_or_recorded_by(self):
        fn_src = extract_function_source(_ROUTES_DOUBT_SRC, "get_learner_ledger_line")
        assert "cut_members" not in fn_src
        assert "recorded_by" not in fn_src
        assert "observation_roots" not in fn_src


class TestOpeningFalsificationWiring:
    """opening 結線: `_assumption_fact_line` に固定文言3種の原文存在（設計書 §7、逐語）。"""

    def test_three_fixed_phrases_present_verbatim(self):
        fn_src = extract_function_source(_OPENING_SRC, "_assumption_fact_line")
        assert "何が起これば覆るかが記帳されている前提です。" in fn_src
        assert "反証条件を定式化できないと記帳されている前提です。" in fn_src
        assert "覆る条件はまだ定式化されていません。" in fn_src

    def test_backward_compatible_with_legacy_item_shape(self):
        """旧形状の item（has_falsification_condition キー無し）は分岐に入らない
        （既存 test_discuss_opening.py / test_discuss_opening_projection.py の
        厳密な文字列一致テストを壊さないための後方互換ガード）。
        """
        fn_src = extract_function_source(_OPENING_SRC, "_assumption_fact_line")
        assert '"has_falsification_condition" not in item' in fn_src


class TestMetricsActionVocabulary:
    """metrics の action 語彙に falsification 系4種 + proposal 遷移が含まれる（§10）。"""

    def test_falsification_action_vocabulary_present(self):
        for action in (
            "falsification_add",
            "falsification_patch",
            "falsification_candidate_confirm",
            "falsification_candidate_dismiss",
        ):
            assert action in _METRICS_SRC, action

    def test_proposal_status_transition_action_present(self):
        assert "status_transition" in _METRICS_SRC
        assert "verification_proposal" in _METRICS_SRC
