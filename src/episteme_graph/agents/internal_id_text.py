"""生成文・材料文に紛れ込んだ解析層の内部 ID を見つけて外す（A層の正本・純関数）。

IK-0819: equation_semantics の材料は近傍ブロックを ``[tex_b80] …`` のように ID 付きで示すため、
LLM が要約の本文に ``(tex_b80)`` を書き写し、二層説明（contextual_explanation）の材料
``local_text`` を経て人向けの説明に流れうる。本モジュールは A層で「生成文に内部 ID を
残さない」ための判定と除去を受け持つ。

表示側の遮断の正本は ``backend/core/display_projection.py::INTERNAL_ID_TOKEN_RE``。
backend の SA層解決器は ``backend`` だけを sys.path に置く別プロセスで純粋性を検査される
ため、backend からこのモジュールを module level で import できない（逆に A層は backend を
import しない）。そこで本モジュールは**A層の材料に現れる ID の形だけ**を持ち、
「ここで捕まえる ID はすべて表示側の語彙にも捕まる（A ⊆ 表示）」ことを
``backend/tests/test_display_projection_guardrails.py`` が見本で固定する。

論文の式番号（``eq_12`` / ``eq_2_7`` / ``(3.14)``）は内部 ID ではないので当てない。
stdlib のみ依存。
"""
from __future__ import annotations

import re

_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"

#: 直後が数字であることを要求するプレフィックス（``span_1`` は当て、語彙語 ``span_id`` は当てない）。
_IMMEDIATE = (
    r"theory_op_",
    r"evidence_",
    r"ev_",
    r"comp_",
    r"node_",
    r"span_",
    r"step_",
    r"blk_",
    r"sym_",
    r"tex_b",
)
#: 本体のどこかに数字があればよいもの（``eq_tex_b78`` / ``claim_span_3`` / ``synth_claim_0001``）。
_ANYWHERE = (
    r"eq_tex_",
    r"eq_blk_",
    r"eq_op_",
    r"eq_eqcand_",
    r"eqcand_",
    r"claim_span_",
    r"synth_claim_",
    r"derivation_eq_",
)

INTERNAL_ID_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"(?:" + "|".join(_IMMEDIATE) + r")[0-9][A-Za-z0-9_\-]*"
    r"|(?:" + "|".join(_ANYWHERE) + r")(?=[A-Za-z0-9_\-]*[0-9])[A-Za-z0-9_\-]+"
    r"|k1:[0-9a-f]{8,}(?:#[0-9]+)?"
    r"|" + _UUID
    + r")(?![A-Za-z0-9_])",
    re.IGNORECASE,
)

# ID が括弧・角括弧に単独で入っている形（「(tex_b80)」「[tex_b80, tex_b81]」）は括弧ごと外す。
_ID_GROUP_RE = re.compile(
    r"\s*[\(\[（［]\s*(?:" + INTERNAL_ID_RE.pattern + r")"
    r"(?:\s*[,、;；]\s*(?:" + INTERNAL_ID_RE.pattern + r"))*\s*[\)\]）］]",
    re.IGNORECASE,
)


def find_internal_ids(text: str | None) -> list[str]:
    """``text`` に含まれる内部 ID トークンを出現順・重複なしで返す。"""
    seen: list[str] = []
    for match in INTERNAL_ID_RE.finditer(str(text or "")):
        token = match.group(0)
        if token not in seen:
            seen.append(token)
    return seen


def contains_internal_id(text: str | None) -> bool:
    return bool(INTERNAL_ID_RE.search(str(text or "")))


def strip_internal_ids(text: str | None) -> str:
    """内部 ID を外した文を返す（括弧に単独で入った ID は括弧ごと外す）。

    除去後に残る空白の重なりと句読点の前の空白を整える。ID を含まない文はそのまま返す。
    """
    source = str(text or "")
    if not INTERNAL_ID_RE.search(source):
        return source
    cleaned = _ID_GROUP_RE.sub("", source)
    cleaned = INTERNAL_ID_RE.sub("", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"[ \t]+([.,;:。、，．])", r"\1", cleaned)
    cleaned = re.sub(r"[\(\[（［]\s*[\)\]）］]", "", cleaned)
    return cleaned.strip()
