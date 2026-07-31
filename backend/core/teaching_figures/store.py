"""``course_teaching_figures`` / ``teaching_figure_suggestions`` の DB プリミティブ
（migration 063・設計書 §3・§8）。

``core/deliberation/store.py`` と同じ生 SQL（``sqlalchemy.text``）の流儀だが、
**セッションは呼び出し側が管理する**（各関数の第1引数に受け取り、
``commit`` / ``rollback`` / ``close`` は一切しない）。理由: 図の採用（adopted 遷移）は
トピック側の ``linked_figure_ids`` / ``evidence_links`` 登録と**同一トランザクション**で
行う必要がある（設計書 §7.1b）。store 側で commit すると原子性が壊れる。

守っている不変条項:

- **FG7 情報を落とさない**: 図・提案の行削除 API は無い（状態遷移のみ）。
  SVG 差し替え時は旧版を ``revisions`` へ append する（上限 :data:`MAX_REVISIONS`。
  古い順に間引いた場合は間引いた事実を残す）。唯一の DELETE は
  :func:`delete_figures_for_course`（コース物理削除に同乗する孤児掃除）。
- **FG8 数値を見せない**: :func:`list_suggestions` は confidence の生値を返さず
  ``confidence_label``（段階ラベル）へ変換する。
- **FG2 確定は人間**: :func:`create_suggestions` は常に ``status='candidate'`` で挿入する
  （引数で status を受け取らない）。
- **FG5 模式図の表記**: ``figure_kind='data_plot_schematic'`` の caption には
  「模式図」表記をここで強制付与する（:func:`schema.enforce_schematic_caption`）。
  DB 書き込みの**唯一の入口**である store に置くことで、route を経由しない将来の
  呼び出し元でも実データと誤読される caption が保存されない
  （sanitizer と同じ「唯一の入口」原則）。

**発行する SQL の列は migration 063 の定義列だけ**（``updated_by`` 列は存在しない —
更新者は ``revisions`` エントリと ``theory_review_events`` の監査に残す設計）。
実 PostgreSQL でしか出ない 42703 を fake session が隠さないよう、列名の包含検査は
``tests/test_teaching_figures_guardrails.py::TestStoreSqlColumnsExistInMigration`` が
構造的に固定する。

FastAPI は import しない（core/ 共通ルール）。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from sqlalchemy import text as sa_text

from core.teaching_figures import schema

# revisions（旧版）の保持上限。超過分は古い順に間引き、間引いた事実を
# 最古の保持エントリに ``revisions_truncated: true`` として残す（FG7）。
MAX_REVISIONS = 20

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class TeachingFigureNotFoundError(LookupError):
    """指定した figure_id の行が存在しない（API 層は 404 にマッピングする）。"""


# ---------------------------------------------------------------------------
# 小さなヘルパ
# ---------------------------------------------------------------------------


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_list(value: Any) -> list:
    """JSONB 列の素朴な正規化（psycopg2 は多くの場合すでに list へデコード済み）。"""
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except Exception:  # noqa: BLE001 — 壊れた JSON は空として扱う（読みを止めない）
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _is_uuid(value: Any) -> bool:
    """UUID 文字列かを判定する。

    不正な id を SQL の ``CAST(... AS uuid)`` に渡すとトランザクションが汚れる
    （呼び出し側の同一トランザクションを巻き込む）ため、事前に弾いて
    「該当なし」に倒す。
    """
    return bool(_UUID_RE.match(str(value or "")))


def _iso(value: Any) -> str:
    return value.isoformat() if value else ""


# ---------------------------------------------------------------------------
# course_teaching_figures
# ---------------------------------------------------------------------------

_FIGURE_COLUMNS_SQL = """
    id::text, course_id, topic_id, created_by::text, title, caption, figure_kind,
    svg_source, minio_key, content_type, status, source_suggestion_id::text,
    revisions, created_at, updated_at
