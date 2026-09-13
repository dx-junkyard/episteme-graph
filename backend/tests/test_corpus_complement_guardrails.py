"""コーパスを補う論文 — 層横断ガードレール（設計書 ``corpus_complement_design.md`` §7）。

ここで固定するのは**構造**であり、振る舞いの検査は
``test_corpus_complement_core.py`` / ``test_corpus_complement_foundation.py`` /
``test_corpus_complement_api.py`` / ``test_corpus_complement_ui_static.py`` 側。

検査項目（不変条項 CC1〜CC8 のうち構造で守れるもの）:

- core 3ファイル（``complement.py`` / ``foundation.py`` / ``reference_cache.py``）が
  FastAPI・``core.llm``・``core.url_fetch``・``routes`` を import しない
  （CC2 — 発見層の LLM 接触 allowlist は ``ranking.py`` / ``compare.py`` の2本のまま）
- **CC1**: 補完のレンズが学習者信号（``interest_traces`` / ``frontier_interest`` /
  つまづき）に触れない
- **CC5**: SL1 の denylist 語彙（「この分野では未検証」「誰も検証していない」
  「世界初」「未踏」）が core・フロント・マニュアル節の**利用者に見える文字列**に
  無く、固定文が原文のまま存在する
- **CC6**: 断定語（「おすすめ」「必読」「重要な論文」）が同じ範囲に無い
- **CC3**: migration 077 が INSERT なし・users FK なし・DELETE なし・冪等ガードあり・
  予約語を避けた列名（``reference_entries``）である
- 2ルートが ``_require_teacher`` を通り、監査を記帳せず（CC3）、パッケージの
  ``__init__`` 再エクスポートに依存せずサブモジュールを直接 import している

**利用者に見える文字列**の判定: Python はコメント・docstring を除いた文字列リテラル、
JS は行・ブロックコメントを除いた本文を対象にする（禁止語彙を「書かない」ことを
説明する注記そのものが違反になるのを避けるため。マニュアルは全文が利用者向けの
文章なのでそのまま検査する）。
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api")):
    if _path not in sys.path:
        sys.path.insert(0, str(_path))

from tests.guardrail_helpers import (  # noqa: E402
    assert_module_tree_does_not_import,
    assert_source_forbids,
    extract_function_source,
    read_migration_sql,
)

CORE_DIR = BACKEND / "core" / "paper_discovery"
COMPLEMENT_SOURCE = CORE_DIR / "complement.py"
FOUNDATION_SOURCE = CORE_DIR / "foundation.py"
REFERENCE_CACHE_SOURCE = CORE_DIR / "reference_cache.py"
RANKING_SOURCE = CORE_DIR / "ranking.py"
ROUTE_SOURCE = BACKEND / "api" / "routes" / "paper_discovery.py"
DISCOVERY_JS = ROOT / "frontend" / "public" / "js" / "admin-paper-discovery.js"
MANUAL_DOC = ROOT / "docs" / "manual" / "teacher" / "11-admin-materials.md"

#: 本層が新設した core ファイル（3本）。
LAYER_SOURCES = (COMPLEMENT_SOURCE, FOUNDATION_SOURCE, REFERENCE_CACHE_SOURCE)

MIGRATION_NUMBER = 77

#: SL1 の denylist（賭け金の台帳から継承。台帳はコーパスの射影であって分野の射影ではない）。
CLOSED_WORLD_DENYLIST = (
    "この分野では未検証",
    "誰も検証していない",
    "世界初",
    "未踏",
)

#: CC6 — 候補を「良い論文」と断定する語。
ASSERTIVE_DENYLIST = ("おすすめ", "必読", "重要な論文")

#: CC1 — 学習者信号（選定入力にしない）。
LEARNER_SIGNAL_TERMS = ("interest_traces", "frontier_interest", "stumble")

_ROUTE_SRC = ROUTE_SOURCE.read_text(encoding="utf-8")
_JS_COMMENT_RE = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)
_SQL_LINE_COMMENT_RE = re.compile(r"--[^\n]*")


def _strip_docstrings(tree: ast.AST) -> ast.AST:
    """モジュール / クラス / 関数の docstring を取り除いた AST を返す。"""
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = list(getattr(node, "body", None) or [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:] or [ast.Pass()]
    return ast.fix_missing_locations(tree)


def _python_code_text(path: Path) -> str:
    """Python ソースの**実コード**（コメント・docstring を除く）を文字列で返す。

    「学習者信号に触れない」のような**参照の不在**は、設計注記の文面ではなく
    実コードで検査する（禁止対象を説明する docstring が違反にならないように）。
    """
    return ast.unparse(_strip_docstrings(ast.parse(path.read_text(encoding="utf-8"))))


def _sql_without_comments(sql: str) -> str:
    """SQL から ``--`` 行コメントを取り除く（設計注記を DDL と混同しない）。"""
    return _SQL_LINE_COMMENT_RE.sub(" ", sql)


def _python_visible_strings(path: Path) -> str:
    """Python ソースのうち**利用者に見え得る文字列リテラル**だけを連結して返す。

    コメントと docstring（モジュール / クラス / 関数の先頭文）は除く。禁止語彙を
    「書かない」ことを説明する設計注記が、検査そのものに引っかからないようにする。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    docstrings.add(id(body[0].value))
    parts = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]
    return "\n".join(parts)


