"""HELP KB（docs/manual の AI アシスタント知識源化）ガードレールテスト。

正本: `docs/features/manual_help_kb_design.md` §6「新設ガードレールテスト一覧」。
`guardrail_helpers.py`（`assert_module_tree_does_not_import` / `assert_source_forbids` /
`extract_function_source`）と既存パターン（`test_next_steps_guardrails.py` の
ソース検査スタイル）を踏襲する。

§6-10（k=3 再定義禁止）と §6-14（G層整合）は Phase 2 実装済みの G層ルール3本
（`manual.help_gaps_pending` / `assistant_kb.undocumented` / `manual.todo_unresolved`）
に対する検証であるため、本ファイルではなく `backend/tests/test_next_steps_guardrails.py`
（`TestHelpGapsPendingUsesPrivacyModule` / `TestHelpKbRulesRegistered` 等）で担保する。

本ファイルは「動く」ことの確認（test_help_kb.py / test_help_usage_route.py）とは別に、
構造的な不変条項（audience 越境禁止・fail-closed・情報を落とさない・数値非表示・
権限 fail-closed・chunks 非汚染）を横断的に検証する。
"""

from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.admin_assistant import capabilities as caps  # noqa: E402
from core.admin_assistant.schema import role_rank  # noqa: E402
from core.help_kb import index as kb_index  # noqa: E402
from core.help_kb import manual as kb_manual  # noqa: E402
from core.help_kb import validator as kb_validator  # noqa: E402
from tests.guardrail_helpers import (  # noqa: E402
    assert_module_tree_does_not_import,
    assert_module_tree_forbids,
    assert_paths_forbid,
    assert_source_forbids,
    extract_function_source,
)

HELP_KB_DIR = BACKEND / "core" / "help_kb"
MANUAL_DIR = ROOT / "docs" / "manual"
ADMIN_OPS_DIR = ROOT / "docs" / "admin_operations"

_MANUAL_PY_SRC = (HELP_KB_DIR / "manual.py").read_text(encoding="utf-8")
_ADMIN_ASSISTANT_KNOWLEDGE_SRC = (
    BACKEND / "core" / "admin_assistant" / "knowledge.py"
).read_text(encoding="utf-8")
_LEARNING_PY_SRC = (BACKEND / "api" / "routes" / "learning.py").read_text(encoding="utf-8")
_SERVICES_PY_SRC = (BACKEND / "api" / "services.py").read_text(encoding="utf-8")
_PERSONAL_GRAPH_QUERIES_SRC = (
    BACKEND / "core" / "personal_graph" / "queries.py"
).read_text(encoding="utf-8")
_PERSONAL_GRAPH_DERIVE_SRC = (
    BACKEND / "core" / "personal_graph" / "derive.py"
).read_text(encoding="utf-8")
_TENSION_WORKER_SRC = (BACKEND / "core" / "tension" / "worker.py").read_text(encoding="utf-8")
_STRUCTURE_ANCHOR_WORKER_SRC = (
    BACKEND / "core" / "structure_anchor" / "worker.py"
).read_text(encoding="utf-8")
_DOCKERFILE_SRC = (BACKEND / "Dockerfile").read_text(encoding="utf-8")

_DENYLIST_TERMS = (
    "ADMIN_PASSWORD",
    "JWT_SECRET",
    "OPENAI_API_KEY",
    "admin.html",
    "localhost:8001",
    "localhost:9001",
    "/api/admin",
    "_require_",
    "MAX_CALLS_PER_DAY",
)

# 改変履歴（開発経緯）の言い回し。機能語彙としての「廃止（retired 状態への遷移）」は
# 正当なので含めない — 「以前は〜だった」型の履歴表現だけを列挙する。
_REVISION_HISTORY_TERMS = (
    "以前あった",
    "かつては",
    "旧仕様",
    "従来どおり",
    "は廃止され",
    "廃止されました",
    "撤去済み",
    "統合されました",
    "に変更されました",
    "追加されました",
    "新しくなりました",
)


