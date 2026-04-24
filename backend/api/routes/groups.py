"""Episteme Graph — グループ管理エンドポイント (/api/groups, /api/me/invitations)。

Issue #121: 資料のアクセス制御およびグループ招待機能。

Endpoints
---------
POST   /api/groups                                    グループを作成
GET    /api/groups                                    自分が参加するグループ一覧
GET    /api/groups/{group_id}                         グループ詳細 + メンバー一覧
PUT    /api/groups/{group_id}                         グループ更新 (admin)
DELETE /api/groups/{group_id}                         グループ削除 (admin)
POST   /api/groups/{group_id}/members                 ユーザーを直接招待 (admin)
DELETE /api/groups/{group_id}/members/{user_id}       メンバー退会/除名 (admin or self)
POST   /api/groups/{group_id}/invite-code/rotate      招待コードを再発行 (admin)
POST   /api/groups/join-by-code                       招待コードで参加
GET    /api/groups/{group_id}/invitations             直接招待の一覧 (admin)
GET    /api/me/invitations                            自分への未承諾の招待一覧
POST   /api/me/invitations/{invitation_id}/accept     招待を承諾
POST   /api/me/invitations/{invitation_id}/decline    招待を辞退
"""

from __future__ import annotations

import logging
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text as sa_text

from dependencies import _get_current_user
from schemas import (
    GroupCreateRequest,
    GroupDetailOut,
    GroupInvitationOut,
    GroupInviteByCodeRequest,
    GroupInviteByUserRequest,
    GroupMemberOut,
    GroupOut,
    GroupUpdateRequest,
)
from core.postgres import get_session as _pg_session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Groups"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_invite_code() -> str:
    """URL-safe な 12 文字の招待コードを生成する。"""
    return secrets.token_urlsafe(9)[:12]


