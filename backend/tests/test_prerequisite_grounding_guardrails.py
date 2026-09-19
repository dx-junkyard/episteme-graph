"""前提知識の3段解決（是正 F4）ガードレール。

正本: docs/architecture/six_lenses_2026-09-10/06_coldstart.md 提案6 /
      docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md §4 第1波 #4。

観点:
  1. `check_prerequisites` は習得判定に `learning_chat_history`（接触の痕跡）を使わない。
     判定の根拠は本人の明示的な答えの記帳（acknowledged_prerequisites）だけ（§3.6 / UC5）。
  2. LEARNING_ADVICE 分岐の全 `LearningChatResponse` が `content_grounding` を設定する
     （原則8: 出所の正直さ。一番あやふやな回答が一番確からしく見える状態を作らない）。
  3. ② の資料検索は可視性ゲート（`allowed_document_ids`）を必ず通す（fail-closed）。
  4. 解決できなかった前提の事実文は閉世界語彙（SL1）だけで、分野レベルの不在は言わない。
  5. G層ルール `course.prerequisite_uncovered` が登録され、capability が実在する。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.admin_assistant import capabilities as caps  # noqa: E402
from core.admin_assistant import next_steps as next_steps_mod  # noqa: E402
from tests.guardrail_helpers import (  # noqa: E402
    assert_source_forbids,
    extract_function_source,
)

_SERVICES = BACKEND / "api" / "services.py"
_LEARNING = BACKEND / "api" / "routes" / "learning.py"
_NEXT_STEPS = BACKEND / "core" / "admin_assistant" / "next_steps.py"
_SERVICES_SRC = _SERVICES.read_text(encoding="utf-8")
_LEARNING_SRC = _LEARNING.read_text(encoding="utf-8")
_NEXT_STEPS_SRC = _NEXT_STEPS.read_text(encoding="utf-8")

#: SL1 が禁じる分野レベルの不在言明（台帳・コーパスの射影であって分野の射影ではない）。
_BANNED_CLOSED_WORLD = ("この分野では未検証", "誰も検証していない", "世界初", "未踏")


# ---------------------------------------------------------------------------
# 1. 履歴による自動スキップの撤去
# ---------------------------------------------------------------------------


class TestNoHistoryBasedMastery:
    def test_check_prerequisites_does_not_read_chat_history(self):
        src = extract_function_source(_SERVICES_SRC, "check_prerequisites")
        assert_source_forbids(
            src,
            ("learning_chat_history", "topics_with_history"),
            context="check_prerequisites",
        )

    def test_mastery_comes_from_explicit_acknowledgement(self):
        src = extract_function_source(_SERVICES_SRC, "check_prerequisites")
        assert "get_acknowledged_prerequisites(" in src
        assert "_is_explicit_prerequisite_acknowledgement(" in src

    def test_acknowledgement_store_has_no_delete(self):
        """記帳は status/JSONB の追記だけで、行削除・キー削除の経路を持たない（P4）。"""
        src = extract_function_source(_SERVICES_SRC, "record_prerequisite_acknowledgement")
        assert_source_forbids(
            src,
            ("DELETE", "pop(", "del "),
            context="record_prerequisite_acknowledgement",
        )


# ---------------------------------------------------------------------------
# 2. LEARNING_ADVICE の全分岐に content_grounding
# ---------------------------------------------------------------------------


def _learning_advice_branch() -> ast.If:
    tree = ast.parse(_LEARNING_SRC)
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test_src = ast.get_source_segment(_LEARNING_SRC, node.test) or ""
        if 'intent == "LEARNING_ADVICE"' in test_src:
            return node
    raise AssertionError("LEARNING_ADVICE 分岐が見つからない（実装が変わった？）")


class TestLearningAdviceGrounding:
    def test_every_response_in_branch_sets_content_grounding(self):
        branch = _learning_advice_branch()
        calls = [
            node for node in ast.walk(branch)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "LearningChatResponse"
        ]
        assert calls, "LEARNING_ADVICE 分岐に LearningChatResponse の生成が無い"
        for call in calls:
            keywords = {kw.arg for kw in call.keywords if kw.arg}
            assert "content_grounding" in keywords, (
                "LEARNING_ADVICE 分岐の応答が content_grounding を設定していない "
                f"(line {call.lineno})"
            )

    def test_branch_resolves_prerequisites_before_the_llm_call(self):
        """3段解決は LLM コールの前に決定論的に走り、抜粋を同じ1コールへ渡す。"""
        branch = _learning_advice_branch()
        src = ast.get_source_segment(_LEARNING_SRC, branch) or ""
        assert "_resolve_prerequisite_context(" in src
        assert "source_context=" in src
        assert src.index("_resolve_prerequisite_context(") < src.index(
            "_generate_learning_advice_response("
        )

    def test_model_generated_is_used_for_general_advice(self):
        branch = _learning_advice_branch()
        src = ast.get_source_segment(_LEARNING_SRC, branch) or ""
        assert 'content_grounding="model_generated"' in src


# ---------------------------------------------------------------------------
# 3. ② の検索は可視性ゲートを通す
# ---------------------------------------------------------------------------


class TestPrerequisiteSearchIsFailClosed:
    def test_resolver_passes_allowed_document_ids(self):
        src = extract_function_source(_LEARNING_SRC, "_resolve_prerequisite_context")
        assert "search_chunks_with_metadata(" in src
        assert "allowed_document_ids=allowed_document_ids" in src
        assert "list_visible_document_ids(" in src

    def test_resolver_does_not_expose_raw_scores_beyond_existing_dto(self):
        """出典 DTO は既存 RAG 経路と同じ形（新しい数値フィールドを足さない）。"""
        src = extract_function_source(_LEARNING_SRC, "_resolve_prerequisite_context")
        assert '"confidence"' not in src

    def test_resolver_marks_source_text_as_untrusted(self):
        src = extract_function_source(_LEARNING_SRC, "_resolve_prerequisite_context")
        assert "UNTRUSTED_SOURCE_NOTICE" in src


# ---------------------------------------------------------------------------
# 4. 閉世界の事実文
# ---------------------------------------------------------------------------


class TestClosedWorldFact:
    def test_fixed_phrase_present(self):
        assert (
            'PREREQUISITE_CLOSED_WORLD_FACT = "このコーパスの中には、この前提を扱う資料がありません。"'
            in _LEARNING_SRC
        )

    def test_no_banned_field_level_claims(self):
        """検査対象は本層が書いた文言だけ（`未踏` は L1 tier の既存語彙なので全文検査しない）。"""
        scoped = "\n".join([
            extract_function_source(_LEARNING_SRC, "_prerequisite_closed_world_note"),
            extract_function_source(_LEARNING_SRC, "_resolve_prerequisite_context"),
            extract_function_source(_SERVICES_SRC, "check_prerequisites"),
            extract_function_source(_NEXT_STEPS_SRC, "_eval_course_prerequisite_uncovered"),
        ])
        assert_source_forbids(scoped, _BANNED_CLOSED_WORLD, context="prerequisite layer")
        # 固定文の周辺（定数の宣言とコメント）にも分野レベルの言明を置かない。
        idx = _LEARNING_SRC.index("PREREQUISITE_CLOSED_WORLD_FACT =")
        assert_source_forbids(
            _LEARNING_SRC[idx - 400: idx + 400],
            _BANNED_CLOSED_WORLD,
            context="closed world constant",
        )

    def test_note_has_no_counts(self):
        src = extract_function_source(_LEARNING_SRC, "_prerequisite_closed_world_note")
        assert_source_forbids(src, ("件", "len(names)}"), context="closed world note")


# ---------------------------------------------------------------------------
# 5. G層ルール
# ---------------------------------------------------------------------------


class TestPrerequisiteUncoveredRule:
    def test_rule_registered_with_existing_capability(self):
        rule_id = next_steps_mod.RULE_COURSE_PREREQUISITE_UNCOVERED
        assert rule_id == "course.prerequisite_uncovered"
        rule = next_steps_mod.RULE_CATALOG[rule_id]
        assert rule["severity"] == next_steps_mod.SEVERITY_RECOMMENDED
        cap = caps.get_capability(rule["capability_id"])
        assert cap is not None, "capability が registry に存在しない（G3 fail-closed）"
        assert cap.kind == "guidance_only"
        assert rule_id in next_steps_mod._RULE_EVALUATORS

    def test_evaluator_ignores_learner_signals(self):
        """学習者の痕跡・習得状態を選定入力にしない（原則5 / 監視しない）。"""
        src = extract_function_source(_NEXT_STEPS_SRC, "_eval_course_prerequisite_uncovered")
        assert_source_forbids(
            src,
            ("interest_traces", "learning_chat_history", "learning_states"),
            context="_eval_course_prerequisite_uncovered",
        )

    def test_evaluator_binds_terms_as_parameters(self):
        src = extract_function_source(_NEXT_STEPS_SRC, "_eval_course_prerequisite_uncovered")
        assert 'f"(CAST(:t{i} AS text))"' in src
        assert "params[f\"t{i}\"]" in src

    def test_reason_has_no_counts(self):
        """事実文に件数を書かない（G6 / 原則4）。名前の列挙 + 「など」だけ。"""
        src = extract_function_source(_NEXT_STEPS_SRC, "_eval_course_prerequisite_uncovered")
        assert "など" in src
        assert "len(uncovered)}" not in src
        assert "{pending}" not in src


class TestPrerequisiteUncoveredEvaluation:
    """フェイクセッションでの評価（DB 不要）。"""

    class _Rows:
        def __init__(self, rows):
            self._rows = rows

        def mappings(self):
            return self

        def fetchall(self):
            return self._rows

    class _Session:
        def __init__(self, courses, documents, matches):
            self._courses = courses
            self._documents = documents
            self._matches = matches
            self.statements: list[str] = []

        def execute(self, statement, params=None):
            sql = str(statement)
            self.statements.append(sql)
            if "FROM learning_courses" in sql:
                return TestPrerequisiteUncoveredEvaluation._Rows(self._courses)
            if "FROM documents" in sql:
                return TestPrerequisiteUncoveredEvaluation._Rows(self._documents)
            if "chunks" in sql:
                return TestPrerequisiteUncoveredEvaluation._Rows(self._matches)
            raise AssertionError(f"想定外のクエリ: {sql}")

    def _course_row(self):
        return {
            "id": "course-1",
            "title": "有効場の理論",
            "data": {
                "sources": [{"material_id": "mat-1"}],
                "topics": [
                    {
                        "id": "t1",
                        "title": "有効演算子",
                        "prerequisites": [{"name": "グリーン関数"}, {"name": "有効演算子"}],
                    }
                ],
            },
            "created_at": "2026-09-01T00:00:00+00:00",
        }

    def test_lights_up_when_no_source_covers_the_prerequisite(self):
        session = self._Session(
            courses=[self._course_row()],
            documents=[{"id": "11111111-1111-1111-1111-111111111111", "source_path": "mat-1"}],
            matches=[],
        )
        out = next_steps_mod._eval_course_prerequisite_uncovered(session, "user-1")
        assert len(out) == 1
        step, _ = out[0]
        assert step.rule_id == "course.prerequisite_uncovered"
        assert "グリーン関数" in step.reason
        # コース内トピックと同名の前提（有効演算子）は①で解決済みなので挙がらない。
        assert "有効演算子" not in step.reason

    def test_silent_when_source_text_mentions_the_prerequisite(self):
        doc_id = "11111111-1111-1111-1111-111111111111"
        session = self._Session(
            courses=[self._course_row()],
            documents=[{"id": doc_id, "source_path": "mat-1"}],
            matches=[{"term": "グリーン関数", "document_id": doc_id}],
        )
        out = next_steps_mod._eval_course_prerequisite_uncovered(session, "user-1")
        assert out == []

    def test_no_query_when_no_course_has_prerequisites(self):
        row = self._course_row()
        row["data"]["topics"][0]["prerequisites"] = []
        session = self._Session(courses=[row], documents=[], matches=[])
        assert next_steps_mod._eval_course_prerequisite_uncovered(session, "user-1") == []
        assert all("FROM documents" not in s for s in session.statements)
