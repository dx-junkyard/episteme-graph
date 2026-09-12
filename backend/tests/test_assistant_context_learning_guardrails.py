"""画面文脈アダプター Phase 4 — 学習側のガードレール（設計 §11.9）。

構造で守るのは5つ:

- **生テーブルを引かない**（§11.3 の中心規律）。解決器は学習者射影の DTO しか見ず、
  ``sqlalchemy`` / ``fastapi`` / ``core.llm`` / ``core.postgres`` を**推移的にも**
  import しない。成果テーブル名を文字列としても持たない。
- **4-c は実装しない**（§11.13-3 のオーナー判断）。``topic`` / ``visible`` の解決器が
  登録されていないことを固定し、後から黙って生えないようにする。
- **SL1 の閉世界語彙**（``test_stakes_ledger_guardrails`` の denylist を再利用）が
  事実文・固定文・テンプレートのどこにも現れない。出力側の拘束文は原文で存在する。
- **出所ラベルを剥がさない**（§11.13-1）。AI 推定の配置は「AIによる推定（未確認）」を
  含んだまま事実文になる。
- **Phase 1 は不変**（§11.10 の後方互換）。既定ヘッダの文言が変わっていない。
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from tests.guardrail_helpers import (  # noqa: E402
    assert_module_tree_does_not_import,
    assert_module_tree_forbids,
    assert_source_forbids,
)
from tests.test_assistant_context_learning_core import (  # noqa: E402
    _ctx,
    _sources,
)
from tests.test_stakes_ledger_guardrails import TestClosedWorldVocabulary  # noqa: E402

from core.assistant_context import (  # noqa: E402
    BLOCK_HEADER,
    KNOWN_SCREENS,
    LEARNING_VERIFICATION_OUTPUT_CONSTRAINT,
    SCREEN_LEARNING,
    registered_kinds,
    resolve,
)
from core.assistant_context import schema as ac_schema  # noqa: E402

CORE_DIR = BACKEND / "core" / "assistant_context"
LEARNING_RESOLVER = CORE_DIR / "resolvers" / "learning.py"
SELECTION_MODULE = CORE_DIR / "selection.py"
NEW_MODULES = (LEARNING_RESOLVER, SELECTION_MODULE)

#: SL1 の閉世界語彙 denylist は**再定義しない**（正本は賭け金の台帳のガードレール）。
BANNED_CLOSED_WORLD = TestClosedWorldVocabulary.BANNED


def _facts() -> list[str]:
    return resolve(_ctx(), _sources())


# ---------------------------------------------------------------------------
# 1. 生テーブルを引かない（§11.3）
# ---------------------------------------------------------------------------


class TestLearningResolverReadsProjectionsOnly:
    def test_new_modules_do_not_import_frameworks_db_or_llm(self):
        for path in NEW_MODULES:
            assert_module_tree_does_not_import(
                path.parent,
                ["fastapi", "sqlalchemy", "core.llm", "core.postgres", "openai", "routes"],
                glob=path.name,
            )

    def test_purity_holds_transitively(self):
        """依存先が将来重い依存を掴んだ瞬間に落ちるよう、実測で検査する。

        ``learner_context_common``（内部 ID 遮断の正本）を import するので、
        自ファイルのソース検査だけでは推移的な純粋性が保証できない。
        """
        code = (
            "import sys; import core.assistant_context.resolvers.learning; "
            "import core.assistant_context.selection; "
            "bad = sorted(m for m in sys.modules "
            "if m.split('.')[0] in ('fastapi', 'sqlalchemy', 'openai') "
            "or m in ('core.postgres', 'core.llm')); "
            "print(','.join(bad)); sys.exit(1 if bad else 0)"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, cwd=str(BACKEND)
        )
        assert proc.returncode == 0, (
            "学習側の解決器が推移的に重量依存を掴んでいる: "
            f"{proc.stdout.strip()}{proc.stderr.strip()}"
        )

    def test_no_result_table_names_appear(self):
        """成果テーブルへ直接 SELECT を書く誘惑を構造的に断つ。"""
        for path in NEW_MODULES:
            assert_source_forbids(
                path.read_text(encoding="utf-8"),
                [
                    "theory_claims",
                    "theory_components",
                    "epistemic_ledger",
                    "landscape_placements",
                    "reconstruction_items",
                    "interest_traces",
                ],
                context=str(path),
            )

    def test_no_sql_or_session_handling(self):
        for path in NEW_MODULES:
            assert_module_tree_forbids(
                path.parent,
                ["DELETE FROM", "INSERT INTO", "SELECT ", "get_session", "sa_text"],
                glob=path.name,
            )

    def test_no_write_verbs(self):
        for path in NEW_MODULES:
            assert_source_forbids(
                path.read_text(encoding="utf-8"),
                ["def save_", "def store_", "def persist_", "def delete_", "def approve_"],
                context=str(path),
            )

    def test_budgets_are_code_constants(self):
        """SA7: 学習側の予算も env で緩めない。"""
        for path in NEW_MODULES:
            assert_source_forbids(
                path.read_text(encoding="utf-8"),
                ["os.environ", "getenv", "Settings", "settings."],
                context=str(path),
            )


# ---------------------------------------------------------------------------
# 2. 4-c（topic / visible）を実装しない（§11.13-3）
# ---------------------------------------------------------------------------


class TestStagedScope:
    def test_registered_kinds_are_exactly_the_four_shipped_ones(self):
        assert registered_kinds(SCREEN_LEARNING) == (
            "element",
            "verification",
            "placement",
            "view",
        )

    def test_topic_and_visible_resolvers_are_not_registered(self):
        assert "topic" not in registered_kinds(SCREEN_LEARNING)
        assert "visible" not in registered_kinds(SCREEN_LEARNING)

    def test_no_resolver_function_is_defined_for_the_deferred_kinds(self):
        """関数だけ先に生やして「登録は後で」にしない（4-c は実測後に判断する）。"""
        tree = ast.parse(LEARNING_RESOLVER.read_text(encoding="utf-8"))
        names = {
            node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
        }
        assert "resolve_topic" not in names
        assert "resolve_visible" not in names

    def test_visible_entities_are_normalized_but_never_resolved(self):
        """正規化では受け取る（画面の申告は壊さない）が、事実文にはしない。"""
        ctx = _ctx(entities=[{"type": "component", "id": "x", "title": "見えている題名"}])
        assert all("見えている題名" not in fact for fact in resolve(ctx, _sources()))

    def test_learning_screen_is_declared(self):
        assert SCREEN_LEARNING in KNOWN_SCREENS
        assert ac_schema.SCREEN_LEARNING == "learning"


# ---------------------------------------------------------------------------
# 3. SL1 閉世界語彙（出力側の拘束を含む）
# ---------------------------------------------------------------------------


class TestClosedWorldVocabularyIsPreserved:
    def test_no_banned_phrase_in_the_adapter_sources(self):
        assert_module_tree_forbids(CORE_DIR, BANNED_CLOSED_WORLD)

    def test_no_banned_phrase_reaches_the_facts(self):
        blob = "\n".join(_facts())
        for phrase in BANNED_CLOSED_WORLD:
            assert phrase not in blob, phrase

    def test_output_constraint_exists_verbatim(self):
        """§11.13-2: 事実 + 出力側の拘束の2段構え。拘束文の原文を固定する。"""
        assert "このコーパスの中では" in LEARNING_VERIFICATION_OUTPUT_CONSTRAINT
        assert (
            "このコーパスの中では"
            in (CORE_DIR / "schema.py").read_text(encoding="utf-8")
        )

    def test_output_constraint_does_not_quote_the_banned_phrases(self):
        """禁止語の例示を書かない（denylist に carve-out を作らない・プライミングを避ける）。"""
        for phrase in BANNED_CLOSED_WORLD:
            assert phrase not in LEARNING_VERIFICATION_OUTPUT_CONSTRAINT

    def test_ledger_fact_lines_are_not_reworded(self):
        sources = _sources()
        fact_line = sources["ledger"]["fact_line"]
        assert fact_line in _facts()


# ---------------------------------------------------------------------------
# 4. 出所ラベルを剥がさない（§11.13-1）
# ---------------------------------------------------------------------------


class TestProvenanceLabelsSurvive:
    def test_inferred_placement_keeps_its_label(self):
        blob = "\n".join(_facts())
        assert "AIによる推定（未確認）" in blob

    def test_candidate_explanation_keeps_its_label(self):
        blob = "\n".join(_facts())
        assert ac_schema.EXPLANATION_STATUS_CANDIDATE_LABEL in blob


# ---------------------------------------------------------------------------
# 5. Phase 1 は不変（§11.10）
# ---------------------------------------------------------------------------


class TestPhase1IsUnchanged:
    def test_teacher_header_wording_is_frozen(self):
        assert BLOCK_HEADER == (
            "[画面文脈 — 教員がいま画面で選んでいる対象を、サーバが解析結果から解決した事実。"
            "根拠ではなく範囲の手がかり]"
        )

    def test_learning_header_states_the_block_is_not_evidence(self):
        assert "学習者" in ac_schema.BLOCK_HEADER_LEARNING
        assert "根拠ではなく" in ac_schema.BLOCK_HEADER_LEARNING
        assert ac_schema.BLOCK_HEADER_LEARNING != BLOCK_HEADER

    def test_selection_block_always_declares_whether_it_matched(self):
        assert ac_schema.SELECTION_MATCH_CONFIRMED != ac_schema.SELECTION_MATCH_UNCONFIRMED
        assert "確認できていません" in ac_schema.SELECTION_MATCH_UNCONFIRMED
