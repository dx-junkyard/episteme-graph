"""L層ライブラリ — draft (`library_entries`) / 凍結版 (`library_entry_versions`) の DB ストア。

`core/atlas_store.py` の draft/凍結/楽観ロックパターンを踏襲する。

- draft 編集は ``expected_revision`` 照合の楽観ロック（rowcount != 1 は
  :class:`LibraryConflictError` / 対象行が無ければ :class:`LibraryNotFoundError`）。
- 凍結 (`freeze_entry`) はエントリ全体スナップショットを version として append するのみで、
  取り消しは無い（修正は次版として発行する）。
- 削除 API は無い（P4）。``retire_entry`` / ``restore_entry`` の状態遷移のみ。
- 本モジュールは FastAPI を import しない（core/ 共通ルール）。

セッションは各公開関数が自前で開閉する（呼び出し側はセッション管理を意識しなくてよい）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text as sa_text

from core.postgres import get_session

from . import schema

logger = logging.getLogger(__name__)


class LibraryError(Exception):
    """L層ライブラリ操作の基底エラー。"""


class LibraryNotFoundError(LibraryError):
    """指定した entry_id が存在しない。"""


class LibraryConflictError(LibraryError):
    """楽観ロック衝突（expected_revision が現在値と不一致）。current_revision に現在値を持つ。"""

    def __init__(self, message: str, current_revision: int | None = None):
        super().__init__(message)
        self.current_revision = current_revision


# ---------------------------------------------------------------------------
# 行 ⇄ dict 変換
# ---------------------------------------------------------------------------

# id は uuid → text にキャストして返す（呼び出し側は常に str の id を扱う）。
_ENTRY_COLUMNS_SQL = """
    id::text, domain_key, entry_type, name, aliases, summary, body,
    exemplar_images, source_component_ids, source_document_ids,
    status, revision, latest_version_no, created_by, updated_by,
    created_at, updated_at
"""

_SELECT_ENTRY_SQL = f"SELECT {_ENTRY_COLUMNS_SQL} FROM library_entries"

_VERSION_COLUMNS_SQL = """
    id::text, entry_id::text, version_no, content, note, published_by, created_at
