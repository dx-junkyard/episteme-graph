"""頭脳（Claude Code の子エージェント）が mailbox に応えるための補助 CLI（外部 API を呼ばない）。

  python -m uxsim.sandbox.brain pending <mailbox_dir> [--wait SEC]   # 未応答の seq を古い順に列挙（--wait で 1 件出るまで待つ）
  python -m uxsim.sandbox.brain show <mailbox_dir> <seq>            # 要求を読みやすく出す（persona: system 抜粋 + user 全文 + schema / product: messages + note）
  python -m uxsim.sandbox.brain answer <mailbox_dir> <seq> <file>   # file の JSON を res-<seq>.json として原子的に置く
                                                                     # persona は {"output": {...}} / product は {"content": "..."} の形を検査する
第 13 周の教訓: 製品頭脳は要求ごとに **その messages だけ** から答え、前の要求や別の学生の会話を持ち込まない。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


def _pending(root: Path) -> list[int]:
    seqs = []
    for req in sorted(root.glob("req-*.json")):
        seq = int(req.stem.split("-")[1])
        if not (root / f"res-{seq:05d}.json").is_file():
            seqs.append(seq)
    return seqs


def cmd_pending(a) -> int:
    root = Path(a.mailbox)
    deadline = time.time() + (a.wait or 0)
    while True:
        seqs = _pending(root)
        if seqs or time.time() >= deadline:
            break
        time.sleep(2)
    for s in seqs:
        kind = json.loads((root / f"req-{s:05d}.json").read_text(encoding="utf-8")).get("kind")
        print(f"{s:05d}\t{kind}")
    print(f"# pending={len(seqs)}", file=sys.stderr)
    return 0


def _trim(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + f"…(+{len(s)-n} chars)"


def cmd_show(a) -> int:
    root = Path(a.mailbox)
    d = json.loads((root / f"req-{int(a.seq):05d}.json").read_text(encoding="utf-8"))
    print(f"### seq={d.get('seq')} kind={d.get('kind')} created_at={d.get('created_at')}")
    if d.get("kind") == "persona":
        print("--- system (抜粋)")
        print(_trim(str(d.get("system", "")), a.system_chars))
        for m in d.get("messages") or []:
            print(f"--- {m.get('role')}")
            print(m.get("content"))
        print("--- schema_hint（output はこの形の JSON）")
        print(json.dumps(d.get("schema_hint"), ensure_ascii=False, indent=1))
    else:
        print(f"model={d.get('model')}  response_format={json.dumps(d.get('response_format'), ensure_ascii=False)[:200]}")
        if d.get("note"):
            print("--- note"); print(d["note"])
        for m in d.get("messages") or []:
            c = m.get("content")
            if not isinstance(c, str):
                c = json.dumps(c, ensure_ascii=False)
            print(f"--- {m.get('role')}")
            print(c if a.full else _trim(c, a.msg_chars))
    return 0


def cmd_answer(a) -> int:
    root = Path(a.mailbox)
    seq = int(a.seq)
    req = json.loads((root / f"req-{seq:05d}.json").read_text(encoding="utf-8"))
    raw = Path(a.file).read_text(encoding="utf-8") if a.file != "-" else sys.stdin.read()
    data = json.loads(raw)
    if req.get("kind") == "persona":
        if not isinstance(data.get("output"), dict) or "action_id" not in data["output"]:
            print("persona の応答は {\"output\": {... action_id ...}} の形", file=sys.stderr); return 2
    else:
        if not isinstance(data.get("content"), str) and "error" not in data:
            print("product の応答は {\"content\": \"<文字列>\"} の形", file=sys.stderr); return 2
        rf = req.get("response_format") or {}
        if isinstance(rf, dict) and rf.get("type") in ("json_object", "json_schema"):
            try:
                json.loads(data["content"])
            except Exception as exc:  # noqa: BLE001
                print(f"JSON モードの要求だが content が JSON でない: {exc}", file=sys.stderr); return 2
    res = root / f"res-{seq:05d}.json"
    if res.is_file() and not a.force:
        print(f"{res.name} は既にある（--force で上書き）", file=sys.stderr); return 3
    tmp = res.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, res)
    print(f"answered {res.name}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pending"); p.add_argument("mailbox"); p.add_argument("--wait", type=float, default=0); p.set_defaults(fn=cmd_pending)
    p = sub.add_parser("show"); p.add_argument("mailbox"); p.add_argument("seq"); p.add_argument("--system-chars", type=int, default=6000)
    p.add_argument("--msg-chars", type=int, default=12000); p.add_argument("--full", action="store_true"); p.set_defaults(fn=cmd_show)
    p = sub.add_parser("answer"); p.add_argument("mailbox"); p.add_argument("seq"); p.add_argument("file"); p.add_argument("--force", action="store_true"); p.set_defaults(fn=cmd_answer)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