@pytest.fixture(autouse=True)
def _clear_manual_cache():
    kb_manual.clear_manual_cache()
    yield
    kb_manual.clear_manual_cache()


# ===========================================================================
# 1. audience 越境禁止（最重要）
# ===========================================================================


class TestAudienceBoundary:
    def test_student_index_builder_source_never_references_other_audiences(self):
        """学生索引ビルダーのソースが teacher/system_admin ディレクトリを参照しない。"""
        fn_src = extract_function_source(_MANUAL_PY_SRC, "_student_index")
        assert_source_forbids(fn_src, ["teacher", "system_admin"], context="_student_index")

    def test_built_student_index_provenance_is_student_only(self):
        """実 docs で構築した学生索引の全節が docs/manual/student/ 由来のファイルのみ。"""
        student_dir = MANUAL_DIR / "student"
        assert student_dir.is_dir(), "docs/manual/student/ が見つからない"
        student_filenames = {p.name for p in student_dir.glob("*.md")}

        idx, _excluded = kb_manual._student_index()
        assert idx, "学生索引が空 — 実 docs から構築できていない"
        for key, section in idx.items():
            assert section["file"] in student_filenames, (
                f"{key}: file={section['file']!r} は docs/manual/student/ に無い"
            )
            assert section.get("audience") == "student"

    def test_search_manual_student_citations_are_all_from_student_dir(self):
        """search_manual(audience='student') の全 citation が manual/student/ 始まり。"""
        queries = [
            "画面の使い方を教えて",
            "音声モードの使い方",
            "システム概要",
            "前提知識",
            "ヘルプ",
        ]
        seen_any = False
        for q in queries:
            for hit in kb_manual.search_manual(q, audience="student", limit=5):
                seen_any = True
                assert hit["citation"].startswith("manual/student/"), hit["citation"]
        assert seen_any, "検証対象の citation が1件も得られなかった（クエリ調整が必要）"


# ===========================================================================
# 2. denylist（student/ 配下に運用機微・権限内部名を漏らさない）
# ===========================================================================


class TestDenylist:
    def test_student_docs_contain_no_denylisted_terms(self):
        student_dir = MANUAL_DIR / "student"
        assert student_dir.is_dir()
        violations: list[str] = []
        for md in sorted(student_dir.glob("*.md")):
            text = md.read_text(encoding="utf-8")
            for term in _DENYLIST_TERMS:
                if term in text:
                    violations.append(f"{md.name}: {term!r}")
        assert violations == [], f"denylist violations: {violations}"

    def test_manuals_contain_no_revision_history_narrative(self):
        """マニュアルは「いまの使い方・できること」だけを書く（改変履歴を書かない）。

        マニュアルは AI アシスタントの知識源であり、節本文がそのままツールチップ・
        HELP 回答として利用者に出る。「以前あった〇〇は廃止され」のような開発経緯は
        利用者にとって不要な情報なので、機能語彙としての「廃止（retire 操作）」とは
        区別して、履歴の言い回しだけを構造的に禁止する。
        """
        assert MANUAL_DIR.is_dir()
        violations: list[str] = []
        for md in sorted(MANUAL_DIR.rglob("*.md")):
            text = md.read_text(encoding="utf-8")
            for term in _REVISION_HISTORY_TERMS:
                if term in text:
                    violations.append(f"{md.relative_to(MANUAL_DIR)}: {term!r}")
        assert violations == [], f"revision-history narrative in manuals: {violations}"


# ===========================================================================
# 3. audience 引数必須の fail-closed
# ===========================================================================


class TestAudienceRequiredKeyword:
    def test_search_manual_audience_is_keyword_only_without_default(self):
        sig = inspect.signature(kb_manual.search_manual)
        audience_param = sig.parameters.get("audience")
        assert audience_param is not None, "search_manual に audience 引数が無い"
        assert audience_param.kind == inspect.Parameter.KEYWORD_ONLY, (
            f"audience は KEYWORD_ONLY であるべき（現状 {audience_param.kind}）"
        )
        assert audience_param.default is inspect.Parameter.empty, (
            "audience にデフォルト値があってはならない（呼び忘れで漏洩する）"
        )

    def test_search_manual_rejects_positional_audience(self):
        with pytest.raises(TypeError):
            kb_manual.search_manual("query", "student")  # type: ignore[misc]

    def test_learning_py_usage_help_route_uses_literal_student_audience(self):
        """learning.py の HELP ハンドラが audience='student' をリテラルで固定していること。"""
        fn_src = extract_function_source(_LEARNING_PY_SRC, "_usage_help_response")
        assert 'audience="student"' in fn_src