def _js_without_comments(path: Path) -> str:
    return _JS_COMMENT_RE.sub(" ", path.read_text(encoding="utf-8"))


def _visible_texts() -> dict[str, str]:
    """禁止語彙を検査する「利用者に見える文字列」の集合（出所つき）。"""
    texts = {
        str(path): _python_visible_strings(path)
        for path in (*LAYER_SOURCES, RANKING_SOURCE)
    }
    texts[str(DISCOVERY_JS)] = _js_without_comments(DISCOVERY_JS)
    texts[str(MANUAL_DOC)] = MANUAL_DOC.read_text(encoding="utf-8")
    return texts


# ---------------------------------------------------------------------------
# 1. core の独立性（開発ルール2 / CC2 の LLM 接触点）
# ---------------------------------------------------------------------------


class TestCoreIsolation:
    def test_layer_sources_exist(self):
        for path in LAYER_SOURCES:
            assert path.exists(), f"missing core module: {path}"

    def test_core_does_not_import_fastapi_or_llm(self):
        """CC2 — 3ファイルは FastAPI にも ``core.llm`` にも触れない。

        embedding は ``ranking.py`` の既存1バッチに相乗りするので、本層は
        ``core.llm`` 接触の allowlist を増やさない。
        """
        for path in LAYER_SOURCES:
            src = path.read_text(encoding="utf-8")
            assert_source_forbids(
                src,
                [
                    "import fastapi",
                    "from fastapi",
                    "import core.llm",
                    "from core.llm",
                    "import openai",
                    "from openai",
                ],
                context=str(path),
            )

    def test_core_does_not_import_fetch_or_routes(self):
        """PD2 — 論文の取得・受理経路（url_fetch / routes）に core が触れない。"""
        for path in LAYER_SOURCES:
            assert_source_forbids(
                path.read_text(encoding="utf-8"),
                [
                    "core.url_fetch",
                    "from routes",
                    "import routes",
                    "_accept_material_source",
                    "import requests",
                ],
                context=str(path),
            )

    def test_core_tree_still_isolated_from_fastapi(self):
        """パッケージ全体の不変（新モジュールが例外を作っていないこと）。"""
        assert_module_tree_does_not_import(CORE_DIR, ["fastapi"])

    def test_llm_exempt_allowlist_is_unchanged(self):
        """発見層で ``core.llm`` に触れてよいのは既存の2本だけ（CC2）。"""
        import tests.test_paper_discovery_guardrails as pd_guardrails

        assert pd_guardrails.LLM_EXEMPT_FILES == ("ranking.py", "compare.py")


# ---------------------------------------------------------------------------
# 2. CC1 — 補完の根拠はコーパス構造のみ（学習者信号を混ぜない）
# ---------------------------------------------------------------------------


