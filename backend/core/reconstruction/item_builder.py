"""claim → ELICIT 変換の非LLM部分（predict 可否判定・restate 縮退・降下プローブ・伏せ）。

LLM が最終的に choices / expected を生成するが、mode の下地判定と、learner に
返す item / claim フィールドの伏せ（=答えの秘匿, §3.2）はここで決める。
"""

from __future__ import annotations

from typing import Any

from core.element_vocab import claim_type_label
from core.reconstruction.claim_context import (
    RELATION_WITHHELD_FEW_SYMBOLS,
    RELATION_WITHHELD_NOT_RELATIONAL,
    RELATION_WITHHELD_NOT_SOURCE_EXTRACTED,
    symbol_names,
)
from core.reconstruction.schema import (
    ResponseOption,
    is_relational_claim_type,
    subject_driver_concepts,
)


#: predict を下地にしない理由（IK-0483。記帳・報告用の語彙）。
RESTATE_CLAIM_TYPE_NOT_RELATIONAL = "claim_type_not_relational"
RESTATE_NO_CONCEPTS_OR_EQUATION = "fewer_than_two_concepts_and_no_equation"
RESTATE_EQUATION_NOT_SOURCE_EXTRACTED = RELATION_WITHHELD_NOT_SOURCE_EXTRACTED
RESTATE_EQUATION_NOT_RELATIONAL = RELATION_WITHHELD_NOT_RELATIONAL
RESTATE_EQUATION_FEW_SYMBOLS = RELATION_WITHHELD_FEW_SYMBOLS

RESTATE_REASONS = (
    RESTATE_CLAIM_TYPE_NOT_RELATIONAL,
    RESTATE_NO_CONCEPTS_OR_EQUATION,
    RESTATE_EQUATION_NOT_SOURCE_EXTRACTED,
    RESTATE_EQUATION_NOT_RELATIONAL,
    RESTATE_EQUATION_FEW_SYMBOLS,
)


def elicit_mode_decision(claim: dict[str, Any]) -> tuple[str, str | None]:
    """下地の出題モードと、restate にした理由（predict なら None）。

    predict: 関係型 claim で、概念 2 個以上 または 関係型の式（``equation.relation_type``。
    ``claim_context.pick_equation`` が PDF からそのまま抽出できた関係型の式にだけ付ける）。
    それ以外は restate（言い直し）へ縮退し、理由を返す。predict を無理に選ばない。
    """
    if not is_relational_claim_type(claim.get("claim_type", "")):
        return "restate", RESTATE_CLAIM_TYPE_NOT_RELATIONAL
    concepts = subject_driver_concepts(claim)
    equation = claim.get("equation") if isinstance(claim.get("equation"), dict) else {}
    has_relational_equation = bool(
        (equation.get("relation_type") or "").strip()
        and (equation.get("defined_symbols") or equation.get("symbols") or equation.get("latex"))
    )
    if len(concepts) >= 2 or has_relational_equation:
        return "predict", None
    withheld = str(equation.get("relation_withheld") or "").strip()
    if withheld in RESTATE_REASONS:
        return "restate", withheld
    return "restate", RESTATE_NO_CONCEPTS_OR_EQUATION


def preferred_elicit_mode(claim: dict[str, Any]) -> str:
    """claim から predict 可否を決定論的に判定する（理由は ``elicit_mode_decision``）。"""
    return elicit_mode_decision(claim)[0]


def visible_claim_fields(claim: dict[str, Any]) -> dict:
    """learner に見せてよい claim フィールドのみ（concepts=subject/driver, source_scope）。

    伏せフィールド（text / normalized_text / equation / evidence_text）は返さない（§3.2）。
    """
    return {
        "concepts": subject_driver_concepts(claim),
        "claim_type": str(claim.get("claim_type") or ""),
        # 種別は日本語の表示名で渡す（語彙の正本は core/element_vocab。未知は空）。
        "claim_type_label": claim_type_label(claim.get("claim_type")),
        "source_scope": _public_source_scope(claim.get("source_scope")),
    }


def _public_source_scope(scope: Any) -> dict:
    """出典の文脈（section 見出し・ページ）だけを返す。本文は含めない。"""
    if not isinstance(scope, dict):
        return {}
    return {
        "level": str(scope.get("level") or ""),
        # section_id は解析層の内部 ID なので学習者に返さない（見出しだけで足りる）。
        "section_title": str(scope.get("section_title") or ""),
        "pages": [p for p in (scope.get("pages") or []) if isinstance(p, int)],
    }


def public_item_view(item: dict[str, Any], claim: dict[str, Any]) -> dict:
    """`next` / `submit` の再出題で learner に返す item 表現（伏せフィールドを含めない）。"""
    return {
        "item_id": str(item.get("id") or ""),
        "claim_id": str(item.get("claim_id") or ""),
        "elicit_mode": str(item.get("elicit_mode") or "restate"),
        "prompt": str(item.get("prompt") or ""),
        "response_space": _public_response_space(item.get("response_space")),
        "author": str(item.get("author") or "llm"),
        "auto_generated": str(item.get("author") or "llm") == "llm",
        "claim_context": visible_claim_fields(claim),
    }


def _public_response_space(space: Any) -> list[dict]:
    """選択肢の id / label のみ（expected へのヒントを混ぜない）。"""
    out: list[dict] = []
    if not isinstance(space, list):
        return out
    for opt in space:
        if isinstance(opt, dict) and str(opt.get("id") or "").strip():
            out.append({"id": str(opt["id"]), "label": str(opt.get("label") or "")})
    return out


def symbol_probe(claim: dict[str, Any]) -> dict:
    """記号葉への降下プローブ（§3.4）。

    claim の concepts / equation.defined_symbols から記号を引き、「この記号は何を指す？」
    の一行プローブを作る。SymbolRegistry の DB 化前は claim 自身のフィールドから引く
    （domain-independent）。
    """
    symbols: list[str] = []
    equation = claim.get("equation") if isinstance(claim.get("equation"), dict) else {}
    for s in symbol_names(equation.get("defined_symbols")):
        if s not in symbols:
            symbols.append(s)
    for name in subject_driver_concepts(claim):
        if name and name not in symbols:
            symbols.append(name)
    target = symbols[0] if symbols else ""
    prompt = (
        "この記号「" + target + "」は何を指していますか？一言で述べてください。"
        if target
        else "この主張に出てくる記号・用語のうち、意味が曖昧なものを一つ挙げて説明してください。"
    )
    return {
        "elicit_mode": "symbol",
        "prompt": prompt,
        "symbols": symbols,
        "target_symbol": target,
    }


def response_options_to_dicts(options: list[ResponseOption]) -> list[dict]:
    return [o.to_dict() for o in options]
