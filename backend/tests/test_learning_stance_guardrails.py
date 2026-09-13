"""入口統合 Phase 1 の不変条項 LC1〜LC8 の構造ガードレール。

正本: ``docs/features/learning_chat_entry_unification_design.md``（§2 / §8）。

| # | 条項 | ここで固定する事実 |
|---|---|---|
| LC1 | 推定してよいのは様相だけ | 推定経路のソースに discuss_scope / cycle_mode / backstage / check_scaffold への代入が無い |
| LC2 | 明示は常に推定に勝つ | 一次判定は明示の様相が立っていないときだけ計算される |
| LC3 | HELP pre-route は最前段 | pre-route の index < 推定器の index |
| LC4 | 入力は当該発話 + 画面の明示状態だけ | 推定器が history / 過去の様相を受け取らない |
| LC5 | LLM 呼び出しを増やさない | core/learning_stance/ が core.llm を import しない・推定経路に generate_text が無い |
| LC6 | 推定した事実は記録し本人に見せる | 痕跡 payload とレスポンスの両方に配線されている |
| LC7 | 数値を見せない | 様相 DTO に confidence / score が再帰的に無い |
| LC8 | 既存層は非改変 | _is_casual / _is_discuss の定義行と下流の条件式が逐語のまま |
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tests.guardrail_helpers import (  # noqa: E402
    assert_module_tree_does_not_import,
    assert_module_tree_forbids,
    extract_function_source,
)

_STANCE_DIR = BACKEND / "core" / "learning_stance"
_LEARNING_SRC = (BACKEND / "api" / "routes" / "learning.py").read_text(encoding="utf-8")
_CORE_SRC = extract_function_source(_LEARNING_SRC, "_learning_chat_core")

#: 推定器を組み立てている区画（一次判定の計算 〜 意図分類の呼び出しまで）。
_ESTIMATOR_BLOCK = _CORE_SRC.split("_prejudged: str | None = None")[1].split(
    "# ルート①"
)[0]


# ===========================================================================
# LC5 / LC4: core/learning_stance/ の純粋性
# ===========================================================================


class TestPrejudgeIsPureAndNonLlm:
    def test_prejudge_is_pure_and_non_llm(self):
        assert_module_tree_does_not_import(
            _STANCE_DIR,
            ["fastapi", "sqlalchemy", "core.postgres", "core.llm", "openai", "episteme_graph",
             "services", "requests", "httpx"],
        )

    def test_no_db_session_and_no_io(self):
        assert_module_tree_forbids(
            _STANCE_DIR,
            ["get_session", "generate_text", "open(", "os.environ", "random.", "datetime.now"],
        )

    def test_prejudge_only_takes_the_current_message(self):
        """LC4: 履歴・過去の様相・学習者モデルを入力にしない。"""
        from core.learning_stance import prejudge

        params = inspect.signature(prejudge).parameters
        assert set(params) == {"message", "content_terms"}
        for forbidden in ("history", "user_id", "previous_stance", "traces", "progress"):
            assert forbidden not in params

    def test_no_domain_words_in_the_module(self):
        """分野語をコードに書かない（開発ルール7。供給口は呼び出し側の引数）。"""
        assert_module_tree_forbids(_STANCE_DIR, ["物理", "素粒子", "場の理論", "有効理論"])

    def test_no_detour_vocabulary(self):
        """DM5: 「寄り道」を様相の語彙・文言に使わない。"""
        assert_module_tree_forbids(_STANCE_DIR, ["寄り道"])


# ===========================================================================
# LC1: 推定が切り替えないもの
# ===========================================================================


class TestStanceNeverSwitchesScopeOrMode:
    def test_stance_never_sets_discuss_scope(self):
        assert "discuss_scope" not in _ESTIMATOR_BLOCK
        # 代入形だけを禁じる（docstring は「触らない」ことを明記するために語を含む）。
        assert_module_tree_forbids(
            _STANCE_DIR,
            ["discuss_scope =", "discuss_scope=", "allowed_document_ids"],
        )

    def test_stance_never_sets_cycle_mode_or_backstage(self):
        for forbidden in ("cycle_mode =", "backstage =", "check_scaffold ="):
            assert forbidden not in _ESTIMATOR_BLOCK, forbidden
        assert_module_tree_forbids(
            _STANCE_DIR, ["cycle_mode =", "backstage =", "check_scaffold ="],
        )

    def test_resolve_stance_does_not_receive_scope_or_mode_switches(self):
        from core.learning_stance import resolve_stance

        params = set(inspect.signature(resolve_stance).parameters)
        for forbidden in ("discuss_scope", "backstage", "check_scaffold", "intent_mode"):
            assert forbidden not in params

    def test_estimator_block_only_assigns_prejudged_and_explicit_casual(self):
        """推定区画で書き換えるのは一次判定の結果と、明示 casual の控えだけ。"""
        assigned = {
            line.split("=")[0].strip()
            for line in _ESTIMATOR_BLOCK.splitlines()
            if " = " in line
            and not line.strip().startswith("#")
            and not line.strip().startswith(("if ", "elif ", "or ", "and ", "+", ")"))
            and "==" not in line
            and "(" not in line.split("=")[0]
        }
        assert assigned <= {"_prejudged", "_stance_cartridge_id", "_explicit_casual", "intent"}, assigned


# ===========================================================================
# LC2 / LC3: 解決順
# ===========================================================================


class TestResolutionOrder:
    def test_help_preroute_precedes_stance_resolution(self):
        """LC3: HELP pre-route は非LLM・最前段のまま（推定器はその後）。"""
        preroute_idx = _CORE_SRC.index("_is_usage_help = (")
        prejudge_idx = _CORE_SRC.index("_prejudged: str | None = None")
        resolve_idx = _CORE_SRC.index("_stance, _stance_source = resolve_stance(")
        assert preroute_idx < prejudge_idx < resolve_idx

    def test_explicit_modes_short_circuit_the_estimator(self):
        """LC2: casual / discuss / 地図アクションが立っていたら一次判定を計算しない。"""
        assert (
            "if not (_is_casual or _is_discuss or _atlas_ctx) and not _is_greeting(body.message):"
            in _ESTIMATOR_BLOCK
        )

    def test_typed_action_is_evaluated_before_the_estimator(self):
        """順序: typed action → 一次判定 → LLM 分類（設計 §4.2）。"""
        assert (
            "_route_for_typed_action(body.support_action)\n"
            "            or _prejudged\n"
            "            or _classify_intent(" in _CORE_SRC
        )

    def test_classifier_bypass_expression_is_unchanged(self):
        """LC8: 既存ガードレールが固定している逐語を壊していない。"""
        assert "intent = None if (_is_casual or _is_discuss or _atlas_ctx) else (" in _CORE_SRC
        assert '_is_casual = (body.intent_mode or "").strip() == "casual"' in _CORE_SRC
        assert '_is_discuss = (body.intent_mode or "").strip() == "discuss"' in _CORE_SRC

    def test_explicit_casual_is_captured_before_the_reassignment(self):
        explicit_idx = _CORE_SRC.index("_explicit_casual = _is_casual")
        reassign_idx = _CORE_SRC.index("_is_casual = True")
        assert explicit_idx < reassign_idx


# ===========================================================================
# LC5: LLM 呼び出しを増やさない
# ===========================================================================


class TestNoAdditionalLlmCallPerTurn:
    def test_no_additional_llm_call_per_turn(self):
        """推定区画に新しい LLM 呼び出しが無い（既存の分類コールに吸収する）。"""
        assert "generate_text(" not in _ESTIMATOR_BLOCK
        assert "generate_conversation_turn" not in _ESTIMATOR_BLOCK
        # 分類コールは1箇所のまま（_classify_intent の呼び出し）。
        assert _ESTIMATOR_BLOCK.count("_classify_intent(") == 1

    def test_no_new_cost_gate_or_env_setting(self):
        """専用上限・専用 env を作らない（既存 LEARNING_CHAT_MAX_CALLS_PER_DAY に相乗り）。"""
        assert "STANCE_MAX_CALLS" not in _LEARNING_SRC
        assert_module_tree_forbids(_STANCE_DIR, ["CostGate", "MAX_CALLS", "get_settings"])


# ===========================================================================
# LC6 / LC7: 記録と提示、数値非表示
# ===========================================================================


class TestRecordAndDisclose:
    def test_stance_source_recorded_in_trace_payload_and_not_in_backstage(self):
        payload_block = _CORE_SRC.split("_trace_payload = {")[1].split("\n    }")[0]
        assert '"stance": _stance, "stance_source": _stance_source' in payload_block
        # 楽屋には焼き込まない（entry_mode と同じ SD4 のガード）。
        stance_entry = payload_block.split('"stance": _stance')[1][:200]
        assert "if not _is_backstage" in stance_entry

    def test_stance_returned_only_on_the_rag_response(self):
        """RAG の最終 return だけが stance を設定する（早期 return は None のまま）。"""
        assert _CORE_SRC.count("stance=build_stance_dto(") == 1

    def test_trace_payload_has_no_confidence(self):
        payload_block = _CORE_SRC.split("_trace_payload = {")[1].split("\n    }")[0]
        for forbidden in ('"confidence"', '"stance_score"', '"stance_confidence"'):
            assert forbidden not in payload_block

    def test_stance_response_has_no_numeric_confidence(self):
        from core.learning_stance import STANCE_SOURCES, STANCES, build_stance_dto

        for stance in STANCES:
            for source in STANCE_SOURCES:
                dto = build_stance_dto(stance, source)
                assert set(dto) == {"stance", "source", "label"}
                assert all(isinstance(v, str) for v in dto.values())

    def test_labels_are_owned_by_label_vocab(self):
        """表示ラベルの正本は core/label_vocab.py（語彙モジュールに日本語表を二重化しない）。"""
        from core.label_vocab import LEARNING_STANCE_LABELS

        schema_src = (_STANCE_DIR / "schema.py").read_text(encoding="utf-8")
        for label in LEARNING_STANCE_LABELS.values():
            assert label not in schema_src, f"ラベルが語彙モジュールにも書かれている: {label}"
        assert "from core.label_vocab import LEARNING_STANCE_LABELS" in schema_src


# ===========================================================================
# LC8: 既存層は非改変
# ===========================================================================


class TestExistingLayersUnchanged:
    def test_downstream_conditions_are_verbatim(self):
        for literal in (
            "None if (_is_casual or _is_discuss or _atlas_ctx) else check_prerequisites(",
            "if overall_tier == TIER_OUT_OF_SOURCE and not _is_casual:",
            "if not _is_casual and topic_info and any(",
            '"casual": _is_casual',
            '(body.intent_mode or "").strip() in ("on_path", "casual", "discuss"):',
            '**({"entry_mode": "discuss"} if _is_discuss and not _is_backstage else {}),',
        ):
            assert literal in _LEARNING_SRC, literal

    def test_chit_chat_branch_only_reassigns_the_casual_flag(self):
        branch = _CORE_SRC.split('if intent == "CHIT_CHAT":')[1].split("\n\n")[0]
        assert branch.strip() == "_is_casual = True"

    def test_casual_prompt_spoken_and_text_variants(self):
        body = _LEARNING_SRC.split("def _get_casual_teacher_system_prompt")[1].split("\ndef ")[0]
        assert "*, \n" not in body
        assert "spoken: bool = True" in body
        spoken_branch, text_branch = body.split("if spoken:")[1].split("    else:")
        # spoken=True 側は現行の本文を維持（音声向けの制約が残っている）。
        assert "2〜4文" in spoken_branch
        assert "箇条書き" in spoken_branch
        assert "ACTION_BUTTON" in spoken_branch
        # spoken=False 側は LaTeX と出典マーカーを許可し、システム記法だけ禁止する。
        assert "LaTeX" in text_branch
        assert "$...$" in text_branch
        assert "[出典1]" in text_branch
        assert "ACTION_BUTTON" in text_branch  # 引き続き禁止する旨の明示

    def test_discuss_prompt_contracts_unchanged(self):
        """DA6 契約フレーズ・DM4 必須要素が無傷（§8 の再確認項目）。"""
        body = _LEARNING_SRC.split("def _get_discuss_system_prompt(")[1].split("\ndef ")[0]
        for literal in ("〔鏡〕", "言い直し", "即答"):
            assert literal in body, literal
        # discuss プロンプトは spoken 分岐を持たない（伝達形式の分離は casual だけ）。
        assert "spoken" not in body


# ===========================================================================
# §7: 観測（制度指標カタログを増やさない）
# ===========================================================================


class TestObservationWiring:
    def test_no_new_indicator_registered(self):
        """v1 は教員向け集約を作らないので、指標カタログに 1 件も足さない（IG4）。"""
        from core.indicator_catalog import INDICATORS

        for indicator_id, spec in INDICATORS.items():
            assert "stance" not in indicator_id, indicator_id
            assert "stance" not in getattr(spec, "route", ""), indicator_id

    def test_metric_event_and_payload_vocab_registered(self):
        from core.discuss import observation

        assert "stance_corrected" in observation.METRIC_EVENT_VOCAB
        assert observation._METRIC_EVENT_PAYLOAD_VALUE_VOCAB["stance"] == frozenset(
            {"tutor", "casual_light"}
        )
        assert "stance" in observation._METRIC_EVENT_PAYLOAD_KEYS

    def test_dump_columns_include_the_stance(self):
        from core.discuss import observation

        src = inspect.getsource(observation._fetch_discuss_trace_rows)
        assert "payload->>'stance' AS stance" in src
        assert "payload->>'stance_source' AS stance_source" in src
        projected = observation.project_trace_row(
            {"user_id": "u", "stance": "tutor", "stance_source": "inferred"}
        )
        assert projected["stance"] == "tutor"
        assert projected["stance_source"] == "inferred"

    def test_entry_mode_semantics_untouched(self):
        """DO1: entry_mode の書き込み条件・集計 SQL は一切変えない。"""
        from core.discuss import observation

        src = inspect.getsource(observation)
        # 集計2本 + ダンプ2本（うち2本は AND 条件が続く）で計4箇所。
        assert src.count("WHERE payload ->> 'entry_mode' = 'discuss'") == 4