# ===========================================================================
# 4. TODO 凍結拒否
# ===========================================================================


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestTodoFreezeRejection:
    def test_todo_marked_chunk_is_excluded_from_index(self, tmp_path):
        _write(
            tmp_path / "x.md",
            (
                "## 節A {#a}\n本文A\n\n"
                "## 節B {#b}\n本文B <!-- TODO: 要確認 -->\n\n"
                "## 節C {#c}\n本文C\n"
            ),
        )
        index, excluded = kb_index.build_section_index(
            tmp_path, manual_transforms=True, audience_tag="student"
        )
        assert "x.md#b" not in index
        assert "x.md#a" in index and "x.md#c" in index
        assert any(e["file"] == "x.md" and e["anchor"] == "b" for e in excluded)

    @pytest.mark.parametrize("audience", ["student", "teacher", "system_admin"])
    def test_real_docs_index_has_no_leftover_markers(self, audience):
        """実 docs から構築した索引（3 audience）の全 body に TODO/コメント/生テーブル行が残らない。"""
        directory = MANUAL_DIR / audience
        if not directory.is_dir():
            pytest.skip(f"docs/manual/{audience}/ が無い")
        index, _excluded = kb_index.build_section_index(
            directory, manual_transforms=True, audience_tag=audience
        )
        assert index, f"{audience} 索引が空"
        table_row_re = re.compile(r"^\s*\|[-:\s|]+\|\s*$", re.MULTILINE)
        for key, section in index.items():
            body = section["body"]
            assert "TODO" not in body, f"{key}: TODO が残存"
            assert "<!--" not in body, f"{key}: HTML コメントが残存"
            assert not table_row_re.search(body), f"{key}: 生テーブル区切り行が残存"


# ===========================================================================
# 5. HELP ルートの痕跡非汚染（構造検査）
# ===========================================================================


class TestHelpRouteIsolation:
    def test_usage_help_response_never_calls_other_pipelines(self):
        fn_src = extract_function_source(_LEARNING_PY_SRC, "_usage_help_response")
        assert_source_forbids(
            fn_src,
            ["judge_tension_hint(", "detect_and_record_misconception(", "check_prerequisites("],
            context="_usage_help_response",
        )

    def test_usage_help_response_does_not_pass_verbatim_message_to_record_interest_trace(self):
        """HELP ハンドラの record_interest_trace 呼び出しに body.message（逐語）を渡していない。"""
        fn_src = extract_function_source(_LEARNING_PY_SRC, "_usage_help_response")
        assert "text=body.message" not in fn_src

    def test_prerouting_precedes_casual_bypass(self):
        """casual バイパス（_is_casual 判定）より手前に HELP pre-route が置かれている。"""
        pre_route_idx = _LEARNING_PY_SRC.index("_usage_help_response(")
        # 呼び出し箇所（learning_chat 内の pre-route）を、定義箇所より後ろから探す。
        call_idx = _LEARNING_PY_SRC.index(
            "_usage_help_response(", _LEARNING_PY_SRC.index("def learning_chat(")
        )
        casual_assign_idx = _LEARNING_PY_SRC.index('_is_casual = (body.intent_mode or "").strip()')
        assert call_idx < casual_assign_idx, (
            "HELP pre-route の呼び出しが _is_casual 判定より後ろにある"
            "（音声・casual ユーザーに構造的に届かない）"
        )
        assert pre_route_idx < casual_assign_idx


# ===========================================================================
# 6. help_usage の消費者排除
# ===========================================================================


