"""砂場に宇宙物理コーパス（CC-BY の arXiv 論文）を一度だけ投入する。

製品の正規経路だけを使う（PE2）: Administrator でログイン → arxiv.org を URL 取得の許可リストへ →
取り込み用の教員アカウントを作る → 「URLから取得」で 1 本ずつ投入（3 秒以上あける）→ 解析完了を待つ。
arXiv への到達は論文 1 本につき 1 回（P-0007）。製品コードを import しない。

使い方: python uxsim/sandbox/bootstrap_corpus.py --base-url http://localhost:3100 --papers 2606.00411 2606.02318 ...
Administrator のパスワードは uxsim/sandbox/.env.uxsim の ADMIN_PASSWORD から読む。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

import httpx

ROOT = pathlib.Path(__file__).resolve().parents[2]


def env_value(key: str) -> str:
    for line in (ROOT / "uxsim" / "sandbox" / ".env.uxsim").read_text().splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip()
    return ""


def login(c: httpx.Client, username: str, password: str) -> str:
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    r.raise_for_status()
    return r.json()["access_token"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:3100")
    ap.add_argument("--papers", nargs="+", required=True, help="arXiv id（version 無し）")
    ap.add_argument("--teacher", default="uxsim-bootstrap-teacher")
    ap.add_argument("--poll", type=int, default=30)
    ap.add_argument("--timeout", type=int, default=5400, help="1 本あたりの解析待ち上限（秒）")
    a = ap.parse_args()

    c = httpx.Client(base_url=a.base_url, timeout=120)
    admin = login(c, "Administrator", env_value("ADMIN_PASSWORD"))
    ah = {"Authorization": f"Bearer {admin}"}

    r = c.post("/api/admin/url-fetch-domains", json={"domain": "arxiv.org"}, headers=ah)
    print("allow arxiv.org:", r.status_code, r.text[:120])

    teacher_pw = "uxsim-" + env_value("JWT_SECRET")[:12]
    r = c.post("/api/admin/users/teacher", json={"username": a.teacher, "email": f"{a.teacher}@uxsim.local", "password": teacher_pw}, headers=ah)
    print("create teacher:", r.status_code, r.text[:120])
    th = {"Authorization": f"Bearer {login(c, a.teacher, teacher_pw)}"}

    results = []
    for i, pid in enumerate(a.papers):
        if i:
            time.sleep(4)  # arXiv の 3 秒規律
        url = f"https://arxiv.org/pdf/{pid}"
        r = c.post("/api/admin/materials/upload-from-url", json={"url": url, "analyze_images": False, "cartridge_id": None}, headers=th)
        print("fetch", pid, r.status_code, r.text[:200])
        if r.status_code != 202:
            results.append({"arxiv_id": pid, "status": f"fetch_failed:{r.status_code}"})
            continue
        body = r.json()
        results.append({"arxiv_id": pid, "material_id": body.get("material_id"), "task_id": body.get("task_id"), "status": "queued"})

    # 解析完了を待つ（タスクは並列に走る）
    deadline = time.time() + a.timeout
    pending = [x for x in results if x.get("task_id")]
    while pending and time.time() < deadline:
        time.sleep(a.poll)
        for x in list(pending):
            r = c.get(f"/api/admin/tasks/{x['task_id']}", headers=th)
            st = r.json().get("status") if r.status_code == 200 else f"http_{r.status_code}"
            if st in ("completed", "failed"):
                x["status"] = st
                x["error"] = (r.json().get("error_message") or "")[:200]
                pending.remove(x)
                print("task", x["arxiv_id"], st, x["error"])
    for x in pending:
        x["status"] = "timeout"

    out = ROOT / "uxsim" / "runs" / "bootstrap_corpus.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main())
