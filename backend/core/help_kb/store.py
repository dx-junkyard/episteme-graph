"""``docs/manual`` の DB draft/freeze ストア（Phase 3 条件付き実装, migration 059）。

設計は ``docs/features/manual_help_kb_design.md`` §7-2。着手条件（(a) git 非利用の
編集者の運用開始 / (b) デプロイ独立の版操作要求 / (c) audience 別部分公開の必要）の
いずれかが満たされたときの実装で、``atlas_store.py`` / ``library/store.py`` の
draft/凍結/楽観ロックパターンを ``core/revision_store.py`` 経由で踏襲する。

**配信ソースの既定は ``files`` のまま**（デプロイ=凍結版の切替という Phase 1 運用を
壊さない）。DB 配信になるのは :func:`freeze` 実行後のみ（freeze = 配信ソースの明示
切替。:func:`set_serving_source` は files への切戻し・db への再切替の escape hatch）。
起動時に同梱データを自動で DB へ取り込み
以後 DB を正本化する ``atlas_store.import_bundled_skeletons()`` とは意図的に異なる
（help_kb は Phase 1/2 の「デプロイ=ファイル更新」運用を DB 導入後も既定として維持し、
DB 配信は運用者の明示操作でのみ有効化する）。

- draft は revision 楽観ロック（:func:`core.revision_store.update_with_revision_lock`
  へ委譲。コピペ禁止）。
- freeze は凍結検証ゲート（``validator.validate_manual_texts`` の全チェック +
  student denylist）を通過しないと :class:`FreezeValidationError` を送出する
  （PR レビューが効かない DB 経路の代替ガード）。
- 版は append-only（削除 API なし）。
- DB 障害時に files へ fail-open するのは呼び出し側（``manual.py``）の責務。
  本モジュール自体は例外を隠さず伝播する（呼び出し側が判断できるように）。

FastAPI は import しない（core/ 共通ルール）。セッションは各公開関数が自前で
開閉する（``core/library/store.py`` と同じ規約）。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from sqlalchemy import text as sa_text

from core import revision_store
from core.postgres import get_session

from . import validator as _validator
from .manual import AUDIENCES, manual_root

logger = logging.getLogger(__name__)


class ManualKbError(Exception):
    """help_kb DB ストア操作の基底エラー。"""


class DraftConflictError(ManualKbError, revision_store.RevisionConflictError):
    """楽観ロック衝突（expected_revision が現在値と不一致）。current_revision を持つ。"""


class DraftNotFoundError(ManualKbError):
    """指定 (audience, file) の draft が存在しない。"""


class FreezeValidationError(ManualKbError):
    """凍結検証ゲート不通過。``violations`` に人間可読の一覧を持つ（versions は増えない）。"""

    def __init__(self, violations: list[str]):
        super().__init__("manual kb freeze validation failed")
        self.violations = violations


class ServingSourceError(ManualKbError):
    """serving_source 切替の前提条件不足（例: freeze 未実行で db へ切替しようとした）。"""


def is_valid_audience(audience: str) -> bool:
    return audience in AUDIENCES


def is_valid_filename(filename: str) -> bool:
    """path traversal 防止（``.md`` のみ・パス区切り/相対参照を拒否）。"""
    if not filename or not filename.endswith(".md"):
        return False
    if "/" in filename or "\\" in filename or ".." in filename or "\x00" in filename:
        return False
    return True


def _require_valid_ref(audience: str, filename: str) -> None:
    if not is_valid_audience(audience):
        raise ValueError(f"invalid audience: {audience!r}")
    if not is_valid_filename(filename):
        raise ValueError(f"invalid file: {filename!r}")


# ---------------------------------------------------------------------------
# draft
# ---------------------------------------------------------------------------


def _row_to_draft(row: Any) -> dict:
    updated_at = row[4]
    return {
        "audience": row[0],
        "file": row[1],
        "content": row[2],
        "revision": int(row[3] or 1),
        "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else (updated_at or ""),
        "updated_by": row[5],
    }


_DRAFT_COLUMNS_SQL = "audience, file, content, revision, updated_at, updated_by"


def list_drafts() -> list[dict]:
    """全 draft 一覧（audience, file 昇順）。"""
    session = get_session()
    try:
        rows = session.execute(
            sa_text(
                f"SELECT {_DRAFT_COLUMNS_SQL} FROM manual_kb_drafts ORDER BY audience, file"
            )
        ).fetchall()
    finally:
        session.close()
    return [_row_to_draft(r) for r in rows]


def get_draft(audience: str, file: str) -> Optional[dict]:
    """1件の draft を取得する（存在しなければ None）。"""
    _require_valid_ref(audience, file)
    session = get_session()
    try:
        row = session.execute(
            sa_text(
                f"SELECT {_DRAFT_COLUMNS_SQL} FROM manual_kb_drafts "
                "WHERE audience = :audience AND file = :file"
            ),
            {"audience": audience, "file": file},
        ).fetchone()
    finally:
        session.close()
    return _row_to_draft(row) if row else None


def update_draft(
    audience: str,
    file: str,
    content: str,
    *,
    expected_revision: int,
    user_id: Optional[str] = None,
) -> dict:
    """draft を楽観ロック更新する。

    raises:
        ValueError — audience/file がパストラバーサル等で不正
        DraftNotFoundError — 対象行が存在しない（新規作成は :func:`seed_drafts_from_served`
            または直接 INSERT の専用経路のみ。UI からの draft 編集は既存行の更新に限定する）
        DraftConflictError — expected_revision が現在値と不一致（current_revision を持つ）
    """
    _require_valid_ref(audience, file)
    session = get_session()
    try:
        revision_store.update_with_revision_lock(
            session,
            update_sql="""
                UPDATE manual_kb_drafts
                   SET content = :content,
                       revision = revision + 1,
                       updated_by = :updated_by,
                       updated_at = now()
                 WHERE audience = :audience AND file = :file AND revision = :expected_revision
                """,
            update_params={
                "audience": audience,
                "file": file,
                "content": content,
                "updated_by": user_id or None,
                "expected_revision": int(expected_revision),
            },
            select_sql=(
                "SELECT revision FROM manual_kb_drafts "
                "WHERE audience = :audience AND file = :file"
            ),
            select_params={"audience": audience, "file": file},
            conflict_exc=DraftConflictError,
            conflict_message="draft が他の編集で更新されています。最新を読み込み直してください",
            not_found_exc=DraftNotFoundError,
            not_found_message=f"draft not found: {audience}/{file}",
            revision_default=1,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    draft = get_draft(audience, file)
    if draft is None:  # pragma: no cover — 更新直後の再取得なので通常到達しない
        raise DraftNotFoundError(f"draft not found after update: {audience}/{file}")
    return draft


def _served_texts_by_audience() -> dict[str, dict[str, str]]:
    """現在ファイルシステムに存在する ``docs/manual/<audience>/*.md`` の生テキスト。

    seed は常にファイルシステムを起点にする（DB 配信中であっても、シードは
    「今のファイルの中身を draft として複製する」操作であるため）。
    """
    root = manual_root()
    out: dict[str, dict[str, str]] = {}
    if root is None:
        return out
    for audience in AUDIENCES:
        directory = root / audience
        files: dict[str, str] = {}
        if directory.is_dir():
            for md in sorted(directory.glob("*.md")):
                try:
                    files[md.name] = md.read_text(encoding="utf-8")
                except OSError:
                    continue
        out[audience] = files
    return out


def seed_drafts_from_served(*, user_id: Optional[str] = None) -> dict:
    """現配信ファイルのスナップショットから draft を冪等シードする。

    既に draft 行がある (audience, file) はスキップする（上書きしない — 編集中の
    draft を壊さない）。:func:`core.revision_store.idempotent_seed_import` へ委譲する。

    Returns:
        ``{"imported": int, "skipped": int, "errors": list[str]}``
    """
    served = _served_texts_by_audience()
    candidates: list[tuple[str, str, str]] = [
        (audience, filename, content)
        for audience, files in served.items()
        for filename, content in files.items()
    ]

    session = get_session()
    try:

        def _exists(candidate: tuple[str, str, str]) -> bool:
            audience, filename, _content = candidate
            row = session.execute(
                sa_text(
                    "SELECT 1 FROM manual_kb_drafts WHERE audience = :audience AND file = :file"
                ),
                {"audience": audience, "file": filename},
            ).fetchone()
            return row is not None

        def _create(candidate: tuple[str, str, str]) -> None:
            audience, filename, content = candidate
            session.execute(
                sa_text(
                    """
                    INSERT INTO manual_kb_drafts (audience, file, content, revision, updated_by)
                    VALUES (:audience, :file, :content, 1, :updated_by)
                    ON CONFLICT (audience, file) DO NOTHING
                    """
                ),
                {
                    "audience": audience,
                    "file": filename,
                    "content": content,
                    "updated_by": user_id or None,
                },
            )

        result = revision_store.idempotent_seed_import(
            candidates,
            exists_fn=_exists,
            create_fn=_create,
            catch_create_errors=True,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return result


# ---------------------------------------------------------------------------
# 凍結（版発行）
# ---------------------------------------------------------------------------

# freeze の版採番（``SELECT COALESCE(MAX(version_no), 0) + 1``）を直列化するための
# advisory lock 名前空間。59 は本ストアを導入した migration 番号（衝突回避のための
# 識別子で、値そのものに意味は無い。``atlas_store.lock_domain_for_write`` と同型）。
_FREEZE_LOCK_SEED = 59


def _lock_freeze_for_write(session) -> None:
    """freeze のトランザクションを直列化する (トランザクションスコープの advisory lock)。

    freeze() は「現在の MAX(version_no) を読んで +1 して INSERT する」read-then-write
    のため、複数リクエストが同時に freeze すると version_no の UNIQUE 制約違反
    （生 IntegrityError）が起き得る。採番の SELECT より前でこのロックを取得することで、
    同時 freeze を直列化する。ロックはトランザクション終了 (commit / rollback) で
    自動解放される（``atlas_store.lock_domain_for_write`` と同じパターン）。
    """
    session.execute(
        sa_text(f"SELECT pg_advisory_xact_lock(hashtextextended(:key, {_FREEZE_LOCK_SEED}))"),
        {"key": "help_kb_freeze"},
    )


def freeze(*, user_id: Optional[str] = None) -> dict:
    """全 draft を集めて凍結検証ゲートを通過すれば新版を発行する。

    通過しない場合は :class:`FreezeValidationError`（``violations`` 属性）を送出し、
    ``manual_kb_versions`` / ``manual_kb_state`` は一切変更しない（部分適用しない）。
    通過すれば版を追記し ``manual_kb_state`` を ``serving_source='db'`` へ切り替える
    （凍結 = 配信ソースの明示切替。atlas/library と異なり draft は削除しない —
    次回編集の起点として残す）。

    raises:
        FreezeValidationError — 検証違反あり
    """
    session = get_session()
    try:
        rows = session.execute(
            sa_text(
                "SELECT audience, file, content FROM manual_kb_drafts ORDER BY audience, file"
            )
        ).fetchall()
    finally:
        session.close()

    texts_by_audience: dict[str, dict[str, str]] = {audience: {} for audience in AUDIENCES}
    for audience, filename, content in rows:
        texts_by_audience.setdefault(audience, {})[filename] = content

    violations = _validator.validate_manual_texts(texts_by_audience)
    if violations:
        raise FreezeValidationError(violations)

    files_snapshot = {
        audience: dict(files) for audience, files in texts_by_audience.items() if files
    }

    session = get_session()
    try:
        _lock_freeze_for_write(session)
        next_version = session.execute(
            sa_text("SELECT COALESCE(MAX(version_no), 0) + 1 FROM manual_kb_versions")
        ).scalar()
        next_version = int(next_version)
        session.execute(
            sa_text(
                """
                INSERT INTO manual_kb_versions (version_no, files, validation, created_by)
                VALUES (:version_no, CAST(:files AS jsonb), CAST(:validation AS jsonb), :created_by)
                """
            ),
            {
                "version_no": next_version,
                "files": json.dumps(files_snapshot, ensure_ascii=False),
                # 通過した検証結果（違反 0 件であることの記録。P4: 「検証していない」
                # わけではないことを正直に残す）。
                "validation": json.dumps({"violations": []}, ensure_ascii=False),
                "created_by": user_id or None,
            },
        )
        session.execute(
            sa_text(
                """
                UPDATE manual_kb_state
                   SET serving_source = 'db', active_version_no = :version_no, updated_at = now()
                 WHERE id = 1
                """
            ),
            {"version_no": next_version},
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    return {
        "version_no": next_version,
        "audience_file_counts": {a: len(f) for a, f in files_snapshot.items()},
    }


def get_active_version_texts() -> Optional[dict[str, dict[str, str]]]:
    """現行 active version の files（``{audience: {ファイル名: 本文}}``）。無ければ None。"""
    session = get_session()
    try:
        state_row = session.execute(
            sa_text("SELECT active_version_no FROM manual_kb_state WHERE id = 1")
        ).fetchone()
        if not state_row or state_row[0] is None:
            return None
        version_row = session.execute(
            sa_text("SELECT files FROM manual_kb_versions WHERE version_no = :v"),
            {"v": int(state_row[0])},
        ).fetchone()
    finally:
        session.close()
    if not version_row or version_row[0] is None:
        return None
    files = version_row[0]
    if isinstance(files, str):
        files = json.loads(files)
    return files


def list_versions() -> list[dict]:
    """版メタデータ一覧（version_no 降順。内容は含めず件数のみ — 削除 API は無い）。"""
    session = get_session()
    try:
        rows = session.execute(
            sa_text(
                "SELECT version_no, created_at, created_by, files "
                "FROM manual_kb_versions ORDER BY version_no DESC"
            )
        ).fetchall()
    finally:
        session.close()
    out: list[dict] = []
    for version_no, created_at, created_by, files in rows:
        if isinstance(files, str):
            files = json.loads(files)
        out.append(
            {
                "version_no": int(version_no),
                "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else (created_at or ""),
                "created_by": created_by,
                "audience_file_counts": {a: len(f) for a, f in (files or {}).items()},
            }
        )
    return out


# ---------------------------------------------------------------------------
# 配信ソース状態
# ---------------------------------------------------------------------------


def get_state() -> dict:
    """``manual_kb_state`` の単一行を返す（行が無い/未 migration 環境は既定 files）。"""
    session = get_session()
    try:
        row = session.execute(
            sa_text(
                "SELECT serving_source, active_version_no, updated_at "
                "FROM manual_kb_state WHERE id = 1"
            )
        ).fetchone()
    finally:
        session.close()
    if not row:
        return {"serving_source": "files", "active_version_no": None, "updated_at": ""}
    updated_at = row[2]
    return {
        "serving_source": row[0],
        "active_version_no": row[1],
        "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else (updated_at or ""),
    }


def set_serving_source(source: str) -> dict:
    """配信ソースを明示切替する（``"files"`` へはいつでも戻せる = fail-open の後始末）。

    raises:
        ValueError — source が files/db 以外
        ServingSourceError — db へ切替しようとしたが active version が無い（freeze 未実行）
    """
    if source not in ("files", "db"):
        raise ValueError(f"invalid serving_source: {source!r}")
    session = get_session()
    try:
        if source == "db":
            active = session.execute(
                sa_text("SELECT active_version_no FROM manual_kb_state WHERE id = 1")
            ).fetchone()
            if not active or active[0] is None:
                raise ServingSourceError(
                    "db serving source を有効化するには先に freeze してください"
                )
        session.execute(
            sa_text(
                "UPDATE manual_kb_state SET serving_source = :source, updated_at = now() "
                "WHERE id = 1"
            ),
            {"source": source},
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return get_state()
