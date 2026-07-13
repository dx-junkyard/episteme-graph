"""分野の地図 — 修正報告フロー (Issue D)。

骨格(モデル生成の地形)への修正報告を、帰属つき・骨格バージョンつきで記録し、
既存の C層教員レビュー導線(review_status 語彙 + theory_review_events 監査 +
管理画面のレビュー表示)へ流す。新規のキュー機構は作らない。

設計上の重要事項:

- 送信は帰属つき(匿名不可)。reporter_id は NOT NULL で、API 側も要認証。
- どの版への指摘かを固定するため skeleton_version を必ず添付する。
  レビュー画面では現行凍結版との不一致(旧版への報告)を識別できる。
- 採用された修正は骨格の次版に反映し、報告者への帰属を changelog[].credits に
  残す(Issue A のスキーマ)。凍結時に自動で credits へ合流する。
- 旧版への報告は、新版凍結時に「採用済みなら自動クローズ(applied_version を刻印)、
  未対応(pending)なら新版へ引き継ぎ」。concept id は版を跨いで不変で、統合・分割は
  id_migrations (core.atlas) に従って報告の対象 id を付け替える(§16-4)。
- Stage 4 の challenge(型: 地図修正)昇格を妨げないよう kind 列を持つ
  (現状は 'map_correction' のみ)。
- 本モジュールは FastAPI を import しない(テスタビリティ確保)。SQL 実行は
  session を引数で受け取り、commit は呼び出し側(ルーター)が行う。
"""

from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import text as sa_text

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

STATUS_PENDING = "pending"      # レビュー待ち(キュー内)
STATUS_ACCEPTED = "accepted"    # 採用 — incorporated_at 記録後に凍結で applied_version 刻印
STATUS_DECLINED = "declined"    # 見送り(理由つき)
STATUS_MERGED = "merged"        # 重複統合(merged_into に統合先)
REPORT_STATUSES = (STATUS_PENDING, STATUS_ACCEPTED, STATUS_DECLINED, STATUS_MERGED)

# レビュー処理アクション → 遷移先ステータス
RESOLVE_ACTIONS = {
    "accept": STATUS_ACCEPTED,
    "decline": STATUS_DECLINED,
    "merge": STATUS_MERGED,
}

REPORT_KIND = "map_correction"

# 未決事項の決定(issue A と共同): 同一対象(node/region)への未クローズ報告
# (pending + 未反映 accepted)がこの件数に達したら、レビュー画面に改版検討ヒントを
# 出す。自動改版はしない(表示のみ。改版判断は教員)。
REVISION_TRIGGER_THRESHOLD = 5

_REPORT_COLUMNS = (
    "id",
    "cartridge_id",
    "skeleton_version",
    "node_id",
    "region_id",
    "level",
    "node_label",
    "report_text",
    "reporter_id",
    "reporter_name",
    "status",
    "resolution_note",
    "resolved_by",
    "resolved_at",
    "merged_into",
    "incorporated_at",
    "incorporated_by",
    "incorporation_note",
    "applied_version",
    "notified_at",
    "created_at",
)

_SELECT_REPORTS = """
    SELECT r.id::text, r.cartridge_id, r.skeleton_version, r.node_id, r.region_id,
           r.level, r.node_label, r.report_text, r.reporter_id::text,
           COALESCE(u.display_name, r.reporter_id::text) AS reporter_name,
           r.status, r.resolution_note, r.resolved_by::text, r.resolved_at,
           r.merged_into::text, r.incorporated_at, r.incorporated_by::text,
           r.incorporation_note, r.applied_version, r.notified_at, r.created_at
      FROM atlas_correction_reports r
 LEFT JOIN users u ON u.id = r.reporter_id
"""


