"""頭脳（Claude Code の子エージェント）が mailbox に応えるための補助 CLI（外部 API を呼ばない）。

  python -m uxsim.sandbox.brain pending <mailbox_dir> [--wait SEC] [--after SEQ]
                                                                     # 未応答の seq を古い順に列挙（--wait で 1 件出るまで待つ。
                                                                     # --after で SEQ 以下の古い未応答を数えない — 第 25 周第 5 周:
                                                                     # 前のレーンの取り残しで --wait が即返り、新しい要求を待てなかった）
  python -m uxsim.sandbox.brain pending <mailbox_dir> --claim [--owner NAME]
                                                                     # 未応答・未予約の最古の 1 件を予約（claim-<seq>.lock）して返す
                                                                     # （第 25 周 第 7 周: 2 体目の頭脳が同じ要求を取らずに飛ばせる）
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


def _pending(root: Path, after: int = -1) -> list[int]:
    """未応答の seq（古い順）。``after`` 以下の seq は数えない（古い取り残しで待ちが即返らないように）。"""
    seqs = []
    for req in sorted(root.glob("req-*.json")):
        try:
            seq = int(req.stem.split("-")[1])
        except (IndexError, ValueError):
            continue
        if seq > after and not (root / f"res-{seq:05d}.json").is_file():
            seqs.append(seq)
    return seqs


def claim_path(root: Path, seq: int) -> Path:
    """未応答の要求の予約ファイル（第 25 周 第 7 周 — 頭脳の運転: 2 体目の頭脳が同じ要求を取らず次へ飛ばせる）。"""
    return root / f"claim-{int(seq):05d}.lock"


def try_claim(root: Path, seq: int, owner: str = "") -> bool:
    """予約ファイルを排他作成で置く（既にあれば False）。中身は予約した頭脳の名前と時刻（任意）。"""
    try:
        fd = os.open(str(claim_path(root, seq)), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(f"{owner}\t{time.strftime('%Y-%m-%dT%H:%M:%S')}\n")
    return True


def claim_next(root: Path, after: int = -1, owner: str = "") -> list[int]:
    """未応答で、まだ予約されていない最古の要求を 1 件予約して返す（無ければ空）。"""
    for seq in _pending(root, after=after):
        if not claim_path(root, seq).exists() and try_claim(root, seq, owner):
            return [seq]
    return []


def cmd_pending(a) -> int:
    root = Path(a.mailbox)
    deadline = time.time() + (a.wait or 0)
    after = a.after if a.after is not None else -1
    while True:
        if getattr(a, "claim", False):
            seqs = claim_next(root, after=after, owner=str(getattr(a, "owner", "") or ""))
        else:
            seqs = _pending(root, after=after)
        if seqs or time.time() >= deadline:
            break
        time.sleep(2)
    for s in seqs:
        kind = json.loads((root / f"req-{s:05d}.json").read_text(encoding="utf-8")).get("kind")
        print(f"{s:05d}\t{kind}")
    print(f"# pending={len(seqs)}", file=sys.stderr)
    return 0


# 第 25 周 第 6 周の是正（atlas H36・H39・H40）: 頭脳が毎回読む要求の頭に置く固定の注意。
NUMBERING_NOTE = ("（ハーネス）観察の「N.」「（前の表示の番号 N）」「リンクN」「疑義N」などの番号はハーネスが振った印で、"
                  "製品の画面に番号は無い。並べ替え・取り違えの所見は番号ではなく名前で書く")
GONE_NOTE = ("（ハーネス）前の周の課題を GONE と書くときは、是正前（前の周の観察）と是正後（この観察）の差を 1 行ずつ引用する。"
             "差を引用できなければ「この観察では判定できない」と書く")


def persona_request_header(d: dict) -> list[str]:
    """persona の要求の頭に出す行（transcript の seq と固定の注意）。"""
    lines = []
    seq = d.get("transcript_seq")
    if seq is not None:
        lines.append(f"--- transcript: 直前に見た画面 = seq {int(seq)} の結果（ノートの seq はこの番号を写す。"
                     f"この要求で選ぶ行為は seq {int(seq) + 1} 以降になる。自分で数えない）")
    else:
        lines.append("--- transcript: 直前に見た画面の seq なし（場面の最初の手）")
    lines.append(NUMBERING_NOTE)
    lines.append(GONE_NOTE)
    return lines


def _trim(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + f"…(+{len(s)-n} chars)"


def cmd_show(a) -> int:
    root = Path(a.mailbox)
    d = json.loads((root / f"req-{int(a.seq):05d}.json").read_text(encoding="utf-8"))
    print(f"### seq={d.get('seq')} kind={d.get('kind')} created_at={d.get('created_at')}")
    if d.get("kind") == "persona":
        for line in persona_request_header(d):
            print(line)
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
    p = sub.add_parser("pending"); p.add_argument("mailbox"); p.add_argument("--wait", type=float, default=0)
    p.add_argument("--after", type=int, default=None, help="この seq 以下の未応答は数えない（直前に答えた seq を渡す）")
    p.add_argument("--claim", action="store_true",
                   help="未応答で未予約の最古の 1 件を予約（claim-<seq>.lock）して返す。予約済みは飛ばす（2 体目の頭脳が並走できる）")
    p.add_argument("--owner", default="", help="--claim の予約ファイルに書く頭脳の名前（任意）")
    p.set_defaults(fn=cmd_pending)
    p = sub.add_parser("show"); p.add_argument("mailbox"); p.add_argument("seq"); p.add_argument("--system-chars", type=int, default=6000)
    p.add_argument("--msg-chars", type=int, default=12000); p.add_argument("--full", action="store_true"); p.set_defaults(fn=cmd_show)
    p = sub.add_parser("answer"); p.add_argument("mailbox"); p.add_argument("seq"); p.add_argument("file"); p.add_argument("--force", action="store_true"); p.set_defaults(fn=cmd_answer)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
