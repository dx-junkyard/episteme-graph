"""draft / freeze / 楽観ロック パターンの共通基盤（Tier 3-20）。

`core/atlas_store.py`（`atlas_skeletons` 1テーブル、draft/frozen 排他行方式）と
`core/library/store.py`（`library_entries` + `library_entry_versions` 親子2テーブル
方式）に並存していた、以下2つの同型制御フローをここへ一本化する。

1. **楽観ロック更新** (:func:`update_with_revision_lock`): 「revision 照合 UPDATE
   → 不一致なら現在値を再 SELECT → conflict（または not-found）例外を送出」。
2. **同梱シードの冪等取込** (:func:`idempotent_seed_import`): 「候補列挙 → 存在
   チェック → 無ければ作成 → 1件の失敗は握って続行 → {imported, skipped, errors}
   を返す」。

**統合しないもの**（各ドメインに残す・意図的な差分）:
- draft の粒度（atlas=1 domain 1行 / library=多数行）
- freeze の実体（atlas=同一テーブル別 status 行 + draft 物理削除 /
  library=別テーブルへ追記 + 親カウンタ更新）
- status 語彙の意味論・created_by 列型（UUID vs TEXT）
- セッション規約（atlas=呼び出し側管理 / library=各関数が自前 open/close）
- 同梱シード作成失敗時の扱い（atlas=同梱データは信頼できる前提で fail-fast /
  library=1エントリ単位で握って続行。:func:`idempotent_seed_import` の
  ``catch_create_errors`` で明示的に選択させる）

本モジュールは FastAPI を import しない（core/ 共通ルール）。SQL 文字列は各
ドメインのテーブル・列に依存するため呼び出し側が組み立てて渡す（本モジュールは
制御フローのみを担い、過度な汎用化はしない）。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Iterable

from sqlalchemy import text as sa_text

logger = logging.getLogger(__name__)


class RevisionConflictError(Exception):
    """楽観ロックの衝突を表す共通基底例外。

    ``current_revision`` に衝突時点の最新値を持つ（無ければ None）。
    ドメイン側の例外（``atlas_store.DraftRevisionConflict`` /
    ``library.store.LibraryConflictError``）はこれを継承する。
    """

    def __init__(self, message: str, current_revision: int | None = None):
        super().__init__(message)
        self.current_revision = current_revision


# ---------------------------------------------------------------------------
# 1. 楽観ロック更新
# ---------------------------------------------------------------------------


def update_with_revision_lock(
    session: Any,
    *,
    update_sql: str,
    update_params: dict[str, Any],
    select_sql: str,
    select_params: dict[str, Any],
    conflict_exc: type[RevisionConflictError],
    conflict_message: str,
    not_found_exc: type[Exception] | None = None,
    not_found_message: str = "",
    revision_index: int = 0,
    revision_default: int | None = None,
):
    """revision 照合付きの UPDATE を実行する共通プリミティブ。

    1. ``update_sql``（``WHERE ... AND revision = :expected_revision`` を含む
       UPDATE 文であること）を実行する。``rowcount == 1`` なら成功として
       SQLAlchemy の ``Result`` をそのまま返す（``RETURNING`` 句がある場合は
       呼び出し側で ``.fetchone()`` すればよい）。
    2. ``rowcount != 1`` の場合は衝突または対象不在。``select_sql`` で現在の
       行を再取得する。

       - 行が無く ``not_found_exc`` が指定されていれば、それを
         ``not_found_exc(not_found_message or conflict_message)`` として送出する。
       - 行が無く ``not_found_exc`` 未指定なら
         ``conflict_exc(conflict_message, current_revision=None)`` を送出する。
       - 行があれば ``conflict_exc(conflict_message, current_revision=...)`` を
         送出する。``revision_default`` が指定されていれば
         ``int(row[revision_index] or revision_default)``、未指定なら
         ``int(row[revision_index])`` を現在値とする（ドメインごとの既存の
         コーナーケース挙動をそのまま踏襲するための差分）。

    トランザクション境界（commit/rollback）はここでは管理しない
    （atlas/library でセッション規約が異なるため、呼び出し側の責務のまま）。
    """
    result = session.execute(sa_text(update_sql), update_params)
    if getattr(result, "rowcount", 0) == 1:
        return result

    current = session.execute(sa_text(select_sql), select_params).fetchone()
    if current is None:
        if not_found_exc is not None:
            raise not_found_exc(not_found_message or conflict_message)
        raise conflict_exc(conflict_message, current_revision=None)

    if revision_default is not None:
        current_revision = int(current[revision_index] or revision_default)
    else:
        current_revision = int(current[revision_index])
    raise conflict_exc(conflict_message, current_revision=current_revision)


# ---------------------------------------------------------------------------
# 2. 同梱シードの冪等取込
# ---------------------------------------------------------------------------


def idempotent_seed_import(
    candidates: Iterable[Any],
    *,
    exists_fn: Callable[[Any], bool],
    create_fn: Callable[[Any], None],
    catch_create_errors: bool = True,
    on_created: Callable[[Any], None] | None = None,
    on_create_error: Callable[[Any, Exception], None] | None = None,
) -> dict:
    """カートリッジ同梱シードの冪等取込テンプレート。

    ``candidates`` を順に処理し、``exists_fn(candidate)`` が真ならスキップする
    （``skipped`` に加算）。偽なら ``create_fn(candidate)`` を呼ぶ。

    - ``catch_create_errors=True``（既定）: ``create_fn`` の例外を1件の失敗
      として握って続行する（取込全体を止めない）。``on_create_error`` が
      あれば ``(candidate, exc)`` で通知したうえで ``skipped`` に加算し、
      ``errors`` に ``str(exc)`` を追加する。
    - ``catch_create_errors=False``: ``create_fn`` の例外をそのまま伝播する
      （同梱データは信頼できる前提で fail-fast するドメイン向け）。

    ``exists_fn`` 自体の例外は握らない（DB 接続断など致命的な失敗はそのまま
    伝播させる。両ドメインの現行挙動を踏襲）。

    Returns:
        ``{"imported": int, "skipped": int, "errors": list[str]}``。
        ``errors`` は ``catch_create_errors=True`` のときのみ要素が入り得る。
    """
    imported = 0
    skipped = 0
    errors: list[str] = []

    for candidate in candidates:
        if exists_fn(candidate):
            skipped += 1
            continue

        if not catch_create_errors:
            create_fn(candidate)
            imported += 1
            if on_created is not None:
                on_created(candidate)
            continue

        try:
            create_fn(candidate)
        except Exception as exc:  # noqa: BLE001 — 1件の失敗で取込全体を止めない
            skipped += 1
            errors.append(str(exc))
            if on_create_error is not None:
                on_create_error(candidate, exc)
            continue

        imported += 1
        if on_created is not None:
            on_created(candidate)

    return {"imported": imported, "skipped": skipped, "errors": errors}
