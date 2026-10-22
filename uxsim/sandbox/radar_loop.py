"""論文レーダーの自律周回 v2（P-0023）— 「浅い段」と「深い段」の 2 段で、ペルソナの判定を挟んで回す。

製品の HTTP API だけを使う（PE2 と同じ立場・製品コードを import しない）。取り込みの弁は教員の明示操作を
本スクリプトが代行する立場で、製品側に自動クロールは入れない（PD1）。arXiv への到達は 1 周あたり最大 2 回
（レーダー検索 1 + 取得 1。seed のメタデータと比較の要旨は写し = 0 — P-0007）。

浅い段（scan）: seed → GET radar/seed → [出所が推定なら POST radar/provenance] → POST radar/search → 未取り込み・
  未試行の候補のうち上位 K 件を POST radar/compare（LLM 1 コール）→ `scan-NN.json` を書き、審判（ペルソナ）の
  `scan-NN.decision.json` を待つ。
深い段（deep）: 審判が選んだ 1 本を POST ingest（TeX 既定）→ tasks/{id} を完了まで待つ → 類似性の材料
  （compare・landscape placements・theory-modules + related・reference-health・paper-layer・新 seed）→ `deep-NN.json`
  → 審判の `deep-NN.decision.json`（連鎖するか・次の距離）を待つ。

decision の形:
  scan: {"ingest": "<arxiv_id>" | null, "next": {"seed": "chain"|"stay"|"<document_ref>", "distance": "near"|"mid"|"far"}, "note": "..."}
  deep: {"next": {"seed": "chain"|"stay"|"<document_ref>", "distance": "..."}, "note": "..."}
止め方: 出力ディレクトリに STOP ファイル（次の arXiv 呼び出しの前で止まる）。パスワードは環境変数 RADAR_LOOP_PASSWORD。

サブコマンド:
  run     …… 周回本体（既定）
  attach  …… 既に走っている解析（document_id / arxiv_id / task_id）を深い段として引き取り deep-NN.json を書く
"""
from __future__ import annotations

import argparse
import json
import re
import os
import pathlib
import sys
import time
from typing import Any

import httpx

ARXIV_SPACING_S = 4.0  # arXiv の 3 秒規律に余裕を持たせる
DISTANCES = ["near", "mid", "far"]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


class Api:
    def __init__(self, base_url: str, timeout: float = 600.0) -> None:
        self.c = httpx.Client(base_url=base_url, timeout=timeout)
        self.h: dict[str, str] = {}
        self.calls: list[dict] = []
        self._cred: tuple[str, str] | None = None

    def login(self, username: str, password: str) -> None:
        self._cred = (username, password)
        r = self.c.post("/api/auth/login", json={"username": username, "password": password})
        r.raise_for_status()
        self.h = {"Authorization": f"Bearer {r.json()['access_token']}"}

    def call(self, method: str, path: str, **kw) -> tuple[int, Any]:
        t0 = time.time()
        r = self.c.request(method, path, headers=self.h, **kw)
        if r.status_code == 401 and self._cred:  # トークン期限切れ → 1 回だけ再ログイン
            self.login(*self._cred)
            r = self.c.request(method, path, headers=self.h, **kw)
        try:
            body = r.json()
        except Exception:  # noqa: BLE001
            body = {"_text": r.text[:2000]}
        self.calls.append({"t": _now(), "method": method, "path": path, "status": r.status_code, "ms": int((time.time() - t0) * 1000)})
        return r.status_code, body