def _require_member(session, group_id: str, user_id: str) -> str:
    """ユーザーがグループのメンバーであることを確認し、ロールを返す。"""
    row = session.execute(
        sa_text("""
            SELECT role FROM group_members
            WHERE group_id = CAST(:gid AS uuid) AND user_id = CAST(:uid AS uuid)
            LIMIT 1
        """),
        {"gid": group_id, "uid": user_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="You are not a member of this group")
    return row[0]


def _require_admin(session, group_id: str, user_id: str) -> None:
    """ユーザーがグループの admin であることを確認する。"""
    role = _require_member(session, group_id, user_id)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Group admin role required")


def _group_row_to_out(row, my_role: str, member_count: int, show_invite_code: bool) -> GroupOut:
    return GroupOut(
        id=str(row[0]),
        name=row[1] or "",
        description=row[2] or "",
        invite_code=(row[3] or None) if show_invite_code else None,
        created_by=str(row[4]),
        my_role=my_role,
        member_count=member_count,
        created_at=row[5].isoformat() if row[5] else "",
        updated_at=row[6].isoformat() if row[6] else "",
    )


# ---------------------------------------------------------------------------
# Groups CRUD
# ---------------------------------------------------------------------------


@router.post("/api/groups", response_model=GroupOut, status_code=201)
def create_group(
    body: GroupCreateRequest,
    current_user: dict = Depends(_get_current_user),
) -> GroupOut:
    """グループを作成する。作成者は自動的に admin として参加する。"""
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Group name is required")

    group_id = uuid.uuid4()
    invite_code = _generate_invite_code()
    session = _pg_session()
    try:
        session.execute(
            sa_text("""
                INSERT INTO groups (id, name, description, invite_code, created_by)
                VALUES (:id, :name, :description, :invite_code, CAST(:created_by AS uuid))
            """),
            {
                "id": group_id,
                "name": body.name.strip(),
                "description": body.description or "",
                "invite_code": invite_code,
                "created_by": current_user["id"],
            },
        )
        session.execute(
            sa_text("""
                INSERT INTO group_members (group_id, user_id, role)
                VALUES (:gid, CAST(:uid AS uuid), 'admin')
            """),
            {"gid": group_id, "uid": current_user["id"]},
        )
        row = session.execute(
            sa_text("""
                SELECT id, name, description, invite_code, created_by, created_at, updated_at
                FROM groups WHERE id = :id
            """),
            {"id": group_id},
        ).fetchone()
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    logger.info("Group '%s' (id=%s) created by user=%s", body.name, group_id, current_user["id"])
    return _group_row_to_out(row, my_role="admin", member_count=1, show_invite_code=True)


@router.get("/api/groups", response_model=list[GroupOut])
def list_my_groups(
    current_user: dict = Depends(_get_current_user),
) -> list[GroupOut]:
    """自分が所属しているグループ一覧を返す。"""
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT g.id, g.name, g.description, g.invite_code, g.created_by,
                       g.created_at, g.updated_at, gm.role,
                       (SELECT COUNT(*) FROM group_members gm2 WHERE gm2.group_id = g.id) AS member_count
                FROM groups g
                JOIN group_members gm ON gm.group_id = g.id
                WHERE gm.user_id = CAST(:uid AS uuid)
                ORDER BY g.updated_at DESC
            """),
            {"uid": current_user["id"]},
        ).fetchall()
    finally:
        session.close()

    return [
        _group_row_to_out(
            r[:7],
            my_role=r[7],
            member_count=int(r[8] or 0),
            show_invite_code=(r[7] == "admin"),
        )
        for r in rows
    ]


@router.get("/api/groups/{group_id}", response_model=GroupDetailOut)
def get_group_detail(
    group_id: str,
    current_user: dict = Depends(_get_current_user),
) -> GroupDetailOut:
    """グループ詳細（メンバーリスト付き）を返す。メンバーのみアクセス可能。"""
    session = _pg_session()
    try:
        my_role = _require_member(session, group_id, current_user["id"])

        group_row = session.execute(
            sa_text("""
                SELECT id, name, description, invite_code, created_by, created_at, updated_at
                FROM groups WHERE id = CAST(:id AS uuid)
            """),
            {"id": group_id},
        ).fetchone()
        if not group_row:
            raise HTTPException(status_code=404, detail="Group not found")

        member_rows = session.execute(
            sa_text("""
                SELECT u.id, u.display_name, u.email, gm.role, gm.joined_at
                FROM group_members gm
                JOIN users u ON u.id = gm.user_id
                WHERE gm.group_id = CAST(:gid AS uuid)
                ORDER BY gm.joined_at ASC
            """),
            {"gid": group_id},
        ).fetchall()
    finally:
        session.close()

    members = [
        GroupMemberOut(
            user_id=str(mr[0]),
            username=mr[1] or "",
            email=mr[2] or "",
            role=mr[3],
            joined_at=mr[4].isoformat() if mr[4] else "",
        )
        for mr in member_rows
    ]

    base = _group_row_to_out(
        group_row,
        my_role=my_role,
        member_count=len(members),
        show_invite_code=(my_role == "admin"),
    )
    return GroupDetailOut(**base.model_dump(), members=members)


@router.put("/api/groups/{group_id}", response_model=GroupOut)
def update_group(
    group_id: str,
    body: GroupUpdateRequest,
    current_user: dict = Depends(_get_current_user),
) -> GroupOut:
    """グループの名前・説明を更新する（admin のみ）。"""
    session = _pg_session()
    try:
        _require_admin(session, group_id, current_user["id"])

        updates: list[str] = []
        params: dict = {"id": group_id}
        if body.name is not None:
            if not body.name.strip():
                raise HTTPException(status_code=400, detail="Group name cannot be empty")
            updates.append("name = :name")
            params["name"] = body.name.strip()
        if body.description is not None:
            updates.append("description = :description")
            params["description"] = body.description

        if updates:
            updates.append("updated_at = now()")
            session.execute(
                sa_text(f"UPDATE groups SET {', '.join(updates)} WHERE id = CAST(:id AS uuid)"),
                params,
            )

        row = session.execute(
            sa_text("""
                SELECT id, name, description, invite_code, created_by, created_at, updated_at
                FROM groups WHERE id = CAST(:id AS uuid)
            """),
            {"id": group_id},
        ).fetchone()
        member_count = session.execute(
            sa_text("SELECT COUNT(*) FROM group_members WHERE group_id = CAST(:gid AS uuid)"),
            {"gid": group_id},
        ).scalar() or 0
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    return _group_row_to_out(row, my_role="admin", member_count=int(member_count), show_invite_code=True)


@router.delete("/api/groups/{group_id}")
def delete_group(
    group_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """グループを削除する（admin のみ）。

    CASCADE により group_members / group_invitations も削除される。
    関連する Document/LearningCourse の group_id は SET NULL される。
    """
    session = _pg_session()
    try:
        _require_admin(session, group_id, current_user["id"])
        session.execute(
            sa_text("DELETE FROM groups WHERE id = CAST(:id AS uuid)"),
            {"id": group_id},
        )
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    logger.info("Group %s deleted by user=%s", group_id, current_user["id"])
    return {"group_id": group_id, "deleted": True}


# ---------------------------------------------------------------------------
# Invite code rotation
# ---------------------------------------------------------------------------


@router.post("/api/groups/{group_id}/invite-code/rotate")
def rotate_invite_code(
    group_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """招待コードを再発行する（admin のみ）。"""
    new_code = _generate_invite_code()
    session = _pg_session()
    try:
        _require_admin(session, group_id, current_user["id"])
        session.execute(
            sa_text("""
                UPDATE groups SET invite_code = :code, updated_at = now()
                WHERE id = CAST(:id AS uuid)
            """),
            {"code": new_code, "id": group_id},
        )
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return {"group_id": group_id, "invite_code": new_code}


# ---------------------------------------------------------------------------
# Direct invitation by user (admin)
# ---------------------------------------------------------------------------


@router.post("/api/groups/{group_id}/members", response_model=GroupInvitationOut, status_code=201)
def invite_user(
    group_id: str,
    body: GroupInviteByUserRequest,
    current_user: dict = Depends(_get_current_user),
) -> GroupInvitationOut:
    """特定のユーザー（username または email）をグループへ直接招待する（admin のみ）。"""
    if not body.username and not body.email:
        raise HTTPException(status_code=400, detail="username または email のいずれかを指定してください")

    session = _pg_session()
    try:
        _require_admin(session, group_id, current_user["id"])

        # 招待対象ユーザーを解決
        if body.username:
            target = session.execute(
                sa_text("SELECT id, display_name, email FROM users WHERE display_name = :name LIMIT 1"),
                {"name": body.username},
            ).fetchone()
        else:
            target = session.execute(
                sa_text("SELECT id, display_name, email FROM users WHERE email = :email LIMIT 1"),
                {"email": body.email},
            ).fetchone()

        if not target:
            raise HTTPException(status_code=404, detail="User not found")

        target_id = target[0]
        target_username = target[1] or ""

        # 既にメンバーならエラー
        already = session.execute(
            sa_text("""
                SELECT 1 FROM group_members
                WHERE group_id = CAST(:gid AS uuid) AND user_id = :uid
                LIMIT 1
            """),
            {"gid": group_id, "uid": target_id},
        ).fetchone()
        if already:
            raise HTTPException(status_code=409, detail="User is already a member")

        # 既存の pending 招待があれば再利用
        existing = session.execute(
            sa_text("""
                SELECT id, created_at FROM group_invitations
                WHERE group_id = CAST(:gid AS uuid)
                  AND invitee_user_id = :uid
                  AND status = 'pending'
                LIMIT 1
            """),
            {"gid": group_id, "uid": target_id},
        ).fetchone()
        if existing:
            inv_id = existing[0]
            created_at = existing[1]
        else:
            inv_id = uuid.uuid4()
            session.execute(
                sa_text("""
                    INSERT INTO group_invitations
                        (id, group_id, invitee_user_id, inviter_user_id, status)
                    VALUES (:id, CAST(:gid AS uuid), :uid, CAST(:inviter AS uuid), 'pending')
                """),
                {
                    "id": inv_id,
                    "gid": group_id,
                    "uid": target_id,
                    "inviter": current_user["id"],
                },
            )
            created_at = None

        group_name = session.execute(
            sa_text("SELECT name FROM groups WHERE id = CAST(:id AS uuid)"),
            {"id": group_id},
        ).scalar() or ""

        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    return GroupInvitationOut(
        id=str(inv_id),
        group_id=group_id,
        group_name=group_name,
        invitee_user_id=str(target_id),
        invitee_username=target_username,
        inviter_user_id=current_user["id"],
        inviter_username=current_user.get("username", ""),
        status="pending",
        created_at=created_at.isoformat() if created_at else "",
    )


@router.delete("/api/groups/{group_id}/members/{user_id}")
def remove_member(
    group_id: str,
    user_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """メンバーを退会/除名する。admin は任意メンバーを、非 admin は自分自身のみ可能。"""
    session = _pg_session()
    try:
        # 自分自身か admin である必要がある
        if user_id != current_user["id"]:
            _require_admin(session, group_id, current_user["id"])

        # 除名対象
        result = session.execute(
            sa_text("""
                DELETE FROM group_members
                WHERE group_id = CAST(:gid AS uuid) AND user_id = CAST(:uid AS uuid)
                RETURNING id
            """),
            {"gid": group_id, "uid": user_id},
        ).fetchone()

        if not result:
            raise HTTPException(status_code=404, detail="Member not found")

        # 最後の admin を削除すると admin-less になるため確認
        admin_count = session.execute(
            sa_text("""
                SELECT COUNT(*) FROM group_members
                WHERE group_id = CAST(:gid AS uuid) AND role = 'admin'
            """),
            {"gid": group_id},
        ).scalar() or 0
        if admin_count == 0:
            session.rollback()
            raise HTTPException(
                status_code=400,
                detail="最後の管理者は削除できません。先に別のメンバーを admin に昇格させてください。",
            )

        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    return {"group_id": group_id, "user_id": user_id, "removed": True}


# ---------------------------------------------------------------------------
# Join by invite code
# ---------------------------------------------------------------------------


@router.post("/api/groups/join-by-code", response_model=GroupOut)
def join_by_code(
    body: GroupInviteByCodeRequest,
    current_user: dict = Depends(_get_current_user),
) -> GroupOut:
    """招待コードを使ってグループに参加する。"""
    code = (body.invite_code or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="invite_code is required")

    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                SELECT id, name, description, invite_code, created_by, created_at, updated_at
                FROM groups WHERE invite_code = :code LIMIT 1
            """),
            {"code": code},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Invalid invite code")

        group_id = row[0]

        # 既にメンバーなら成功（冪等）
        existing = session.execute(
            sa_text("""
                SELECT role FROM group_members
                WHERE group_id = :gid AND user_id = CAST(:uid AS uuid)
                LIMIT 1
            """),
            {"gid": group_id, "uid": current_user["id"]},
        ).fetchone()

        if not existing:
            session.execute(
                sa_text("""
                    INSERT INTO group_members (group_id, user_id, role)
                    VALUES (:gid, CAST(:uid AS uuid), 'member')
                """),
                {"gid": group_id, "uid": current_user["id"]},
            )
            # もし自分宛の pending 招待があれば accepted に更新
            session.execute(
                sa_text("""
                    UPDATE group_invitations
                    SET status = 'accepted', responded_at = now()
                    WHERE group_id = :gid
                      AND invitee_user_id = CAST(:uid AS uuid)
                      AND status = 'pending'
                """),
                {"gid": group_id, "uid": current_user["id"]},
            )
        my_role = existing[0] if existing else "member"

        member_count = session.execute(
            sa_text("SELECT COUNT(*) FROM group_members WHERE group_id = :gid"),
            {"gid": group_id},
        ).scalar() or 0
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    logger.info("User=%s joined group=%s by invite code", current_user["id"], group_id)
    return _group_row_to_out(
        row,
        my_role=my_role,
        member_count=int(member_count),
        show_invite_code=(my_role == "admin"),
    )


