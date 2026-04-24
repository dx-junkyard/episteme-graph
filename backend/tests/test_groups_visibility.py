"""Issue #121: グループ管理と開示範囲制御のテスト。

対象:
  - `backend/api/routes/groups.py` (グループ CRUD / 招待 / 参加)
  - `backend/api/routes/learning.py` の `_validate_visibility`
  - `backend/api/schemas.py` の Group/Invitation/Visibility モデル

戦略:
  FastAPI のルータはすべて `_pg_session()` を経由して raw SQL を実行するため、
  ここでは SQL 文字列から意図を判別して in-memory なデータを返す `_FakeSession` を使う。
  services.get_user_group_ids / user_can_access_group など DB ヘルパはモンキーパッチで差し替える。
"""

from __future__ import annotations

import copy
import datetime as dt
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))


# ---------------------------------------------------------------------------
# In-memory ストア
# ---------------------------------------------------------------------------

USER_ALICE = {
    "id": "00000000-0000-0000-0000-00000000aaaa",
    "username": "alice",
    "email": "alice@test.local",
    "role": "TEACHER",
}

USER_BOB = {
    "id": "00000000-0000-0000-0000-00000000bbbb",
    "username": "bob",
    "email": "bob@test.local",
    "role": "STUDENT",
}


class _Result:
    def __init__(self, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def scalar(self):
        if self._scalar is not None:
            return self._scalar
        if self._rows:
            return self._rows[0][0]
        return None


class _Store:
    """グループ・メンバー・招待・ユーザーを保持する in-memory 疑似 DB。"""

    def __init__(self):
        self.users = {USER_ALICE["id"]: USER_ALICE, USER_BOB["id"]: USER_BOB}
        self.groups = {}  # id -> {id,name,description,invite_code,created_by,created_at,updated_at}
        self.members = []  # list of {group_id, user_id, role, joined_at}
        self.invitations = []  # list of invitations


class _FakeSession:
    """SQL 文字列の特徴から意図を判別し、`_Store` に対する CRUD を行う最小実装。"""

    def __init__(self, store: _Store):
        self.store = store
        # rollback 用にスナップショットを取る（shallow なリスト/辞書コピー）
        self._snapshot = {
            "groups": {k: dict(v) for k, v in store.groups.items()},
            "members": [dict(m) for m in store.members],
            "invitations": [dict(i) for i in store.invitations],
        }

    # -- helpers ----------------------------------------------------------
    def _group_row(self, g):
        return (
            g["id"],
            g["name"],
            g["description"],
            g.get("invite_code"),
            g["created_by"],
            g["created_at"],
            g["updated_at"],
        )

    # -- Session API -----------------------------------------------------
    def execute(self, query, params=None):
        sql = str(query).strip()
        p = params or {}

        # --- DELETE / UPDATE ブランチは SELECT より先に評価する
        # (両者が "FROM groups WHERE id = CAST" という部分文字列を共有するため)
        if sql.startswith("DELETE FROM group_members"):
            gid = str(p["gid"]); uid = str(p["uid"])
            before = len(self.store.members)
            self.store.members = [
                m for m in self.store.members
                if not (m["group_id"] == gid and m["user_id"] == uid)
            ]
            if len(self.store.members) < before:
                return _Result([(str(uuid.uuid4()),)])  # RETURNING id dummy
            return _Result([])

        if sql.startswith("DELETE FROM groups"):
            gid = str(p["id"])
            self.store.groups.pop(gid, None)
            self.store.members = [m for m in self.store.members if m["group_id"] != gid]
            self.store.invitations = [i for i in self.store.invitations if i["group_id"] != gid]
            return _Result()

        if sql.startswith("UPDATE groups"):
            gid = str(p["id"])
            g = self.store.groups.get(gid)
            if g:
                if "name" in p:
                    g["name"] = p["name"]
                if "description" in p:
                    g["description"] = p["description"]
                if "code" in p:
                    g["invite_code"] = p["code"]
                g["updated_at"] = dt.datetime.utcnow()
            return _Result()

        # --- INSERT into groups ---
        if sql.startswith("INSERT INTO groups"):
            gid = str(p["id"])
            now = dt.datetime.utcnow()
            self.store.groups[gid] = {
                "id": gid,
                "name": p["name"],
                "description": p.get("description", ""),
                "invite_code": p.get("invite_code"),
                "created_by": p["created_by"],
                "created_at": now,
                "updated_at": now,
            }
            return _Result()

        # --- INSERT into group_members ---
        if sql.startswith("INSERT INTO group_members"):
            self.store.members.append({
                "group_id": str(p["gid"]),
                "user_id": str(p["uid"]),
                "role": p.get("role", "member") if "role" in p else ("admin" if "admin" in sql else "member"),
                "joined_at": dt.datetime.utcnow(),
            })
            return _Result()

        # --- INSERT into group_invitations ---
        if sql.startswith("INSERT INTO group_invitations"):
            self.store.invitations.append({
                "id": str(p["id"]),
                "group_id": str(p["gid"]),
                "invitee_user_id": str(p["uid"]),
                "inviter_user_id": str(p["inviter"]),
                "status": "pending",
                "created_at": dt.datetime.utcnow(),
                "responded_at": None,
            })
            return _Result()

        # --- SELECT role FROM group_members (membership check) ---
        if "SELECT role FROM group_members" in sql and "WHERE group_id" in sql:
            gid = str(p["gid"]); uid = str(p["uid"])
            for m in self.store.members:
                if m["group_id"] == gid and m["user_id"] == uid:
                    return _Result([(m["role"],)])
            return _Result([])

        # --- SELECT 1 FROM group_members (already-member check) ---
        if "SELECT 1 FROM group_members" in sql:
            gid = str(p["gid"]); uid = str(p["uid"])
            for m in self.store.members:
                if m["group_id"] == gid and m["user_id"] == uid:
                    return _Result([(1,)])
            return _Result([])

        # --- SELECT group detail by id ---
        if "FROM groups WHERE id = CAST" in sql or "FROM groups WHERE id = :id" in sql:
            gid = str(p["id"])
            g = self.store.groups.get(gid)
            if not g:
                return _Result([])
            if "SELECT name FROM groups" in sql:
                return _Result([(g["name"],)])
            return _Result([self._group_row(g)])

        # --- SELECT group by invite_code ---
        if "FROM groups WHERE invite_code" in sql:
            code = p["code"]
            for g in self.store.groups.values():
                if g.get("invite_code") == code:
                    return _Result([self._group_row(g)])
            return _Result([])

        # --- SELECT user by display_name ---
        if "FROM users WHERE display_name" in sql:
            name = p.get("name")
            for u in self.store.users.values():
                if u["username"] == name:
                    return _Result([(u["id"], u["username"], u["email"])])
            return _Result([])

        # --- SELECT user by email ---
        if "FROM users WHERE email" in sql:
            email = p.get("email")
            for u in self.store.users.values():
                if u["email"] == email:
                    return _Result([(u["id"], u["username"], u["email"])])
            return _Result([])

        # --- SELECT existing pending invitation ---
        if "FROM group_invitations" in sql and "status = 'pending'" in sql and "LIMIT 1" in sql and "gi.invitee_user_id" not in sql:
            gid = str(p["gid"]); uid = str(p["uid"])
            for inv in self.store.invitations:
                if (
                    inv["group_id"] == gid
                    and inv["invitee_user_id"] == uid
                    and inv["status"] == "pending"
                ):
                    return _Result([(inv["id"], inv["created_at"])])
            return _Result([])

        # --- SELECT my invitations (user-scoped pending) ---
        if "WHERE gi.invitee_user_id = CAST(:uid AS uuid)" in sql and "status = 'pending'" in sql:
            uid = str(p["uid"])
            out = []
            for inv in self.store.invitations:
                if inv["invitee_user_id"] == uid and inv["status"] == "pending":
                    g = self.store.groups.get(inv["group_id"], {})
                    invitee = self.store.users.get(inv["invitee_user_id"], {})
                    inviter = self.store.users.get(inv["inviter_user_id"], {})
                    out.append((
                        inv["id"], inv["group_id"], g.get("name", ""),
                        inv["invitee_user_id"], invitee.get("username", ""),
                        inv["inviter_user_id"], inviter.get("username", ""),
                        inv["status"], inv["created_at"], inv["responded_at"],
                    ))
            return _Result(out)

        # --- SELECT single invitation by id (for _respond_invitation) ---
        if "FROM group_invitations gi" in sql and "WHERE gi.id = CAST(:id AS uuid)" in sql and "status" in sql and "g.name" not in sql:
            iid = str(p["id"])
            for inv in self.store.invitations:
                if inv["id"] == iid:
                    return _Result([(inv["id"], inv["group_id"], inv["invitee_user_id"], inv["status"])])
            return _Result([])

        # --- SELECT invitation detail (joined) by id ---
        if "FROM group_invitations gi" in sql and "WHERE gi.id = CAST(:id AS uuid)" in sql and "g.name" in sql:
            iid = str(p["id"])
            for inv in self.store.invitations:
                if inv["id"] == iid:
                    g = self.store.groups.get(inv["group_id"], {})
                    invitee = self.store.users.get(inv["invitee_user_id"], {})
                    inviter = self.store.users.get(inv["inviter_user_id"], {})
                    return _Result([(
                        inv["id"], inv["group_id"], g.get("name", ""),
                        inv["invitee_user_id"], invitee.get("username", ""),
                        inv["inviter_user_id"], inviter.get("username", ""),
                        inv["status"], inv["created_at"], inv["responded_at"],
                    )])
            return _Result([])

        # --- UPDATE invitation status ---
        if sql.startswith("UPDATE group_invitations") and "SET status" in sql and "WHERE id = CAST" in sql:
            iid = str(p["id"])
            for inv in self.store.invitations:
                if inv["id"] == iid:
                    inv["status"] = p["status"]
                    inv["responded_at"] = dt.datetime.utcnow()
            return _Result()

        # --- UPDATE invitation on join-by-code (pending → accepted for user) ---
        if sql.startswith("UPDATE group_invitations") and "invitee_user_id" in sql:
            gid = str(p["gid"]); uid = str(p["uid"])
            for inv in self.store.invitations:
                if (
                    inv["group_id"] == gid
                    and inv["invitee_user_id"] == uid
                    and inv["status"] == "pending"
                ):
                    inv["status"] = "accepted"
                    inv["responded_at"] = dt.datetime.utcnow()
            return _Result()

        # --- INSERT with ON CONFLICT (idempotent member add) ---
        if "INSERT INTO group_members" in sql and "ON CONFLICT" in sql:
            gid = str(p["gid"]); uid = str(p["uid"])
            exists = any(m["group_id"] == gid and m["user_id"] == uid for m in self.store.members)
            if not exists:
                self.store.members.append({
                    "group_id": gid, "user_id": uid, "role": "member",
                    "joined_at": dt.datetime.utcnow(),
                })
            return _Result()

        # --- list_my_groups: SELECT g.id ... JOIN group_members gm ... WHERE gm.user_id ---
        # (must come before the generic COUNT(*) branch because this query contains a
        # correlated subquery `(SELECT COUNT(*) FROM group_members gm2 ...)`.)
        if "FROM groups g" in sql and "JOIN group_members gm" in sql and "gm.user_id = CAST" in sql:
            uid = str(p["uid"])
            out = []
            for m in self.store.members:
                if m["user_id"] != uid:
                    continue
                g = self.store.groups.get(m["group_id"])
                if not g:
                    continue
                member_count = sum(1 for m2 in self.store.members if m2["group_id"] == g["id"])
                out.append((
                    g["id"], g["name"], g["description"], g.get("invite_code"),
                    g["created_by"], g["created_at"], g["updated_at"],
                    m["role"], member_count,
                ))
            return _Result(out)

        # --- SELECT COUNT(*) member_count ---
        if "SELECT COUNT(*) FROM group_members" in sql and "role = 'admin'" in sql:
            gid = str(p["gid"])
            n = sum(1 for m in self.store.members if m["group_id"] == gid and m["role"] == "admin")
            return _Result(scalar=n)
        if "SELECT COUNT(*) FROM group_members" in sql:
            gid = str(p.get("gid") or p.get("id"))
            n = sum(1 for m in self.store.members if m["group_id"] == gid)
            return _Result(scalar=n)

        # --- SELECT members for group detail ---
        if "FROM group_members gm" in sql and "JOIN users u" in sql:
            gid = str(p["gid"])
            out = []
            for m in self.store.members:
                if m["group_id"] == gid:
                    u = self.store.users.get(m["user_id"], {})
                    out.append((
                        u.get("id"), u.get("username"), u.get("email"),
                        m["role"], m["joined_at"],
                    ))
            return _Result(out)

        # Default
        return _Result()

    def commit(self):
        # スナップショットを現在の状態で更新
        self._snapshot = {
            "groups": {k: dict(v) for k, v in self.store.groups.items()},
            "members": [dict(m) for m in self.store.members],
            "invitations": [dict(i) for i in self.store.invitations],
        }

    def rollback(self):
        # 直近 commit 時点の状態に戻す
        self.store.groups = {k: dict(v) for k, v in self._snapshot["groups"].items()}
        self.store.members = [dict(m) for m in self._snapshot["members"]]
        self.store.invitations = [dict(i) for i in self._snapshot["invitations"]]

    def close(self):
        pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client_and_state(monkeypatch):
    """TestClient + in-memory store + current-user holder を返す。"""
    from fastapi.testclient import TestClient
    from main import app
    from dependencies import _get_current_user

    store = _Store()
    current = {"user": USER_ALICE}

    def _override_current_user():
        return current["user"]

    app.dependency_overrides[_get_current_user] = _override_current_user

    # routes.groups._pg_session と services の DB-touch 関数を差し替え
    monkeypatch.setattr("routes.groups._pg_session", lambda: _FakeSession(store))
    # services.user_can_access_group は visibility=group バリデーションで呼ばれる
    def _user_can_access_group(user_id, group_id):
        return any(m["user_id"] == str(user_id) and m["group_id"] == str(group_id) for m in store.members)
    def _get_user_group_ids(user_id):
        return [m["group_id"] for m in store.members if m["user_id"] == str(user_id)]
    monkeypatch.setattr("services.user_can_access_group", _user_can_access_group)
    monkeypatch.setattr("services.get_user_group_ids", _get_user_group_ids)
    monkeypatch.setattr("routes.learning.user_can_access_group", _user_can_access_group)
    monkeypatch.setattr("routes.learning.get_user_group_ids", _get_user_group_ids)

    client = TestClient(app)
    try:
        yield client, current, store
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# ヘルパ関数
# ---------------------------------------------------------------------------


def _h():
    return {"Authorization": "Bearer dummy"}


# ---------------------------------------------------------------------------
# Group CRUD + invite-code join
# ---------------------------------------------------------------------------


class TestGroupLifecycle:
    def test_create_group_sets_creator_as_admin_with_invite_code(self, client_and_state):
        client, current, store = client_and_state
        current["user"] = USER_ALICE

        resp = client.post(
            "/api/groups",
            json={"name": "研究室A", "description": "学習G"},
            headers=_h(),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()

        assert body["name"] == "研究室A"
        assert body["description"] == "学習G"
        assert body["my_role"] == "admin"
        assert body["member_count"] == 1
        assert body["invite_code"] and len(body["invite_code"]) >= 8

        # メンバーに Alice が admin として追加されている
        assert len(store.members) == 1
        assert store.members[0]["role"] == "admin"
        assert store.members[0]["user_id"] == USER_ALICE["id"]

    def test_list_my_groups_shows_only_joined_groups(self, client_and_state):
        client, current, store = client_and_state

        # Alice が 1 つグループを作る
        current["user"] = USER_ALICE
        client.post("/api/groups", json={"name": "AliceGrp"}, headers=_h())

        # Alice には見える
        resp_a = client.get("/api/groups", headers=_h())
        assert resp_a.status_code == 200
        assert len(resp_a.json()) == 1
        assert resp_a.json()[0]["name"] == "AliceGrp"

        # Bob には見えない
        current["user"] = USER_BOB
        resp_b = client.get("/api/groups", headers=_h())
        assert resp_b.status_code == 200
        assert resp_b.json() == []

    def test_join_by_invalid_code_returns_404(self, client_and_state):
        client, current, _ = client_and_state
        current["user"] = USER_BOB

        resp = client.post(
            "/api/groups/join-by-code",
            json={"invite_code": "no-such-code"},
            headers=_h(),
        )
        assert resp.status_code == 404

    def test_join_by_valid_code_adds_member(self, client_and_state):
        client, current, store = client_and_state

        current["user"] = USER_ALICE
        created = client.post("/api/groups", json={"name": "OpenGrp"}, headers=_h()).json()
        code = created["invite_code"]

        current["user"] = USER_BOB
        resp = client.post(
            "/api/groups/join-by-code",
            json={"invite_code": code},
            headers=_h(),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["name"] == "OpenGrp"
        assert body["my_role"] == "member"
        # Bob はメンバー（admin ではない）なので invite_code は隠される
        assert body["invite_code"] is None

        # Bob がメンバーに追加された
        assert any(m["user_id"] == USER_BOB["id"] for m in store.members)


class TestInvitationFlow:
    def test_admin_invites_user_and_invitee_accepts(self, client_and_state):
        client, current, store = client_and_state

        # Alice がグループ作成
        current["user"] = USER_ALICE
        grp = client.post("/api/groups", json={"name": "InviteGrp"}, headers=_h()).json()
        gid = grp["id"]

        # Alice が Bob を招待
        resp = client.post(
            f"/api/groups/{gid}/members",
            json={"username": "bob"},
            headers=_h(),
        )
        assert resp.status_code == 201, resp.text
        inv = resp.json()
        assert inv["status"] == "pending"
        assert inv["invitee_username"] == "bob"

        # Bob の招待一覧に出る
        current["user"] = USER_BOB
        mine = client.get("/api/me/invitations", headers=_h())
        assert mine.status_code == 200
        assert len(mine.json()) == 1
        assert mine.json()[0]["group_name"] == "InviteGrp"

        # Bob が承諾
        acc = client.post(f"/api/me/invitations/{inv['id']}/accept", headers=_h())
        assert acc.status_code == 200
        assert acc.json()["status"] == "accepted"

        # Bob がメンバーに加わっている
        assert any(m["user_id"] == USER_BOB["id"] and m["group_id"] == gid for m in store.members)

    def test_decline_does_not_add_member(self, client_and_state):
        client, current, store = client_and_state
        current["user"] = USER_ALICE
        grp = client.post("/api/groups", json={"name": "DeclineGrp"}, headers=_h()).json()
        inv = client.post(
            f"/api/groups/{grp['id']}/members",
            json={"email": "bob@test.local"},
            headers=_h(),
        ).json()

        current["user"] = USER_BOB
        dec = client.post(f"/api/me/invitations/{inv['id']}/decline", headers=_h())
        assert dec.status_code == 200
        assert dec.json()["status"] == "declined"
        assert not any(m["user_id"] == USER_BOB["id"] and m["group_id"] == grp["id"] for m in store.members)

    def test_cannot_respond_to_other_users_invitation(self, client_and_state):
        client, current, store = client_and_state
        current["user"] = USER_ALICE
        grp = client.post("/api/groups", json={"name": "X"}, headers=_h()).json()
        inv = client.post(
            f"/api/groups/{grp['id']}/members",
            json={"username": "bob"},
            headers=_h(),
        ).json()

        # Alice が Bob 宛の招待を承諾しようとする → 403
        current["user"] = USER_ALICE
        resp = client.post(f"/api/me/invitations/{inv['id']}/accept", headers=_h())
        assert resp.status_code == 403

    def test_invite_unknown_user_returns_404(self, client_and_state):
        client, current, _ = client_and_state
        current["user"] = USER_ALICE
        grp = client.post("/api/groups", json={"name": "Y"}, headers=_h()).json()
        resp = client.post(
            f"/api/groups/{grp['id']}/members",
            json={"username": "nobody"},
            headers=_h(),
        )
        assert resp.status_code == 404


class TestInviteCodeGeneration:
    def test_generated_codes_are_url_safe_and_12_chars(self):
        from routes.groups import _generate_invite_code
        codes = {_generate_invite_code() for _ in range(50)}
        # 衝突しない(ほぼ)
        assert len(codes) == 50
        for c in codes:
            assert len(c) == 12
            assert all(ch.isalnum() or ch in "-_" for ch in c)


class TestInviteCodeRotation:
    """招待コード再発行（無効化）フロー。"""

    def test_rotate_invalidates_old_code_and_new_code_works(self, client_and_state):
        client, current, store = client_and_state

        # Alice がグループ作成
        current["user"] = USER_ALICE
        grp = client.post("/api/groups", json={"name": "RotGrp"}, headers=_h()).json()
        old_code = grp["invite_code"]
        gid = grp["id"]
        assert old_code

        # 招待コード再発行
        resp = client.post(f"/api/groups/{gid}/invite-code/rotate", headers=_h())
        assert resp.status_code == 200, resp.text
        new_code = resp.json()["invite_code"]
        assert new_code and new_code != old_code

        # Bob が古いコードで参加しようとすると 404
        current["user"] = USER_BOB
        old_resp = client.post(
            "/api/groups/join-by-code",
            json={"invite_code": old_code},
            headers=_h(),
        )
        assert old_resp.status_code == 404

        # Bob が新しいコードで参加すると成功
        new_resp = client.post(
            "/api/groups/join-by-code",
            json={"invite_code": new_code},
            headers=_h(),
        )
        assert new_resp.status_code == 200, new_resp.text
        assert new_resp.json()["id"] == gid

    def test_non_admin_cannot_rotate_invite_code(self, client_and_state):
        client, current, store = client_and_state

        current["user"] = USER_ALICE
        grp = client.post("/api/groups", json={"name": "G"}, headers=_h()).json()
        code = grp["invite_code"]

        # Bob が参加
        current["user"] = USER_BOB
        client.post("/api/groups/join-by-code", json={"invite_code": code}, headers=_h())

        # Bob (member) が rotate を叩く → 403
        resp = client.post(f"/api/groups/{grp['id']}/invite-code/rotate", headers=_h())
        assert resp.status_code == 403


class TestMemberRemoval:
    """メンバー削除・退会・最後の admin 保護。"""

    def test_admin_can_remove_member(self, client_and_state):
        client, current, store = client_and_state

        # Alice（admin）がグループ作成、Bob が参加
        current["user"] = USER_ALICE
        grp = client.post("/api/groups", json={"name": "RmGrp"}, headers=_h()).json()
        current["user"] = USER_BOB
        client.post(
            "/api/groups/join-by-code",
            json={"invite_code": grp["invite_code"]},
            headers=_h(),
        )
        assert any(m["user_id"] == USER_BOB["id"] for m in store.members)

        # Alice が Bob を除名
        current["user"] = USER_ALICE
        resp = client.delete(
            f"/api/groups/{grp['id']}/members/{USER_BOB['id']}",
            headers=_h(),
        )
        assert resp.status_code == 200
        assert not any(
            m["user_id"] == USER_BOB["id"] and m["group_id"] == grp["id"]
            for m in store.members
        )

    def test_member_can_leave_themselves(self, client_and_state):
        client, current, store = client_and_state

        current["user"] = USER_ALICE
        grp = client.post("/api/groups", json={"name": "LeaveGrp"}, headers=_h()).json()

        current["user"] = USER_BOB
        client.post(
            "/api/groups/join-by-code",
            json={"invite_code": grp["invite_code"]},
            headers=_h(),
        )
        # Bob が自分自身を退会
        resp = client.delete(
            f"/api/groups/{grp['id']}/members/{USER_BOB['id']}",
            headers=_h(),
        )
        assert resp.status_code == 200
        assert not any(m["user_id"] == USER_BOB["id"] for m in store.members)

    def test_non_admin_cannot_remove_others(self, client_and_state):
        client, current, store = client_and_state

        current["user"] = USER_ALICE
        grp = client.post("/api/groups", json={"name": "G"}, headers=_h()).json()
        current["user"] = USER_BOB
        client.post(
            "/api/groups/join-by-code",
            json={"invite_code": grp["invite_code"]},
            headers=_h(),
        )

        # Bob（member）が Alice（admin）を除名しようとする → 403
        resp = client.delete(
            f"/api/groups/{grp['id']}/members/{USER_ALICE['id']}",
            headers=_h(),
        )
        assert resp.status_code == 403
        # Alice はまだメンバーのまま
        assert any(m["user_id"] == USER_ALICE["id"] for m in store.members)

    def test_removing_last_admin_is_rejected(self, client_and_state):
        client, current, store = client_and_state

        # Alice だけのグループを作り、Alice が自分自身を退会しようとする
        current["user"] = USER_ALICE
        grp = client.post("/api/groups", json={"name": "LastAdmin"}, headers=_h()).json()

        resp = client.delete(
            f"/api/groups/{grp['id']}/members/{USER_ALICE['id']}",
            headers=_h(),
        )
        assert resp.status_code == 400
        # Alice は残っている（ロールバックされた）
        assert any(
            m["user_id"] == USER_ALICE["id"] and m["group_id"] == grp["id"]
            for m in store.members
        )

    def test_remove_nonexistent_member_returns_404(self, client_and_state):
        client, current, store = client_and_state

        current["user"] = USER_ALICE
        grp = client.post("/api/groups", json={"name": "G"}, headers=_h()).json()

        # Bob は未参加
        resp = client.delete(
            f"/api/groups/{grp['id']}/members/{USER_BOB['id']}",
            headers=_h(),
        )
        assert resp.status_code == 404


class TestGroupDeletion:
    """グループ削除と関連レコードの削除。"""

    def test_admin_can_delete_group(self, client_and_state):
        client, current, store = client_and_state

        current["user"] = USER_ALICE
        grp = client.post("/api/groups", json={"name": "ToDelete"}, headers=_h()).json()
        gid = grp["id"]
        assert gid in store.groups

        resp = client.delete(f"/api/groups/{gid}", headers=_h())
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        assert gid not in store.groups
        # メンバーレコードも全て消える
        assert not any(m["group_id"] == gid for m in store.members)

    def test_non_admin_cannot_delete_group(self, client_and_state):
        client, current, store = client_and_state

        current["user"] = USER_ALICE
        grp = client.post("/api/groups", json={"name": "NoDel"}, headers=_h()).json()
        current["user"] = USER_BOB
        client.post(
            "/api/groups/join-by-code",
            json={"invite_code": grp["invite_code"]},
            headers=_h(),
        )

        # Bob (member) が削除を試みる → 403
        resp = client.delete(f"/api/groups/{grp['id']}", headers=_h())
        assert resp.status_code == 403
        assert grp["id"] in store.groups

    def test_delete_cascades_invitations(self, client_and_state):
        client, current, store = client_and_state

        current["user"] = USER_ALICE
        grp = client.post("/api/groups", json={"name": "CascadeGrp"}, headers=_h()).json()
        # Bob を招待
        client.post(
            f"/api/groups/{grp['id']}/members",
            json={"username": "bob"},
            headers=_h(),
        )
        assert len(store.invitations) == 1

        # グループ削除
        resp = client.delete(f"/api/groups/{grp['id']}", headers=_h())
        assert resp.status_code == 200
        # 招待も削除される
        assert not any(inv["group_id"] == grp["id"] for inv in store.invitations)


class TestEndToEndGroupLifecycle:
    """作成 → 招待 → 参加 → ローテーション → 退会 → 削除 の一連フロー。"""

    def test_full_group_lifecycle(self, client_and_state):
        client, current, store = client_and_state

        # 1. Alice がグループ作成
        current["user"] = USER_ALICE
        created = client.post(
            "/api/groups",
            json={"name": "FullFlow", "description": "E2E flow"},
            headers=_h(),
        )
        assert created.status_code == 201
        grp = created.json()
        gid = grp["id"]
        first_code = grp["invite_code"]

        # 2. Bob を直接招待
        inv_resp = client.post(
            f"/api/groups/{gid}/members",
            json={"username": "bob"},
            headers=_h(),
        )
        assert inv_resp.status_code == 201
        inv_id = inv_resp.json()["id"]

        # 3. Bob が招待を承諾
        current["user"] = USER_BOB
        acc = client.post(f"/api/me/invitations/{inv_id}/accept", headers=_h())
        assert acc.status_code == 200
        assert acc.json()["status"] == "accepted"
        assert any(m["user_id"] == USER_BOB["id"] and m["group_id"] == gid for m in store.members)

        # 4. Alice が招待コードを再発行 → 旧コード無効化
        current["user"] = USER_ALICE
        rot = client.post(f"/api/groups/{gid}/invite-code/rotate", headers=_h())
        assert rot.status_code == 200
        new_code = rot.json()["invite_code"]
        assert new_code != first_code

        # 5. Bob が退会
        current["user"] = USER_BOB
        leave = client.delete(
            f"/api/groups/{gid}/members/{USER_BOB['id']}",
            headers=_h(),
        )
        assert leave.status_code == 200
        assert not any(m["user_id"] == USER_BOB["id"] and m["group_id"] == gid for m in store.members)

        # 6. Alice がグループを削除
        current["user"] = USER_ALICE
        dele = client.delete(f"/api/groups/{gid}", headers=_h())
        assert dele.status_code == 200
        assert gid not in store.groups


# ---------------------------------------------------------------------------
# Visibility validation
# ---------------------------------------------------------------------------


class TestVisibilityValidation:
    def test_invalid_visibility_string_400(self, client_and_state, monkeypatch):
        from routes.learning import _validate_visibility
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            _validate_visibility("garbage", None, USER_ALICE["id"])
        assert exc.value.status_code == 400

    def test_group_visibility_requires_group_id(self, client_and_state):
        from routes.learning import _validate_visibility
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            _validate_visibility("group", None, USER_ALICE["id"])
        assert exc.value.status_code == 400

    def test_group_visibility_requires_membership(self, client_and_state):
        from routes.learning import _validate_visibility
        from fastapi import HTTPException

        # Alice は未参加のグループを指定 → 403
        foreign_gid = "99999999-9999-9999-9999-999999999999"
        with pytest.raises(HTTPException) as exc:
            _validate_visibility("group", foreign_gid, USER_ALICE["id"])
        assert exc.value.status_code == 403

    def test_group_visibility_ok_when_member(self, client_and_state):
        client, current, store = client_and_state
        current["user"] = USER_ALICE
        grp = client.post("/api/groups", json={"name": "MemGrp"}, headers=_h()).json()

        from routes.learning import _validate_visibility
        # Alice は作成者なので member（admin）。例外は飛ばないはず
        _validate_visibility("group", grp["id"], USER_ALICE["id"])

    def test_public_and_private_never_need_group_id(self, client_and_state):
        from routes.learning import _validate_visibility
        _validate_visibility("public", None, USER_ALICE["id"])
        _validate_visibility("private", None, USER_ALICE["id"])


# ---------------------------------------------------------------------------
# Schema round-trip
# ---------------------------------------------------------------------------


class TestSchemas:
    def test_group_out_serializes_minimum_fields(self):
        from schemas import GroupOut
        g = GroupOut(id="g1", name="G", created_by="u1")
        d = g.model_dump()
        assert d["id"] == "g1"
        assert d["invite_code"] is None
        assert d["my_role"] == "member"
        assert d["member_count"] == 0

    def test_group_invitation_out_defaults_pending(self):
        from schemas import GroupInvitationOut
        inv = GroupInvitationOut(id="i1", group_id="g1", invitee_user_id="u2", inviter_user_id="u1")
        assert inv.status == "pending"

    def test_visibility_update_request(self):
        from schemas import VisibilityUpdateRequest
        body = VisibilityUpdateRequest(visibility="group", group_id="gid-1")
        assert body.visibility == "group"
        assert body.group_id == "gid-1"