class TestHelpUsageExclusion:
    def test_personal_graph_queries_do_not_select_help_usage(self):
        assert_source_forbids(
            _PERSONAL_GRAPH_QUERIES_SRC, ["help_usage"], context="personal_graph/queries.py"
        )

    def test_personal_graph_derive_does_not_branch_on_help_usage(self):
        assert_source_forbids(
            _PERSONAL_GRAPH_DERIVE_SRC, ["help_usage"], context="personal_graph/derive.py"
        )

    def test_tension_worker_does_not_reference_help_usage(self):
        assert_source_forbids(_TENSION_WORKER_SRC, ["help_usage"], context="tension/worker.py")

    def test_structure_anchor_worker_does_not_reference_help_usage(self):
        assert_source_forbids(
            _STRUCTURE_ANCHOR_WORKER_SRC, ["help_usage"], context="structure_anchor/worker.py"
        )

    def test_get_interest_traces_explicitly_excludes_help_usage(self):
        fn_src = extract_function_source(_SERVICES_PY_SRC, "get_interest_traces")
        assert "help_usage" in fn_src, (
            "get_interest_traces が help_usage を明示除外していない（設計 §1-3-8）"
        )
        assert "<> 'help_usage'" in fn_src or "!= 'help_usage'" in fn_src

    def test_usage_help_handler_does_not_persist_verbatim_question_in_payload(self):
        fn_src = extract_function_source(_LEARNING_PY_SRC, "_usage_help_response")
        # record_interest_trace(... text=..., extra_payload={...}) の呼び出しに
        # body.message をそのまま渡していないこと（P3: 質問逐語を積まない）。
        calls = re.findall(r"record_interest_trace\(.*?\)\n", fn_src, re.DOTALL)
        assert calls, "record_interest_trace 呼び出しが見つからない"
        for call in calls:
            assert "body.message" not in call, f"逐語 body.message が渡されている: {call}"


# ===========================================================================
# 7. Docker 同梱の回帰防止
# ===========================================================================


class TestDockerfileCopies:
    def test_dockerfile_copies_manual_and_admin_operations_only(self):
        assert "COPY docs/admin_operations/" in _DOCKERFILE_SRC
        assert "COPY docs/manual/" in _DOCKERFILE_SRC

    def test_dockerfile_does_not_copy_whole_docs_or_features(self):
        assert not re.search(r"COPY\s+docs/\s", _DOCKERFILE_SRC), (
            "docs/ 全体を COPY している（開発文書がイメージに混入する）"
        )
        assert "docs/features" not in _DOCKERFILE_SRC


# ===========================================================================
# 8. front-matter 契約 + anchor 整合
# ===========================================================================


class TestFrontMatterContract:
    def test_validate_manual_returns_no_violations_on_real_docs(self):
        violations = kb_validator.validate_manual()
        assert violations == [], f"validate_manual violations: {violations}"

    def test_admin_operations_role_does_not_contradict_capability_required_role(self):
        """admin_operations の front-matter role が、同一 screen の capability の
        うち最も緩い required_role と矛盾しない。"""
        violations: list[str] = []
        for md in sorted(ADMIN_OPS_DIR.glob("*.md")):
            lines = md.read_text(encoding="utf-8").splitlines()
            front_matter, _body = kb_index.parse_front_matter(lines)
            screen = front_matter.get("screen")
            declared_role = front_matter.get("role")
            if not screen or not declared_role:
                violations.append(f"{md.name}: front-matter に screen/role が無い")
                continue
            matching = [c for c in caps.all_capabilities() if c.screen == screen]
            if not matching:
                # このファイルの screen に対応する capability が無ければ判定不能
                # （矛盾は起きようがないので許容する）。
                continue
            most_lenient_rank = min(role_rank(c.required_role) for c in matching)
            declared_rank = role_rank(declared_role)
            if declared_rank != most_lenient_rank:
                violations.append(
                    f"{md.name}: front-matter role={declared_role!r}(rank={declared_rank}) "
                    f"が screen={screen!r} の最も緩い required_role(rank={most_lenient_rank}) と不一致"
                )
        assert violations == [], violations

    def test_all_manual_headings_have_explicit_anchor(self):
        """全 audience の全見出しに明示 {#anchor} がある（validate_manual がカバー済みだが
        単独でも確認する）。"""
        for audience_dir in MANUAL_DIR.iterdir():
            if not audience_dir.is_dir() or audience_dir.name == "README.md":
                continue
            for md in sorted(audience_dir.glob("*.md")):
                for line in md.read_text(encoding="utf-8").splitlines():
                    m = kb_index.HEADING_RE.match(line)
                    if not m:
                        continue
                    assert kb_index.ANCHOR_RE.search(m.group(2)), (
                        f"{audience_dir.name}/{md.name}: 見出し「{m.group(2)}」に "
                        "明示 {#anchor} が無い"
                    )