# ---------------------------------------------------------------------------
# Invitations (group-scoped & user-scoped)
# ---------------------------------------------------------------------------


@router.get("/api/groups/{group_id}/invitations", response_model=list[GroupInvitationOut])
def list_group_invitations(
    group_id: str,
    current_user: dict = Depends(_get_current_user),
) -> list[GroupInvitationOut]:
    """グループ管理者向けの招待一覧（pending のみ）。"""
    session = _pg_session()
    try:
        _require_admin(session, group_id, current_user["id"])
        rows = session.execute(
            sa_text("""
                SELECT gi.id, gi.group_id, g.name, gi.invitee_user_id, u.display_name,
                       gi.inviter_user_id, iu.display_name,
                       gi.status, gi.created_at, gi.responded_at
                FROM group_invitations gi
                JOIN groups g ON g.id = gi.group_id
                JOIN users u ON u.id = gi.invitee_user_id
                JOIN users iu ON iu.id = gi.inviter_user_id
                WHERE gi.group_id = CAST(:gid AS uuid) AND gi.status = 'pending'
                ORDER BY gi.created_at DESC
            """),
            {"gid": group_id},
        ).fetchall()
    finally:
        session.close()

    return [
        GroupInvitationOut(
            id=str(r[0]),
            group_id=str(r[1]),
            group_name=r[2] or "",
            invitee_user_id=str(r[3]),
            invitee_username=r[4] or "",
            inviter_user_id=str(r[5]),
            inviter_username=r[6] or "",
            status=r[7],
            created_at=r[8].isoformat() if r[8] else "",
            responded_at=r[9].isoformat() if r[9] else "",
        )
        for r in rows
    ]


