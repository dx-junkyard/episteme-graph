"""論文ディスカバリー層のガードレール（設計書 §4.7）。

正本は ``docs/features/paper_discovery_design.md``（不変条項 PD1〜PD8）。
ここで固定するのは**構造**であり、振る舞いの検査は
``test_paper_discovery_core.py`` 側。

検査項目（設計書 §4.7 のうち Phase 1 core / migration が担う分）:

- ``core/paper_discovery/`` が FastAPI / ``core.llm`` を import しない（開発ルール2・
  発見層は LLM 0回）
- ``store.py`` に ``DELETE FROM`` が無い（見送りは ``revoked`` 遷移 — P4 / PD5）
- ``arxiv_client.py`` にスロットル実装が存在し、宛先が ``export.arxiv.org`` 定数である（PD7）
- ``run_search`` の DTO に数値スコア・類似度のキーが現れない（PD4）
- migration 071 が ``INSERT`` を含まず、冪等ガードを持つ（UF2 継承）
- **全自動取り込み経路の不在**（PD1）: core が ``url_fetch`` / ``_accept_material_source``
  を import しない。取り込みは route 層の教員操作だけが持つ

route 層（`test_paper_discovery_api.py`）・UI（`test_paper_discovery_ui_static.py`）の
検査は別担当・別ファイル。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
for _path in (str(BACKEND), str(BACKEND / "api")):
    if _path not in sys.path:
        sys.path.insert(0, str(_path))

from tests.guardrail_helpers import (  # noqa: E402
    assert_module_tree_does_not_import,
    assert_module_tree_forbids,
    assert_paths_forbid,
    assert_source_forbids,
    extract_function_source,
    read_migration_sql,
)

CORE_DIR = BACKEND / "core" / "paper_discovery"

#: Phase 3 の関連度ランキングだけが ``core.llm``（embedding）に触れてよい
#: （設計書 §6。他ファイルは Phase 1〜2 と同じく LLM 0回のまま）。
LLM_EXEMPT_FILES = ("ranking.py",)
MIGRATION_NUMBER = 71
#: Phase 2（取り込みキュー）の DDL。振る舞いの検査は ``test_paper_discovery_worker.py``。
QUEUE_MIGRATION_NUMBER = 72


_SQL_LINE_COMMENT_RE = re.compile(r"--[^\n]*")


def _read(name: str) -> str:
    return (CORE_DIR / name).read_text(encoding="utf-8")


def _core_paths(*, exclude: tuple[str, ...] = ()) -> list:
    return [p for p in sorted(CORE_DIR.rglob("*.py")) if p.name not in exclude]


def _migration_statements() -> str:
    """migration 071 の SQL 文だけ（行コメントを落とす）。

    「シードしない」「冪等」の検査は**実行される文**に対して行う（解説コメントに
    ``INSERT`` の語が出てくるのは正常）。
    """
    return _SQL_LINE_COMMENT_RE.sub("", read_migration_sql(BACKEND, MIGRATION_NUMBER))


def _queue_migration_statements() -> str:
    """migration 072（Phase 2 の取り込みキュー）の SQL 文だけ。"""
    return _SQL_LINE_COMMENT_RE.sub("", read_migration_sql(BACKEND, QUEUE_MIGRATION_NUMBER))


class TestCoreIsolation:
    """core は FastAPI にも LLM にも依存しない。"""

    def test_package_exists(self):
        assert CORE_DIR.is_dir(), f"expected core package at {CORE_DIR}"
        assert (CORE_DIR / "__init__.py").is_file()
        for name in (
            "schema.py",
            "arxiv_client.py",
            "vocab.py",
            "store.py",
            "search.py",
            # Phase 3（設計書 §6）
            "corpus.py",
            "ranking.py",
            "citation_client.py",
            "citation_search.py",
        ):
            assert (CORE_DIR / name).is_file(), f"missing {name}"

    def test_does_not_import_fastapi(self):
        assert_module_tree_does_not_import(CORE_DIR, ["fastapi"])

    def test_only_ranking_touches_the_llm_layer(self):
        """発見層で embedding を使うのは Phase 3 の ``ranking.py`` **だけ**。

        Phase 1〜2 の経路（検索・購読・見送り・キュー・引用グラフ供給）は LLM 0回・
        embedding 0回のままであることを構造として固定する。
        """
        paths = _core_paths(exclude=LLM_EXEMPT_FILES)
        assert_paths_forbid(
            paths,
            [
                "import core.llm",
                "from core.llm",
                "import openai",
                "from openai",
                "core import llm",
                "generate_text",
                "generate_embeddings",
            ],
        )

    def test_ranking_calls_embeddings_through_core_llm_with_usage_context(self):
        """embedding は ``core.llm`` 経由 + U層計測（設計書 §6 / U3）。"""
        src = _read("ranking.py")
        assert "from core.llm import generate_embeddings" in src
        assert 'usage_context("discovery:ranking")' in src

        from core.llm_usage.schema import KNOWN_FEATURES

        assert "discovery:ranking" in KNOWN_FEATURES


class TestNoAutomaticIngestPath:
    """PD1: 発見は自動、取り込みは教員の明示承認のみ。

    core 側に取得・受理の経路が無いことを構造として固定する（route 層の
    ``POST /ingest`` だけが ``url_fetch.fetch_source_from_url`` を呼ぶ）。
    """

    def test_core_does_not_import_url_fetch_or_accept(self):
        assert_module_tree_does_not_import(CORE_DIR, ["core.url_fetch", "url_fetch"])
        assert_module_tree_forbids(
            CORE_DIR,
            [
                "_accept_material_source",
                "fetch_source_from_url",
                "upload_material",
            ],
        )

    def test_core_has_no_worker_or_scheduler(self):
        """検索はモーダルを開いたときだけ（PD8 — cron/worker からの ingest を作らない）。"""
        assert_module_tree_forbids(
            CORE_DIR, ["threading.Thread", "schedule", "APScheduler"]
        )


class TestStoreKeepsHistory:
    """P4 / PD5: 見送りは行削除ではなく revoked 遷移。"""

    def test_no_delete_from_in_store(self):
        assert_source_forbids(
            _read("store.py"),
            ["DELETE FROM", "DELETE  FROM", "delete from"],
            context="core/paper_discovery/store.py",
        )

    def test_no_delete_anywhere_in_the_tree(self):
        assert_module_tree_forbids(CORE_DIR, ["DELETE FROM"])

    def test_restore_is_an_update(self):
        src = extract_function_source(_read("store.py"), "restore")
        assert "UPDATE paper_discovery_dismissals" in src
        assert "revoked = TRUE" in src


class TestNoCandidateTable:
    """PD5: 候補は保存せず読み時導出（候補スナップショットの表を作らない）。"""

    def test_migration_has_no_candidate_table(self):
        sql = _migration_statements()
        tables = set(
            re.findall(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", sql, re.IGNORECASE)
        )
        assert tables == {
            "paper_discovery_subscriptions",
            "paper_discovery_dismissals",
        }, f"unexpected tables in migration {MIGRATION_NUMBER}: {sorted(tables)}"

    def test_store_writes_only_subscriptions_and_dismissals(self):
        src = _read("store.py")
        written = set(re.findall(r"INSERT INTO\s+(\w+)", src)) | {
            # ``ON CONFLICT ... DO UPDATE SET`` は upsert のイディオムなので除く
            name
            for name in re.findall(r"\bUPDATE\s+(\w+)", src)
            if name != "SET"
        }
        assert written <= {
            "paper_discovery_subscriptions",
            "paper_discovery_dismissals",
        }, f"unexpected write target(s): {sorted(written)}"


class TestArxivClientManners:
    """PD7: 宛先固定と 3 秒スロットル。"""

    def test_throttle_constant_and_sleep_exist(self):
        src = _read("arxiv_client.py")
        assert "_MIN_INTERVAL_SECONDS" in src
        assert re.search(r"_MIN_INTERVAL_SECONDS\s*=\s*3(\.0)?\b", src), (
            "arXiv API の最小リクエスト間隔は 3 秒（PD7）"
        )
        throttle = extract_function_source(src, "_throttle")
        assert "time.sleep" in throttle
        assert "_last_request_at" in throttle, "前回リクエスト時刻をモジュール変数で保持する"

    def test_http_get_goes_through_the_throttle(self):
        src = extract_function_source(_read("arxiv_client.py"), "_http_get")
        assert "_throttle()" in src
        assert "timeout=timeout" in src, "タイムアウトなしのリクエストを作らない"

    def test_destination_is_the_fixed_constant(self):
        from core.paper_discovery import arxiv_client, schema

        assert schema.ARXIV_API_HOST == "export.arxiv.org"
        api_url = extract_function_source(_read("arxiv_client.py"), "_api_url")
        assert "ARXIV_API_HOST" in api_url
        # 宛先ホストを直接書かない（呼び出し側から URL を渡せる引数も作らない）。
        assert "arxiv.org" not in api_url.replace("ARXIV_API_HOST", "")
        assert arxiv_client._api_url() == "https://export.arxiv.org/api/query"

    def test_all_http_calls_use_the_fixed_endpoint(self):
        src = _read("arxiv_client.py")
        calls = re.findall(r"requests\.(?:get|post|put|request)\(\s*([^,\n]+?)\s*,", src)
        assert calls, "expected at least one requests call"
        assert all(call.strip() == "_api_url()" for call in calls), (
            f"arXiv client must only call the fixed endpoint, found: {calls}"
        )

    def test_search_signature_has_no_url_argument(self):
        import inspect

        from core.paper_discovery import arxiv_client

        params = set(inspect.signature(arxiv_client.search).parameters)
        assert not (params & {"url", "host", "endpoint", "base_url"}), (
            f"arXiv client must not take a destination argument: {sorted(params)}"
        )


class TestCitationClientManners:
    """Phase 3（設計書 §6）: 引用グラフ client も arXiv と同じ規律を独立に持つ。"""

    def test_destination_is_a_fixed_constant(self):
        from core.paper_discovery import citation_client, schema

        assert schema.SEMANTIC_SCHOLAR_API_HOST == "api.semanticscholar.org"
        assert citation_client.SEMANTIC_SCHOLAR_API_HOST == "api.semanticscholar.org"
        api_url = extract_function_source(_read("citation_client.py"), "_api_url")
        assert "SEMANTIC_SCHOLAR_API_HOST" in api_url
        assert "semanticscholar.org" not in api_url.replace(
            "SEMANTIC_SCHOLAR_API_HOST", ""
        )

    def test_all_http_calls_use_the_fixed_endpoint(self):
        src = _read("citation_client.py")
        calls = re.findall(r"requests\.(?:get|post|put|request)\(\s*([^,\n]+?)\s*,", src)
        assert calls, "expected at least one requests call"
        assert all(call.strip().startswith("_api_url(") for call in calls), (
            f"citation client must only call the fixed endpoint, found: {calls}"
        )

    def test_throttle_is_three_seconds_and_independent_from_arxiv(self):
        src = _read("citation_client.py")
        assert re.search(r"_MIN_INTERVAL_SECONDS\s*=\s*3(\.0)?\b", src), (
            "引用グラフ API の最小リクエスト間隔は 3 秒（PD7 の同族）"
        )
        throttle = extract_function_source(src, "_throttle")
        assert "time.sleep" in throttle
        assert "_last_request_at" in throttle
        # スロットル状態は arXiv 側と共有しない（ホストごとに独立の行儀）。
        assert "from core.paper_discovery.arxiv_client" not in src
        assert "from core.paper_discovery import arxiv_client" not in src

        from core.paper_discovery import arxiv_client, citation_client

        assert citation_client._throttle_lock is not arxiv_client._throttle_lock

    def test_signature_has_no_destination_argument(self):
        import inspect

        from core.paper_discovery import citation_client

        params = set(inspect.signature(citation_client.recommendations_for_arxiv).parameters)
        assert not (params & {"url", "host", "endpoint", "base_url"}), sorted(params)

    def test_only_arxiv_backed_recommendations_are_returned(self):
        """既存の取り込み経路（arXiv の PDF URL）に乗らない論文は候補にしない（PD2）。"""
        from core.paper_discovery import citation_client

        payload = {
            "recommendedPapers": [
                {"title": "with arxiv", "externalIds": {"ArXiv": "2608.20293v2"}},
                {"title": "doi only", "externalIds": {"DOI": "10.1/x"}},
                {"title": "no ids"},
            ]
        }
        entries = citation_client.parse_recommendations(payload, "2601.00001")
        assert [e.arxiv_id for e in entries] == ["2608.20293"]
        assert entries[0].seed_arxiv_id == "2601.00001"

    def test_citation_source_is_opt_in_and_gated_in_core(self):
        """オプトインのゲートは core 側にある（route の分岐忘れで外へ出ない）。"""
        src = _read("citation_search.py")
        assert "DISCOVERY_CITATION_SOURCE_ENABLED" in src
        run = extract_function_source(src, "run_citation_search")
        assert "if not citation_source_enabled():" in run

        from core.config import Settings

        assert Settings().discovery_citation_source_enabled is False, "既定は off"

    def test_citation_search_has_no_ingest_path(self):
        """PD1: 引用グラフ供給は候補提示のみ（取得・受理の呼び出しを持たない）。"""
        assert_source_forbids(
            _read("citation_search.py"),
            ["fetch_source_from_url", "_accept_material_source", "requests."],
            context="core/paper_discovery/citation_search.py",
        )


class TestNoNumericScores:
    """PD4: 数値スコア・類似度を（教員にも）見せない。"""

    _FORBIDDEN_KEYS = (
        '"score"',
        '"similarity"',
        '"confidence"',
        '"relevance"',
        '"rank"',
        '"match_score"',
    )

    def test_relevance_is_exposed_as_a_label_only(self):
        """Phase 3: DTO に載るのは段階ラベル（文字列）だけで、cosine の生値ではない。

        ``relevance_label`` は禁止キー ``"relevance"`` とは別の文字列で、値も
        ``core.label_vocab`` の段階ラベル。ここでは「ラベルは可・生値は不可」を
        両側から固定する。
        """
        from core.label_vocab import DISCOVERY_RELEVANCE_SCALE

        src = _read("ranking.py")
        assert '"relevance_label"' in src
        assert_source_forbids(
            src, list(self._FORBIDDEN_KEYS), context="core/paper_discovery/ranking.py"
        )
        # 段階ラベル表を再定義していない（正本は core/label_vocab.py）。
        assert "DISCOVERY_RELEVANCE_SCALE" in src
        for literal in DISCOVERY_RELEVANCE_SCALE.labels:
            assert f'"{literal}"' not in src, literal
        assert ">= 0.45" not in src and ">= 0.3" not in src

    def test_run_search_dto_has_no_score_keys(self):
        src = extract_function_source(_read("search.py"), "run_search")
        assert_source_forbids(
            src, self._FORBIDDEN_KEYS, context="search.run_search"
        )

    def test_tree_has_no_score_keys(self):
        assert_module_tree_forbids(CORE_DIR, list(self._FORBIDDEN_KEYS))

    def test_entry_dto_carries_only_arxiv_metadata(self):
        from core.paper_discovery.schema import ArxivEntry

        payload = ArxivEntry(arxiv_id="2608.20293").to_dict()
        forbidden = ("score", "similarity", "confidence", "relevance", "rank")
        assert not [k for k in payload if any(f in k.lower() for f in forbidden)]


class TestClosedWorldNote:
    """PD6: 候補一覧は「この検索条件で検索した結果」であって分野の全体ではない。"""

    def test_note_is_a_module_constant(self):
        from core.paper_discovery.search import CLOSED_WORLD_NOTE

        assert CLOSED_WORLD_NOTE == "この一覧は検索条件に一致した範囲のみを示します。"

    def test_run_search_always_returns_query_and_note(self):
        src = extract_function_source(_read("search.py"), "run_search")
        assert '"query": query' in src
        assert '"closed_world_note": CLOSED_WORLD_NOTE' in src


class TestMigration:
    """UF2 継承: 毎起動再実行方式のためシードしない・冪等である。"""

    def test_no_insert(self):
        sql = _migration_statements()
        assert not re.search(r"\bINSERT\b", sql, re.IGNORECASE), (
            "migration がシード行を入れると、教員が消した条件が再起動で復活する（UF2）"
        )

    def test_idempotent_guards(self):
        sql = _migration_statements()
        creates = re.findall(r"CREATE TABLE(\s+IF NOT EXISTS)?", sql, re.IGNORECASE)
        assert creates and all(c.strip() for c in creates), (
            "CREATE TABLE には IF NOT EXISTS が必要（毎起動再実行のため）"
        )
        adds = re.findall(r"ADD COLUMN(\s+IF NOT EXISTS)?", sql, re.IGNORECASE)
        assert adds and all(a.strip() for a in adds), (
            "ADD COLUMN には IF NOT EXISTS が必要"
        )

    def test_adds_documents_source_url(self):
        sql = _migration_statements()
        assert re.search(
            r"ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_url TEXT",
            sql,
            re.IGNORECASE,
        )

    def test_dismissals_have_revoked_column(self):
        sql = _migration_statements()
        assert re.search(r"revoked\s+BOOLEAN NOT NULL DEFAULT FALSE", sql, re.IGNORECASE)

    def test_no_foreign_keys_on_actor_columns(self):
        """操作者は後に墓標化されうる（AL1 / migration 068 §3.1・070 と同じ理由）。"""
        sql = _migration_statements()
        assert "REFERENCES users" not in sql


class TestQueueMigration:
    """migration 072（Phase 2 の取り込みキュー）— 071 と同じ規律を継ぐ。"""

    def test_no_insert(self):
        sql = _queue_migration_statements()
        assert not re.search(r"\bINSERT\b", sql, re.IGNORECASE), (
            "キューにシード行を入れると、処理し終えた項目が再起動で復活する（UF2 / 071 継承）"
        )

    def test_idempotent_guards(self):
        sql = _queue_migration_statements()
        creates = re.findall(r"CREATE TABLE(\s+IF NOT EXISTS)?", sql, re.IGNORECASE)
        assert creates and all(c.strip() for c in creates), (
            "CREATE TABLE には IF NOT EXISTS が必要（毎起動再実行のため）"
        )
        indexes = re.findall(r"CREATE INDEX(\s+IF NOT EXISTS)?", sql, re.IGNORECASE)
        assert indexes and all(i.strip() for i in indexes), (
            "CREATE INDEX には IF NOT EXISTS が必要"
        )

    def test_only_the_queue_table(self):
        sql = _queue_migration_statements()
        tables = set(re.findall(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", sql, re.IGNORECASE))
        assert tables == {"paper_discovery_ingest_items"}, (
            f"unexpected tables in migration {QUEUE_MIGRATION_NUMBER}: {sorted(tables)}"
        )

    def test_status_check_vocabulary(self):
        """CHECK 制約と ``ingest_queue.INGEST_STATUSES`` を一致させる（語彙の二重管理を防ぐ）。"""
        from core.paper_discovery import ingest_queue

        sql = _queue_migration_statements()
        match = re.search(r"status\s+IN\s*\(([^)]*)\)", sql, re.IGNORECASE)
        assert match, "status には CHECK 制約が必要"
        assert set(re.findall(r"'([a-z_]+)'", match.group(1))) == set(
            ingest_queue.INGEST_STATUSES
        )

    def test_no_foreign_keys_on_actor_columns(self):
        assert "REFERENCES users" not in _queue_migration_statements()


class TestQueueStoreKeepsHistory:
    """P4: 失敗行を消さない（再試行は教員の明示操作のみ — PD1）。"""

    def test_no_delete_from(self):
        assert_source_forbids(
            _read("ingest_queue.py"),
            ["DELETE FROM", "DELETE  FROM", "delete from"],
            context="core/paper_discovery/ingest_queue.py",
        )

    def test_retry_is_an_update_limited_to_failed(self):
        src = extract_function_source(_read("ingest_queue.py"), "retry_item")
        assert "UPDATE paper_discovery_ingest_items" in src
        assert "AND status = 'failed'" in src

    def test_queue_writes_only_the_queue_table(self):
        src = _read("ingest_queue.py")
        written = set(re.findall(r"INSERT INTO\s+(\w+)", src)) | {
            # ``SET`` は UPDATE ... SET の、``SKIP`` は FOR UPDATE SKIP LOCKED の
            # イディオムなので除く（どちらもテーブル名ではない）
            name
            for name in re.findall(r"\bUPDATE\s+(\w+)", src)
            if name not in ("SET", "SKIP")
        }
        assert written <= {"paper_discovery_ingest_items"}, (
            f"unexpected write target(s): {sorted(written)}"
        )
