"""論文ディスカバリー層 Phase 2 — 取り込みキューと非同期 worker。

正本: ``docs/features/paper_discovery_design.md`` §5（Phase 2）。

対象:
  - ``backend/core/paper_discovery/ingest_queue.py``（キュー行の状態遷移。core 側）
  - ``backend/api/ingest_worker.py``（取得・受理のループ。api 側）
  - ``backend/db/072_paper_discovery_ingest_queue.sql``

検証観点:
  1. ``claim_next`` のアトミック遷移（``FOR UPDATE SKIP LOCKED``）— 多重起動でも二重処理しない
  2. ``retry_item`` は ``failed`` 限定（P4 / PD1 — 再試行は教員の明示操作のみ）
  3. ``requeue_stale_fetching``（プロセス再起動での置き去り回収。行は消さない）
  4. worker が ``arxiv_client`` / ``core.llm`` を import しない（PD1 — worker は発見しない）
  5. env 名と既定値・アイテム間の間隔 3 秒（PD7 の同族）
  6. ``DELETE FROM`` 不在（P4）
  7. ``enqueue_items`` の重複 skip 3種と事実文
  8. 失敗の記録が事実文のみ（スタックトレース・内部情報を入れない — UF6 継承）
  9. ``main.py`` からの起動配線（V層スイーパのテストと同じ流儀）
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, str(_path))

from tests.guardrail_helpers import (  # noqa: E402
    assert_module_tree_forbids,
    assert_source_does_not_import,
    assert_source_forbids,
    extract_function_source,
    read_migration_sql,
)

CORE_DIR = BACKEND / "core" / "paper_discovery"
QUEUE_SOURCE = CORE_DIR / "ingest_queue.py"
WORKER_SOURCE = BACKEND / "api" / "ingest_worker.py"
MAIN_SOURCE = BACKEND / "api" / "main.py"
MIGRATION_NUMBER = 72

_QUEUE_SRC = QUEUE_SOURCE.read_text(encoding="utf-8")
_WORKER_SRC = WORKER_SOURCE.read_text(encoding="utf-8")

_SQL_LINE_COMMENT_RE = re.compile(r"--[^\n]*")


def _migration_statements() -> str:
    return _SQL_LINE_COMMENT_RE.sub("", read_migration_sql(BACKEND, MIGRATION_NUMBER))


# ---------------------------------------------------------------------------
# フェイクセッション
# ---------------------------------------------------------------------------


ITEM_ROW = (
    "11111111-1111-1111-1111-111111111111",
    "astrophysics",
    "2608.20293",
    "https://arxiv.org/pdf/2608.20293",
    "Dark energy",
    "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    False,
    None,
    "queued",
    None,
    1,
    None,
    None,
    "2026-08-27T00:00:00+00:00",
    None,
    None,
)


class _Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeSession:
    """SQL の断片で応答を切り替える最小セッション（DB へは行かない）。"""

    def __init__(self, *, source_urls=(), active_ids=(), rows_by_fragment=None):
        self.source_urls = list(source_urls)
        self.active_ids = list(active_ids)
        self.rows_by_fragment = dict(rows_by_fragment or {})
        self.calls: list[tuple[str, dict]] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.calls.append((sql, dict(params or {})))
        for fragment, rows in self.rows_by_fragment.items():
            if fragment in sql:
                return _Result(rows)
        if "SELECT source_url FROM documents" in sql:
            return _Result([(url,) for url in self.source_urls])
        if "SELECT arxiv_id FROM paper_discovery_ingest_items" in sql:
            return _Result([(i,) for i in self.active_ids])
        if "INSERT INTO paper_discovery_ingest_items" in sql:
            return _Result([("new-item-id",)])
        return _Result()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed += 1

    def sqls(self):
        return [sql for sql, _p in self.calls]


# ===========================================================================
# 1. 投入（enqueue）
# ===========================================================================


class TestEnqueueItems:
    def _queue(self):
        from core.paper_discovery import ingest_queue

        return ingest_queue

    def test_normalizes_and_builds_the_pdf_url(self):
        q = self._queue()
        session = FakeSession()
        result = q.enqueue_items(
            session,
            [{"arxiv_id": "https://arxiv.org/abs/2608.20293v2", "title": "Dark energy"}],
            domain_key="astrophysics",
            requested_by="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        )
        assert result["queued"] == [
            {"item_id": "new-item-id", "arxiv_id": "2608.20293", "title": "Dark energy"}
        ]
        assert result["skipped"] == []
        insert = next(
            params
            for sql, params in session.calls
            if "INSERT INTO paper_discovery_ingest_items" in sql
        )
        assert insert["arxiv_id"] == "2608.20293"
        assert insert["source_url"] == "https://arxiv.org/pdf/2608.20293"
        assert insert["domain_key"] == "astrophysics"

    def test_skip_invalid_id(self):
        q = self._queue()
        result = q.enqueue_items(FakeSession(), [{"arxiv_id": "not-an-id"}])
        assert result["queued"] == []
        assert result["skipped"] == [
            {"arxiv_id": "not-an-id", "detail": q.SKIP_INVALID_ID}
        ]

    def test_skip_already_ingested(self):
        q = self._queue()
        session = FakeSession(source_urls=["https://arxiv.org/pdf/2608.20293v1"])
        result = q.enqueue_items(session, [{"arxiv_id": "2608.20293"}])
        assert result["queued"] == []
        assert result["skipped"] == [
            {"arxiv_id": "2608.20293", "detail": q.SKIP_ALREADY_INGESTED}
        ]
        assert not [s for s in session.sqls() if "INSERT INTO" in s]

    def test_skip_already_queued(self):
        q = self._queue()
        session = FakeSession(active_ids=["2608.20293"])
        result = q.enqueue_items(session, [{"arxiv_id": "2608.20293"}])
        assert result["skipped"] == [
            {"arxiv_id": "2608.20293", "detail": q.SKIP_ALREADY_QUEUED}
        ]

    def test_duplicate_within_the_same_request_is_skipped_once(self):
        q = self._queue()
        session = FakeSession()
        result = q.enqueue_items(
            session, [{"arxiv_id": "2608.20293"}, {"arxiv_id": "2608.20293v3"}]
        )
        assert len(result["queued"]) == 1
        assert result["skipped"][0]["detail"] == q.SKIP_ALREADY_QUEUED

    def test_failed_items_can_be_queued_again(self):
        """``failed`` / ``accepted`` は ``_active_arxiv_ids`` に入らない = 再投入できる。"""
        q = self._queue()
        src = extract_function_source(_QUEUE_SRC, "_active_arxiv_ids")
        assert "status IN ('queued', 'fetching')" in src
        # 実挙動: pending が空なら積める
        result = q.enqueue_items(FakeSession(active_ids=[]), [{"arxiv_id": "2608.20293"}])
        assert len(result["queued"]) == 1

    def test_models_are_stored_as_json(self):
        q = self._queue()
        session = FakeSession()
        q.enqueue_items(
            session, [{"arxiv_id": "2608.20293"}], models={"pipeline": "gpt-x"}
        )
        insert = next(
            params for sql, params in session.calls if "INSERT INTO" in sql
        )
        assert '"pipeline"' in insert["models"]

    def test_no_models_stores_null(self):
        q = self._queue()
        session = FakeSession()
        q.enqueue_items(session, [{"arxiv_id": "2608.20293"}])
        insert = next(params for sql, params in session.calls if "INSERT INTO" in sql)
        assert insert["models"] is None

    def test_empty_input_issues_no_insert(self):
        q = self._queue()
        session = FakeSession()
        result = q.enqueue_items(session, [])
        assert result == {"queued": [], "skipped": []}
        assert not [s for s in session.sqls() if "INSERT INTO" in s]

    def test_plain_string_items_are_accepted(self):
        q = self._queue()
        result = q.enqueue_items(FakeSession(), ["2608.20293"])
        assert result["queued"][0]["arxiv_id"] == "2608.20293"

    def test_default_domain_key(self):
        q = self._queue()
        session = FakeSession()
        q.enqueue_items(session, [{"arxiv_id": "2608.20293"}])
        insert = next(params for sql, params in session.calls if "INSERT INTO" in sql)
        assert insert["domain_key"] == q.DEFAULT_DOMAIN_KEY == "arxiv"


# ===========================================================================
# 2. 状態遷移
# ===========================================================================


class TestClaimNext:
    def test_sql_is_atomic_with_skip_locked(self):
        src = extract_function_source(_QUEUE_SRC, "claim_next")
        assert "FOR UPDATE SKIP LOCKED" in src, (
            "worker が多重起動しても同じ行を2回処理しないこと"
        )
        assert "UPDATE paper_discovery_ingest_items" in src
        assert "status     = 'fetching'" in src or "status = 'fetching'" in src
        assert "attempts   = attempts + 1" in src or "attempts = attempts + 1" in src
        assert "ORDER BY requested_at ASC" in src
        assert "RETURNING" in src

    def test_returns_none_when_queue_is_empty(self):
        from core.paper_discovery import ingest_queue

        assert ingest_queue.claim_next(FakeSession()) is None

    def test_returns_the_row_as_a_dict(self):
        from core.paper_discovery import ingest_queue

        session = FakeSession(
            rows_by_fragment={"UPDATE paper_discovery_ingest_items": [ITEM_ROW]}
        )
        item = ingest_queue.claim_next(session)
        assert item["item_id"] == "11111111-1111-1111-1111-111111111111"
        assert item["arxiv_id"] == "2608.20293"
        assert item["source_url"] == "https://arxiv.org/pdf/2608.20293"
        assert item["analyze_images"] is False
        assert item["models"] is None


class TestRetryItem:
    def test_only_failed_rows_are_requeued(self):
        src = extract_function_source(_QUEUE_SRC, "retry_item")
        assert "AND status = 'failed'" in src, "再試行できるのは失敗行だけ（P4 / PD1）"
        assert "status      = 'queued'" in src or "status = 'queued'" in src
        # detail は消さない（前回何が起きたかの履歴を残す）
        assert "detail" not in src.split("SET", 1)[1].split("WHERE", 1)[0]

    def test_returns_none_when_nothing_matched(self):
        from core.paper_discovery import ingest_queue

        assert ingest_queue.retry_item(FakeSession(), "11111111-1111-1111-1111-111111111111") is None

    def test_blank_id_does_not_hit_the_db(self):
        from core.paper_discovery import ingest_queue

        session = FakeSession()
        assert ingest_queue.retry_item(session, "") is None
        assert session.calls == []


class TestMarkOutcomes:
    def test_mark_accepted_records_material_and_task(self):
        from core.paper_discovery import ingest_queue

        session = FakeSession(
            rows_by_fragment={"UPDATE paper_discovery_ingest_items": [ITEM_ROW]}
        )
        ingest_queue.mark_accepted(session, "i-1", material_id="mat-1", task_id="task-1")
        _sql, params = session.calls[0]
        assert params["material_id"] == "mat-1"
        assert params["task_id"] == "task-1"

    def test_mark_failed_keeps_the_row(self):
        src = extract_function_source(_QUEUE_SRC, "mark_failed")
        assert "UPDATE paper_discovery_ingest_items" in src
        assert "status      = 'failed'" in src or "status = 'failed'" in src
        assert "DELETE" not in src.upper()


class TestRequeueStaleFetching:
    def test_targets_only_fetching_rows(self):
        src = extract_function_source(_QUEUE_SRC, "requeue_stale_fetching")
        assert "WHERE status = 'fetching'" in src
        assert "status     = 'queued'" in src or "status = 'queued'" in src
        assert "DELETE" not in src.upper()

    def test_returns_the_number_of_rows_restored(self):
        from core.paper_discovery import ingest_queue

        session = FakeSession(
            rows_by_fragment={"UPDATE paper_discovery_ingest_items": [("a",), ("b",)]}
        )
        assert ingest_queue.requeue_stale_fetching(session) == 2

    def test_minutes_are_clamped_to_at_least_one(self):
        from core.paper_discovery import ingest_queue

        session = FakeSession()
        ingest_queue.requeue_stale_fetching(session, older_than_minutes=0)
        assert session.calls[0][1]["minutes"] == 1


class TestListItems:
    def test_limit_is_clamped(self):
        from core.paper_discovery import ingest_queue

        session = FakeSession()
        ingest_queue.list_items(session, limit=100000)
        assert session.calls[0][1]["limit"] == 500
        session2 = FakeSession()
        ingest_queue.list_items(session2, limit=0)
        assert session2.calls[0][1]["limit"] == 50

    def test_domain_filter_is_optional(self):
        from core.paper_discovery import ingest_queue

        session = FakeSession()
        ingest_queue.list_items(session)
        assert "WHERE domain_key" not in session.sqls()[0]
        session2 = FakeSession()
        ingest_queue.list_items(session2, domain_key="astrophysics")
        assert "WHERE domain_key = :domain_key" in session2.sqls()[0]

    def test_newest_first(self):
        from core.paper_discovery import ingest_queue

        session = FakeSession()
        ingest_queue.list_items(session)
        assert "ORDER BY requested_at DESC" in session.sqls()[0]


# ===========================================================================
# 3. worker の構造（PD1 — worker は発見しない）
# ===========================================================================


class TestWorkerIsolation:
    def test_worker_does_not_import_arxiv_client(self):
        assert_source_does_not_import(
            _WORKER_SRC,
            ["core.paper_discovery.arxiv_client", "arxiv_client"],
            context="api/ingest_worker.py",
        )
        assert_source_forbids(
            _WORKER_SRC,
            ["arxiv_client.search", "ArxivApiError", "export.arxiv.org"],
            context="api/ingest_worker.py",
        )

    def test_worker_does_not_import_llm(self):
        assert_source_does_not_import(
            _WORKER_SRC, ["core.llm", "openai"], context="api/ingest_worker.py"
        )

    def test_worker_does_not_talk_http_itself(self):
        """取得は url_fetch のみ（PD2 — 独自の HTTP クライアントを持たない）。"""
        assert_source_does_not_import(
            _WORKER_SRC, ["requests", "httpx", "urllib.request"], context="api/ingest_worker.py"
        )
        assert "url_fetch.fetch_source_from_url" in _WORKER_SRC

    def test_worker_calls_the_existing_acceptance_path(self):
        assert "_accept_material_source" in _WORKER_SRC
        assert "source_url=source_url" in _WORKER_SRC

    def test_no_delete_from_in_queue_or_worker(self):
        assert_source_forbids(_QUEUE_SRC, ["DELETE FROM"], context="ingest_queue.py")
        assert_source_forbids(_WORKER_SRC, ["DELETE FROM"], context="ingest_worker.py")

    def test_core_tree_still_has_no_thread(self):
        """ingest_queue.py を足しても core の「worker/scheduler なし」は保たれる。"""
        assert_module_tree_forbids(CORE_DIR, ["threading.Thread", "schedule", "APScheduler"])

    def test_core_tree_still_has_no_fetch_or_accept(self):
        assert_module_tree_forbids(
            CORE_DIR,
            ["_accept_material_source", "fetch_source_from_url", "upload_material"],
        )


class TestWorkerConfiguration:
    def test_env_names_and_defaults(self):
        import ingest_worker

        assert ingest_worker.ENV_ENABLED == "PAPER_DISCOVERY_WORKER_ENABLED"
        assert ingest_worker.ENV_INTERVAL == "PAPER_DISCOVERY_WORKER_INTERVAL_SECONDS"
        assert ingest_worker.DEFAULT_INTERVAL_SECONDS == 30

    def test_enabled_by_default(self, monkeypatch):
        import ingest_worker

        monkeypatch.delenv("PAPER_DISCOVERY_WORKER_ENABLED", raising=False)
        assert ingest_worker._enabled() is True

    def test_can_be_disabled(self, monkeypatch):
        import ingest_worker

        monkeypatch.setenv("PAPER_DISCOVERY_WORKER_ENABLED", "0")
        assert ingest_worker._enabled() is False

    def test_disabled_worker_does_not_start_a_thread(self, monkeypatch):
        import ingest_worker

        monkeypatch.setenv("PAPER_DISCOVERY_WORKER_ENABLED", "0")
        monkeypatch.setattr(ingest_worker, "_started", False)
        started = []
        monkeypatch.setattr(
            ingest_worker.threading, "Thread",
            lambda *a, **k: started.append((a, k)) or pytest.fail("thread must not start"),
        )
        ingest_worker.start_background_worker()
        assert started == []

    def test_interval_is_clamped_and_falls_back(self, monkeypatch):
        import ingest_worker

        monkeypatch.setenv("PAPER_DISCOVERY_WORKER_INTERVAL_SECONDS", "1")
        assert ingest_worker._interval_seconds() == 5
        monkeypatch.setenv("PAPER_DISCOVERY_WORKER_INTERVAL_SECONDS", "abc")
        assert ingest_worker._interval_seconds() == 30

    def test_inter_item_sleep_is_three_seconds(self):
        import ingest_worker

        assert ingest_worker.INTER_ITEM_SLEEP_SECONDS == 3, (
            "arXiv への行儀（PD7 の同族）— アイテム間に3秒の間隔を置く"
        )

    def test_thread_is_a_daemon(self):
        src = extract_function_source(_WORKER_SRC, "start_background_worker")
        assert "threading.Thread" in src
        assert "daemon=True" in src


# ===========================================================================
# 4. worker の振る舞い
# ===========================================================================


@pytest.fixture
def worker_env(monkeypatch):
    import ingest_worker

    state: dict = {
        "sessions": [],
        "claims": [],
        "domains": [["arxiv.org"]],
        "fetch_error": None,
        "accept_error": None,
        "accepted": [],
        "fetches": [],
        "finished": [],
        "sleeps": [],
    }

    def _session():
        session = FakeSession()
        state["sessions"].append(session)
        return session

    monkeypatch.setattr(ingest_worker, "get_session", _session)
    monkeypatch.setattr(
        ingest_worker, "_claim_next",
        lambda: state["claims"].pop(0) if state["claims"] else None,
    )
    monkeypatch.setattr(
        ingest_worker, "_allowed_domains",
        lambda: list(state["domains"][0]),
    )

    def _finish(item_id, *, accepted=None, detail=""):
        state["finished"].append((item_id, accepted, detail))

    monkeypatch.setattr(ingest_worker, "_finish", _finish)

    def _fetch(url, allowed):
        state["fetches"].append((url, list(allowed)))
        if state["fetch_error"] is not None:
            raise state["fetch_error"]
        return ingest_worker.url_fetch.FetchedSource(
            content=b"%PDF-1.7", source_kind="pdf", filename="2608.20293.pdf"
        )

    monkeypatch.setattr(ingest_worker.url_fetch, "fetch_source_from_url", _fetch)

    def _accept(**kwargs):
        state["accepted"].append(kwargs)
        if state["accept_error"] is not None:
            raise state["accept_error"]
        return {"material_id": "mat-1", "task_id": "task-1"}

    monkeypatch.setattr(ingest_worker, "_accept_material_source", _accept)
    state["module"] = ingest_worker
    return state


def _item(**overrides):
    base = {
        "item_id": "i-1",
        "domain_key": "astrophysics",
        "arxiv_id": "2608.20293",
        "source_url": "https://arxiv.org/pdf/2608.20293",
        "title": "Dark energy",
        "requested_by": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "analyze_images": False,
        "models": None,
        "status": "fetching",
    }
    base.update(overrides)
    return base


class TestProcessItem:
    def test_success_marks_accepted_with_ids(self, worker_env):
        assert worker_env["module"].process_item(_item()) is True
        item_id, accepted, detail = worker_env["finished"][0]
        assert item_id == "i-1"
        assert accepted["material_id"] == "mat-1"
        assert detail == ""

    def test_accept_receives_the_requester_and_options(self, worker_env):
        worker_env["module"].process_item(
            _item(analyze_images=True, models={"pipeline": "gpt-x"})
        )
        kwargs = worker_env["accepted"][0]
        assert kwargs["current_user"] == {"id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"}
        assert kwargs["analyze_images"] is True
        assert kwargs["models_option"] == {"pipeline": "gpt-x"}
        assert kwargs["source_url"] == "https://arxiv.org/pdf/2608.20293"

    def test_allowed_domains_are_read_per_item(self, worker_env):
        worker_env["module"].process_item(_item())
        assert worker_env["fetches"][0][1] == ["arxiv.org"]
        # 許可リストを変えると次の取得にすぐ反映される（UI 無効化は補助 — UF1）
        worker_env["domains"][0] = []
        worker_env["module"].process_item(_item())
        assert worker_env["fetches"][1][1] == []

    def test_fetch_error_is_recorded_as_a_fact_sentence(self, worker_env):
        mod = worker_env["module"]
        worker_env["fetch_error"] = mod.url_fetch.DomainNotAllowedError(
            "この取得先は許可されていません。"
        )
        assert mod.process_item(_item()) is False
        item_id, accepted, detail = worker_env["finished"][0]
        assert accepted is None
        assert detail == "この取得先は許可されていません。"
        assert worker_env["accepted"] == []

    def test_missing_domain_configuration_fails_only_that_item(self, worker_env):
        mod = worker_env["module"]
        worker_env["fetch_error"] = mod.url_fetch.NoDomainsConfiguredError(
            "取得先ドメインが登録されていません。"
        )
        assert mod.process_item(_item()) is False
        assert worker_env["finished"][0][2] == "取得先ドメインが登録されていません。"

    def test_acceptance_error_is_recorded(self, worker_env):
        from fastapi import HTTPException

        mod = worker_env["module"]
        worker_env["accept_error"] = HTTPException(status_code=500, detail="Source storage failed")
        assert mod.process_item(_item()) is False
        assert worker_env["finished"][0][2] == "Source storage failed"

    def test_unexpected_error_uses_the_generic_fact_sentence(self, worker_env):
        mod = worker_env["module"]
        worker_env["accept_error"] = RuntimeError("boom at line 42")
        assert mod.process_item(_item()) is False
        detail = worker_env["finished"][0][2]
        assert detail == mod.DETAIL_UNEXPECTED
        assert "boom" not in detail
        assert "Traceback" not in detail

    def test_filename_falls_back_to_the_arxiv_id(self, worker_env, monkeypatch):
        mod = worker_env["module"]
        monkeypatch.setattr(
            mod.url_fetch, "fetch_source_from_url",
            lambda url, allowed: mod.url_fetch.FetchedSource(
                content=b"%PDF", source_kind="pdf", filename=""
            ),
        )
        mod.process_item(_item())
        assert worker_env["accepted"][0]["filename"] == "2608.20293.pdf"


class TestDrainLoop:
    def test_processes_every_queued_item(self, worker_env):
        mod = worker_env["module"]
        worker_env["claims"] = [_item(item_id="i-1"), _item(item_id="i-2")]
        assert mod.drain_once(sleep=worker_env["sleeps"].append) == 2
        assert [f[0] for f in worker_env["finished"]] == ["i-1", "i-2"]

    def test_sleeps_between_items_only(self, worker_env):
        mod = worker_env["module"]
        worker_env["claims"] = [_item(item_id="i-1"), _item(item_id="i-2")]
        mod.drain_once(sleep=worker_env["sleeps"].append)
        assert worker_env["sleeps"] == [mod.INTER_ITEM_SLEEP_SECONDS]

    def test_empty_queue_does_not_sleep(self, worker_env):
        mod = worker_env["module"]
        assert mod.drain_once(sleep=worker_env["sleeps"].append) == 0
        assert worker_env["sleeps"] == []

    def test_max_items_bounds_one_cycle(self, worker_env):
        mod = worker_env["module"]
        worker_env["claims"] = [_item(item_id=f"i-{i}") for i in range(5)]
        assert mod.drain_once(max_items=2, sleep=worker_env["sleeps"].append) == 2
        assert len(worker_env["claims"]) == 3

    def test_one_failure_does_not_stop_the_cycle(self, worker_env):
        mod = worker_env["module"]
        worker_env["claims"] = [_item(item_id="i-1"), _item(item_id="i-2")]
        worker_env["accept_error"] = RuntimeError("boom")
        assert mod.drain_once(sleep=worker_env["sleeps"].append) == 2
        assert all(entry[1] is None for entry in worker_env["finished"])


# ===========================================================================
# 5. migration 072
# ===========================================================================


class TestMigration:
    def test_no_insert(self):
        sql = _migration_statements()
        assert not re.search(r"\bINSERT\b", sql, re.IGNORECASE), (
            "毎起動再実行方式のため、キューにシード行を入れてはならない（UF2 / 071 継承）"
        )

    def test_idempotent_guards(self):
        sql = _migration_statements()
        creates = re.findall(r"CREATE TABLE(\s+IF NOT EXISTS)?", sql, re.IGNORECASE)
        assert creates and all(c.strip() for c in creates)
        indexes = re.findall(r"CREATE INDEX(\s+IF NOT EXISTS)?", sql, re.IGNORECASE)
        assert indexes and all(i.strip() for i in indexes)

    def test_creates_only_the_queue_table(self):
        sql = _migration_statements()
        tables = set(re.findall(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", sql, re.IGNORECASE))
        assert tables == {"paper_discovery_ingest_items"}

    def test_status_check_matches_the_schema_vocabulary(self):
        from core.paper_discovery import ingest_queue

        sql = _migration_statements()
        match = re.search(r"status\s+IN\s*\(([^)]*)\)", sql, re.IGNORECASE)
        assert match, "status には CHECK 制約が必要"
        vocabulary = set(re.findall(r"'([a-z_]+)'", match.group(1)))
        assert vocabulary == set(ingest_queue.INGEST_STATUSES)
        assert vocabulary == {"queued", "fetching", "accepted", "failed"}

    def test_no_foreign_key_on_the_requester(self):
        """操作した教員は後に墓標化されうる（AL1 / 070・071 と同じ理由）。"""
        assert "REFERENCES users" not in _migration_statements()

    def test_no_drop_or_delete(self):
        sql = _migration_statements().upper()
        assert "DROP TABLE" not in sql
        assert "DELETE FROM" not in sql


# ===========================================================================
# 6. 起動配線（V層スイーパのテストと同じ流儀）
# ===========================================================================


class TestStartupWiring:
    def test_main_starts_the_worker(self):
        src = MAIN_SOURCE.read_text(encoding="utf-8")
        assert "ingest_worker" in src
        assert "start_background_worker" in src

    def test_startup_is_best_effort(self):
        """worker の起動失敗でアプリの起動を止めない。"""
        src = MAIN_SOURCE.read_text(encoding="utf-8")
        block = src.split("start_background_worker()", 1)[1][:400]
        assert "except Exception" in block
        assert "logger.warning" in block
