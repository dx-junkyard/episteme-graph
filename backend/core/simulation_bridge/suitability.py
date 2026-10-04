"""検証シミュレーション層（SB層）— 理論モジュールの「数値で動かして意味があるか」の判定（決定論・非LLM）。

第 22 周 A3（第 21 周 seq419）: 機械学習の損失関数の組み立て（ネットワーク・重み付き和・二乗残差）が
「理論モジュール」としてシミュレーション対象に提示された。数値で走らせて論文の予言を確かめられるのは
**量の関係**であって、学習・最適化の手続（損失・目的関数）ではない。

判定の材料は A層が既に持つものだけ（``equation_semantics`` の ``equation_type`` と ``summary``、式本文）。

- 手続の印: 式本文の ``argmin`` / ``argmax`` 系の演算子、または要約が「損失・目的関数・費用関数・学習・
  ニューラルネットワーク・正則化」を語る（方法の語であって分野の語ではない — 分野語を
  ハードコードしない）。``\\mathcal{L}`` は Lagrangian でもあるので記号だけでは判定しない。
- 本文のある式の半数以上が手続の印を持てば ``simulable=False`` + :data:`schema.FACT_NOT_SIMULABLE`。
- 全式に ``equation_type`` があり（``unknown`` を含まない）、手続の印が無く、関係・近似・変換・結果の式を
  1 つ以上含めば ``simulable=True``。
- それ以外（材料が無い・足りない）は ``simulable=None`` と空文字 — **判定しないことを正直に返す**。

FastAPI / sqlalchemy / ``core.llm`` を import しない。入力を mutate しない。例外を外に出さない。
"""

from __future__ import annotations

import json
import re
from typing import Any

from core.simulation_bridge import schema

__all__ = ["assess_module", "is_procedure_equation"]

#: 要約が手続（学習・最適化）を語っている印。方法の語で、特定の分野の語ではない。
_PROCEDURE_SUMMARY_RE = re.compile(
    r"\b(?:loss(?:es)?|loss function|objective function|cost function|training|trained|"
    r"neural[- ]network|regulari[sz](?:ation|er))\b"
    r"|損失|目的関数|費用関数|学習|ニューラルネットワーク|正則化",
    re.IGNORECASE,
)
#: 式本文の最適化演算子。
_PROCEDURE_LATEX_RE = re.compile(r"\\arg\s*\\?(?:min|max)|\b(?:arg\s*min|arg\s*max|argmin|argmax)\b", re.IGNORECASE)
#: 量の関係を表す equation_type（definition / constraint だけのモジュールは判定しない）。
_RELATION_TYPES = frozenset({"relation", "approximation", "transformation", "result"})
_KNOWN_TYPES = frozenset({"definition", "relation", "transformation", "approximation", "result", "constraint"})


def _payload(row: dict) -> dict:
    payload = row.get("agent_payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            payload = {}
    return payload if isinstance(payload, dict) else {}


def _semantics(row: dict) -> dict:
    sem = _payload(row).get("semantics")
    if not isinstance(sem, dict):
        sem = row.get("semantics")
    return sem if isinstance(sem, dict) else {}


def _equation_type(row: dict) -> str:
    sem = _semantics(row)
    value = sem.get("equation_type") or _payload(row).get("equation_type") or row.get("equation_type") or ""
    return str(value).strip().lower()


def _has_text(row: dict) -> bool:
    return bool(str(row.get("latex") or "").strip() or str(row.get("plain_text") or "").strip())


def is_procedure_equation(row: Any) -> bool:
    """式 1 つが学習・最適化の手続を表している印を持つか（決定論）。"""
    if not isinstance(row, dict):
        return False
    latex = f"{row.get('latex') or ''} {row.get('plain_text') or ''}"
    if _PROCEDURE_LATEX_RE.search(latex):
        return True
    summary = str(_semantics(row).get("summary") or row.get("summary") or "")
    return bool(_PROCEDURE_SUMMARY_RE.search(summary))


def assess_module(module: Any, equations: Any, symbols: Any = None) -> dict[str, Any]:
    """理論モジュールの適性 ``{"simulable": bool | None, "note": str}``。

    ``symbols`` は現状の判定には使わない（記号表の ``kind`` は実測で全件 unknown — 埋まれば使う）。
    """
    try:
        rows = [r for r in (equations or []) if isinstance(r, dict) and _has_text(r)]
        if not rows:
            return {"simulable": None, "note": ""}
        procedure = sum(1 for r in rows if is_procedure_equation(r))
        if procedure * 2 >= len(rows) and procedure > 0:
            return {"simulable": False, "note": schema.FACT_NOT_SIMULABLE}
        types = [_equation_type(r) for r in rows]
        if procedure == 0 and all(t in _KNOWN_TYPES for t in types) and any(t in _RELATION_TYPES for t in types):
            return {"simulable": True, "note": ""}
        return {"simulable": None, "note": ""}
    except Exception:  # noqa: BLE001 — 判定できないときは判定しない
        return {"simulable": None, "note": ""}
