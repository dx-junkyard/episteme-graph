"""砂場の教材 claim を教員として承認し、再構成の問い（R層 item）をオーサリングする（運転側の手）。

使い方: backend/.venv/bin/python -m uxsim.sandbox.approve_claims <document_id> [...] [--per-document 12] [--author]
        [--env uxsim/sandbox/.env.uxsim]

理由: R層の出題対象は「source_backed かつ承認済み review_status の claim」だけ（CLAUDE.md 再構成ループ）。
教員ペルソナの経路にグラフレビューの承認が無いと、学生の s-check-and-object は必ず「出題できるものがありません」で
終わる（第 11 周・3 名とも）。本スクリプトは教員操作と同じ API（POST /api/admin/claims/{id}/review）で
source_backed の claim を document ごとに --per-document 件承認し、--author で
POST /api/admin/reconstruction/documents/{id}/author（LLM = proxy 経由・製品頭脳が要る）を呼ぶ。
claim の選び方は決定論（stable_key 昇順）。DB は SELECT のみ（書き込みは全て API 経由）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from uxsim.config import get_settings
from uxsim.runner.client import EpistemeClient


def _pending_claim_ids(db_url: str, document_id: str, limit: int) -> list[str]:
    import psycopg2  # 砂場の DB へ読みだけ

    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text FROM theory_claims_live
                 WHERE document_id::text LIKE %s AND support_status = 'source_backed'
                   AND review_status = 'teacher_review_required'
                   AND coalesce(text, '') <> ''
                 ORDER BY stable_key NULLS LAST, id
                 LIMIT %s
                """,
                (document_id + "%", limit),
            )
            return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("document_ids", nargs="+")
    ap.add_argument("--per-document", type=int, default=12)
    ap.add_argument("--author", action="store_true", help="承認後に R層 item のオーサリングも呼ぶ（LLM）")
    ap.add_argument("--env", default="uxsim/sandbox/.env.uxsim")
    a = ap.parse_args(argv)
    st = get_settings(env_file=Path(a.env))
    if not st.sandbox_database_url:
        print("UXSIM_SANDBOX_DATABASE_URL が未設定", file=sys.stderr)
        return 1
    c = EpistemeClient(st.base_url)
    ok, tr = c.login(st.admin_username, st.admin_password)
    if not ok:
        print("login failed", tr.status, file=sys.stderr)
        return 1
    rc = 0
    for doc in a.document_ids:
        ids = _pending_claim_ids(st.sandbox_database_url, doc, a.per_document)
        approved = 0
        for cid in ids:
            s, b, _ = c.call("POST", f"/api/admin/claims/{cid}/review", json={"review_status": "teacher_approved"})
            if s == 200:
                approved += 1
            else:
                print("review", cid, s, b, file=sys.stderr)
        print(f"{doc}: pending={len(ids)} approved={approved}")
        if a.author:
            s, b, _ = c.call("POST", f"/api/admin/reconstruction/documents/{doc}/author")
            print(f"{doc}: author {s} {b}")
            if s != 200:
                rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
