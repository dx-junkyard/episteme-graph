"""実行時 DIFF / REFLECT（同期・非LLM）。

学習者の産出物と ground truth（claim）を構造照合する。判定は「仮説」であり
authoritative ではない（権威は出典のリビール）。数値スコア・順位・煽り文句は
一切生成しない（P7・ガードレールテスト対象）。

- DIFF: concept 被覆（subject/driver 概念への言及）+ 関係の型（predict の選択肢 ID 照合）
- REFLECT: 「出典はこう述べています / あなたは…と述べました / 食い違いは…の一点です」
  という事実文のみ。verdict が na のときは対比提示のみ。
"""

from __future__ import annotations

from typing import Any

from core.reconstruction.schema import DiffResult, subject_driver_concepts


def _learner_text(response: dict[str, Any]) -> str:
    """response から学習者のテキスト表現を取り出す（restate の自由記述 / predict の選択ラベル）。"""
    if not isinstance(response, dict):
        return ""
    for key in ("text", "learner_text", "statement", "answer"):
        v = response.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _selected_option_id(response: dict[str, Any]) -> str:
    if not isinstance(response, dict):
        return ""
    for key in ("option_id", "selected_option_id", "choice", "selected"):
        v = response.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def response_has_content(response: dict[str, Any]) -> bool:
    """学習者の応答に実体（選択 or 自由記述）があるか。

    CAPTURE なしの REVEAL を許さない（§3.1: 提出 → 照合 → 開示の順）。
    空応答で出典（伏せフィールド）だけ引き出すことを防ぐ。
    """
    return bool(_selected_option_id(response) or _learner_text(response))


def _option_label(item: dict[str, Any], option_id: str) -> str:
    for opt in item.get("response_space") or []:
        if isinstance(opt, dict) and str(opt.get("id") or "") == option_id:
            return str(opt.get("label") or "")
    return ""


def run_diff(item: dict[str, Any], claim: dict[str, Any], response: dict[str, Any]) -> DiffResult:
    """item / claim / response から DiffResult を決定論的に構築する。

    predict: 選択肢 ID と expected.option_id の照合 → match / mismatch。
    restate: 構造照合は concept 被覆のみ（verdict は na が既定。最終判断は自己確認）。
    """
    mode = str(item.get("elicit_mode") or "restate")
    expected = item.get("expected") if isinstance(item.get("expected"), dict) else {}
    expected_concepts = subject_driver_concepts(claim)

    result = DiffResult(expected_concepts=list(expected_concepts))

    if mode == "predict":
        selected = _selected_option_id(response)
        gold = str(expected.get("option_id") or "").strip()
        # concept 被覆は選択ラベルへの言及で近似（情報提示用。verdict の主軸ではない）
        selected_label = _option_label(item, selected)
        result.covered_concepts = _covered(expected_concepts, selected_label)
        result.missing_concepts = [c for c in expected_concepts if c not in result.covered_concepts]
        if not selected or not gold:
            result.machine_verdict = "na"
        elif selected == gold:
            result.machine_verdict = "match"
        else:
            result.machine_verdict = "mismatch"
            result.focus = "選択した関係の型"
        return result

    # restate（言い直し）: concept 被覆のみ。verdict は na（鏡=自己確認に委ねる）。
    text = _learner_text(response)
    result.covered_concepts = _covered(expected_concepts, text)
    result.missing_concepts = [c for c in expected_concepts if c not in result.covered_concepts]
    result.machine_verdict = "na"
    if result.missing_concepts:
        result.focus = "、".join(result.missing_concepts) + " への言及"
    return result


def _covered(expected_concepts: list[str], learner_text: str) -> list[str]:
    low = (learner_text or "").lower()
    covered = []
    for name in expected_concepts:
        n = name.strip()
        if not n:
            continue
        if n.lower() in low or n in learner_text:
            covered.append(name)
    return covered


def _reveal_from_claim(claim: dict[str, Any]) -> dict:
    """REVEAL（開示）内容: claim 本文・式・evidence。DIFF 直後にのみ返す（§3.4）。"""
    equation = claim.get("equation") if isinstance(claim.get("equation"), dict) else {}
    return {
        "text": str(claim.get("text") or ""),
        "normalized_text": str(claim.get("normalized_text") or ""),
        "equation_latex": str(equation.get("latex") or ""),
        "equation_label": str(equation.get("label") or ""),
        "evidence_text": str(claim.get("evidence_text") or ""),
    }


def build_reflection(
    item: dict[str, Any],
    claim: dict[str, Any],
    response: dict[str, Any],
    diff: DiffResult,
) -> dict:
    """REFLECT の事実文 + REVEAL を組み立てる（非LLM）。

    点数・順位・煽り文句を含めない。判定を authoritative に見せない（「食い違いの可能性」）。
    """
    reveal = _reveal_from_claim(claim)
    mode = str(item.get("elicit_mode") or "restate")
    statements: list[str] = []

    source_line = reveal["text"] or reveal["normalized_text"]
    if source_line:
        statements.append("出典はこう述べています: " + source_line)

    if mode == "predict":
        selected_label = _option_label(item, _selected_option_id(response))
        if selected_label:
            statements.append("あなたは「" + selected_label + "」と予測しました。")
        if diff.machine_verdict == "mismatch":
            focus = diff.focus or "関係の型"
            statements.append("食い違いの可能性があるのは" + focus + "の一点です。")
        elif diff.machine_verdict == "match":
            statements.append("あなたの予測と出典の関係の型は一致しているようです。最終的な確認はご自身の判断でどうぞ。")
        else:
            statements.append("選択と出典を並べて見比べてみてください。")
    else:
        learner_text = _learner_text(response)
        if learner_text:
            statements.append("あなたは「" + learner_text + "」と述べました。")
        if diff.missing_concepts:
            statements.append(
                "出典が触れている「" + "、".join(diff.missing_concepts) + "」への言及が、あなたの版には見当たりません。"
            )
        else:
            statements.append("あなたの版と出典を並べて、どこが違うかご自身で見比べてみてください。")

    return {
        "verdict": diff.machine_verdict,
        "statements": statements,
        "reveal": reveal,
        "diff": diff.to_dict(),
    }