"""


def _row_to_entry(row: Any) -> dict:
    entry = schema.LibraryEntry(
        id=str(row[0]),
        domain_key=row[1] or "",
        entry_type=row[2] or "",
        name=row[3] or "",
        aliases=schema.as_list(row[4]),
        summary=row[5] or "",
        body=schema.as_dict(row[6]),
        exemplar_images=schema.as_list(row[7]),
        source_component_ids=schema.as_list(row[8]),
        source_document_ids=schema.as_list(row[9]),
        status=row[10] or "",
        revision=int(row[11] or 1),
        latest_version_no=int(row[12] or 0),
        created_by=row[13],
        updated_by=row[14],
        created_at=row[15].isoformat() if row[15] else "",
        updated_at=row[16].isoformat() if row[16] else "",
    )
    return entry.to_dict()


def _row_to_version(row: Any) -> dict:
    version = schema.LibraryEntryVersion(
        id=str(row[0]),
        entry_id=str(row[1]),
        version_no=int(row[2] or 0),
        content=schema.as_dict(row[3]),
        note=row[4] or "",
        published_by=row[5],
        created_at=row[6].isoformat() if row[6] else "",
    )
    return version.to_dict()


def _json_param(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 作成・取得・一覧
# ---------------------------------------------------------------------------


def create_entry(
    *,
    domain_key: str,
    entry_type: str,
    name: str,
    aliases: list[str] | None = None,
    summary: str = "",
    body: dict | None = None,
    exemplar_images: list[dict] | None = None,
    source_component_ids: list[str] | None = None,
    source_document_ids: list[str] | None = None,
    created_by: str | None = None,
) -> dict:
    """draft エントリを新規作成する（昇格 / 手動作成 / シード取込の共通経路）。"""
    if not domain_key:
        raise ValueError("domain_key is required")
    if not schema.is_valid_entry_type(entry_type):
        raise ValueError(f"invalid entry_type: {entry_type!r}")
    if not name:
        raise ValueError("name is required")

    session = get_session()
    try:
        row = session.execute(
            sa_text(
                f"""
                INSERT INTO library_entries
                    (domain_key, entry_type, name, aliases, summary, body,
                     exemplar_images, source_component_ids, source_document_ids,
                     created_by, updated_by)
                VALUES
                    (:domain_key, :entry_type, :name, CAST(:aliases AS jsonb),
                     :summary, CAST(:body AS jsonb), CAST(:exemplar_images AS jsonb),
                     CAST(:source_component_ids AS jsonb), CAST(:source_document_ids AS jsonb),
                     :created_by, :created_by)
                RETURNING {_ENTRY_COLUMNS_SQL}
                """
            ),
            {
                "domain_key": domain_key,
                "entry_type": entry_type,
                "name": name,
                "aliases": _json_param(list(aliases or [])),
                "summary": summary or "",
                "body": _json_param(body or {}),
                "exemplar_images": _json_param(list(exemplar_images or [])),
                "source_component_ids": _json_param(list(source_component_ids or [])),
                "source_document_ids": _json_param(list(source_document_ids or [])),
                "created_by": created_by or None,
            },
        ).fetchone()
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return _row_to_entry(row)


def get_entry(entry_id: str) -> dict | None:
    """id でエントリを1件取得する（存在しなければ None）。"""
    session = get_session()
    try:
        row = session.execute(
            sa_text(f"{_SELECT_ENTRY_SQL} WHERE id = CAST(:id AS uuid) LIMIT 1"),
            {"id": entry_id},
        ).fetchone()
    finally:
        session.close()
    return _row_to_entry(row) if row else None


def list_entries(
    *,
    domain_key: str | None = None,
    entry_type: str | None = None,
    q: str | None = None,
    include_retired: bool = False,
) -> list[dict]:
    """エントリ一覧（既定は status='active' のみ。q は name/aliases/summary の部分一致）。"""
    if entry_type is not None and not schema.is_valid_entry_type(entry_type):
        raise ValueError(f"invalid entry_type: {entry_type!r}")

    conditions: list[str] = []
    params: dict[str, Any] = {}
    if not include_retired:
        conditions.append("status = 'active'")
    if domain_key:
        conditions.append("domain_key = :domain_key")
        params["domain_key"] = domain_key
    if entry_type:
        conditions.append("entry_type = :entry_type")
        params["entry_type"] = entry_type
    if q:
        conditions.append("(name ILIKE :q OR aliases::text ILIKE :q OR summary ILIKE :q)")
        params["q"] = f"%{q}%"
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    session = get_session()
    try:
        rows = session.execute(
            sa_text(f"{_SELECT_ENTRY_SQL} {where} ORDER BY domain_key, entry_type, name"),
            params,
        ).fetchall()
    finally:
        session.close()
    return [_row_to_entry(row) for row in rows]


# ---------------------------------------------------------------------------
# draft 更新（楽観ロック）
# ---------------------------------------------------------------------------


def update_entry(entry_id: str, *, expected_revision: int, updated_by: str | None = None, **fields: Any) -> dict:
    """draft をホワイトリストフィールドのみ更新する（楽観ロック）。

    raises:
        ValueError — ホワイトリスト外のフィールド指定 / フィールド未指定 / name を空にする更新
        LibraryNotFoundError — entry_id が存在しない
        LibraryConflictError — expected_revision が現在値と不一致（current_revision を持つ）
    """
    unknown = set(fields) - set(schema.UPDATABLE_FIELDS)
    if unknown:
        raise ValueError(f"non-updatable fields: {sorted(unknown)}")
    if not fields:
        raise ValueError("no fields to update")
    if "name" in fields and not fields["name"]:
        raise ValueError("name cannot be empty")

    set_clauses: list[str] = []
    params: dict[str, Any] = {
        "id": entry_id,
        "expected_revision": int(expected_revision),
        "updated_by": updated_by or None,
    }
    for key, value in fields.items():
        if key in schema.JSON_FIELDS:
            set_clauses.append(f"{key} = CAST(:{key} AS jsonb)")
            params[key] = _json_param(value)
        else:
            set_clauses.append(f"{key} = :{key}")
            params[key] = value
    set_sql = ", ".join(set_clauses)

    session = get_session()
    try:
        row = session.execute(
            sa_text(
                f"""
                UPDATE library_entries
                   SET {set_sql},
                       revision = revision + 1,
                       updated_by = :updated_by,
                       updated_at = now()
                 WHERE id = CAST(:id AS uuid) AND revision = :expected_revision
                RETURNING {_ENTRY_COLUMNS_SQL}
                """
            ),
            params,
        ).fetchone()
        if row is None:
            existing = session.execute(
                sa_text("SELECT revision FROM library_entries WHERE id = CAST(:id AS uuid) LIMIT 1"),
                {"id": entry_id},
            ).fetchone()
            session.rollback()
            if not existing:
                raise LibraryNotFoundError(f"library entry not found: {entry_id}")
            raise LibraryConflictError(
                "library entry has been updated by someone else",
                current_revision=int(existing[0] or 1),
            )
        session.commit()
    except (LibraryNotFoundError, LibraryConflictError):
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return _row_to_entry(row)


# ---------------------------------------------------------------------------
# 凍結（版発行）
# ---------------------------------------------------------------------------


def freeze_entry(
    entry_id: str,
    *,
    published_by: str | None = None,
    note: str | None = None,
    embed_fn: Any = None,
) -> dict:
    """draft を凍結し新しい version を append する（凍結は取り消さない。§6-3）。

    embedding の計算は ``embed_fn(text) -> list[float]`` が渡されればそれを使い、
    無ければ ``core.llm.generate_embeddings`` を遅延 import して使う（1件呼び）。
    embedding が計算できなくても凍結自体は失敗させない
    （NULL embedding で凍結し、戻り値の ``embedding_status`` に 'failed' を記録する）。

    raises:
        LibraryNotFoundError — entry_id が存在しない
    """
    session = get_session()
    try:
        row = session.execute(
            sa_text(f"{_SELECT_ENTRY_SQL} WHERE id = CAST(:id AS uuid) LIMIT 1"),
            {"id": entry_id},
        ).fetchone()
        if row is None:
            raise LibraryNotFoundError(f"library entry not found: {entry_id}")
        entry = _row_to_entry(row)
        version_no = int(entry["latest_version_no"]) + 1
        content = entry

        embedding_vector: list[float] | None = None
        embedding_status = "ok"
        text_for_embedding = schema.embedding_source_text(entry)
        if text_for_embedding:
            try:
                if embed_fn is not None:
                    embedding_vector = embed_fn(text_for_embedding)
                else:
                    from core.llm import generate_embeddings

                    vectors = generate_embeddings([text_for_embedding])
                    embedding_vector = vectors[0] if vectors else None
            except Exception:  # noqa: BLE001 — embedding 失敗は凍結を止めない
                logger.warning(
                    "library entry embedding failed for %s; freezing without embedding",
                    entry_id,
                    exc_info=True,
                )
                embedding_vector = None
                embedding_status = "failed"
        else:
            # name/summary/body すら空の縮退ケース（通常は create_entry / update_entry の
            # 必須チェックにより発生しない）。実際の計算失敗ではないため区別する。
            embedding_status = "skipped"

        version_row = session.execute(
            sa_text(
                """
                INSERT INTO library_entry_versions
                    (entry_id, version_no, content, embedding, note, published_by)
                VALUES
                    (CAST(:entry_id AS uuid), :version_no, CAST(:content AS jsonb),
                     :embedding, :note, :published_by)
                RETURNING id::text, version_no, created_at
                """
            ),
            {
                "entry_id": entry_id,
                "version_no": version_no,
                "content": _json_param(content),
                "embedding": str(embedding_vector) if embedding_vector is not None else None,
                "note": note or "",
                "published_by": published_by or None,
            },
        ).fetchone()

        session.execute(
            sa_text(
                """
                UPDATE library_entries
                   SET latest_version_no = :version_no, updated_at = now()
                 WHERE id = CAST(:id AS uuid)
                """
            ),
            {"version_no": version_no, "id": entry_id},
        )
        session.commit()
    except LibraryNotFoundError:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    return {
        "id": version_row[0],
        "entry_id": entry_id,
        "version_no": int(version_row[1]),
        "content": content,
        "note": note or "",
        "published_by": published_by or None,
        "created_at": version_row[2].isoformat() if version_row[2] else "",
        "embedding_status": embedding_status,
    }


# ---------------------------------------------------------------------------
# 状態遷移（retire / restore） — 削除 API は無い（P4）
# ---------------------------------------------------------------------------


def _set_status(entry_id: str, status: str, *, updated_by: str | None) -> dict:
    session = get_session()
    try:
        row = session.execute(
            sa_text(
                f"""
                UPDATE library_entries
                   SET status = :status, updated_by = :updated_by, updated_at = now()
                 WHERE id = CAST(:id AS uuid)
                RETURNING {_ENTRY_COLUMNS_SQL}
                """
            ),
            {"status": status, "updated_by": updated_by or None, "id": entry_id},
        ).fetchone()
        if row is None:
            session.rollback()
            raise LibraryNotFoundError(f"library entry not found: {entry_id}")
        session.commit()
    except LibraryNotFoundError:
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return _row_to_entry(row)


def retire_entry(entry_id: str, updated_by: str | None = None) -> dict:
    """status を 'retired' に遷移する（行削除はしない）。"""
    return _set_status(entry_id, schema.STATUS_RETIRED, updated_by=updated_by)


def restore_entry(entry_id: str, updated_by: str | None = None) -> dict:
    """status を 'active' に戻す。"""
    return _set_status(entry_id, schema.STATUS_ACTIVE, updated_by=updated_by)


# ---------------------------------------------------------------------------
# 版履歴
# ---------------------------------------------------------------------------


def list_versions(entry_id: str) -> list[dict]:
    """凍結版履歴を新しい順で返す（embedding は返さない）。"""
    session = get_session()
    try:
        rows = session.execute(
            sa_text(
                f"""
                SELECT {_VERSION_COLUMNS_SQL}
                  FROM library_entry_versions
                 WHERE entry_id = CAST(:entry_id AS uuid)
                 ORDER BY version_no DESC
                """
            ),
            {"entry_id": entry_id},
        ).fetchall()
    finally:
        session.close()
    return [_row_to_version(row) for row in rows]


def get_version(entry_id: str, version_no: int) -> dict | None:
    """特定版を1件取得する（embedding は返さない）。"""
    session = get_session()
    try:
        row = session.execute(
            sa_text(
                f"""
                SELECT {_VERSION_COLUMNS_SQL}
                  FROM library_entry_versions
                 WHERE entry_id = CAST(:entry_id AS uuid) AND version_no = :version_no
                 LIMIT 1
                """
            ),
            {"entry_id": entry_id, "version_no": int(version_no)},
        ).fetchone()
    finally:
        session.close()
    return _row_to_version(row) if row else None


# ---------------------------------------------------------------------------
# ダッシュボード集計
# ---------------------------------------------------------------------------


def domain_summary() -> list[dict]:
    """domain_key ごとの entry 数・凍結済み数・retired 数のサマリ。"""
    session = get_session()
    try:
        rows = session.execute(
            sa_text(
                """
                SELECT domain_key,
                       count(*) FILTER (WHERE status = 'active') AS entry_count,
                       count(*) FILTER (WHERE status = 'active' AND latest_version_no > 0) AS frozen_count,
                       count(*) FILTER (WHERE status = 'retired') AS retired_count
                  FROM library_entries
                 GROUP BY domain_key
                 ORDER BY domain_key
                """
            )
        ).fetchall()
    finally:
        session.close()
    return [
        {
            "domain_key": row[0],
            "entry_count": int(row[1] or 0),
            "frozen_count": int(row[2] or 0),
            "retired_count": int(row[3] or 0),
        }
        for row in rows
    ]
