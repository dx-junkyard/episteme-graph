"""砂場の**学生ペルソナ**の学習痕跡を消す（運転側の手・製品の削除 API ではない）。

第 9 周: 前の周の痕跡（今日の言葉・記録・進捗・持ち越しの問い）が新しい周に混ざり、検証を汚した。
snapshot 復元はコース（教員段の成果）ごと消えるので、学生の状態だけを戻す手段として置く。

使い方: backend/.venv/bin/python -m uxsim.sandbox.reset_learner_state [--yes] [--persona st-01-m1-radio ...]
- 既定は dry-run（件数だけ表示）。--yes で実行。
- 対象は email が ``uxsim_*@uxsim.invalid`` かつ role=learner の利用者の行だけ。教員・管理者・教材・コースは触らない。
- auth_events / llm_usage_events（append-only の観測）と users は消さない。
- 接続先は UXSIM_SANDBOX_DATABASE_URL（砂場）に限る。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from uxsim.config import get_settings

#: 学習者本人に帰属する行だけ（列名: user_id / learner_id）。教員の成果物・観測台帳は含めない。
LEARNER_TABLES: tuple[tuple[str, str], ...] = (
    ("learning_states", "user_id"),
    ("learning_chat_history", "user_id"),
    ("chat_sessions", "user_id"),
    ("interest_traces", "user_id"),
    ("learner_reconstructions", "user_id"),
    ("learner_profiles", "user_id"),
    ("learner_mastered_concepts", "learner_id"),
    ("learner_struggling_concepts", "learner_id"),
    ("learner_misconception_corrections", "learner_id"),
    ("discuss_metric_events", "user_id"),
    ("atlas_cue_events", "user_id"),
    ("assistant_step_dismissals", "user_id"),
    ("unanswered_query_logs", "user_id"),
    ("sessions", "user_id"),
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="実行する（無ければ dry-run）")
    ap.add_argument("--persona", action="append", default=[], help="persona_id（複数可）。無ければ学生ペルソナ全員")
    ap.add_argument("--env", default="uxsim/sandbox/.env.uxsim")
    a = ap.parse_args(argv)
    st = get_settings(env_file=Path(a.env))
    url = st.sandbox_database_url
    if not url or ":5433/" not in url and "sandbox" not in url:
        print("砂場の DB（UXSIM_SANDBOX_DATABASE_URL）が無いか、砂場に見えないため中止します", file=sys.stderr)
        return 2
    from sqlalchemy import create_engine, text  # 遅延 import

    eng = create_engine(url)
    with eng.begin() as conn:
        rows = conn.execute(text(
            "SELECT id::text, email FROM users WHERE role='learner' AND email LIKE 'uxsim\\_%@uxsim.invalid'")).fetchall()
        if a.persona:
            wanted = {"uxsim_" + p.replace("-", "_") + "@uxsim.invalid" for p in a.persona}
            rows = [r for r in rows if r[1] in wanted]
        ids = [r[0] for r in rows]
        print("対象の学生ペルソナ:", [r[1] for r in rows])
        if not ids:
            return 0
        existing = {r[0] for r in conn.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'")).fetchall()}
        for table, col in LEARNER_TABLES:
            if table not in existing:
                continue
            n = conn.execute(text(f"SELECT count(*) FROM {table} WHERE {col}::text = ANY(:ids)"), {"ids": ids}).scalar()
            print(f"{table}: {n} 行")
            if a.yes and n:
                conn.execute(text(f"DELETE FROM {table} WHERE {col}::text = ANY(:ids)"), {"ids": ids})
        if not a.yes:
            print("dry-run（--yes で実行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
