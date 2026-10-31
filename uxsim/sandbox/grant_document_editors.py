"""砂場の教員ペルソナに、教材（document）の **editor 共有**を張る運転側の手（製品の共有 API を所有者として呼ぶだけ）。

第 22 周（SB層）: 台帳候補の確定・見送りは教材の編集権限が要るが、砂場の教材は bootstrap 教員の所有で、
コース所有者 te-04 や担当の te-03 には view しか無かった（確定が 404）。所有者がグループを作り、教員ペルソナが
招待コードで参加し、所有者が教材をそのグループへ editor で共有する。DB を直接触らない。

  backend/.venv/bin/python uxsim/sandbox/grant_document_editors.py --course 1bad7d9f --teacher te-03-meticulous-seminar --teacher te-04-copilot-intro [--yes]
"""
from __future__ import annotations

import argparse
import pathlib
import sys

import httpx

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from uxsim.runner.api import persona_username  # noqa: E402
from uxsim.sandbox.bootstrap_corpus import env_value, login  # noqa: E402

BASE = "http://localhost:3100"
GROUP_NAME = "uxsim 担当教員（教材 editor 共有）"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--course", required=True)
    ap.add_argument("--teacher", action="append", default=[], help="persona_id（複数可）")
    ap.add_argument("--document", action="append", default=[], help="document_id（学習者向けコース DTO に document_id が無いときに明示）")
    ap.add_argument("--owner", default="uxsim-bootstrap-teacher")
    ap.add_argument("--persona-password", default="uxsim-persona-pass")
    ap.add_argument("--yes", action="store_true")
    a = ap.parse_args(argv)
    c = httpx.Client(base_url=BASE, timeout=120)
    owner_pw = "uxsim-" + env_value("JWT_SECRET")[:12]
    oh = {"Authorization": f"Bearer {login(c, a.owner, owner_pw)}"}
    course = c.get(f"/api/learning/courses/{a.course}", headers=oh).json()
    docs = list(a.document) or [s.get("document_id") for s in (course.get("sources") or []) if s.get("document_id")]
    print(f"course {a.course}: documents {len(docs)}")
    groups = c.get("/api/groups", headers=oh).json()
    group = next((g for g in groups if g.get("name") == GROUP_NAME), None)
    if not a.yes:
        print("dry-run: group", "exists" if group else "to create", "; teachers", a.teacher)
        return 0
    if group is None:
        group = c.post("/api/groups", json={"name": GROUP_NAME, "description": "第 22 周の運転用"}, headers=oh).json()
    gid = group["id"]
    code = group.get("invite_code") or c.get(f"/api/groups/{gid}", headers=oh).json().get("invite_code")
    for pid in a.teacher:
        th = {"Authorization": f"Bearer {login(c, persona_username(pid), a.persona_password)}"}
        r = c.post("/api/groups/join-by-code", json={"invite_code": code}, headers=th)
        print(f"  {pid}: join {r.status_code}")
    for did in docs:
        r = c.post(f"/api/admin/documents/{did}/groups", json={"group_id": gid, "permission": "editor"}, headers=oh)
        print(f"  {did[:8]}: editor share {r.status_code}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
