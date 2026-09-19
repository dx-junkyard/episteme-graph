"""コーパス回遊 Phase B — コース無し論文議論の構造ガードレール。

正本設計書: ``docs/features/corpus_roaming_design.md`` §5 / §10。

§10 のうち Phase B（document 直付け discuss）が担当する項目を構造的に固定する:

1. センチネル ``_doc:`` の組み立て・判定が正本（``core/discuss/context.py``）以外に無い
2. センチネル course_id がコース解決（``get_course_data`` /
   ``get_accessible_course_data``）へ流入しない
3. document 直付け chat が可視性ゲート（``resolve_document_access`` の ``can_view``）を
   通り、RAG に ``allowed_document_ids`` を渡す（CR1）
4. 既存コース discuss と**同じコア**を通る（コピペ実装が生えていない = CR2）
5. 行削除 API を作っていない（CR8）
6. 観測2語彙が登録され、学習者向けレスポンスに数値を返さない（§5.5 / DO3）
7. ``core/discuss/context.py`` が FastAPI / LLM を import しない
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tests.guardrail_helpers import (  # noqa: E402
    assert_module_tree_does_not_import,
    extract_function_source,
)

CONTEXT_PY = BACKEND / "core" / "discuss" / "context.py"
LEARNING_PY = BACKEND / "api" / "routes" / "learning.py"
DESIGN_DOC = ROOT / "docs" / "features" / "corpus_roaming_design.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


_LEARNING_SRC = _read(LEARNING_PY)


def _core_body() -> str:
    return extract_function_source(_LEARNING_SRC, "_learning_chat_core(")


# ---------------------------------------------------------------------------
# 1. センチネルの正本性（設計 §5.1 / §10）
# ---------------------------------------------------------------------------


class TestSentinelSingleSourceOfTruth:
    def test_context_module_defines_the_prefix_and_helpers(self):
        src = _read(CONTEXT_PY)
        assert 'DOCUMENT_CONTEXT_PREFIX = "_doc:"' in src
        assert "def document_context_id(" in src
        assert "def parse_document_context(" in src

    def test_sentinel_literal_appears_only_in_the_canonical_module(self):
        """``_doc:`` を含む**実行される文字列リテラル**は正本モジュールにしか無い。

        コメント・docstring での言及（設計の説明）は許すが、他所で
        ``"_doc:" + document_id`` / ``f"_doc:{...}"`` のように組み立てる経路が生えたら
        ここで落ちる（設計 §10「センチネル `_doc:` の組み立てが正本関数以外に無い」）。
        AST の文字列定数だけを見るので、f-string の断片も検出できる。
        """
        import ast

        offenders: list[str] = []
        for path in sorted((BACKEND / "core").rglob("*.py")) + sorted(
            (BACKEND / "api").rglob("*.py")
        ):
            if path == CONTEXT_PY:
                continue
            tree = ast.parse(_read(path))
            docstring_ids = set()
            for node in ast.walk(tree):
                if isinstance(
                    node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    first = node.body[0] if node.body else None
                    if (
                        isinstance(first, ast.Expr)
                        and isinstance(first.value, ast.Constant)
                        and isinstance(first.value.value, str)
                    ):
                        docstring_ids.add(id(first.value))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and "_doc:" in node.value
                    and id(node) not in docstring_ids
                ):
                    offenders.append(f"{path}:{node.lineno}")
        assert offenders == [], f"センチネル文字列の組み立て: {offenders}"

    def test_routes_use_the_canonical_helpers(self):
        assert "from core.discuss.context import document_context_id, parse_document_context" in _LEARNING_SRC

    def test_context_module_does_not_import_fastapi_or_llm(self):
        assert_module_tree_does_not_import(
            CONTEXT_PY.parent, ["fastapi", "core.llm", "openai"], glob="context.py"
        )

    def test_context_module_touches_no_database(self):
        src = _read(CONTEXT_PY)
        for term in ("get_session", "sa_text", "sqlalchemy", "SELECT ", "INSERT "):
            assert term not in src, f"センチネル正本が DB に触れている: {term!r}"


# ---------------------------------------------------------------------------
# 2. コース解決への非流入（設計 §5.1 / §11）
# ---------------------------------------------------------------------------


class TestSentinelDoesNotReachCourseResolution:
    def test_core_resolves_course_only_when_not_provided(self):
        body = _core_body()
        assert "if course_data is None:" in body
        idx = body.index("if course_data is None:")
        block = body[idx: idx + 400]
        assert 'course_data = get_course_data(current_user["id"], course_id)' in block

    def test_document_facade_passes_course_data_and_scope(self):
        body = extract_function_source(_LEARNING_SRC, "document_discuss_chat(")
        assert "course_data=_document_discuss_course_data(" in body
        assert "scope_document_ids={document_id}" in body
        # コース解決関数はファサードから呼ばない
        assert "get_course_data(" not in body
        assert "get_accessible_course_data(" not in body

    def test_document_routes_never_call_course_resolvers(self):
        for fn in (
            "get_document_discussion_opening(",
            "document_discuss_chat(",
            "get_document_discussion_history(",
            "delete_document_discussion_message_from(",
        ):
            body = extract_function_source(_LEARNING_SRC, fn)
            assert "get_course_data(" not in body, fn
            assert "get_accessible_course_data(" not in body, fn
            assert "get_viewable_course_data(" not in body, fn

    def test_course_scoped_llm_model_lookup_is_skipped_for_sentinel(self):
        body = _core_body()
        idx = body.index("_course_chat_model = (")
        block = body[idx: idx + 300]
        assert "if _document_context_id" in block
        assert "get_course_live_llm_models(course_id)" in block


# ---------------------------------------------------------------------------
# 3. 可視性ゲートと RAG スコープ（CR1 / 設計 §5.2）
# ---------------------------------------------------------------------------


class TestVisibilityGateWiring:
    def test_resolver_uses_document_access_can_view_and_404(self):
        body = extract_function_source(_LEARNING_SRC, "_resolve_discuss_document(")
        assert "resolve_document_access(" in body
        assert "access.can_view" in body
        assert "status_code=404" in body
        # 403 で存在を漏らさない（不可も不在も 404 に統一）
        assert "status_code=403" not in body

    def test_all_four_document_routes_go_through_the_resolver(self):
        for fn in (
            "get_document_discussion_opening(",
            "document_discuss_chat(",
            "get_document_discussion_history(",
            "delete_document_discussion_message_from(",
        ):
            body = extract_function_source(_LEARNING_SRC, fn)
            assert "_resolve_discuss_document(current_user[\"id\"], document_ref)" in body, fn

    def test_rag_scope_override_is_wired_before_visible_set(self):
        body = _core_body()
        idx = body.index("if scope_document_ids is not None")
        block = body[idx: idx + 700]
        assert "allowed_document_ids = scope_document_ids" in block
        assert 'elif _is_discuss and _discuss_scope == "all_visible":' in block

    def test_search_is_called_exactly_once_in_the_core(self):
        """DM1 の構造証明: ヒット0件を理由に検索し直す経路が無い。"""
        body = _core_body()
        assert body.count("search_chunks_with_metadata(") == 1

    def test_allowed_document_ids_is_always_passed_to_search(self):
        body = _core_body()
        idx = body.index("chunk_results = search_chunks_with_metadata(")
        call = body[idx: idx + 300]
        assert "allowed_document_ids=allowed_document_ids" in call


# ---------------------------------------------------------------------------
# 4. コース経路との共通化（CR2）
# ---------------------------------------------------------------------------


class TestSharedCoreNotCopyPaste:
    def test_course_route_is_a_thin_delegation_to_the_core(self):
        body = extract_function_source(_LEARNING_SRC, "learning_chat(")
        assert "_learning_chat_core(course_id, topic_id, body, current_user)" in body
        # 旧本体がルート側に残っていない
        assert "search_chunks_with_metadata(" not in body
        assert "generate_text(" not in body

    def test_document_facade_is_a_thin_delegation_to_the_core(self):
        body = extract_function_source(_LEARNING_SRC, "document_discuss_chat(")
        assert "_learning_chat_core(" in body
        # discuss の応答生成・RAG・痕跡記録をファサード側に再実装していない
        for term in (
            "search_chunks_with_metadata(",
            "generate_text(",
            "record_interest_trace(",
            "persist_chat_history(",
            "_get_discuss_system_prompt(",
        ):
            assert term not in body, f"ファサードにコピペ実装: {term}"

    def test_discuss_system_prompt_is_defined_once(self):
        assert _LEARNING_SRC.count("def _get_discuss_system_prompt(") == 1

    def test_facade_forces_discuss_intent_mode(self):
        body = extract_function_source(_LEARNING_SRC, "document_discuss_chat(")
        assert 'body.intent_mode = "discuss"' in body

    def test_facade_drops_course_only_payloads(self):
        """§5.4 の縮退を黙って壊さず、サーバ側で明示的に無効化する。"""
        body = extract_function_source(_LEARNING_SRC, "document_discuss_chat(")
        for line in ("body.action = None", "body.atlas_context = None", "body.cycle_mode = None"):
            assert line in body, line


# ---------------------------------------------------------------------------
# 5. 情報を落とさない（CR8）
# ---------------------------------------------------------------------------


class TestNoHardDelete:
    def test_delete_route_uses_truncate_and_supersede(self):
        body = extract_function_source(_LEARNING_SRC, "delete_document_discussion_message_from(")
        assert "truncate_chat_and_supersede(" in body
        assert "DELETE FROM" not in body

    def test_no_document_discuss_row_deletion_endpoint(self):
        """痕跡・議論の行削除 API を新設していない。"""
        for fn in (
            "get_document_discussion_opening(",
            "document_discuss_chat(",
            "get_document_discussion_history(",
            "delete_document_discussion_message_from(",
            "_resolve_discuss_document(",
            "_document_discuss_course_data(",
            "_record_document_discuss_event(",
        ):
            body = extract_function_source(_LEARNING_SRC, fn)
            assert "DELETE FROM" not in body, fn
            assert "session.execute(" not in body, fn


# ---------------------------------------------------------------------------
# 6. 観測（設計 §5.5・DO1〜DO6）
# ---------------------------------------------------------------------------


class TestObservationVocabulary:
    def test_two_vocab_entries_registered(self):
        from core.discuss import observation

        assert "document_discuss_opened" in observation.METRIC_EVENT_VOCAB
        assert "document_discuss_turn" in observation.METRIC_EVENT_VOCAB

    def test_recorder_sends_empty_payload_only(self):
        """DO1: 本文非含有。payload は常に空で組み立てる。"""
        body = extract_function_source(_LEARNING_SRC, "_record_document_discuss_event(")
        assert '"payload": {}' in body
        assert "body.message" not in body

    def test_recorder_is_best_effort(self):
        """DO6: 計測失敗で UX を止めない。"""
        body = extract_function_source(_LEARNING_SRC, "_record_document_discuss_event(")
        assert "except Exception" in body
        assert "raise" not in body

    def test_document_routes_do_not_return_metric_counts_to_the_learner(self):
        """DO3: 学習者に数値を見せない。"""
        for fn in ("get_document_discussion_opening(", "document_discuss_chat("):
            body = extract_function_source(_LEARNING_SRC, fn)
            assert "recorded" not in body, fn


# ---------------------------------------------------------------------------
# 7. ラベルの正直さ（設計 §5.4 / CR4）
# ---------------------------------------------------------------------------


class TestOutOfCourseLabel:
    def test_label_constant_exists_and_says_out_of_course(self):
        assert 'DOCUMENT_DISCUSSION_TOPIC_LABEL = "論文との議論（コース外）"' in _LEARNING_SRC

    def test_label_conversion_happens_in_the_single_topic_title_branch(self):
        body = _core_body()
        idx = body.index("if topic_id == DISCUSSION_TOPIC_ID:")
        block = body[idx: idx + 700]
        assert "DOCUMENT_DISCUSSION_TOPIC_LABEL if _document_context_id" in block
        # ラベル分岐は1箇所だけ（プロンプト・痕跡・表示に一括で効く）
        assert body.count("DOCUMENT_DISCUSSION_TOPIC_LABEL") == 1


# ---------------------------------------------------------------------------
# 8. 設計書との対応（正本の存在確認）
# ---------------------------------------------------------------------------


class TestDesignDocAlignment:
    def test_design_doc_declares_the_sentinel_contract(self):
        doc = _read(DESIGN_DOC)
        assert "DOCUMENT_CONTEXT_PREFIX" in doc
        assert "parse_document_context" in doc
        assert "document_context_id" in doc

    def test_design_doc_declares_the_two_metric_events(self):
        doc = _read(DESIGN_DOC)
        assert "document_discuss_opened" in doc
        assert "document_discuss_turn" in doc
