"""atlas_skeletons テーブル (migration 027) のインメモリフェイク。

core/atlas_store.py が発行する SQL 面だけを模倣する。テストは
core.postgres.get_session をこのフェイクを返すよう monkeypatch して使う。
learning_courses の SELECT/UPDATE (バインディング API) も最小限模倣する。
"""

from __future__ import annotations

import json
from typing import Any, Callable


class FakeResult:
    def __init__(self, rows=None, rowcount=0):
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class AtlasSkeletonTableFake:
    """atlas_skeletons のインメモリ実装 + learning_courses の最小模倣。

    セッションとテーブルを兼ねる (commit/rollback/close は no-op)。
    複数セッションで状態を共有したい場合は make_session_factory() を使う。
    """

    def __init__(self):
        # atlas_skeletons 行: {domain_key,status,version,content,revision,generated_by,seq}
        self.skeleton_rows: list[dict] = []
        self._seq = 0
        # learning_courses: course_id → {"data": dict, "user_id": str}
        self.courses: dict[str, dict] = {}
        self.review_events: list[dict] = []
        self.calls: list[tuple[str, dict]] = []
        # 未対応 SQL のフォールバック (test 側で差し替え可)
        self.fallback: Callable[[str, dict], FakeResult] = lambda sql, p: FakeResult()

    # -- session インターフェース --
    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        params = params or {}
        self.calls.append((sql, params))
        return self._dispatch(sql, params)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass

    # -- ヘルパ --
    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _drafts(self, domain_key: str) -> list[dict]:
        return [
            r
            for r in self.skeleton_rows
            if r["domain_key"] == domain_key and r["status"] == "draft"
        ]

    def _frozen(self, domain_key: str) -> list[dict]:
        rows = [
            r
            for r in self.skeleton_rows
            if r["domain_key"] == domain_key and r["status"] == "frozen"
        ]
        rows.sort(key=lambda r: (-r["seq"], r["version"]))
        return rows

    # -- SQL ディスパッチ --
    def _dispatch(self, sql: str, p: dict) -> FakeResult:  # noqa: C901
        if "atlas_skeletons" in sql:
            return self._dispatch_skeletons(sql, p)
        if "theory_review_events" in sql and sql.startswith("INSERT"):
            self.review_events.append(dict(p))
            return FakeResult(rowcount=1)
        if "FROM learning_courses" in sql:
            course = self.courses.get(str(p.get("cid") or p.get("course_id") or ""))
            if not course:
                return FakeResult()
            return FakeResult([(course["data"], course["user_id"])])
        if sql.startswith("UPDATE learning_courses"):
            cid = str(p.get("cid") or "")
            if cid in self.courses:
                self.courses[cid]["data"] = json.loads(p["data"])
                return FakeResult(rowcount=1)
            return FakeResult(rowcount=0)
        return self.fallback(sql, p)

    def _dispatch_skeletons(self, sql: str, p: dict) -> FakeResult:  # noqa: C901
        domain_key = str(p.get("domain_key") or "")

        if sql.startswith("SELECT content, revision"):
            drafts = self._drafts(domain_key)
            if not drafts:
                return FakeResult()
            r = drafts[0]
            return FakeResult([(r["content"], r["revision"], r["generated_by"], "")])

        if sql.startswith("SELECT content FROM"):
            frozen = self._frozen(domain_key)
            if not frozen:
                return FakeResult()
            return FakeResult([(frozen[0]["content"],)])

        if sql.startswith("SELECT revision FROM"):
            drafts = self._drafts(domain_key)
            if not drafts:
                return FakeResult()
            return FakeResult([(drafts[0]["revision"],)])

        if sql.startswith("SELECT 1 FROM"):
            version = str(p.get("version") or "")
            hit = any(
                r["domain_key"] == domain_key
                and r["status"] == "frozen"
                and r["version"] == version
                for r in self.skeleton_rows
            )
            return FakeResult([(1,)] if hit else [])

        if sql.startswith("SELECT domain_key, status"):
            # list_domains: row_number() を模倣 (domain×status の最新行に rn=1)
            out: list[tuple] = []
            seen: set[tuple[str, str]] = set()
            for r in sorted(self.skeleton_rows, key=lambda r: -r["seq"]):
                key = (r["domain_key"], r["status"])
                rn = 1 if key not in seen else 2
                seen.add(key)
                out.append((r["domain_key"], r["status"], r["version"], r["revision"], rn))
            return FakeResult(out)

        if sql.startswith("INSERT INTO atlas_skeletons"):
            status = "draft" if "'draft'" in sql else "frozen"
            row = {
                "domain_key": domain_key,
                "status": status,
                "version": str(p.get("version") or ""),
                "content": json.loads(p["content"]),
                "revision": 1,
                "generated_by": str(p.get("generated_by") or ""),
                "seq": self._next_seq(),
            }
            if status == "frozen":
                # 一意制約 (domain_key, version)
                if any(
                    r["domain_key"] == domain_key
                    and r["status"] == "frozen"
                    and r["version"] == row["version"]
                    for r in self.skeleton_rows
                ):
                    raise RuntimeError("duplicate frozen version")
            else:
                if self._drafts(domain_key):
                    raise RuntimeError("duplicate draft")
            self.skeleton_rows.append(row)
            return FakeResult(rowcount=1)

        if sql.startswith("UPDATE atlas_skeletons"):
            expected = int(p.get("expected_revision") or 0)
            for r in self._drafts(domain_key):
                if r["revision"] == expected:
                    r["content"] = json.loads(p["content"])
                    r["revision"] += 1
                    gen = str(p.get("generated_by") or "")
                    if gen:
                        r["generated_by"] = gen
                    return FakeResult(rowcount=1)
            return FakeResult(rowcount=0)

        if sql.startswith("DELETE FROM atlas_skeletons"):
            before = len(self.skeleton_rows)
            self.skeleton_rows = [
                r
                for r in self.skeleton_rows
                if not (r["domain_key"] == domain_key and r["status"] == "draft")
            ]
            return FakeResult(rowcount=before - len(self.skeleton_rows))

        raise AssertionError(f"unhandled atlas_skeletons SQL: {sql}")


def make_session_factory(fake: AtlasSkeletonTableFake) -> Callable[[], Any]:
    """core.postgres.get_session の差し替え用 (常に同じフェイクを返す)。"""

    def factory():
        return fake

    return factory
