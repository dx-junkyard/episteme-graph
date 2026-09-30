"""焦点論文（Focus Document）のガードレール（docs/features/focus_document_design.md FD1〜FD5）。

「どの論文についての往復・操作か」を決める条件式を新しく書かせない。正本は
``core/focus_document.py`` の1関数（段順）と1規則（優先）と1規則（ID 衝突）。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from tests.guardrail_helpers import assert_source_does_not_import, extract_function_source

BACKEND = Path(__file__).resolve().parents[1]
CORE_FOCUS = BACKEND / "core" / "focus_document.py"
LEARNING = BACKEND / "api" / "routes" / "learning.py"

#: 焦点論文を受け取る消費者（core.focus_document を import していなければならない）。
CONSUMERS = (
    BACKEND / "core" / "element_context.py",
    BACKEND / "core" / "component_context.py",
    BACKEND / "core" / "descent" / "resolve.py",
    BACKEND / "core" / "symbol_lookup.py",
    BACKEND / "core" / "personal_graph" / "nearby.py",
    BACKEND / "api" / "services.py",
    LEARNING,
)

#: 独自の「論文の優先」比較を書いた痕跡（focus_document.py の外では禁止）。
FORBIDDEN_PREFERENCE_PATTERNS = (
    "in topic_doc_ids",
    "in focus_document_ids",
    "for document_id in preferred:",
    "topic_source_document_ids(topic) &",
    'or 0.0) > best',
    '"document_id") or "") not in',
)


def _core_body() -> str:
    return extract_function_source(LEARNING.read_text(encoding="utf-8"), "_learning_chat_core")


class TestCorePurity:
    def test_focus_document_does_not_import_frameworks(self):
        src = CORE_FOCUS.read_text(encoding="utf-8")
        assert_source_does_not_import(src, ["fastapi", "sqlalchemy", "openai", "core.llm", "services"])

    def test_focus_document_has_no_sql(self):
        src = CORE_FOCUS.read_text(encoding="utf-8")
        for needle in ("SELECT", "sa_text", "get_session"):
            assert needle not in src


class TestSingleResolutionPoint:
    def test_chat_core_resolves_focus_exactly_once(self):
        assert _core_body().count("resolve_focus_document(") == 1

    def test_chat_core_uses_the_single_preference_rule(self):
        body = _core_body()
        assert "prefer_focus(chunk_results, _focus, limit=8)" in body
        # 旧来の2段（並べ替え → 除外）を core で直接組まない。
        assert "_prefer_topic_documents(" not in body
        assert "_drop_off_topic_chunks(" not in body

    def test_prerequisite_explanation_receives_focus(self):
        body = _core_body()
        idx = body.index("prereq_context = _resolve_prerequisite_context(")
        call = body[idx: idx + 900]
        assert "prefer_document_ids=_learner_focus(" in call

    def test_screen_context_receives_focus(self):
        body = _core_body()
        idx = body.index("_screen_block, _screen_has_ledger = _learning_screen_context_block(")
        assert "preferred_document_ids=_focus.document_ids" in body[idx: idx + 600]

    def test_resolve_focus_document_is_called_only_from_known_places(self):
        """API 層で resolve_focus_document を呼ぶのは chat core と services の1関数だけ。"""
        api_dir = BACKEND / "api"
        callers = []
        for path in sorted(api_dir.rglob("*.py")):
            src = path.read_text(encoding="utf-8")
            if "resolve_focus_document(" not in src:
                continue
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    seg = ast.get_source_segment(src, node) or ""
                    inner = [
                        n for n in ast.walk(node)
                        if isinstance(n, ast.Call)
                        and getattr(n.func, "id", getattr(n.func, "attr", "")) == "resolve_focus_document"
                    ]
                    if inner and seg:
                        callers.append(f"{path.name}:{node.name}")
        assert sorted(set(callers)) == ["learning.py:_learning_chat_core", "services.py:learner_focus_document"]


class TestNoAdHocPreference:
    def test_no_other_module_defines_document_preference(self):
        offenders = []
        for base in (BACKEND / "api", BACKEND / "core"):
            for path in sorted(base.rglob("*.py")):
                if path == CORE_FOCUS:
                    continue
                src = path.read_text(encoding="utf-8")
                for pattern in FORBIDDEN_PREFERENCE_PATTERNS:
                    if pattern in src:
                        offenders.append(f"{path.relative_to(BACKEND)}: {pattern!r}")
        assert not offenders, offenders

    def test_consumers_import_focus_document(self):
        for path in CONSUMERS:
            src = path.read_text(encoding="utf-8")
            assert "from core.focus_document import" in src, path

    def test_component_resolution_no_longer_takes_first_row_across_papers(self):
        src = (BACKEND / "core" / "component_context.py").read_text(encoding="utf-8")
        body = extract_function_source(src, "_resolve_component_row")
        assert "resolve_in_focus(" in body

    def test_descent_equation_uses_collision_rule(self):
        src = (BACKEND / "core" / "descent" / "resolve.py").read_text(encoding="utf-8")
        assert "resolve_in_focus(" in extract_function_source(src, "_resolve_equation")
        assert "resolve_in_focus(" in extract_function_source(src, "resolve_element")


class TestFocusIsNotPersisted:
    def test_focus_source_vocabulary_is_closed(self):
        from core.focus_document import FOCUS_SOURCES

        assert FOCUS_SOURCES == ("explicit", "topic", "previous_citation", "opening", "none")

    def test_trace_payload_does_not_store_focus_document_ids(self):
        """FD5: 痕跡に焦点の document_id 列を焼かない（記録してよいのは enum だけ）。"""
        body = _core_body()
        payload = body[body.index("_trace_payload = {"):]
        payload = payload[: payload.index("\n    }\n")]
        assert not re.search(r'"focus_document_ids"|"focus_documents"', payload)


class TestReviewGatesM4:
    """レビュー是正 M4: 焦点の段の入れ方が意図した統一を越えて挙動を変えない。"""

    def test_all_visible_document_discuss_has_no_explicit_tier(self):
        body = _core_body()
        start = body.index("_focus = resolve_focus_document(")
        call = body[start: body.index("_focus_doc_ids = set(_focus.document_ids)", start)]
        assert 'not (_is_discuss and _discuss_scope == "all_visible")' in call

    def test_previous_citation_tier_is_discuss_only(self):
        body = _core_body()
        start = body.index("_focus = resolve_focus_document(")
        call = body[start: body.index("_focus_doc_ids = set(_focus.document_ids)", start)]
        seg = call[call.index("previous_cited_document_ids="):]
        seg = seg[: seg.index("opening_document_id=")]
        assert "if _is_discuss" in seg and "else ()" in seg

    def test_learner_focus_previous_citation_is_discussion_only(self):
        src = (BACKEND / "api" / "services.py").read_text(encoding="utf-8")
        fn = extract_function_source(src, "learner_focus_document")
        assert 'tid == "_discussion"' in fn

    def test_learning_advice_passes_opening_tier(self):
        body = _core_body()
        start = body.index("prefer_document_ids=_learner_focus(")
        seg = body[start: body.index(").document_ids", start)]
        assert "opening_document_id=" in seg
