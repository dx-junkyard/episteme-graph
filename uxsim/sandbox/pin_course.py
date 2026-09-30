"""campaign が固定したコース（``course_id``）以外の**同名の公開コース**を受講できなくする（運転側の手）。

使い方: backend/.venv/bin/python -m uxsim.sandbox.pin_course [COURSE_ID | --campaign PATH] [--yes]
        [--owner-persona te-01-... ...] [--env uxsim/sandbox/.env.uxsim]

理由（第 12 周 = IK-0506）: 同じ題名の公開コースが 2 本あり、``reset_learner_state`` で受講が外れた学生ペルソナは
一覧から**再生成していない方**を選んで受講した。runner は campaign の ``course_id`` を候補に置くだけで
ペルソナの選択を差し替えない（差し替えると画面の観測ではなくなる）ので、取り違えの余地を砂場の側で消す。

- 既定は dry-run（何をするかを表示するだけ）。``--yes`` で実行する。
- 書き込みは製品の API だけ: ``PUT /api/admin/courses/{id}/visibility`` に ``{"visibility": "private"}``
  （コースの所有者でないと 404 になる製品の規則どおり、所有者でログインして呼ぶ。所有者は
  ``--owner-persona`` のペルソナ → 管理者の順に試す。``UXSIM_SANDBOX_DATABASE_URL`` があれば所有者を SELECT で
  引いて表示する — DB は読みだけ）。
- 固定したコース自体は触らない。公開されていなければ警告だけ出す。
- 実行後に管理者の ``GET /api/learning/courses`` で、その題名の受講可能なコースが固定したものだけになったかを確かめる。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Optional

import yaml

from uxsim.config import get_settings
from uxsim.runner.client import EpistemeClient

PERSONA_EMAIL_DOMAIN = "@uxsim.invalid"


def persona_username(persona_id: str) -> str:
    """``uxsim.runner.api.persona_username`` と同じ規則（api を import すると LLM 層まで読むので複製しない範囲で同じ式）。"""
    return "uxsim_" + persona_id.replace("-", "_")


def pinned_from_campaign(path: Path) -> str:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return str(data.get("course_id") or "")


def select_conflicts(courses: list[dict[str, Any]], pinned: str) -> tuple[Optional[dict], list[dict]]:
    """一覧から固定したコースの行と、同じ題名で**公開されている**別のコースを選ぶ（純関数）。

    題名の比較は前後の空白を落とした完全一致（部分一致で別コースを巻き込まない）。重複行は id で畳む。
    """
    by_id: dict[str, dict] = {}
    for c in courses:
        if isinstance(c, dict) and c.get("id"):
            by_id.setdefault(str(c["id"]), c)
    target = by_id.get(pinned)
    if target is None:
        return None, []
    title = str(target.get("title") or "").strip()
    conflicts = [c for cid, c in by_id.items()
                 if cid != pinned and str(c.get("title") or "").strip() == title
                 and (c.get("is_enrollable") or c.get("visibility") == "public")]
    return target, conflicts


def owner_username_from_email(email: str, admin_username: str) -> str:
    """所有者の email からログイン名を出す（ペルソナは ``<username>@uxsim.invalid``）。分からなければ空。"""
    e = str(email or "")
    if e.endswith(PERSONA_EMAIL_DOMAIN):
        return e[: -len(PERSONA_EMAIL_DOMAIN)]
    return admin_username if e.split("@", 1)[0].lower() == admin_username.lower() else ""


def _owners_from_db(db_url: str, course_ids: list[str]) -> dict[str, str]:
    """course_id → 所有者の email（読みだけ）。"""
    if not db_url or not course_ids:
        return {}
    from sqlalchemy import create_engine, text  # 遅延 import

    eng = create_engine(db_url)
    with eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT lc.id::text, u.email FROM learning_courses lc JOIN users u ON u.id = lc.user_id "
            "WHERE lc.id::text = ANY(:ids)"), {"ids": course_ids}).fetchall()
    return {r[0]: r[1] for r in rows}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("course_id", nargs="?", default="")
    ap.add_argument("--campaign", default="", help="campaign の YAML（course_id をそこから読む）")
    ap.add_argument("--owner-persona", action="append", default=[], help="所有者の候補の persona_id（教員）")
    ap.add_argument("--yes", action="store_true", help="実行する（無ければ dry-run）")
    ap.add_argument("--env", default="uxsim/sandbox/.env.uxsim")
    a = ap.parse_args(argv)
    pinned = a.course_id or (pinned_from_campaign(Path(a.campaign)) if a.campaign else "")
    if not pinned:
        print("固定するコース（COURSE_ID か --campaign）がありません", file=sys.stderr)
        return 2
    st = get_settings(env_file=Path(a.env))
    admin = EpistemeClient(st.base_url)
    ok, tr = admin.login(st.admin_username, st.admin_password)
    if not ok:
        print("管理者でログインできません", tr.status, file=sys.stderr)
        return 1
    status, body, _ = admin.call("GET", "/api/learning/courses")
    if status != 200 or not isinstance(body, list):
        print("コース一覧を取得できません", status, file=sys.stderr)
        return 1
    target, conflicts = select_conflicts(body, pinned)
    if target is None:
        print(f"固定したコース {pinned} が一覧にありません（管理者から見えない / 存在しない）", file=sys.stderr)
        return 1
    print(f"固定したコース: {pinned}「{target.get('title', '')}」 visibility={target.get('visibility', '')}")
    if target.get("visibility") != "public":
        print("  警告: 固定したコースが公開されていません（学生ペルソナは受講できません）。このスクリプトは触りません。")
    if not conflicts:
        print("同じ題名の公開コースはありません。何もしません。")
        return 0
    owners = _owners_from_db(st.sandbox_database_url, [str(c["id"]) for c in conflicts])
    candidates = [persona_username(p) for p in a.owner_persona]
    rc = 0
    for c in conflicts:
        cid = str(c["id"])
        owner_email = owners.get(cid, "")
        owner = owner_username_from_email(owner_email, st.admin_username)
        tried = [u for u in [owner, *candidates, st.admin_username] if u]
        tried = list(dict.fromkeys(tried))
        print(f"- 対象: {cid}「{c.get('title', '')}」 所有者={owner_email or '（DB 未設定のため不明）'} → private にする")
        if not a.yes:
            print(f"  （dry-run）試すログイン: {', '.join(tried)}")
            continue
        done = False
        for user in tried:
            client = admin if user == st.admin_username else EpistemeClient(st.base_url)
            try:
                if client is not admin:
                    lok, ltr = client.login(user, st.persona_password)
                    if not lok:
                        print(f"  {user}: ログインできません（{ltr.status}）")
                        continue
                s, b, _ = client.call("PUT", f"/api/admin/courses/{cid}/visibility", json={"visibility": "private"})
                if s == 200:
                    print(f"  {user}: private にしました")
                    done = True
                    break
                print(f"  {user}: {s} {b if s != 404 else '（所有者ではない）'}")
            finally:
                if client is not admin:
                    client.close()
        if not done:
            print(f"  失敗: {cid} を private にできませんでした（所有者を --owner-persona で指定してください）",
                  file=sys.stderr)
            rc = 1
    if a.yes:
        status, body, _ = admin.call("GET", "/api/learning/courses")
        _, remaining = select_conflicts(body if isinstance(body, list) else [], pinned)
        if remaining:
            print("確認: まだ同じ題名の公開コースがあります:", [r.get("id") for r in remaining], file=sys.stderr)
            rc = 1
        else:
            print("確認: 同じ題名の公開コースは固定したものだけです。")
    admin.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