@router.get("/api/me/invitations", response_model=list[GroupInvitationOut])
def list_my_invitations(
    current_user: dict = Depends(_get_current_user),
) -> list[GroupInvitationOut]:
    """自分宛ての pending 招待一覧。"""
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT gi.id, gi.group_id, g.name, gi.invitee_user_id, u.display_name,
                       gi.inviter_user_id, iu.display_name,
                       gi.status, gi.created_at, gi.responded_at
                FROM group_invitations gi
                JOIN groups g ON g.id = gi.group_id
                JOIN users u ON u.id = gi.invitee_user_id
                JOIN users iu ON iu.id = gi.inviter_user_id
                WHERE gi.invitee_user_id = CAST(:uid AS uuid) AND gi.status = 'pending'
                ORDER BY gi.created_at DESC
            """),
            {"uid": current_user["id"]},
        ).fetchall()
    finally:
        session.close()

    return [
        GroupInvitationOut(
            id=str(r[0]),
            group_id=str(r[1]),
            group_name=r[2] or "",
            invitee_user_id=str(r[3]),
            invitee_username=r[4] or "",
            inviter_user_id=str(r[5]),
            inviter_username=r[6] or "",
            status=r[7],
            created_at=r[8].isoformat() if r[8] else "",
            responded_at=r[9].isoformat() if r[9] else "",
        )
        for r in rows
    ]


def _respond_invitation(invitation_id: str, user_id: str, accept: bool) -> GroupInvitationOut:
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                SELECT gi.id, gi.group_id, gi.invitee_user_id, gi.status
                FROM group_invitations gi
                WHERE gi.id = CAST(:id AS uuid)
                LIMIT 1
            """),
            {"id": invitation_id},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Invitation not found")
        if str(row[2]) != str(user_id):
            raise HTTPException(status_code=403, detail="This invitation is not addressed to you")
        if row[3] != "pending":
            raise HTTPException(status_code=409, detail=f"Invitation already {row[3]}")

        group_id = row[1]
        new_status = "accepted" if accept else "declined"
        session.execute(
            sa_text("""
                UPDATE group_invitations
                SET status = :status, responded_at = now()
                WHERE id = CAST(:id AS uuid)
            """),
            {"status": new_status, "id": invitation_id},
        )
        if accept:
            # 既に他経路で参加済みかもしれないので冪等に扱う
            session.execute(
                sa_text("""
                    INSERT INTO group_members (group_id, user_id, role)
                    VALUES (:gid, CAST(:uid AS uuid), 'member')
                    ON CONFLICT (group_id, user_id) DO NOTHING
                """),
                {"gid": group_id, "uid": user_id},
            )

        detail_row = session.execute(
            sa_text("""
                SELECT gi.id, gi.group_id, g.name, gi.invitee_user_id, u.display_name,
                       gi.inviter_user_id, iu.display_name,
                       gi.status, gi.created_at, gi.responded_at
                FROM group_invitations gi
                JOIN groups g ON g.id = gi.group_id
                JOIN users u ON u.id = gi.invitee_user_id
                JOIN users iu ON iu.id = gi.inviter_user_id
                WHERE gi.id = CAST(:id AS uuid)
                LIMIT 1
            """),
            {"id": invitation_id},
        ).fetchone()
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    return GroupInvitationOut(
        id=str(detail_row[0]),
        group_id=str(detail_row[1]),
        group_name=detail_row[2] or "",
        invitee_user_id=str(detail_row[3]),
        invitee_username=detail_row[4] or "",
        inviter_user_id=str(detail_row[5]),
        inviter_username=detail_row[6] or "",
        status=detail_row[7],
        created_at=detail_row[8].isoformat() if detail_row[8] else "",
        responded_at=detail_row[9].isoformat() if detail_row[9] else "",
    )


@router.post("/api/me/invitations/{invitation_id}/accept", response_model=GroupInvitationOut)
def accept_invitation(
    invitation_id: str,
    current_user: dict = Depends(_get_current_user),
) -> GroupInvitationOut:
    """自分宛ての招待を承諾し、グループに参加する。"""
    return _respond_invitation(invitation_id, current_user["id"], accept=True)


@router.post("/api/me/invitations/{invitation_id}/decline", response_model=GroupInvitationOut)
def decline_invitation(
    invitation_id: str,
    current_user: dict = Depends(_get_current_user),
) -> GroupInvitationOut:
    """自分宛ての招待を辞退する。"""
    return _respond_invitation(invitation_id, current_user["id"], accept=False)
