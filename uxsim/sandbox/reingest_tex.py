"""砂場のコーパスを TeX（arXiv e-print）で取り込み直し、固定コースの sources を差し替える（運転側の手）。

第 17〜18 周の砂場は PDF 経路で解析した教材（corpus-astro-v1）で、根 3（TeX 既定・`language` run option）の
効きが未検証だった。製品の正規経路だけを使う（PE2）: Administrator → 取り込み用教員 → 「URLから取得」
（``source_format: tex`` / ``language``）× 論文数 → 解析完了待ち → 公開 → コース所有者で
``PUT /api/learning/courses/{id}`` の sources を**同じ arXiv 番号の新しい document に差し替え**。
コース内容の再生成は別手（``uxsim/sandbox/regenerate_content.py``）で、本スクリプトは呼ばない
（製品頭脳が要るため、運転側が起動を確認してから回す）。

arXiv への到達は論文 1 本につき TeX 1 回（gzip が読めないときだけ製品が PDF へ 1 回落とす = 最大 2 回・P-0007）。
製品コードを import しない。再取り込みは**新しい document を作る**（旧 document は消さない。旧 run の成果は
IK-0540/0541/0547/0551 の比較対象として残す）。

使い方（リポジトリルートで・砂場起動後）:
  backend/.venv/bin/python -m uxsim.sandbox.reingest_tex --papers 2606.00411 2606.02318 2605.31198 2605.26810 \
      --language ja --course 1bad7d9f [--owner-persona te-02-veteran-intro] [--dry-run]
結果は uxsim/runs/reingest_tex.json（arXiv 番号 → 旧 material_id / 新 material_id / document_id / 取得形式 / 状態）。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time

from uxsim.config import get_settings
from uxsim.runner.client import EpistemeClient

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _env_value(key: str, env_file: pathlib.Path) -> str:
    if not env_file.exists():
        return ""
    for line in env_file.read_text().splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _persona_username(persona_id: str) -> str:
    try:
        from uxsim.sandbox.pin_course import persona_username  # type: ignore

        return persona_username(persona_id)
    except Exception:
        return f"uxsim-{persona_id}"


_ARXIV_RE = re.compile(r"(?<!\d)(\d{4}\.\d{4,5})(?:v\d+)?(?!\d)")


def _arxiv_id_in(text: str) -> str:
    m = _ARXIV_RE.search(str(text or ""))
    return m.group(1) if m else ""


def _materials_index(client: EpistemeClient) -> dict[str, dict]:
    """``GET /api/admin/materials``（include=shared 相当は製品既定）→ material_id → 行。"""
    s, b, _ = client.call("GET", "/api/admin/materials")
    rows = b if (s == 200 and isinstance(b, list)) else []
    return {str(r.get("material_id")): r for r in rows if isinstance(r, dict)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--papers", nargs="+", required=True, help="arXiv id（version 無し）")
    ap.add_argument("--language", default="ja", choices=["ja", "en"], help="解析 run の生成言語（options.language）")
    ap.add_argument("--source-format", default="tex", choices=["tex", "pdf"])
    ap.add_argument("--course", default="", help="sources を差し替える固定コース（campaign の course_id）")
    ap.add_argument("--owner-persona", action="append", default=[], help="コース所有者の候補 persona_id（教員）")
    ap.add_argument("--teacher", default="uxsim-bootstrap-teacher")
    ap.add_argument("--env", default="uxsim/sandbox/.env.uxsim")
    ap.add_argument("--poll", type=int, default=30)
    ap.add_argument("--timeout", type=int, default=7200, help="解析待ち上限（秒・全体）")
    ap.add_argument("--dry-run", action="store_true", help="取り込みも差し替えもせず、何をするかだけ表示")
    ap.add_argument("--resume", default="", help="取得済みの JSON（[{arxiv_id, material_id, task_id}]）。arXiv を呼ばず完了待ちと差し替えだけ")
    a = ap.parse_args(argv)

    env_file = ROOT / a.env
    st = get_settings(env_file=env_file)
    admin = EpistemeClient(st.base_url)
    ok, tr = admin.login(st.admin_username, st.admin_password)
    if not ok:
        print("admin login failed", tr.status, file=sys.stderr)
        return 1

    # 取り込み用教員（bootstrap_corpus.py と同じ規則で作る・既にあれば作成は 4xx で素通し）
    teacher_pw = "uxsim-" + _env_value("JWT_SECRET", env_file)[:12]
    status, body, _ = admin.call(
        "POST", "/api/admin/users/teacher",
        json={"username": a.teacher, "email": f"{a.teacher}@uxsim.local", "password": teacher_pw},
    )
    print("create teacher:", status, str(body)[:120])
    teacher = EpistemeClient(st.base_url)
    ok, tr = teacher.login(a.teacher, teacher_pw)
    if not ok:
        print("teacher login failed", tr.status, file=sys.stderr)
        return 1

    status, body, _ = admin.call("POST", "/api/admin/url-fetch-domains", json={"domain": "arxiv.org"})
    print("allow arxiv.org:", status, str(body)[:120])

    # コースの読み書きは所有者で（学習者 API は所有者 / 受講者以外に 404）。所有者候補 → 管理者の順。
    course_client = admin
    owner_label = "admin"
    for persona in a.owner_persona:
        c = EpistemeClient(st.base_url)
        ok, _ = c.login(_persona_username(persona), st.persona_password)
        if ok:
            s_, b_, _ = c.call("GET", f"/api/learning/courses/{a.course}") if a.course else (200, {}, 0)
            if s_ == 200:
                course_client, owner_label = c, persona
                break
    print("course client:", owner_label)

    # 旧 material_id（同じ arXiv 番号）をコースの sources から控える
    old_by_arxiv: dict[str, dict] = {}
    course_data = None
    if a.course:
        status, course_data, _ = course_client.call("GET", f"/api/learning/courses/{a.course}")
        if status != 200 or not isinstance(course_data, dict):
            print("course GET failed", status, str(course_data)[:200], file=sys.stderr)
            return 1
        # 学習者 API の詳細は {master_course: {...}, personal_layer: {...}} の入れ子
        if isinstance(course_data.get("master_course"), dict):
            course_data = course_data["master_course"]
        for src in course_data.get("sources") or []:
            print("  source:", src.get("material_id"), src.get("document_id"), (src.get("title") or "")[:60])

    plan = [{"arxiv_id": pid, "url": f"https://arxiv.org/abs/{pid}", "source_format": a.source_format,
             "language": a.language} for pid in a.papers]
    if a.dry_run:
        print("DRY RUN — would fetch:", json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    results: list[dict] = []
    if a.resume:
        results = [dict(x, status="queued") for x in json.loads(pathlib.Path(a.resume).read_text())]
        plan = []
    for i, item in enumerate(plan):
        if i:
            time.sleep(4)  # arXiv の 3 秒規律
        status, body, _ = teacher.call(
            "POST", "/api/admin/materials/upload-from-url",
            json={"url": item["url"], "analyze_images": False, "cartridge_id": None,
                  "source_format": a.source_format, "language": a.language},
        )
        print("fetch", item["arxiv_id"], status, str(body)[:200])
        rec = dict(item)
        if status != 202 or not isinstance(body, dict):
            rec["status"] = f"fetch_failed:{status}"
            results.append(rec)
            continue
        rec.update({
            "material_id": body.get("material_id"),
            "filename": body.get("filename"),
            "task_id": body.get("task_id"),
            "effective_format": body.get("effective_format"),
            "fell_back": body.get("fell_back"),
            "status": "queued",
        })
        results.append(rec)

    deadline = time.time() + a.timeout
    pending = [x for x in results if x.get("task_id")]
    while pending and time.time() < deadline:
        time.sleep(a.poll)
        for x in list(pending):
            s, b, _ = teacher.call("GET", f"/api/admin/tasks/{x['task_id']}")
            cur = (b or {}).get("status") if (s == 200 and isinstance(b, dict)) else f"http_{s}"
            if cur in ("completed", "failed", "error"):
                x["status"] = cur
                x["error"] = str((b or {}).get("error_message") or "")[:200]
                pending.remove(x)
                print(time.strftime("%H:%M:%S"), "task", x["arxiv_id"], cur, x["error"])
    for x in pending:
        x["status"] = "timeout"

    # document_id（documents.id）を教材一覧から引く（202 応答には無い）
    index = _materials_index(teacher)
    for x in results:
        row = index.get(str(x.get("material_id")))
        if row:
            x["document_id"] = row.get("document_id")
            x["title"] = row.get("title")

    # 公開（学生ペルソナと所有者が読めるように。旧コーパスと同じ開示範囲）
    for x in results:
        if x.get("material_id") and x.get("status") == "completed":
            s, b, _ = teacher.call("PUT", f"/api/admin/materials/{x['material_id']}/visibility",
                                   json={"visibility": "public"})
            x["visibility"] = f"public:{s}"

    # コース sources の差し替え（同じ arXiv 番号 → 新 document）
    if a.course and isinstance(course_data, dict):
        new_by_arxiv = {x["arxiv_id"]: x for x in results if x.get("status") == "completed" and x.get("material_id")}
        admin_index = _materials_index(admin)
        sources = []
        replaced = []
        for src in course_data.get("sources") or []:
            src = dict(src)
            old_row = admin_index.get(str(src.get("material_id") or "")) or {}
            # 旧 document の filename（URL 取得なら ``2606.00411.pdf`` 等）→ 題名 → sources の題名 の順で番号を引く
            key = (_arxiv_id_in(old_row.get("filename") or "") or _arxiv_id_in(old_row.get("title") or "")
                   or _arxiv_id_in(src.get("title") or "") or _arxiv_id_in(src.get("material_id") or ""))
            hit = new_by_arxiv.get(key)
            if hit:
                old_by_arxiv[key] = {"material_id": src.get("material_id"), "document_id": src.get("document_id")}
                src["material_id"] = hit["material_id"]
                src["document_id"] = hit.get("document_id") or ""
                replaced.append(key)
            sources.append({k: v for k, v in src.items()
                            if k in ("title", "subtitle", "license", "used_section", "material_id", "document_id")})
        print("replace sources:", replaced)
        if replaced:
            s, b, _ = course_client.call("PUT", f"/api/learning/courses/{a.course}", json={"sources": sources})
            print("PUT sources as", owner_label, s, str(b)[:120])
            if s != 200:
                print("sources の差し替えに失敗。--owner-persona にコース所有者を指定してください。", file=sys.stderr)
        for x in results:
            old = old_by_arxiv.get(x["arxiv_id"])
            if old:
                x["replaced_old"] = old

    out = ROOT / "uxsim" / "runs" / "reingest_tex.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print("次: backend/.venv/bin/python -m uxsim.sandbox.regenerate_content", a.course or "<course_id>",
          "（製品頭脳 or 本物の OpenAI が要る）→ reset_learner_state --yes → pin_course --yes → run19_lane.sh")
    return 0 if all(x.get("status") == "completed" for x in results) else 1


if __name__ == "__main__":
    sys.exit(main())
