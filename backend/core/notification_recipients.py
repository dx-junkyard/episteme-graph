"""通知宛先解決の共通ビルディングブロック（Tier2 提案10）。

`core/status/notification_rules.py`（状態遷移通知。S5: 宛先は所有者 + 共有 editor に限定し
viewer には出さない）と `core/versioning/notifications.py`（V層のバージョン公開/削除通知。
viewer|editor の共有先へ配信し、所有者は fan_out 側で除外する）は、どちらも
「document/course の *_group_permissions を group_members と JOIN してユーザーを引く」という
同型の実装を独立に持っていた（互いの docstring が「同型」と明記）。

**宛先セット自体の意味（誰に何を届けるか）は層ごとに意図的に異なる**ため統合しない
（S5 の fail-closed 要件と V層の共有可視性は別の設計判断）。ここに集約するのは
JOIN の形そのもの（このモジュールの4関数）だけで、「owner を含めるか」「どの
permission を対象にするか」「レガシー単一グループ共有を含めるか」の組み立ては
呼び出し側（notification_rules.py / versioning/notifications.py）にそれぞれ残す。

FastAPI 非 import（core 配下のガードレール）。backend/api を import する方向の
依存は作らない。
"""

from __future__ import annotations

from sqlalchemy import text as sa_text


def document_owner_id(session, document_id: str) -> str | None:
    """documents.uploaded_by（所有者 id）を返す。無ければ None。"""
    row = session.execute(
        sa_text("SELECT uploaded_by::text FROM documents WHERE id = CAST(:did AS uuid)"),
        {"did": document_id},
    ).fetchone()
    return str(row[0]) if row and row[0] else None


def course_owner_id(session, course_id: str) -> str | None:
    """learning_courses.user_id（所有者 id）を返す。無ければ None。"""
    row = session.execute(
        sa_text("SELECT user_id::text FROM learning_courses WHERE id = :cid"),
        {"cid": course_id},
    ).fetchone()
    return str(row[0]) if row and row[0] else None


def document_group_member_ids(session, document_id: str, permissions: tuple[str, ...]) -> list[str]:
    """object_group_permissions（migration 044、object_type='document'）経由の
    グループメンバー user_id を返す。

    ``permissions`` でフィルタする（例: ``("editor",)`` / ``("viewer", "editor")``）。
    document 側の object_id は ``CAST(... AS uuid)::text``（小文字 canonical form）で
    正規化して比較する（migration 044 の設計注記どおり、大文字小文字の揺れを避ける）。
    """
    if not permissions:
        return []
    placeholders = ", ".join(f":p{i}" for i in range(len(permissions)))
    params: dict = {"did": document_id}
    for i, perm in enumerate(permissions):
        params[f"p{i}"] = perm
    rows = session.execute(
        sa_text(f"""
            SELECT DISTINCT gm.user_id::text
            FROM object_group_permissions p
            JOIN group_members gm ON gm.group_id = p.group_id
            WHERE p.object_type = 'document'
              AND p.object_id = CAST(:did AS uuid)::text
              AND p.permission IN ({placeholders})
        """),
        params,
    ).fetchall()
    return [str(r[0]) for r in rows]


def course_group_member_ids(session, course_id: str, permissions: tuple[str, ...]) -> list[str]:
    """object_group_permissions（migration 044、object_type='course'）経由の
    グループメンバー user_id を返す（permission でフィルタ）。"""
    if not permissions:
        return []
    placeholders = ", ".join(f":p{i}" for i in range(len(permissions)))
    params: dict = {"cid": course_id}
    for i, perm in enumerate(permissions):
        params[f"p{i}"] = perm
    rows = session.execute(
        sa_text(f"""
            SELECT DISTINCT gm.user_id::text
            FROM object_group_permissions p
            JOIN group_members gm ON gm.group_id = p.group_id
            WHERE p.object_type = 'course'
              AND p.object_id = :cid
              AND p.permission IN ({placeholders})
        """),
        params,
    ).fetchall()
    return [str(r[0]) for r in rows]


def atlas_bound_course_owner_ids(session, domain_key: str) -> list[str]:
    """domain_key（分野の地図の学習マップ）にバインド中のコースの所有者 user_id。

    `learning_courses.data->>'cartridge_id' = domain_key` のコース所有者（重複除去）。
    宛先の組み立て方針（骨格編集者との和・actor 除外）は呼び出し側
    （`core/atlas_lifecycle.py::notify_atlas_event`）に置く（このモジュールの分業規約）。
    """
    if not domain_key:
        return []
    rows = session.execute(
        sa_text("""
            SELECT DISTINCT user_id::text
            FROM learning_courses
            WHERE data->>'cartridge_id' = :domain_key AND user_id IS NOT NULL
        """),
        {"domain_key": domain_key},
    ).fetchall()
    return [str(r[0]) for r in rows]


def atlas_skeleton_editor_ids(session, domain_key: str) -> list[str]:
    """domain の骨格 (`atlas_skeletons`) 編集履歴のある教員 user_id。

    `created_by` / `updated_by` の和（重複除去）。「誰でも凍結できる共同財」の
    透明性担保のため、生成者・更新者のどちらも当事者として扱う。
    """
    if not domain_key:
        return []
    rows = session.execute(
        sa_text("""
            SELECT created_by::text AS uid FROM atlas_skeletons
             WHERE domain_key = :domain_key AND created_by IS NOT NULL
            UNION
            SELECT updated_by::text AS uid FROM atlas_skeletons
             WHERE domain_key = :domain_key AND updated_by IS NOT NULL
        """),
        {"domain_key": domain_key},
    ).fetchall()
    return [str(r[0]) for r in rows]


def document_legacy_group_visibility_member_ids(session, document_id: str) -> list[str]:
    """レガシー単一グループ共有（documents.visibility='group' + group_id）のメンバー user_id。

    object_group_permissions（migration 044。前身のドキュメント単体共有テーブルは
    migration 035）導入前からの共有経路。V層の通知は services.user_can_view_document が
    この経路でも閲覧を許可するため宛先に含める（更新・削除予約・削除の取りこぼしを防ぐ）。
    """
    rows = session.execute(
        sa_text("""
            SELECT gm.user_id::text
            FROM documents d
            JOIN group_members gm ON gm.group_id = d.group_id
            WHERE d.id = CAST(:did AS uuid) AND d.visibility = 'group' AND d.group_id IS NOT NULL
        """),
        {"did": document_id},
    ).fetchall()
    return [str(r[0]) for r in rows]
