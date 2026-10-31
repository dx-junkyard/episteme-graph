"""砂場のコース内容を再生成する（運転側の手。教員操作『コース内容を生成』と同じ API）。

使い方: backend/.venv/bin/python -m uxsim.sandbox.regenerate_content <course_id> [--env uxsim/sandbox/.env.uxsim]
SYSTEM_ADMIN でログインし POST /api/admin/courses/{id}/course-content/generate → task を完了まで待つ。
製品側 LLM は proxy 経由（頭脳が要る）。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from uxsim.config import get_settings
from uxsim.runner.client import EpistemeClient


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("course_id")
    ap.add_argument("--env", default="uxsim/sandbox/.env.uxsim")
    ap.add_argument("--timeout", type=int, default=7200)
    a = ap.parse_args(argv)
    st = get_settings(env_file=Path(a.env))
    c = EpistemeClient(st.base_url)
    ok, tr = c.login(st.admin_username, st.admin_password)
    if not ok:
        print("login failed", tr.status, file=sys.stderr)
        return 1
    status, body, _ = c.call("POST", f"/api/admin/courses/{a.course_id}/course-content/generate")
    print("generate", status, body)
    if status != 200 and status != 202:
        return 1
    task_id = (body or {}).get("task_id") if isinstance(body, dict) else None
    deadline = time.time() + a.timeout
    last = None
    while task_id and time.time() < deadline:
        s, b, _ = c.call("GET", f"/api/admin/tasks/{task_id}")
        cur = (b or {}).get("status") if isinstance(b, dict) else None
        if cur != last:
            print(time.strftime("%H:%M:%S"), "task", cur, (b or {}).get("progress") if isinstance(b, dict) else "")
            last = cur
        if cur in ("completed", "failed", "error"):
            print(b)
            return 0 if cur == "completed" else 1
        time.sleep(15)
    print("timeout")
    return 1


if __name__ == "__main__":
    sys.exit(main())