def _rows_to_dicts(rows: Iterable[Any]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        rec = dict(zip(_REPORT_COLUMNS, row))
        for key in ("resolved_at", "incorporated_at", "notified_at", "created_at"):
            if rec.get(key) is not None:
                rec[key] = str(rec[key])
        out.append(rec)
    return out


# ---------------------------------------------------------------------------
# 入力検証 (純粋関数)
# ---------------------------------------------------------------------------


def validate_report_input(
    *,
    text: str,
    node_id: str,
    region_id: str,
    level: int,
    skeleton_version: str,
) -> list[str]:
    """報告入力の検証。空リスト = OK。

    - 空文字送信のガード(モックの「(内容未記入)」は本実装では送信不可)
    - 対象(node_id | region_id)・レベル・骨格バージョンの自動添付が揃っていること
    """
    errors: list[str] = []
    if not (text or "").strip():
        errors.append("報告本文が空です(内容未記入では送信できません)")
    if not (node_id or "").strip() and not (region_id or "").strip():
        errors.append("対象(node_id または region_id)がありません")
    if level not in (1, 2, 3):
        errors.append(f"level が不正です: {level!r}")
    if not (skeleton_version or "").strip():
        errors.append("skeleton_version がありません(どの版への指摘かを固定するため必須)")
    return errors


# ---------------------------------------------------------------------------
# キュー整形 (純粋関数 — レビュー画面用)
# ---------------------------------------------------------------------------


def report_target(report: dict[str, Any]) -> str:
    """報告の対象キー。ノード指定を優先し、無ければ領域。"""
    return (report.get("node_id") or "").strip() or (report.get("region_id") or "").strip()


def summarize_queue(
    reports: list[dict[str, Any]], current_version: str
) -> dict[str, Any]:
    """レビューキューの表示用サマリを組み立てる。

    - version_mismatch: 現行凍結版と異なる版への報告(旧版)を識別(受け入れ条件5)
    - target_counts: 同一対象への報告蓄積の件数(未クローズ分)— 改版トリガ判断の材料
    - revision_hint_targets: 閾値(REVISION_TRIGGER_THRESHOLD)以上に達した対象
    """
    open_counts: dict[str, int] = {}
    for report in reports:
        target = report_target(report)
        if not target:
            continue
        is_open = report.get("status") == STATUS_PENDING or (
            report.get("status") == STATUS_ACCEPTED
            and not report.get("incorporated_at")
            and not report.get("applied_version")
        )
        if is_open:
            open_counts[target] = open_counts.get(target, 0) + 1

    annotated = []
    for report in reports:
        rec = dict(report)
        rec["version_mismatch"] = bool(
            current_version and report.get("skeleton_version") != current_version
        )
        rec["target"] = report_target(report)
        rec["open_count_for_target"] = open_counts.get(rec["target"], 0)
        annotated.append(rec)

    return {
        "reports": annotated,
        "target_counts": open_counts,
        "revision_hint_targets": sorted(
            t for t, n in open_counts.items() if n >= REVISION_TRIGGER_THRESHOLD
        ),
        "revision_trigger_threshold": REVISION_TRIGGER_THRESHOLD,
        "current_version": current_version,
    }


def credits_from_reports(reports: list[dict[str, Any]]) -> list[str]:
    """採用済み報告から changelog[].credits に載せる帰属を作る(重複除去・順序保持)。"""
    seen: set[str] = set()
    credits: list[str] = []
    for report in reports:
        name = str(report.get("reporter_name") or report.get("reporter_id") or "").strip()
        if name and name not in seen:
            seen.add(name)
            credits.append(name)
    return credits


def migration_map_for_version(
    id_migrations: Iterable[Any], version: str
) -> dict[str, str]:
    """core.atlas の id_migrations から、当該版の旧→新 id 対応を取り出す。"""
    mapping: dict[str, str] = {}
    for m in id_migrations or ():
        m_version = getattr(m, "version", None) or (m.get("version") if isinstance(m, dict) else "")
        if m_version != version:
            continue
        from_id = getattr(m, "from_id", None) or (m.get("from") if isinstance(m, dict) else "")
        to_id = getattr(m, "to_id", None) or (m.get("to") if isinstance(m, dict) else "")
        if from_id and to_id:
            mapping[str(from_id)] = str(to_id)
    return mapping


# ---------------------------------------------------------------------------
# DB 操作 (session は呼び出し側が管理。commit も呼び出し側)
# ---------------------------------------------------------------------------


def create_report(
    session,
    *,
    cartridge_id: str,
    skeleton_version: str,
    node_id: str,
    region_id: str,
    level: int,
    node_label: str,
    text: str,
    reporter_id: str,
) -> str:
    """報告を1件記録して report_id を返す。帰属(reporter_id)は必須。"""
    row = session.execute(
        sa_text(
            """
            INSERT INTO atlas_correction_reports
                (cartridge_id, skeleton_version, node_id, region_id, level,
                 node_label, report_text, reporter_id, kind)
            VALUES (:cartridge_id, :skeleton_version, :node_id, :region_id, :level,
                    :node_label, :report_text, CAST(:reporter_id AS uuid), :kind)
            RETURNING id::text
            """
        ),
        {
            "cartridge_id": (cartridge_id or "").strip(),
            "skeleton_version": skeleton_version.strip(),
            "node_id": (node_id or "").strip(),
            "region_id": (region_id or "").strip(),
            "level": level,
            "node_label": (node_label or "").strip(),
            "report_text": text.strip(),
            "reporter_id": reporter_id,
            "kind": REPORT_KIND,
        },
    ).fetchone()
    return row[0]


def fetch_reports(
    session, *, cartridge_id: str | None = None, status: str | None = None
) -> list[dict[str, Any]]:
    """レビュー画面用の報告一覧(報告者名つき)。新しい順。"""
    where = []
    params: dict[str, Any] = {}
    if cartridge_id:
        where.append("r.cartridge_id = :cartridge_id")
        params["cartridge_id"] = cartridge_id
    if status:
        where.append("r.status = :status")
        params["status"] = status
    sql = _SELECT_REPORTS
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY r.created_at DESC"
    return _rows_to_dicts(session.execute(sa_text(sql), params).fetchall())


def fetch_reports_for_user(
    session, user_id: str, *, unacked_only: bool = False
) -> list[dict[str, Any]]:
    """本人の報告一覧。unacked_only=True で「処理済みだが未読の結果」のみ(通知用)。"""
    sql = _SELECT_REPORTS + " WHERE r.reporter_id = CAST(:user_id AS uuid)"
    if unacked_only:
        sql += " AND r.resolved_at IS NOT NULL AND r.notified_at IS NULL"
    sql += " ORDER BY r.created_at DESC"
    return _rows_to_dicts(session.execute(sa_text(sql), {"user_id": user_id}).fetchall())


def resolve_report(
    session,
    *,
    report_id: str,
    action: str,
    note: str,
    resolver_id: str,
    merge_into: str | None = None,
) -> dict[str, Any]:
    """レビュー処理: 採用 / 見送り(理由必須) / 重複統合。

    戻り値は監査記録用の {old_status, new_status}。不正入力は ValueError。
    """
    new_status = RESOLVE_ACTIONS.get(action)
    if new_status is None:
        raise ValueError(f"不明な処理アクションです: {action!r}")
    note = (note or "").strip()
    if action == "decline" and not note:
        raise ValueError("見送りには理由(note)が必須です")
    if action == "merge" and not (merge_into or "").strip():
        raise ValueError("重複統合には統合先(merge_into)が必須です")
    if action != "merge":
        merge_into = None
    elif merge_into == report_id:
        raise ValueError("自分自身へは統合できません")

    row = session.execute(
        sa_text("SELECT status FROM atlas_correction_reports WHERE id = CAST(:id AS uuid)"),
        {"id": report_id},
    ).fetchone()
    if row is None:
        raise LookupError(f"報告が見つかりません: {report_id}")
    old_status = row[0]

    if merge_into:
        target = session.execute(
            sa_text("SELECT status FROM atlas_correction_reports WHERE id = CAST(:id AS uuid)"),
            {"id": merge_into},
        ).fetchone()
        if target is None:
            raise ValueError(f"統合先の報告が見つかりません: {merge_into}")
        if target[0] == STATUS_MERGED:
            raise ValueError("統合済みの報告へは統合できません(統合先を辿ってください)")

    session.execute(
        sa_text(
            """
            UPDATE atlas_correction_reports
               SET status = :status,
                   resolution_note = :note,
                   resolved_by = CAST(:resolver_id AS uuid),
                   resolved_at = now(),
                   merged_into = CAST(:merge_into AS uuid),
                   notified_at = NULL,
                   updated_at = now()
             WHERE id = CAST(:id AS uuid)
            """
        ),
        {
            "id": report_id,
            "status": new_status,
            "note": note,
            "resolver_id": resolver_id,
            "merge_into": merge_into,
        },
    )
    return {"old_status": old_status, "new_status": new_status}


def ack_report(session, *, report_id: str, user_id: str) -> bool:
    """報告者本人が結果通知を既読にする。本人以外・未処理には効かない。"""
    result = session.execute(
        sa_text(
            """
            UPDATE atlas_correction_reports
               SET notified_at = now(), updated_at = now()
             WHERE id = CAST(:id AS uuid)
               AND reporter_id = CAST(:user_id AS uuid)
               AND resolved_at IS NOT NULL
               AND notified_at IS NULL
            """
        ),
        {"id": report_id, "user_id": user_id},
    )
    return bool(getattr(result, "rowcount", 0))


def mark_report_incorporated(
    session, *, report_id: str, resolver_id: str, note: str = ""
) -> dict[str, Any]:
    """採用済み報告を「次版で対応済み」に進める。

    採用判断と実際の編集完了を分離し、公開時に未対応の報告を誤って
    applied 扱いにしないための明示的な状態遷移。
    """
    row = session.execute(
        sa_text(
            "SELECT status, incorporated_at, applied_version "
            "FROM atlas_correction_reports WHERE id = CAST(:id AS uuid)"
        ),
        {"id": report_id},
    ).fetchone()
    if row is None:
        raise LookupError(f"報告が見つかりません: {report_id}")
    if row[0] != STATUS_ACCEPTED:
        raise ValueError("採用済みの報告だけを次版で対応済みにできます")
    if row[2]:
        raise ValueError(f"この報告は版 {row[2]} に反映済みです")
    if row[1] is not None:
        raise ValueError("この報告はすでに次版で対応済みです")

    session.execute(
        sa_text(
            """
            UPDATE atlas_correction_reports
               SET incorporated_at = now(),
                   incorporated_by = CAST(:resolver_id AS uuid),
                   incorporation_note = :note,
                   updated_at = now()
             WHERE id = CAST(:id AS uuid)
            """
        ),
        {"id": report_id, "resolver_id": resolver_id, "note": (note or "").strip()},
    )
    return {"old_status": STATUS_ACCEPTED, "new_status": "incorporated"}


def accepted_unapplied_reports(session, cartridge_id: str) -> list[dict[str, Any]]:
    """次版で対応済みかつ未公開の報告(凍結時の credits 合流対象)。古い順。"""
    sql = (
        _SELECT_REPORTS
        + " WHERE r.cartridge_id = :cartridge_id AND r.status = :status"
        + " AND r.incorporated_at IS NOT NULL"
        + " AND r.applied_version = '' ORDER BY r.created_at ASC"
    )
    return _rows_to_dicts(
        session.execute(
            sa_text(sql), {"cartridge_id": cartridge_id, "status": STATUS_ACCEPTED}
        ).fetchall()
    )


def apply_freeze_to_reports(
    session, *, cartridge_id: str, version: str, id_migrations: Iterable[Any] = ()
) -> dict[str, int]:
    """新版凍結時の報告の後処理(受け入れ条件・D-3)。

    - 採用済みかつ次版で対応済みの報告に applied_version を刻印する
    - 未対応(pending)は新版へ引き継ぐ。改版で概念 id が統合・分割された場合は
      id_migrations(当該版のもの)に従って対象 node_id を付け替える
    """
    result = session.execute(
        sa_text(
            """
            UPDATE atlas_correction_reports
               SET applied_version = :version, notified_at = NULL, updated_at = now()
             WHERE cartridge_id = :cartridge_id
               AND status = :status
               AND incorporated_at IS NOT NULL
               AND applied_version = ''
            """
        ),
        {"cartridge_id": cartridge_id, "version": version, "status": STATUS_ACCEPTED},
    )
    applied = int(getattr(result, "rowcount", 0) or 0)

    migrated = 0
    for from_id, to_id in migration_map_for_version(id_migrations, version).items():
        res = session.execute(
            sa_text(
                """
                UPDATE atlas_correction_reports
                   SET node_id = :to_id, updated_at = now()
                 WHERE cartridge_id = :cartridge_id
                   AND status = :status
                   AND node_id = :from_id
                """
            ),
            {
                "cartridge_id": cartridge_id,
                "to_id": to_id,
                "from_id": from_id,
                "status": STATUS_PENDING,
            },
        )
        migrated += int(getattr(res, "rowcount", 0) or 0)
    return {"applied": applied, "migrated": migrated}