"""


def _row_to_figure(row: Any) -> dict:
    revisions = _json_list(row[12])
    return {
        "id": str(row[0]),
        "course_id": row[1] or "",
        "topic_id": row[2],
        "created_by": row[3],
        "title": row[4] or "",
        "caption": row[5] or "",
        "figure_kind": row[6] or "",
        "svg_source": row[7] or "",
        "minio_key": row[8] or "",
        "content_type": row[9] or schema.SVG_CONTENT_TYPE,
        "status": row[10] or "",
        "source_suggestion_id": row[11],
        "revisions": revisions,
        # 間引きが起きたかどうかを1つのフラグでも読めるようにする（FG7 の正直さ）。
        "revisions_truncated": any(
            isinstance(entry, dict) and entry.get("revisions_truncated")
            for entry in revisions
        ),
        "created_at": _iso(row[13]),
        "updated_at": _iso(row[14]),
    }


def create_teaching_figure(
    session: Any,
    *,
    course_id: str,
    topic_id: str | None,
    created_by: str | None,
    title: str,
    caption: str,
    figure_kind: str,
    svg_source: str,
    minio_key: str,
    content_type: str,
    status: str,
    source_suggestion_id: str | None = None,
    figure_id: str | None = None,
) -> dict:
    """生成図を1件作成する（``svg_source`` は**サニタイズ済み**であること）。

    ``status`` は ``draft``（ストック）/ ``adopted``（配信対象）のいずれか。
    採用時のトピック側登録（設計書 §7.1b）は呼び出し側が同一トランザクションで行う。
    ``caption`` は ``figure_kind='data_plot_schematic'`` のとき「模式図」表記を
    強制付与して保存する（FG5。**保存後の caption は戻り値の行から読むこと** —
    引数の caption と一致しない場合がある）。

    ``figure_id`` は任意。MinIO キーが ``teaching/{course_id}/{id}.svg``（id 依存）で
    あるため、**呼び出し側が id とキーを先に決められる**ように受け口を開けている
    （``schema.new_figure_id()`` + ``schema.minio_key_for(course_id, figure_id)``）。
    省略時は DB 既定の ``gen_random_uuid()`` が採番する。
    配信スナップショットの実アップロードは**行の commit 後**に行う
    （正本は DB の ``svg_source``。``routes/teaching_figures.py::_upload_figure_snapshot``）。

    Raises:
        ValueError: 語彙外の ``figure_kind`` / ``status`` / ``figure_id``、または必須値が空。
    """
    if not str(course_id or "").strip():
        raise ValueError("course_id is required")
    if not schema.is_valid_figure_kind(figure_kind):
        raise ValueError(f"invalid figure_kind: {figure_kind!r}")
    if not schema.is_valid_figure_status(status):
        raise ValueError(f"invalid status: {status!r}")
    if status == schema.FIGURE_STATUS_RETIRED:
        # 新規行が最初から retired になる経路は無い（回収は既存行の遷移）。
        raise ValueError("a new teaching figure cannot start as 'retired'")
    if not str(svg_source or "").strip():
        raise ValueError("svg_source is required")
    if not str(minio_key or "").strip():
        raise ValueError("minio_key is required")
    if figure_id is not None and not _is_uuid(figure_id):
        raise ValueError(f"invalid figure_id: {figure_id!r}")

    # id 列は既定 gen_random_uuid()。明示指定があるときのみ INSERT 対象に加える。
    id_column = "id, " if figure_id else ""
    id_value = "CAST(:figure_id AS uuid), " if figure_id else ""

    row = session.execute(
        sa_text(
            f"""
            INSERT INTO course_teaching_figures
                ({id_column}course_id, topic_id, created_by, title, caption, figure_kind,
                 svg_source, minio_key, content_type, status, source_suggestion_id)
            VALUES
                ({id_value}:course_id, :topic_id, CAST(:created_by AS uuid), :title, :caption,
                 :figure_kind, :svg_source, :minio_key, :content_type, :status,
                 CAST(:source_suggestion_id AS uuid))
            RETURNING {_FIGURE_COLUMNS_SQL}
            """
        ),
        {
            "figure_id": str(figure_id) if figure_id else None,
            "course_id": str(course_id),
            "topic_id": str(topic_id) if topic_id else None,
            "created_by": created_by if _is_uuid(created_by) else None,
            "title": str(title or ""),
            # FG5: 模式グラフは caption でも模式であることを示す（唯一の入口で強制）。
            "caption": schema.enforce_schematic_caption(caption, figure_kind),
            "figure_kind": figure_kind,
            "svg_source": svg_source,
            "minio_key": str(minio_key),
            "content_type": str(content_type or schema.SVG_CONTENT_TYPE),
            "status": status,
            "source_suggestion_id": (
                source_suggestion_id if _is_uuid(source_suggestion_id) else None
            ),
        },
    ).fetchone()
    return _row_to_figure(row)


def get_teaching_figure(session: Any, figure_id: str) -> dict | None:
    """id で1件取得する（存在しない・UUID でない id は ``None``）。"""
    if not _is_uuid(figure_id):
        return None
    row = session.execute(
        sa_text(
            f"""
            SELECT {_FIGURE_COLUMNS_SQL}
              FROM course_teaching_figures
             WHERE id = CAST(:id AS uuid)
             LIMIT 1
            """
        ),
        {"id": str(figure_id)},
    ).fetchone()
    return _row_to_figure(row) if row else None


#: 画像配信に必要な最小列（**``revisions`` を含めない**）。旧版 SVG は数MB になり得るので、
#: 画像1枚を返すだけのリクエストで引かない（設計書 §7.2 の配信経路）。
_FIGURE_DELIVERY_COLUMNS_SQL = """
    id::text, course_id, status, minio_key, content_type, svg_source