class TestNoLearnerSignals:
    def test_lenses_do_not_touch_learner_traces(self):
        """CR10 / IG2 を構造として守る（関心・つまづき・違和感を選定入力にしない）。"""
        offending = [
            f"{path}:{term!r}"
            for path in LAYER_SOURCES
            for term in LEARNER_SIGNAL_TERMS
            if term in _python_code_text(path)
        ]
        assert offending == [], f"learner signals referenced: {offending}"

    def test_lenses_read_only_corpus_structures(self):
        """レンズが読むのは配置・台帳・引用関係だけ（表名の allowlist）。"""
        src = _python_code_text(COMPLEMENT_SOURCE)
        assert "landscape_placements" in src
        assert "epistemic_ledger" in src
        assert_source_forbids(
            src,
            ["interest_traces", "discuss_metric_events", "learning_states"],
            context=str(COMPLEMENT_SOURCE),
        )


# ---------------------------------------------------------------------------
# 3. CC5 — 閉世界語彙の固定
# ---------------------------------------------------------------------------


class TestClosedWorldVocabulary:
    def test_fixed_sentence_is_verbatim(self):
        from core.paper_discovery import complement

        assert (
            complement.CLOSED_WORLD_SKY_NOTE
            == "このコーパスの中では検証記録がありません"
        )

    def test_fixed_sentence_is_the_only_source_of_the_note(self):
        """事実文の組み立てを呼び出し側へ散らさない（core が唯一の出所）。"""
        note = "このコーパスの中では検証記録がありません"
        assert note in COMPLEMENT_SOURCE.read_text(encoding="utf-8")
        # フロントは server の ``closed_world_note`` をそのまま描く（文言を持たない）。
        assert note not in _js_without_comments(DISCOVERY_JS)

    def test_denylist_is_absent_from_visible_text(self):
        offending = [
            f"{origin}:{word!r}"
            for origin, text in _visible_texts().items()
            for word in CLOSED_WORLD_DENYLIST
            if word in text
        ]
        assert offending == [], f"closed-world denylist found: {offending}"


# ---------------------------------------------------------------------------
# 4. CC6 — 断定しない（推定であることを剥がさない）
# ---------------------------------------------------------------------------


class TestNoAssertiveVocabulary:
    def test_assertive_words_are_absent_from_visible_text(self):
        offending = [
            f"{origin}:{word!r}"
            for origin, text in _visible_texts().items()
            for word in ASSERTIVE_DENYLIST
            if word in text
        ]
        assert offending == [], f"assertive vocabulary found: {offending}"


# ---------------------------------------------------------------------------
# 5. CC3 — migration 077（外部事実のキャッシュ1表だけ）
# ---------------------------------------------------------------------------


class TestMigration:
    def _sql(self) -> str:
        """DDL 本体（``--`` の設計注記は取り除く）。"""
        return _sql_without_comments(read_migration_sql(BACKEND, MIGRATION_NUMBER))

    def test_creates_the_cache_table_idempotently(self):
        sql = self._sql()
        assert "CREATE TABLE IF NOT EXISTS paper_discovery_reference_cache" in sql
        assert "CREATE TABLE " in sql
        assert sql.count("CREATE TABLE") == sql.count("CREATE TABLE IF NOT EXISTS")

    def test_does_not_seed_rows(self):
        """毎起動・全再実行方式なのでシード行を入れない（070 と同じ判断）。"""
        assert "INSERT" not in self._sql().upper()

    def test_has_no_user_foreign_key(self):
        """users を参照しない = account_lifecycle の PURGE/RETAIN 宣言が要らない。"""
        upper = self._sql().upper()
        assert "REFERENCES USERS" not in upper
        assert "REFERENCES " not in upper.replace("REFERENCE_ENTRIES", "")

    def test_has_no_delete(self):
        """更新は upsert のみ（行削除しない — P4 / CC3）。"""
        assert "DELETE" not in self._sql().upper()

    def test_column_avoids_reserved_word(self):
        """``references`` は予約語なので列名は ``reference_entries``。"""
        sql = self._sql()
        assert "reference_entries JSONB" in sql
        assert not re.search(r"^\s+references\s+JSONB", sql, re.MULTILINE)

    def test_cache_module_writes_only_upserts(self):
        src = REFERENCE_CACHE_SOURCE.read_text(encoding="utf-8")
        assert "ON CONFLICT" in src
        assert_source_forbids(
            src, ["DELETE FROM", "DROP TABLE", "TRUNCATE"], context=str(REFERENCE_CACHE_SOURCE)
        )


