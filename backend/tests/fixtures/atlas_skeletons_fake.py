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

    def mappings(self):
        """dict 行 (atlas_lifecycle._affected_courses_for_domain 用) はそのまま自身を返す。

        呼び出し側は ``.mappings().fetchall()`` で dict-like 行 (``row["id"]`` 等) を
        期待するため、rows が既に dict のときは self で十分 (SQLAlchemy の RowMapping
        の完全互換は不要)。
        """
        return self


class AtlasSkeletonTableFake:
    """atlas_skeletons のインメモリ実装 + learning_courses の最小模倣。

    セッションとテーブルを兼ねる (commit/rollback/close は no-op)。
    複数セッションで状態を共有したい場合は make_session_factory() を使う。
    """

    def __init__(self):
        # atlas_skeletons 行: {domain_key,status,version,content,revision,generated_by,seq}
        self.skeleton_rows: list[dict] = []
        self._seq = 0
        # atlas_domain_meta 行 (migration 028 + 057): domain_key → {name, description,
        # target_domain, concept_vocabulary, lifecycle, retired_at, retired_by, retire_note}
        self.domain_meta: dict[str, dict] = {}
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
        if "pg_advisory_xact_lock" in sql:
            # atlas_store.lock_domain_for_write (domain 単位の書き込み直列化)。
            # 単一プロセスのテストでは no-op。取得記録は self.calls で検証できる。
            return FakeResult([(True,)])
        if "atlas_domain_meta" in sql:
            return self._dispatch_domain_meta(sql, p)
        if "atlas_skeletons" in sql:
            return self._dispatch_skeletons(sql, p)
        if "theory_review_events" in sql and sql.startswith("INSERT"):
            self.review_events.append(dict(p))
            return FakeResult(rowcount=1)
        if "theory_review_events" in sql and "COUNT" in sql:
            # atlas assist の日次コスト判定 (routes/atlas.py _assist_calls_today)。
            # 日付フィルタは無視し、当該ユーザーの atlas_assist 行数を返す。
            uid = str(p.get("uid") or "")
            n = sum(
                1
                for e in self.review_events
                if e.get("entity_type") == "atlas_assist"
                and str(e.get("changed_by") or "") == uid
            )
            return FakeResult([(n,)])
        if sql.startswith("SELECT id, title, data FROM learning_courses"):
            # atlas_lifecycle._affected_courses_for_domain (freeze-impact §4.4) の模倣。
            # 行は dict で返す (.mappings().fetchall() で row["id"] 等のキーアクセスをする)。
            domain_key = str(p.get("domain_key") or "")
            rows = [
                {
                    "id": cid,
                    "title": (course.get("title") or course["data"].get("title") or cid),
                    "data": course["data"],
                }
                for cid, course in self.courses.items()
                if isinstance(course.get("data"), dict)
                and course["data"].get("cartridge_id") == domain_key
            ]
            return FakeResult(rows)

        if sql.startswith("SELECT DISTINCT user_id::text"):
            # notification_recipients.atlas_bound_course_owner_ids (§3.4) の模倣。
            domain_key = str(p.get("domain_key") or "")
            owner_ids = {
                str(course["user_id"])
                for course in self.courses.values()
                if isinstance(course.get("data"), dict)
                and course["data"].get("cartridge_id") == domain_key
                and course.get("user_id")
            }
            return FakeResult([(uid,) for uid in owner_ids])

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

        if "AS uid FROM atlas_skeletons" in sql:
            # notification_recipients.atlas_skeleton_editor_ids (§3.4) の模倣
            # (UNION の created_by / updated_by を合成して返す)。
            #
            # 実 SQL は `SELECT s.created_by::text AS uid FROM atlas_skeletons s
            # JOIN users u ON ... AND u.status <> 'deleted' ...`（アカウント
            # ライフサイクル管理 §8.4 で墓標ユーザー・孤児 UUID を除外する JOIN が
            # 付いた）。このフェイクは users を持たないため JOIN 条件は再現せず、
            # 編集者を全員返す。**墓標除外そのものの検証は
            # tests/core/test_notification_recipients.py（SQL 検査）で行う。**
            # 前方一致ではなく別名句の部分一致で照合するのは、SELECT 句の
            # テーブル別名（`s.`）の有無に追随するため。
            uids = {
                str(r.get("created_by"))
                for r in self.skeleton_rows
                if r["domain_key"] == domain_key and r.get("created_by")
            }
            uids |= {
                str(r.get("updated_by"))
                for r in self.skeleton_rows
                if r["domain_key"] == domain_key and r.get("updated_by")
            }
            return FakeResult([(uid,) for uid in uids])

        if sql.startswith("INSERT INTO atlas_skeletons"):
            status = "draft" if "'draft'" in sql else "frozen"
            row = {
                "domain_key": domain_key,
                "status": status,
                "version": str(p.get("version") or ""),
                "content": json.loads(p["content"]),
                "revision": 1,
                "generated_by": str(p.get("generated_by") or ""),
                "created_by": p.get("user_id"),
                "updated_by": p.get("user_id"),
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
                    if p.get("user_id"):
                        r["updated_by"] = p.get("user_id")
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

    def _dispatch_domain_meta(self, sql: str, p: dict) -> FakeResult:  # noqa: C901
        """atlas_domain_meta (migration 028 + 057 lifecycle 列) の最小模倣。"""
        domain_key = str(p.get("domain_key") or "")

        if sql.startswith("SELECT domain_key, name, lifecycle FROM atlas_domain_meta"):
            # list_domains 用: 全行 (WHERE 無し)
            rows = [
                (key, meta.get("name", ""), meta.get("lifecycle", "active"))
                for key, meta in self.domain_meta.items()
            ]
            return FakeResult(rows)

        if sql.startswith("SELECT domain_key, name FROM atlas_domain_meta"):
            rows = [(key, meta.get("name", "")) for key, meta in self.domain_meta.items()]
            return FakeResult(rows)

        if sql.startswith("SELECT domain_key FROM atlas_domain_meta"):
            rows = [
                (key,)
                for key, meta in self.domain_meta.items()
                if meta.get("lifecycle", "active") == "retired"
            ]
            return FakeResult(rows)

        if sql.startswith("SELECT lifecycle FROM atlas_domain_meta"):
            meta = self.domain_meta.get(domain_key)
            if not meta:
                return FakeResult()
            return FakeResult([(meta.get("lifecycle", "active"),)])

        if sql.startswith("SELECT name, description, target_domain, concept_vocabulary"):
            meta = self.domain_meta.get(domain_key)
            if not meta:
                return FakeResult()
            return FakeResult([(
                meta.get("name", ""),
                meta.get("description", ""),
                json.dumps(meta.get("target_domain", [])),
                meta.get("concept_vocabulary", ""),
            )])

        if sql.startswith("INSERT INTO atlas_domain_meta"):
            existing = self.domain_meta.setdefault(domain_key, {"name": domain_key, "lifecycle": "active"})
            if "retire_note" in sql:
                # retire_domain(): upsert + lifecycle='retired'
                existing["lifecycle"] = "retired"
                existing["retired_at"] = "now"
                existing["retired_by"] = p.get("user_id")
                existing["retire_note"] = str(p.get("note") or "")
            else:
                # save_domain_meta(): 本文フィールドの upsert (lifecycle は変更しない)
                existing["name"] = str(p.get("name") or domain_key)
                existing["description"] = str(p.get("description") or "")
                existing["target_domain"] = json.loads(p.get("target_domain") or "[]")
                existing["concept_vocabulary"] = str(p.get("concept_vocabulary") or "")
            return FakeResult(rowcount=1)

        if sql.startswith("UPDATE atlas_domain_meta"):
            # restore_domain(): lifecycle='active' + retired_at/by クリア
            meta = self.domain_meta.get(domain_key)
            if not meta:
                return FakeResult(rowcount=0)
            meta["lifecycle"] = "active"
            meta["retired_at"] = None
            meta["retired_by"] = None
            return FakeResult(rowcount=1)

        raise AssertionError(f"unhandled atlas_domain_meta SQL: {sql}")


def make_session_factory(fake: AtlasSkeletonTableFake) -> Callable[[], Any]:
    """core.postgres.get_session の差し替え用 (常に同じフェイクを返す)。"""

    def factory():
        return fake

    return factory