class Loop:
    def __init__(self, api: Api, out: pathlib.Path, args) -> None:
        self.api = api
        self.out = out
        self.args = args
        self.out.mkdir(parents=True, exist_ok=True)
        self.ledger_path = out / "ledger.json"
        self.ledger = json.loads(self.ledger_path.read_text()) if self.ledger_path.exists() else {}
        self.ledger.setdefault("tried", {})
        self.ledger.setdefault("iterations", [])
        self.ledger.setdefault("chain", [])

    # --- 記録 -------------------------------------------------------------
    def save(self) -> None:
        self.ledger_path.write_text(json.dumps(self.ledger, ensure_ascii=False, indent=1))

    def log(self, msg: str) -> None:
        line = f"{_now()} {msg}"
        print(line, flush=True)
        with (self.out / "loop.log").open("a") as f:
            f.write(line + "\n")

    def stop_requested(self) -> bool:
        return (self.out / "STOP").exists()

    def write_json(self, name: str, data: dict) -> None:
        data = dict(data)
        data["http_calls"] = self.api.calls[-300:]
        self.api.calls = []
        (self.out / name).write_text(json.dumps(data, ensure_ascii=False, indent=1))

    def wait_decision(self, name: str, *, newer_than: float = 0.0) -> dict | None:
        """審判の decision ファイルを待つ（STOP で None）。

        ``newer_than``（epoch 秒）より古い decision は前の周の残骸とみなして使わない
        （周番号の再利用で古い decision を即採用し、同じ論文を二重に取り込んだ事故の再発防止）。
        """
        path = self.out / name
        self.log(f"waiting for decision {name}")
        while not path.exists() or (newer_than and path.stat().st_mtime < newer_than):
            if self.stop_requested():
                return None
            time.sleep(15)
        try:
            return json.loads(path.read_text())
        except Exception as exc:  # noqa: BLE001
            self.log(f"decision {name} unreadable: {exc}; keep waiting")
            path.rename(path.with_suffix(".bad"))
            return self.wait_decision(name, newer_than=newer_than)

    # --- 浅い段 -------------------------------------------------------------
    def seed_info(self, document_ref: str) -> dict:
        s, b = self.api.call("GET", "/api/admin/discovery/radar/seed", params={"document_ref": document_ref})
        if s != 200:
            raise RuntimeError(f"radar/seed {s}: {json.dumps(b, ensure_ascii=False)[:300]}")
        seed = b.get("seed") or {}
        prov = seed.get("provenance") or {}
        # 画面の自動記帳（タイトル完全一致時のみ・サーバが再照合）と同じ 1 回の POST。
        if prov.get("status") == "inferred" and prov.get("can_register") and prov.get("title_match") and prov.get("arxiv_id"):
            s2, b2 = self.api.call("POST", "/api/admin/discovery/radar/provenance", json={"document_ref": document_ref, "arxiv_id": prov["arxiv_id"], "confirm": False})
            self.log(f"provenance auto-register -> {s2} {json.dumps(b2, ensure_ascii=False)[:160]}")
            seed["_provenance_register"] = {"status": s2, "body": {k: v for k, v in (b2 or {}).items() if k != "seed"}}
        return seed

    def search(self, document_ref: str, distance: str) -> dict:
        s, b = self.api.call("POST", "/api/admin/discovery/radar/search", json={"document_ref": document_ref, "distance": distance, "max_results": self.args.max_results})
        if s != 200:
            raise RuntimeError(f"radar/search {s}: {json.dumps(b, ensure_ascii=False)[:300]}")
        return b

    def fresh_candidates(self, result: dict) -> tuple[list[dict], list[dict]]:
        cands = [c for c in (result.get("candidates") or []) if isinstance(c, dict)]
        fresh, considered = [], []
        for c in cands:
            aid = c.get("arxiv_id") or ""
            why = None
            if c.get("status") == "ingested":
                why = "ingested"
            elif aid in self.ledger["tried"]:
                why = "tried:" + str(self.ledger["tried"][aid].get("outcome"))
            considered.append({"arxiv_id": aid, "title": c.get("title"), "distance_label": c.get("distance_label"), "status": c.get("status"), "skip": why})
            if why is None:
                fresh.append(c)
        return fresh, considered

    def compare(self, seed_ref: str, arxiv_ids: list[str]) -> dict:
        s, b = self.api.call("POST", "/api/admin/discovery/radar/compare", json={"document_ref": seed_ref, "arxiv_ids": arxiv_ids})
        return {"status": s, "body": b}

    def scan(self, n: int, seed_ref: str, distance: str) -> dict:
        rec: dict[str, Any] = {"iteration": n, "kind": "scan", "started_at": _now(), "seed_ref": seed_ref, "distance": distance, "arxiv_calls_estimate": 0}
        seed = self.seed_info(seed_ref)
        rec["seed"] = seed
        if seed.get("metadata_source") == "arxiv":
            rec["arxiv_calls_estimate"] += 1
        if seed.get("arxiv_blocked"):
            rec["outcome"] = "arxiv_blocked"
            return rec
        time.sleep(ARXIV_SPACING_S)
        result = self.search(seed_ref, distance)
        rec["arxiv_calls_estimate"] += 0 if result.get("arxiv_blocked") else 1
        rec["search"] = {k: v for k, v in result.items() if k != "seed"}
        if result.get("arxiv_blocked"):
            rec["outcome"] = "arxiv_blocked"
            return rec
        fresh, considered = self.fresh_candidates(result)
        rec["considered"] = considered
        if not fresh:
            rec["outcome"] = "no_new_candidate"
            return rec
        top = fresh[: self.args.compare_top]
        rec["compare_ids"] = [c["arxiv_id"] for c in top]
        rec["compare"] = self.compare(seed_ref, rec["compare_ids"])  # 要旨は写し → arXiv 0 回・LLM 1 コール
        rec["outcome"] = "scanned"
        return rec

    # --- 深い段 -------------------------------------------------------------
    def ingest(self, arxiv_id: str) -> dict:
        body: dict[str, Any] = {"items": [{"arxiv_id": arxiv_id}], "language": self.args.language}
        # 解析の分野は cartridge_id で送る（domain_key は購読の監査用で解析には効かない）。
        if self.args.cartridge_id is not None:
            body["cartridge_id"] = self.args.cartridge_id
        s, b = self.api.call("POST", "/api/admin/discovery/ingest", json=body)
        if s != 202:
            raise RuntimeError(f"ingest {s}: {json.dumps(b, ensure_ascii=False)[:300]}")
        return b

    def wait_task(self, task_id: str) -> dict:
        deadline = time.time() + self.args.timeout
        last = None
        while time.time() < deadline:
            s, b = self.api.call("GET", f"/api/admin/tasks/{task_id}")
            st = (b or {}).get("status") if s == 200 else f"http{s}"
            if st != last:
                self.log(f"task {task_id[:8]} -> {st}")
                last = st
            if st in ("completed", "failed"):
                return b
            time.sleep(self.args.poll)
        return {"status": "timeout", "task_id": task_id}

    def material_row(self, material_id: str = "", document_id: str = "") -> dict:
        s, b = self.api.call("GET", "/api/admin/materials")
        for row in (b if isinstance(b, list) else []):
            if (material_id and row.get("material_id") == material_id) or (document_id and row.get("document_id") == document_id):
                return row
        return {}

    def similarity(self, seed_ref: str, new_doc: str, arxiv_id: str) -> dict:
        out: dict[str, Any] = {}
        out["compare"] = self.compare(seed_ref, [arxiv_id])
        for key, path in (
            ("placements", f"/api/admin/landscape/documents/{new_doc}/placements"),
            ("theory_modules", f"/api/admin/documents/{new_doc}/theory-modules"),
            ("theory_modules_related", f"/api/admin/documents/{new_doc}/theory-modules/related"),
            ("reference_health", f"/api/admin/documents/{new_doc}/reference-health"),
            ("paper_layer", f"/api/admin/documents/{new_doc}/paper-layer"),
            ("seed_placements", f"/api/admin/landscape/documents/{seed_ref}/placements"),
            ("seed_theory_modules_related", f"/api/admin/documents/{seed_ref}/theory-modules/related"),
        ):
            s, b = self.api.call("GET", path)
            out[key] = {"status": s, "body": b}
        return out

    def deep(self, n: int, seed_ref: str, arxiv_id: str, *, task_id: str = "", document_id: str = "", material_id: str = "") -> dict:
        rec: dict[str, Any] = {"iteration": n, "kind": "deep", "started_at": _now(), "seed_ref": seed_ref, "arxiv_id": arxiv_id, "arxiv_calls_estimate": 0}
        self.ledger["tried"][arxiv_id] = {"iteration": n, "outcome": "ingesting", "seed_ref": seed_ref, "at": _now()}
        self.save()
        if not task_id:
            time.sleep(ARXIV_SPACING_S)
            ing = self.ingest(arxiv_id)
            rec["arxiv_calls_estimate"] += 1
            rec["ingest"] = ing
            acc = (ing.get("accepted") or [{}])[0] if ing.get("accepted") else {}
            if not acc:
                self.ledger["tried"][arxiv_id]["outcome"] = "ingest_failed"
                rec["outcome"] = "ingest_failed"
                return rec
            task_id, material_id = acc.get("task_id") or "", acc.get("material_id") or ""
        task = self.wait_task(task_id)
        rec["task"] = task
        row = self.material_row(material_id=material_id, document_id=document_id)
        rec["material"] = row
        new_doc = row.get("document_id") or document_id
        if task.get("status") != "completed" or not new_doc:
            self.ledger["tried"][arxiv_id]["outcome"] = f"pipeline_{task.get('status')}"
            rec["outcome"] = f"pipeline_{task.get('status')}"
            return rec
        rec["similarity"] = self.similarity(seed_ref, new_doc, arxiv_id)
        time.sleep(ARXIV_SPACING_S)
        rec["new_seed"] = self.seed_info(new_doc)  # 写しがあるので arXiv 0 回
        self.ledger["tried"][arxiv_id].update({"outcome": "completed", "document_id": new_doc, "material_id": material_id})
        self.ledger["chain"].append({"iteration": n, "from": seed_ref, "to": new_doc, "arxiv_id": arxiv_id, "title": row.get("title")})
        rec["outcome"] = "completed"
        rec["new_document_id"] = new_doc
        return rec

    # --- 周回 -------------------------------------------------------------
    def _next_from(self, decision: dict, seed_ref: str, distance: str, chained_doc: str) -> tuple[str, str]:
        nxt = (decision or {}).get("next") or {}
        s = str(nxt.get("seed") or "stay")
        d = str(nxt.get("distance") or distance)
        if d not in DISTANCES:
            d = distance
        if s == "chain" and chained_doc:
            return chained_doc, d
        if s in ("chain", "stay"):
            return seed_ref, d
        return s, d  # 明示の document_ref

    def _next_iteration_number(self) -> int:
        """ledger（iterations / tried / chain）と出力ファイル名から、使用済みの最大周番号を返す。

        attach で深い段だけ記録した周や、kill された周の番号を再利用しない。
        """
        used = [int(e.get("iteration") or 0) for e in self.ledger.get("iterations", [])]
        used += [int(v.get("iteration") or 0) for v in self.ledger.get("tried", {}).values()]
        used += [int(c.get("iteration") or 0) for c in self.ledger.get("chain", [])]
        for f in self.out.glob("scan-*.json"):
            m = re.match(r"scan-(\d+)", f.name)
            if m:
                used.append(int(m.group(1)))
        for f in self.out.glob("deep-*.json"):
            m = re.match(r"deep-(\d+)", f.name)
            if m:
                used.append(int(m.group(1)))
        return max(used) if used else 0

    def run(self) -> None:
        # 明示の --seed / --distance は ledger の current_* に勝つ（再開時に別の起点を指定できる）
        seed_ref = self.args.seed or self.ledger.get("current_seed")
        distance = self.args.distance if self.args.distance_explicit else (self.ledger.get("current_distance") or self.args.distance)
        n = self._next_iteration_number()
        while not self.stop_requested() and (self.args.max_iter <= 0 or n < self.args.max_iter):
            n += 1
            self.log(f"=== iteration {n} seed={seed_ref} distance={distance}")
            entry: dict[str, Any] = {"iteration": n, "seed_ref": seed_ref, "distance": distance}
            try:
                rec = self.scan(n, seed_ref, distance)
            except Exception as exc:  # noqa: BLE001
                rec = {"iteration": n, "kind": "scan", "seed_ref": seed_ref, "distance": distance, "outcome": "error", "error": str(exc)[:1000]}
                self.log(f"iter {n}: scan error {exc}")
            rec["finished_at"] = _now()
            self.write_json(f"scan-{n:02d}.json", rec)
            scan_written_at = time.time()
            entry["scan_outcome"] = rec.get("outcome")
            chained_doc = ""
            if rec.get("outcome") == "scanned":
                decision = self.wait_decision(f"scan-{n:02d}.decision.json", newer_than=scan_written_at)
                if decision is None:
                    break
                entry["scan_decision"] = decision
                aid = decision.get("ingest")
                if aid:
                    try:
                        drec = self.deep(n, seed_ref, str(aid))
                    except Exception as exc:  # noqa: BLE001
                        drec = {"iteration": n, "kind": "deep", "seed_ref": seed_ref, "arxiv_id": aid, "outcome": "error", "error": str(exc)[:1000]}
                        self.ledger["tried"].setdefault(str(aid), {})["outcome"] = "error"
                        self.log(f"iter {n}: deep error {exc}")
                    drec["finished_at"] = _now()
                    self.write_json(f"deep-{n:02d}.json", drec)
                    deep_written_at = time.time()
                    entry["deep_outcome"] = drec.get("outcome")
                    chained_doc = drec.get("new_document_id") or ""
                    if drec.get("outcome") == "completed":
                        ddec = self.wait_decision(f"deep-{n:02d}.decision.json", newer_than=deep_written_at)
                        if ddec is None:
                            break
                        entry["deep_decision"] = ddec
                        decision = ddec
                seed_ref, distance = self._next_from(decision, seed_ref, distance, chained_doc)
                gap = self.args.gap
            elif rec.get("outcome") == "no_new_candidate":
                i = DISTANCES.index(distance) if distance in DISTANCES else 0
                if i + 1 < len(DISTANCES):
                    distance = DISTANCES[i + 1]
                else:
                    chain = self.ledger["chain"]
                    seed_ref = chain[-2]["to"] if len(chain) >= 2 else self.args.seed
                    distance = self.args.distance
                gap = self.args.gap
            else:
                gap = max(self.args.gap, 900)
            self.ledger["iterations"].append(entry)
            self.ledger["current_seed"] = seed_ref
            self.ledger["current_distance"] = distance
            self.save()
            self.log(f"iter {n}: next_seed={seed_ref} next_distance={distance} sleep={gap}s")
            for _ in range(int(gap)):
                if self.stop_requested():
                    break
                time.sleep(1)
        self.save()
        self.log("loop finished (STOP or max_iter)")

    def attach(self, n: int, seed_ref: str, arxiv_id: str, task_id: str, document_id: str) -> None:
        rec = self.deep(n, seed_ref, arxiv_id, task_id=task_id, document_id=document_id)
        rec["finished_at"] = _now()
        self.write_json(f"deep-{n:02d}.json", rec)
        self.save()
        self.log(f"attach {n}: outcome={rec.get('outcome')}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", nargs="?", default="run", choices=["run", "attach"])
    ap.add_argument("--base-url", default="http://localhost:3000")
    ap.add_argument("--user", default="urakawa")
    ap.add_argument("--seed", default="", help="起点の document_ref（document_id か material_id）。run では ledger の current_seed に優先する")
    ap.add_argument("--out", default="uxsim/runs/radar_loop")
    ap.add_argument("--distance", default="near", choices=DISTANCES)
    ap.add_argument("--max-results", type=int, default=10)
    ap.add_argument("--compare-top", type=int, default=5, help="浅い段で比較に掛ける新規候補の数（1 LLM コール）")
    ap.add_argument("--max-iter", type=int, default=0, help="0 = STOP まで")
    ap.add_argument("--gap", type=int, default=120, help="周の間の待ち（秒）")
    ap.add_argument("--poll", type=int, default=60)
    ap.add_argument("--timeout", type=int, default=12 * 3600, help="1 本の解析待ち上限（秒）")
    ap.add_argument("--language", default="ja")
    ap.add_argument("--cartridge-id", default=None, help="None = 送らない（env 既定）/ '' = 「指定しない」の明示")
    # attach 用
    ap.add_argument("--iteration", type=int, default=0)
    ap.add_argument("--arxiv-id", default="")
    ap.add_argument("--task-id", default="")
    ap.add_argument("--document-id", default="")
    a = ap.parse_args(argv)
    a.distance_explicit = any(x.startswith("--distance") for x in (argv if argv is not None else sys.argv[1:]))
    if a.command == "attach" and not a.seed:
        print("attach には --seed が必要", file=sys.stderr)
        return 2
    pw = os.environ.get("RADAR_LOOP_PASSWORD", "")
    if not pw:
        print("RADAR_LOOP_PASSWORD を環境変数で渡す", file=sys.stderr)
        return 2
    api = Api(a.base_url)
    api.login(a.user, pw)
    loop = Loop(api, pathlib.Path(a.out), a)
    if a.command == "attach":
        loop.attach(a.iteration, a.seed, a.arxiv_id, a.task_id, a.document_id)
    else:
        loop.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