"""


def get_teaching_figure_for_delivery(session: Any, figure_id: str) -> dict | None:
    """画像配信用の最小投影で1件取得する（``revisions`` を SELECT しない）。

    返すキーは ``id`` / ``course_id`` / ``status`` / ``minio_key`` / ``content_type`` /
    ``svg_source`` のみ。配信ゲート（course_id 一致・``status='adopted'``）と MinIO 取得、
    正本 ``svg_source`` へのフェイルソフトに必要なものだけを載せる。

    メタデータ編集や revisions の検査が必要な経路は :func:`get_teaching_figure` を使う。
    """
    if not _is_uuid(figure_id):
        return None
    row = session.execute(
        sa_text(
            f"""
            SELECT {_FIGURE_DELIVERY_COLUMNS_SQL}
              FROM course_teaching_figures
             WHERE id = CAST(:id AS uuid)
             LIMIT 1
            """
        ),
        {"id": str(figure_id)},
    ).fetchone()
    if not row:
        return None
    return {
        "id": str(row[0]),
        "course_id": row[1] or "",
        "status": row[2] or "",
        "minio_key": row[3] or "",
        "content_type": row[4] or schema.SVG_CONTENT_TYPE,
        "svg_source": row[5] or "",
    }


def list_teaching_figures(
    session: Any,
    course_id: str,
    *,
    statuses: Sequence[str] | None = None,
) -> list[dict]:
    """コースの生成図一覧（新しい順）。``statuses`` 未指定なら全状態（retired 含む）。

    Raises:
        ValueError: 語彙外の status が含まれている（fail-closed）。
    """
    params: dict[str, Any] = {"course_id": str(course_id or "")}
    where = "WHERE course_id = :course_id"
    if statuses is not None:
        wanted = [str(s) for s in statuses]
        invalid = [s for s in wanted if not schema.is_valid_figure_status(s)]
        if invalid:
            raise ValueError(f"invalid status filter: {invalid}")
        if not wanted:
            return []
        where += " AND status = ANY(:statuses)"
        params["statuses"] = wanted

    rows = session.execute(
        sa_text(
            f"""
            SELECT {_FIGURE_COLUMNS_SQL}
              FROM course_teaching_figures
              {where}
             ORDER BY created_at DESC, id
            """
        ),
        params,
    ).fetchall()
    return [_row_to_figure(row) for row in rows]


def adopted_figures_map(session: Any, course_id: str) -> dict[str, dict]:
    """``figure_id -> {caption, content_type, title}``（``status='adopted'`` のみ）。

    ``routes/lecture.py::_load_course_figures_by_id`` が抽出図の解決マップへ
    マージするための最小投影（設計書 §7.1）。draft / retired は入らない（FG4）。
    SVG 本体は含めない（配信は画像エンドポイント経由）。
    """
    rows = session.execute(
        sa_text(
            """
            SELECT id::text, caption, content_type, title
              FROM course_teaching_figures
             WHERE course_id = :course_id AND status = :status
             ORDER BY created_at
            """
        ),
        {"course_id": str(course_id or ""), "status": schema.FIGURE_STATUS_ADOPTED},
    ).fetchall()
    return {
        str(row[0]): {
            "caption": row[1] or "",
            "content_type": row[2] or schema.SVG_CONTENT_TYPE,
            "title": row[3] or "",
        }
        for row in rows
    }


def _append_revision(current: list, *, svg_source: str, caption: str, updated_by: Any) -> list:
    """旧版を ``revisions`` へ append し、上限で古い順に間引く（FG7）。"""
    entry = {
        "svg_source": svg_source,
        "caption": caption,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": updated_by,
    }
    revisions = [e for e in current if isinstance(e, dict)] + [entry]
    if len(revisions) > MAX_REVISIONS:
        revisions = revisions[-MAX_REVISIONS:]
        # 「間引いた」という事実を最古の保持エントリに残す（黙って消さない）。
        oldest = dict(revisions[0])
        oldest["revisions_truncated"] = True
        revisions[0] = oldest
    return revisions


def update_teaching_figure(
    session: Any,
    figure_id: str,
    *,
    title: str | None = None,
    caption: str | None = None,
    svg_source: str | None = None,
    minio_key: str | None = None,
    status: str | None = None,
    updated_by: str | None = None,
) -> dict:
    """タイトル・キャプション・SVG・MinIO キー・状態を更新する。

    ``svg_source`` を渡した場合（SVG 差し替え）は、**更新前**の
    ``{svg_source, caption, updated_at, updated_by}`` を ``revisions`` へ append する
    （上限 :data:`MAX_REVISIONS`・古い順に間引き、間引いた事実を残す）。
    ``svg_source`` はサニタイズ済みであること（サニタイズは呼び出し側の責務）。

    ``caption`` は現在の ``figure_kind`` に応じて「模式図」表記を強制付与する（FG5）。

    ``updated_by`` は **UPDATE 文の列にしない**（migration 063 に ``updated_by`` 列は
    無い。実 PostgreSQL では 42703 で全 PATCH が落ちる）。更新者は ``revisions``
    エントリと ``theory_review_events`` の監査記帳に残す。

    Raises:
        ValueError: 語彙外の ``status`` / 更新フィールドが1つも無い。
        TeachingFigureNotFoundError: 対象行が無い（UUID でない id も同様）。
    """
    if status is not None and not schema.is_valid_figure_status(status):
        raise ValueError(f"invalid status: {status!r}")
    if all(v is None for v in (title, caption, svg_source, minio_key, status)):
        raise ValueError("no fields to update")
    if not _is_uuid(figure_id):
        raise TeachingFigureNotFoundError(f"teaching figure not found: {figure_id}")

    current = get_teaching_figure(session, figure_id)
    if current is None:
        raise TeachingFigureNotFoundError(f"teaching figure not found: {figure_id}")

    # 更新者は列に持たない（migration 063 に updated_by 列は無い）。revisions エントリと
    # 監査記帳が帰属の正本。
    updated_by_id = updated_by if _is_uuid(updated_by) else None
    if caption is not None:
        caption = schema.enforce_schematic_caption(
            caption, str(current.get("figure_kind") or "")
        )

    set_clauses = ["updated_at = now()"]
    params: dict[str, Any] = {"id": str(figure_id)}
    for column, value in (
        ("title", title),
        ("caption", caption),
        ("minio_key", minio_key),
        ("status", status),
    ):
        if value is not None:
            set_clauses.append(f"{column} = :{column}")
            params[column] = str(value)

    if svg_source is not None:
        set_clauses.append("svg_source = :svg_source")
        params["svg_source"] = svg_source
        set_clauses.append("revisions = CAST(:revisions AS jsonb)")
        params["revisions"] = _dump(
            _append_revision(
                current.get("revisions") or [],
                svg_source=current.get("svg_source") or "",
                caption=current.get("caption") or "",
                updated_by=updated_by_id,
            )
        )

    row = session.execute(
        sa_text(
            f"""
            UPDATE course_teaching_figures
               SET {', '.join(set_clauses)}
             WHERE id = CAST(:id AS uuid)
            RETURNING {_FIGURE_COLUMNS_SQL}
            """
        ),
        params,
    ).fetchone()
    if row is None:
        raise TeachingFigureNotFoundError(f"teaching figure not found: {figure_id}")
    return _row_to_figure(row)


def delete_figures_for_course(session: Any, course_id: str) -> list[str]:
    """コース物理削除に同乗する孤児掃除（**唯一の DELETE 経路**）。

    行を削除し、MinIO オブジェクトキーの一覧を返す。MinIO 側の掃除は呼び出し側が
    :meth:`core.storage.StorageManager.remove_object` で行う（best-effort・commit 後。
    失敗しても DB 削除は止めない — 設計書 §3.1）。

    **呼び出し元はコース物理削除の5経路すべて**（設計書 §3.1 は「3経路」と書くが
    実装には ④⑤ が別にある。2026-07-31 確認）:

    1. ``api/services.py::delete_course_data``（learning 側の DELETE 経路）
    2. ``core/versioning/deletion.py::_purge_course``（V層スイーパ）
    3. 同 ``_purge_document`` 内のコース削除（document 巻き添え）
    4. ``api/routes/admin.py::delete_material``（教材削除の巻き添え。教員の主経路）
    5. ``api/routes/admin.py::delete_course``（管理画面のコース削除ボタン）

    新しいコース削除経路を足すときはここも呼ぶこと（``teaching_figure_suggestions``
    も同時に掃除されるので、提案用の追加 DELETE は不要）。

    通常の運用で図を消す API は存在しない（FG7。回収は ``status='retired'`` 遷移）。
    """
    course_id = str(course_id or "")
    if not course_id:
        return []
    rows = session.execute(
        sa_text(
            """
            DELETE FROM course_teaching_figures
             WHERE course_id = :course_id
            RETURNING minio_key
            """
        ),
        {"course_id": course_id},
    ).fetchall()
    # 提案（candidate 層）も同じコースに紐づくので同時に掃除する。
    session.execute(
        sa_text("DELETE FROM teaching_figure_suggestions WHERE course_id = :course_id"),
        {"course_id": course_id},
    )
    return [str(row[0]) for row in rows if row[0]]


# ---------------------------------------------------------------------------
# teaching_figure_suggestions
# ---------------------------------------------------------------------------

_SUGGESTION_COLUMNS_SQL = """
    id::text, course_id, topic_id, anchor_excerpt, gap_reason, signal_basis,
    figure_kind, figure_brief, confidence, status, created_by::text, created_at
