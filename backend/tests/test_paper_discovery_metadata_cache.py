"""arXiv メタデータ（外部事実）の写し — ``core/paper_discovery/metadata_cache.py``。

正本: ``docs/features/paper_radar_design.md`` §14「arXiv 呼び出しの上限」。
DDL は ``backend/db/085_paper_discovery_arxiv_metadata_cache.sql``。
先例: migration 077 の ``reference_cache``（CC3 = PD5 の設計明示例外）。

検証観点:

1. :func:`get_fresh` — 正規化・重複除去・**空入力は SQL を撃たない**・TTL の受け渡し・
   行 → :class:`ArxivEntry` の復元
2. :func:`upsert_entries` — ``ON CONFLICT DO UPDATE``・``fetched_at = now()``・
   正規化できない ID を飛ばす・空入力は SQL を撃たない・**候補や教員の判断を書かない**
3. :func:`read_fresh` / :func:`remember` — fail-soft（読めない・書けないで探索を止めない）
4. 日時の往復（``...Z`` ⇄ ``datetime``）と、解釈不能な日時でエントリ全体を落とさないこと
5. migration 085 の形（冪等・シードしない・行削除しない・FK を張らない）
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, str(_path))

from core.paper_discovery import metadata_cache  # noqa: E402
from core.paper_discovery.schema import ArxivEntry  # noqa: E402
from tests.guardrail_helpers import assert_source_forbids  # noqa: E402

MIGRATION = BACKEND / "db" / "085_paper_discovery_arxiv_metadata_cache.sql"
TABLE = "paper_discovery_arxiv_metadata_cache"


# ---------------------------------------------------------------------------
# フェイクセッション（DB へ行かない）
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeSession:
    """SQL 文と束縛パラメータを記録するだけのセッション。"""

    def __init__(self, rows=(), *, fail: Exception | None = None):
        self.rows = list(rows)
        self.fail = fail
        self.calls: list[tuple[str, dict]] = []
        self.rollbacks = 0

    def execute(self, stmt, params=None):
        if self.fail is not None:
            raise self.fail
        sql = " ".join(str(stmt).split())
        self.calls.append((sql, dict(params or {})))
        if sql.startswith("SELECT"):
            return _Result(self.rows)
        return _Result()

    def rollback(self):
        self.rollbacks += 1

    @property
    def sql_log(self) -> str:
        return "\n".join(sql for sql, _ in self.calls)


def _row(
    arxiv_id="2608.20293",
    title="Seed",
    summary="An abstract.",
    categories=("astro-ph.CO",),
    primary="astro-ph.CO",
    authors=("Doe, J",),
    published=datetime(2026, 8, 1, 17, 0, tzinfo=timezone.utc),
    updated=None,
    abs_url="https://arxiv.org/abs/2608.20293",
    pdf_url="https://arxiv.org/pdf/2608.20293",
):
    def _seq(value):
        # str はドライバが JSONB を文字列で返す場合の模擬なのでそのまま渡す。
        return value if isinstance(value, str) else list(value)

    return (
        arxiv_id, title, summary, _seq(categories), primary, _seq(authors),
        published, updated, abs_url, pdf_url,
    )


# ---------------------------------------------------------------------------
# 1. 読み取り
# ---------------------------------------------------------------------------


class TestGetFresh:
    def test_empty_input_does_not_touch_the_database(self):
        session = FakeSession()
        assert metadata_cache.get_fresh(session, [], ttl_days=30) == {}
        assert metadata_cache.get_fresh(session, ["", "not-an-id"], ttl_days=30) == {}
        assert session.calls == [], "空 IN 句を全件条件に化けさせない"

    def test_normalizes_and_deduplicates_the_keys(self):
        session = FakeSession()
        metadata_cache.get_fresh(
            session,
            ["2608.20293v2", "https://arxiv.org/abs/2608.20293", "2608.00002"],
            ttl_days=7,
        )
        sql, params = session.calls[0]
        assert params["arxiv_ids"] == ["2608.20293", "2608.00002"]
        assert params["ttl_days"] == 7
        assert TABLE in sql
        assert "make_interval(days => :ttl_days)" in sql

    def test_negative_or_bad_ttl_becomes_zero(self):
        session = FakeSession()
        metadata_cache.get_fresh(session, ["2608.20293"], ttl_days=-5)
        assert session.calls[0][1]["ttl_days"] == 0
        session = FakeSession()
        metadata_cache.get_fresh(session, ["2608.20293"], ttl_days="x")
        assert session.calls[0][1]["ttl_days"] == 0

    def test_restores_entries_from_rows(self):
        session = FakeSession(rows=[_row()])
        out = metadata_cache.get_fresh(session, ["2608.20293"], ttl_days=30)
        entry = out["2608.20293"]
        assert isinstance(entry, ArxivEntry)
        assert entry.title == "Seed"
        assert entry.summary == "An abstract."
        assert entry.categories == ["astro-ph.CO"]
        assert entry.primary_category == "astro-ph.CO"
        assert entry.authors == ["Doe, J"]
        assert entry.published == "2026-08-01T17:00:00Z"
        assert entry.updated == ""
        assert entry.abs_url == "https://arxiv.org/abs/2608.20293"
        # 版は写しに持たせない（キーが version 抜きなので版を騙らない）。
        assert entry.version is None

    def test_json_text_columns_are_absorbed(self):
        """ドライバが JSONB を str で返しても復元できる。"""
        session = FakeSession(rows=[_row(categories='["astro-ph.CO"]', authors='["Doe, J"]')])
        entry = metadata_cache.get_fresh(session, ["2608.20293"], ttl_days=30)["2608.20293"]
        assert entry.categories == ["astro-ph.CO"]
        assert entry.authors == ["Doe, J"]

    def test_blank_primary_category_becomes_none(self):
        session = FakeSession(rows=[_row(primary="")])
        entry = metadata_cache.get_fresh(session, ["2608.20293"], ttl_days=30)["2608.20293"]
        assert entry.primary_category is None

    def test_rows_without_an_id_are_skipped(self):
        session = FakeSession(rows=[_row(arxiv_id=""), _row()])
        out = metadata_cache.get_fresh(session, ["2608.20293"], ttl_days=30)
        assert list(out) == ["2608.20293"]


class TestTimestamps:
    @pytest.mark.parametrize(
        "value",
        ["2026-08-01T17:00:00Z", "2026-08-01T17:00:00+00:00"],
    )
    def test_parses_arxiv_formats(self, value):
        parsed = metadata_cache.parse_timestamp(value)
        assert parsed == datetime(2026, 8, 1, 17, 0, tzinfo=timezone.utc)

    @pytest.mark.parametrize("value", ["", None, "yesterday", 17])
    def test_unparsable_values_become_none(self, value):
        assert metadata_cache.parse_timestamp(value) is None

    def test_round_trip_keeps_the_arxiv_spelling(self):
        text = "2026-08-01T17:00:00Z"
        assert metadata_cache.format_timestamp(metadata_cache.parse_timestamp(text)) == text

    def test_none_formats_to_empty(self):
        assert metadata_cache.format_timestamp(None) == ""


# ---------------------------------------------------------------------------
# 2. 書き込み
# ---------------------------------------------------------------------------


class TestUpsertEntries:
    def test_empty_input_does_not_touch_the_database(self):
        session = FakeSession()
        assert metadata_cache.upsert_entries(session, []) == 0
        assert metadata_cache.upsert_entries(session, [ArxivEntry(arxiv_id="")]) == 0
        assert session.calls == []

    def test_writes_one_row_per_entry_as_an_upsert(self):
        session = FakeSession()
        written = metadata_cache.upsert_entries(
            session,
            [
                ArxivEntry(
                    arxiv_id="2608.20293v2",
                    title="Seed",
                    summary="An abstract.",
                    categories=["astro-ph.CO", "astro-ph.CO"],
                    primary_category="astro-ph.CO",
                    authors=["Doe, J"],
                    published="2026-08-01T17:00:00Z",
                    abs_url="https://arxiv.org/abs/2608.20293",
                ),
                ArxivEntry(arxiv_id="2608.00002", title="候補"),
            ],
        )
        assert written == 2
        assert len(session.calls) == 2
        sql, params = session.calls[0]
        assert sql.startswith(f"INSERT INTO {TABLE}")
        assert "ON CONFLICT (arxiv_id) DO UPDATE" in sql
        assert "fetched_at = now()" in sql
        # キーは version 抜きの正規化 ID。
        assert params["arxiv_id"] == "2608.20293"
        assert params["categories"] == '["astro-ph.CO"]'
        assert params["authors"] == '["Doe, J"]'
        assert params["published_at"] == datetime(2026, 8, 1, 17, 0, tzinfo=timezone.utc)
        assert params["updated_at"] is None

    def test_unparsable_ids_and_duplicates_are_dropped(self):
        session = FakeSession()
        written = metadata_cache.upsert_entries(
            session,
            [
                ArxivEntry(arxiv_id="not-an-id"),
                ArxivEntry(arxiv_id="2608.20293v1"),
                ArxivEntry(arxiv_id="2608.20293v2"),
            ],
        )
        assert written == 1
        assert session.calls[0][1]["arxiv_id"] == "2608.20293"

    def test_only_external_facts_are_bound(self):
        """写しに教員・教材・候補の状態を混ぜない（PD5 / CC3 同型の例外の範囲）。"""
        session = FakeSession()
        metadata_cache.upsert_entries(session, [ArxivEntry(arxiv_id="2608.20293")])
        params = session.calls[0][1]
        assert set(params) == {
            "arxiv_id", "title", "summary", "categories", "primary_category",
            "authors", "published_at", "updated_at", "abs_url", "pdf_url",
        }

    def test_module_has_no_delete(self):
        src = (BACKEND / "core" / "paper_discovery" / "metadata_cache.py").read_text(
            encoding="utf-8"
        )
        assert_source_forbids(
            src,
            ["DELETE FROM", "delete from", "TRUNCATE"],
            context="core/paper_discovery/metadata_cache.py",
        )


# ---------------------------------------------------------------------------
# 3. fail-soft ラッパ
# ---------------------------------------------------------------------------


class TestFailSoftWrappers:
    def test_read_fresh_returns_empty_when_the_table_is_unreadable(self, caplog):
        session = FakeSession(fail=RuntimeError("relation does not exist"))
        with caplog.at_level("WARNING"):
            assert metadata_cache.read_fresh(session, ["2608.20293"]) == {}
        assert "cache read failed" in caplog.text

    def test_read_fresh_skips_the_query_for_empty_input(self):
        session = FakeSession()
        assert metadata_cache.read_fresh(session, []) == {}
        assert session.calls == []

    def test_remember_never_raises_and_rolls_back(self, caplog):
        session = FakeSession(fail=RuntimeError("relation does not exist"))
        with caplog.at_level("WARNING"):
            metadata_cache.remember(session, [ArxivEntry(arxiv_id="2608.20293")])
        assert "cache write failed" in caplog.text
        assert session.rollbacks == 1, "後続の読み取りを失敗トランザクションに巻き込まない"

    def test_remember_skips_entries_without_an_id(self):
        session = FakeSession()
        metadata_cache.remember(session, [ArxivEntry(arxiv_id="")])
        assert session.calls == []

    def test_remember_does_not_commit(self):
        """``commit`` は呼び出し側（route）の責務（core はトランザクションを閉じない）。"""
        src = (BACKEND / "core" / "paper_discovery" / "metadata_cache.py").read_text(
            encoding="utf-8"
        )
        assert_source_forbids(
            src, ["commit("], context="core/paper_discovery/metadata_cache.py"
        )

    def test_ttl_comes_from_the_settings(self, monkeypatch):
        from types import SimpleNamespace

        monkeypatch.setattr(
            "core.config.get_settings",
            lambda: SimpleNamespace(discovery_arxiv_metadata_ttl_days=3),
        )
        assert metadata_cache.ttl_days() == 3

    def test_unreadable_settings_disable_the_cache(self, monkeypatch):
        def _boom():
            raise RuntimeError("no settings")

        monkeypatch.setattr("core.config.get_settings", _boom)
        assert metadata_cache.ttl_days() == 0


# ---------------------------------------------------------------------------
# 4. migration 085 の形
# ---------------------------------------------------------------------------


class TestMigration:
    @property
    def statements(self) -> str:
        """実行される文だけ（解説コメントに INSERT 等の語が出るのは正常）。"""
        return re.sub(r"--[^\n]*", "", MIGRATION.read_text(encoding="utf-8"))

    def test_file_exists_and_creates_one_table(self):
        assert MIGRATION.is_file()
        tables = set(
            re.findall(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", self.statements, re.IGNORECASE)
        )
        assert tables == {TABLE}

    def test_is_idempotent(self):
        statements = self.statements
        assert "CREATE TABLE IF NOT EXISTS" in statements
        assert "CREATE INDEX IF NOT EXISTS" in statements
        assert re.search(r"CREATE TABLE(?! IF NOT EXISTS)", statements) is None
        assert re.search(r"CREATE INDEX(?! IF NOT EXISTS)", statements) is None
        assert "CONCURRENTLY" not in statements

    def test_does_not_seed_or_delete(self):
        statements = self.statements
        assert "INSERT" not in statements, "毎起動再実行方式ではシードが更新を巻き戻す"
        assert "DELETE" not in statements

    def test_has_no_foreign_keys_or_actor_columns(self):
        statements = self.statements
        assert "REFERENCES" not in statements
        for column in ("user_id", "document_id", "updated_by", "status"):
            assert column not in statements

    def test_columns_match_the_dto(self):
        statements = self.statements
        for column in (
            "arxiv_id", "title", "summary", "categories", "primary_category",
            "authors", "published_at", "updated_at", "abs_url", "pdf_url", "fetched_at",
        ):
            assert column in statements
        assert "fetched_at" in statements.split("CREATE INDEX")[1], "TTL 絞り込みの索引"