# ===========================================================================
# 9. 数値非表示
# ===========================================================================


class TestNoNumericLeak:
    def test_search_manual_results_have_no_confidence_or_score_keys(self):
        hits = kb_manual.search_manual("音声モードの使い方", audience="student", limit=3)
        assert hits, "検証対象のヒットが0件"
        for hit in hits:
            assert "confidence" not in hit
            assert "score" not in hit

    def test_usage_help_manual_citations_construction_has_no_numeric_keys(self):
        fn_src = extract_function_source(_LEARNING_PY_SRC, "_usage_help_response")
        assert_source_forbids(fn_src, ["confidence", "score"], context="_usage_help_response")


# ===========================================================================
# 11. 構造規約
# ===========================================================================


class TestStructuralRules:
    def test_help_kb_does_not_import_fastapi(self):
        assert_module_tree_does_not_import(HELP_KB_DIR, ["fastapi"])

    def test_help_kb_has_no_delete_or_remove_public_api(self):
        # "def delete" / "def remove" (a dedicated deletion API) remain forbidden
        # across the whole help_kb tree, including vector.py.
        forbidden_defs = ("def delete", "def remove")
        assert_module_tree_forbids(HELP_KB_DIR, forbidden_defs)

    def test_help_kb_forbids_delete_from_outside_vector_sync(self):
        """``DELETE FROM`` は ``vector.py`` の全置換スナップショット同期にのみ許可する。

        設計書 §5 Phase 3 ②（manual_help_kb_design.md）は「シード = 全置換
        スナップショット。現行ファイル集合に無い (audience, file, anchor) 行を
        同一トランザクションで DELETE する」ことを明示的に要求している
        （孤児行が存在しない機能の古い説明を返すドリフトの遮断）。これは
        「削除 API」ではなく同期処理の内部実装であるため、他の help_kb
        モジュールでは従来どおり ``DELETE FROM`` を禁止しつつ、``vector.py``
        だけを対象外にする（ガードレールの意図を弱めるのではなく、設計書の
        明示要求に合わせて対象を最小限に絞る）。
        """
        other_paths = [p for p in HELP_KB_DIR.glob("*.py") if p.name != "vector.py"]
        assert_paths_forbid(other_paths, ["DELETE FROM"])


# ===========================================================================
# 12. 委譲互換
# ===========================================================================


class TestDelegationCompat:
    def test_admin_assistant_knowledge_delegates_to_help_kb(self):
        assert "from core.help_kb import" in _ADMIN_ASSISTANT_KNOWLEDGE_SRC
        assert "core.help_kb" in _ADMIN_ASSISTANT_KNOWLEDGE_SRC

    def test_admin_assistant_knowledge_public_signature_unchanged(self):
        from core.admin_assistant import knowledge as admin_kb

        for name in ("search", "section_for_howto", "clear_cache", "kb_available"):
            assert hasattr(admin_kb, name), f"admin_assistant/knowledge.py に {name} が無い"


# ===========================================================================
# 13. chunks 非汚染
# ===========================================================================


class TestChunksIsolation:
    def test_help_kb_never_references_chunks_table_or_search(self):
        assert_module_tree_forbids(HELP_KB_DIR, ["chunks", "search_chunks_with_metadata"])