# ---------------------------------------------------------------------------
# 6. API 契約（2ルート）
# ---------------------------------------------------------------------------


ROUTE_FUNCTIONS = ("complement_search_candidates", "complement_foundation_candidates")


class TestRouteContract:
    def test_routes_are_registered(self):
        from api.main import app
        from tests.guardrail_helpers import collect_route_pairs

        pairs = collect_route_pairs(app)
        assert ("/api/admin/discovery/complement/search", "POST") in pairs
        assert ("/api/admin/discovery/complement/foundation", "POST") in pairs

    def test_routes_require_teacher(self):
        for name in ROUTE_FUNCTIONS:
            body = extract_function_source(_ROUTE_SRC, name)
            assert "_require_teacher" in body, f"{name} must depend on _require_teacher"

    def test_routes_do_not_record_audit(self):
        """CC3 — 書き込みは外部事実のキャッシュだけなので監査対象にしない。"""
        for name in ROUTE_FUNCTIONS:
            body = extract_function_source(_ROUTE_SRC, name)
            assert_source_forbids(
                body,
                ["record_review_event", "AUDIT_ENTITY_PAPER_DISCOVERY"],
                context=name,
            )

    def test_routes_do_not_call_the_llm(self):
        """CC2 — ルート層も LLM に触れない（埋め込みは ranking の1バッチのみ）。"""
        for name in ROUTE_FUNCTIONS + ("_apply_complement_order",):
            body = extract_function_source(_ROUTE_SRC, name)
            assert_source_forbids(
                body, ["core.llm", "generate_", "openai"], context=name
            )

    def test_routes_do_not_ingest(self):
        """CC7 — 補完の2本は取得・受理経路に触れない（取り込みは既存の弁のみ）。"""
        for name in ROUTE_FUNCTIONS:
            body = extract_function_source(_ROUTE_SRC, name)
            assert_source_forbids(
                body,
                ["_accept_material_source", "fetch_source_from_url", "enqueue_items"],
                context=name,
            )

    def test_core_is_imported_as_submodules(self):
        """パッケージ ``__init__`` の再エクスポートに依存しない（import 面を固定）。"""
        assert (
            "from core.paper_discovery import complement as pd_complement" in _ROUTE_SRC
        )
        assert (
            "from core.paper_discovery import foundation as pd_foundation" in _ROUTE_SRC
        )
        assert_source_forbids(
            _ROUTE_SRC,
            [
                "from core.paper_discovery import build_complement_context",
                "from core.paper_discovery import run_foundation_search",
                "from core.paper_discovery import reference_cache",
            ],
            context=str(ROUTE_SOURCE),
        )

    def test_foundation_error_detail_is_the_shared_fixed_sentence(self):
        """502 の事実文は引用グラフ供給と同じ固定文（内部情報を載せない）。"""
        body = extract_function_source(_ROUTE_SRC, "complement_foundation_candidates")
        assert "_DETAIL_CITATION_UNAVAILABLE" in body
        assert "str(exc)" not in body

    def test_foundation_commits_the_cache_upsert(self):
        body = extract_function_source(_ROUTE_SRC, "complement_foundation_candidates")
        assert "session.commit()" in body
        assert "session.rollback()" in body

    def test_no_learner_facing_complement_routes(self):
        """CC8 / CR7 — 学習者向けの補完ルートを作らない。"""
        from api.main import app
        from tests.guardrail_helpers import collect_route_pairs

        offending = [
            path
            for path, _method in collect_route_pairs(app)
            if path.startswith("/api/learning") and "complement" in path
        ]
        assert offending == []