"""


def _row_to_suggestion(row: Any) -> dict:
    """行 → API 応答用 dict。**confidence の生値は載せない**（FG8）。"""
    return {
        "id": str(row[0]),
        "course_id": row[1] or "",
        "topic_id": row[2] or "",
        "anchor_excerpt": row[3] or "",
        "gap_reason": row[4] or "",
        "signal_basis": row[5] or "",
        "figure_kind": row[6] or "",
        "figure_kind_label": schema.figure_kind_label(row[6] or ""),
        "figure_brief": row[7] or "",
        "confidence_label": schema.confidence_label(row[8]),
        "status": row[9] or "",
        "created_by": row[10],
        "created_at": _iso(row[11]),
    }


def create_suggestions(session: Any, rows: Iterable[dict]) -> int:
    """ギャップ候補を挿入する（常に ``status='candidate'``。FG2）。

    挿入前に、対象 ``(course_id, topic_id)`` の既存 ``candidate`` を ``superseded`` へ
    遷移させる（再生成の原則。``accepted`` / ``dismissed`` は教員の判断済みなので不変
    — 設計書 §3.2）。``rows`` が空なら DB に一切触らず 0 を返す
    （生成結果ゼロで既存候補を消さない）。

    Returns:
        挿入件数。

    Raises:
        ValueError: 語彙外の ``figure_kind`` / ``signal_basis``、course_id/topic_id 欠落。
    """
    prepared: list[dict[str, Any]] = []
    for raw in rows or []:
        if not isinstance(raw, dict):
            raise ValueError("each suggestion row must be a dict")
        course_id = str(raw.get("course_id") or "").strip()
        topic_id = str(raw.get("topic_id") or "").strip()
        if not course_id or not topic_id:
            raise ValueError("course_id and topic_id are required for a suggestion")
        figure_kind = str(raw.get("figure_kind") or "")
        if not schema.is_valid_figure_kind(figure_kind):
            raise ValueError(f"invalid figure_kind: {figure_kind!r}")
        signal_basis = str(raw.get("signal_basis") or schema.SIGNAL_BASIS_TEXT_ANALYSIS)
        if not schema.is_valid_signal_basis(signal_basis):
            raise ValueError(f"invalid signal_basis: {signal_basis!r}")
        created_by = raw.get("created_by")
        prepared.append(
            {
                "course_id": course_id,
                "topic_id": topic_id,
                "anchor_excerpt": str(raw.get("anchor_excerpt") or ""),
                "gap_reason": str(raw.get("gap_reason") or ""),
                "signal_basis": signal_basis,
                "figure_kind": figure_kind,
                "figure_brief": str(raw.get("figure_brief") or ""),
                "confidence": schema.normalize_confidence(raw.get("confidence")),
                "created_by": created_by if _is_uuid(created_by) else None,
            }
        )

    if not prepared:
        return 0

    for course_id, topic_id in sorted({(r["course_id"], r["topic_id"]) for r in prepared}):
        session.execute(
            sa_text(
                """
                UPDATE teaching_figure_suggestions
                   SET status = :superseded
                 WHERE course_id = :course_id AND topic_id = :topic_id
                   AND status = :candidate
                """
            ),
            {
                "superseded": schema.SUGGESTION_STATUS_SUPERSEDED,
                "candidate": schema.SUGGESTION_STATUS_CANDIDATE,
                "course_id": course_id,
                "topic_id": topic_id,
            },
        )

    inserted = 0
    for params in prepared:
        session.execute(
            sa_text(
                """
                INSERT INTO teaching_figure_suggestions
                    (course_id, topic_id, anchor_excerpt, gap_reason, signal_basis,
                     figure_kind, figure_brief, confidence, status, created_by)
                VALUES
                    (:course_id, :topic_id, :anchor_excerpt, :gap_reason, :signal_basis,
                     :figure_kind, :figure_brief, :confidence, :status,
                     CAST(:created_by AS uuid))
                """
            ),
            {**params, "status": schema.SUGGESTION_STATUS_CANDIDATE},
        )
        inserted += 1
    return inserted


def list_suggestions(
    session: Any,
    course_id: str,
    topic_id: str,
    *,
    statuses: Sequence[str] = schema.SUGGESTION_VISIBLE_STATUSES,
) -> list[dict]:
    """トピックのギャップ候補一覧（新しい順）。**confidence は段階ラベルのみ**（FG8）。

    Raises:
        ValueError: 語彙外の status が含まれている（fail-closed）。
    """
    wanted = [str(s) for s in statuses or ()]
    invalid = [s for s in wanted if not schema.is_valid_suggestion_status(s)]
    if invalid:
        raise ValueError(f"invalid status filter: {invalid}")
    if not wanted:
        return []
    rows = session.execute(
        sa_text(
            f"""
            SELECT {_SUGGESTION_COLUMNS_SQL}
              FROM teaching_figure_suggestions
             WHERE course_id = :course_id AND topic_id = :topic_id
               AND status = ANY(:statuses)
             ORDER BY created_at DESC, id
            """
        ),
        {
            "course_id": str(course_id or ""),
            "topic_id": str(topic_id or ""),
            "statuses": wanted,
        },
    ).fetchall()
    return [_row_to_suggestion(row) for row in rows]


def set_suggestion_status(
    session: Any,
    suggestion_id: str,
    status: str,
    *,
    course_id: str,
) -> dict | None:
    """候補を ``accepted`` / ``dismissed`` / ``superseded`` へ遷移させる。

    遷移元は ``candidate`` のみ（教員が判断済みの行は書き換えない・行削除しない。P4）。
    対象が無い・既に candidate でない場合は ``None``（呼び出し側が 404/409 にマッピング）。

    ``course_id`` は**必須キーワード**（権限の fail-closed）。id だけで一致させると、
    自コースの操作から**他コースの候補**を accepted / dismissed にできてしまう
    （提案 id はクライアント由来なので id の秘匿を前提にしない）。スコープ外の id は
    UPDATE が0行 = ``None`` になり、副作用ゼロで返る。

    Raises:
        ValueError: ``candidate`` へ戻す等の受理しない遷移先 / ``course_id`` が空。
    """
    if status not in schema.SUGGESTION_DECIDABLE_STATUSES:
        raise ValueError(f"invalid status for set_suggestion_status(): {status!r}")
    if not str(course_id or "").strip():
        raise ValueError("course_id is required for set_suggestion_status()")
    if not _is_uuid(suggestion_id):
        return None
    row = session.execute(
        sa_text(
            f"""
            UPDATE teaching_figure_suggestions
               SET status = :status
             WHERE id = CAST(:id AS uuid) AND course_id = :course_id
               AND status = :current_status
            RETURNING {_SUGGESTION_COLUMNS_SQL}
            """
        ),
        {
            "id": str(suggestion_id),
            "course_id": str(course_id),
            "status": status,
            "current_status": schema.SUGGESTION_STATUS_CANDIDATE,
        },
    ).fetchone()
    return _row_to_suggestion(row) if row else None


def get_suggestion(session: Any, suggestion_id: str) -> dict | None:
    """id で候補1件を取得する（**confidence は段階ラベルのみ**。存在しなければ None）。"""
    if not _is_uuid(suggestion_id):
        return None
    row = session.execute(
        sa_text(
            f"""
            SELECT {_SUGGESTION_COLUMNS_SQL}
              FROM teaching_figure_suggestions
             WHERE id = CAST(:id AS uuid)
             LIMIT 1
            """
        ),
        {"id": str(suggestion_id)},
    ).fetchone()
    return _row_to_suggestion(row) if row else None


__all__ = [
    "MAX_REVISIONS",
    "TeachingFigureNotFoundError",
    "adopted_figures_map",
    "create_suggestions",
    "create_teaching_figure",
    "delete_figures_for_course",
    "get_suggestion",
    "get_teaching_figure",
    "get_teaching_figure_for_delivery",
    "list_suggestions",
    "list_teaching_figures",
    "set_suggestion_status",
    "update_teaching_figure",
]
