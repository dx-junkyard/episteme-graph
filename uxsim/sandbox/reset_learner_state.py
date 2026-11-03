"""砂場の**学生ペルソナ**の学習痕跡を消す（運転側の手・製品の削除 API ではない）。

第 9 周: 前の周の痕跡（今日の言葉・記録・進捗・持ち越しの問い）が新しい周に混ざり、検証を汚した。
snapshot 復元はコース（教員段の成果）ごと消えるので、学生の状態だけを戻す手段として置く。

使い方: backend/.venv/bin/python -m uxsim.sandbox.reset_learner_state [--yes] [--persona st-01-m1-radio ...]
- 既定は dry-run（件数だけ表示）。--yes で実行。
- 対象は email が ``uxsim_*@uxsim.invalid`` かつ role=learner の利用者の行だけ。教員・管理者・教材・コースは触らない。
- auth_events / llm_usage_events（append-only の観測）と users は消さない。
- 接続先は UXSIM_SANDBOX_DATABASE_URL（砂場）に限る。
- ``--exclude-persona`` で残す学生を指定できる（第 22 周 (l): st-06 の第 21 周の実行を残したまま他を戻す）。

第 22 周: 検証シミュレーション層（SB層）の本人の行も戻す。``simulation_runs`` / ``instrument_wirings`` /
``simulation_programs`` は ``owner_user_id`` が本人の行だけ（教員の行は owner_user_id が NULL で触らない）。
``epistemic_ledger.simulation_candidates`` は**行を消さず**、配列の要素のうち ``created_by`` がリセット対象の
学生のものだけを除く UPDATE をする（台帳行・他の候補・教員の記帳は残す）。これは**砂場の運転側の手**で、
製品の削除 API ではない（製品は候補を status 遷移でしか扱わない — SB7）。教員が確定した候補が台帳に足した
``verification_scopes`` / ``verification_status`` は戻さない（教員の判断の記録で、学生の状態ではない）。
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
    # SB層（第 22 周）: 本人所有の行だけ。参照の向き（runs → programs / wirings）に合わせて runs を先に消す
    ("simulation_runs", "owner_user_id"),
    ("instrument_wirings", "owner_user_id"),
    ("simulation_programs", "owner_user_id"),
)

#: ``epistemic_ledger.simulation_candidates`` の要素のうち、リセット対象の学生が共有で起こした候補。
LEDGER_CANDIDATES_COUNT_SQL = (
    "SELECT count(*) FROM epistemic_ledger l, "
    "jsonb_array_elements(COALESCE(l.simulation_candidates, '[]'::jsonb)) AS c "
    "WHERE c->>'created_by' = ANY(:ids)"
)
#: 配列から対象の要素だけを除く（行は消さない・他の要素の順は保つ）。
LEDGER_CANDIDATES_STRIP_SQL = (
    "UPDATE epistemic_ledger SET simulation_candidates = COALESCE(("
    "SELECT jsonb_agg(c ORDER BY ord) FROM jsonb_array_elements(simulation_candidates) WITH ORDINALITY AS t(c, ord) "
    "WHERE NOT (c->>'created_by' = ANY(:ids))), '[]'::jsonb), updated_at = now() "
    "WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(COALESCE(simulation_candidates, '[]'::jsonb)) AS c "
    "WHERE c->>'created_by' = ANY(:ids))"
)


def persona_email(persona_id: str) -> str:
    return "uxsim_" + persona_id.replace("-", "_") + "@uxsim.invalid"


def select_targets(rows: list[tuple[str, str]], personas: list[str], excluded: list[str]) -> list[tuple[str, str]]:
    """(user_id, email) の列から、--persona で絞り --exclude-persona を外した対象を返す（純関数）。"""
    if personas:
        wanted = {persona_email(p) for p in personas}
        rows = [r for r in rows if r[1] in wanted]
    if excluded:
        skip = {persona_email(p) for p in excluded}
        rows = [r for r in rows if r[1] not in skip]
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="実行する（無ければ dry-run）")
    ap.add_argument("--persona", action="append", default=[], help="persona_id（複数可）。無ければ学生ペルソナ全員")
    ap.add_argument("--exclude-persona", action="append", default=[],
                    help="残す persona_id（複数可）。--persona より後に外す")
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
        rows = select_targets([(r[0], r[1]) for r in rows], a.persona, a.exclude_persona)
        ids = [r[0] for r in rows]
        print("対象の学生ペルソナ:", [r[1] for r in rows])
        if a.exclude_persona:
            print("残す学生ペルソナ:", [persona_email(p) for p in a.exclude_persona])
        if not ids:
            return 0
        existing = {r[0] for r in conn.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'")).fetchall()}
        has_candidates = bool(conn.execute(text(
            "SELECT 1 FROM information_schema.columns WHERE table_schema='public' "
            "AND table_name='epistemic_ledger' AND column_name='simulation_candidates'")).fetchone())
        if has_candidates:  # 列の無い旧 DB では数えない（失敗した SQL でトランザクションを壊さない）
            # 台帳の候補（共有で起こしたもの）を先に数える・除く。行は消さない
            n = conn.execute(text(LEDGER_CANDIDATES_COUNT_SQL), {"ids": ids}).scalar()
            print(f"epistemic_ledger.simulation_candidates（共有で起こした候補の要素）: {n} 件")
            if a.yes and n:
                conn.execute(text(LEDGER_CANDIDATES_STRIP_SQL), {"ids": ids})
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
