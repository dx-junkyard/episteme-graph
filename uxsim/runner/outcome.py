"""run の到達点（``outcome.json``）— 第 25 周 第 10 波（share H65）。

``lane.sh`` の依存（``LANE_DEPS``）は依存先の exit だけを見ていた。(c) が目標の前提（記号の確定・凍結）を作らずに exit 0 で
終わり、(e) が前提なしで走った。そこで runner が run の終わりに **到達点**（状態を変える手が成功した事実）を書き、
lane.sh が依存先の到達点を読んで「前提の状態」で判定する。

到達点の語（決定論・transcript だけから導く — 製品を読まない）:

- ``<action_id>`` — その手で書き込み（POST / PUT / PATCH / DELETE）が 2xx で通った
- ``<action_id>=<値>`` — 同じく、引数 ``decision`` / ``status`` / ``what`` / ``level`` の値ごと（例
  ``admin.library.bound_link_decide=confirm``）。見送りで終わった判断は ``=reject`` になり ``=confirm`` に届かない

``seen`` は読み取り（GET の 2xx）だけの手。依存の判定には使わない（画面を開いただけで前提は変わらない）。

CLI（lane.sh から）::

    python -m uxsim.runner.outcome check <OUTCOME.json> <persona-id> <point,point,...>

満たせば exit 0、満たさなければ欠けた到達点を 1 行で印字して exit 1、ファイルが無ければ exit 2。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")
POINT_ARG_KEYS = ("decision", "status", "what", "level")
OUTCOME_FILE = "outcome.json"
ENV_OUTCOME_PATH = "UXSIM_OUTCOME_PATH"


def _ok(status: Any) -> bool:
    return isinstance(status, int) and 200 <= status < 300


def step_points(step: Any) -> tuple[list[str], bool]:
    """1 手の到達点（書き込みが 2xx で通ったときの語）と、読み取りだけの 2xx があったか。純関数。"""
    action = str(getattr(step, "action_id", "") or "")
    if not action or action.startswith("unsupported:"):
        return [], False
    http = list(getattr(step, "http", None) or [])
    wrote = any(str(getattr(h, "method", "")).upper() in WRITE_METHODS and _ok(getattr(h, "status", None)) for h in http)
    read = any(str(getattr(h, "method", "")).upper() == "GET" and _ok(getattr(h, "status", None)) for h in http)
    if not wrote:
        return [], read
    args = getattr(step, "args", None) or {}
    points = [action]
    for k in POINT_ARG_KEYS:
        v = args.get(k) if isinstance(args, dict) else None
        if isinstance(v, str) and v.strip():
            points.append(f"{action}={v.strip()}")
    return points, read


def build_outcome(steps: Iterable[Any], *, run_id: str = "", exit_code: int = 0) -> dict:
    """transcript の手から到達点の表を作る（ペルソナ別・出現順・重複なし）。純関数。"""
    personas: dict[str, dict] = {}
    for st in steps:
        pid = str(getattr(st, "persona_id", "") or "")
        if not pid:
            continue
        row = personas.setdefault(pid, {"scenarios": [], "reached": [], "seen": []})
        sid = str(getattr(st, "scenario_id", "") or "")
        if sid and sid not in row["scenarios"]:
            row["scenarios"].append(sid)
        points, read = step_points(st)
        for p in points:
            if p not in row["reached"]:
                row["reached"].append(p)
        action = str(getattr(st, "action_id", "") or "")
        if read and not points and action and action not in row["seen"]:
            row["seen"].append(action)
    return {"run_id": run_id, "exit": int(exit_code), "personas": personas}


def write_outcome(run_dir: Path, *, exit_code: int = 0, extra_path: Optional[str] = None) -> dict:
    """run の中に ``outcome.json`` を書き、``extra_path``（既定は環境変数 ``UXSIM_OUTCOME_PATH``）にも同じものを写す。"""
    from uxsim.oracles.findings import load_transcript

    run_dir = Path(run_dir)
    data = build_outcome(load_transcript(run_dir), run_id=run_dir.name, exit_code=exit_code)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    (run_dir / OUTCOME_FILE).write_text(text, encoding="utf-8")
    extra = extra_path if extra_path is not None else os.environ.get(ENV_OUTCOME_PATH, "")
    if extra:
        Path(extra).parent.mkdir(parents=True, exist_ok=True)
        Path(extra).write_text(text, encoding="utf-8")
    return data


def parse_points(text: str) -> list[str]:
    return [p.strip() for p in str(text or "").replace("+", ",").split(",") if p.strip()]


def missing_points(outcome: dict, persona: str, points: Iterable[str]) -> list[str]:
    """依存先のペルソナ（id か id の頭）が届いていない到達点。純関数。"""
    rows = (outcome or {}).get("personas") or {}
    row = rows.get(persona) or next((v for k, v in rows.items() if k.startswith(persona) or persona.startswith(k)), None)
    reached = set((row or {}).get("reached") or [])
    return [p for p in points if p not in reached]


def main(argv: Optional[list[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 4 or args[0] != "check":
        print("usage: python -m uxsim.runner.outcome check <OUTCOME.json> <persona-id> <point,point,...>")
        return 64
    _, path, persona, points_text = args
    p = Path(path)
    if not p.is_file():
        print(f"到達点の記録が無い（{p.name}）— 依存先が走っていない")
        return 2
    try:
        outcome = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        print(f"到達点の記録が読めない（{p.name}）")
        return 2
    lacking = missing_points(outcome, persona, parse_points(points_text))
    if lacking:
        print("届いていない到達点: " + "、".join(lacking))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
