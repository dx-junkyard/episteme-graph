"""PDF text extraction (GROBID) と PaperStructure diff/merge ユーティリティ。

現行のドキュメント解析パイプラインは
``core/document_pipeline/orchestrator.py`` + ``src/episteme_graph/agents/``
であり、本モジュールが提供するのはその下請けとなる GROBID 変換
（PDF → TEI XML）と、構造の差分計算・LLM マージ（Gateway 層向け）のみ。

かつて存在した仮説駆動型の逐次 LLM 構造抽出パイプライン
（``extract_paper_structure`` とその内部ステップ）と、未使用の
テキストフォールバック群（``parse_tei_to_logical_chunks`` /
``extract_text_from_pdf_bytes`` / ``chunk_text``）は本番呼び出し元が
存在しなかったため削除済み（2026-07 整理）。

Notes on Reasoning models
--------------------------
o1 / o3-mini / gpt-5.2 等の reasoning モデルは以下の制約がある:
- ``system`` ロールは使用不可。``developer`` または ``user`` ロールのみ。
- ``temperature`` / ``max_tokens`` は非推奨のため指定しない。
  （必要なら ``max_completion_tokens`` を使う）
ここではすべてのプロンプトを ``user`` ロールで送信し、
temperature 等のパラメータは一切指定しないことで制約に対応している。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import requests

from core.config import get_settings as get_app_settings
from core.llm import generate_text
from core.schema import FieldDiff, MergeResult, PaperStructure

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public: GROBID API を使った PDF → TEI XML 変換
# ---------------------------------------------------------------------------

def extract_tei_xml_from_pdf_bytes(pdf_bytes: bytes) -> str:
    """PDF バイナリを GROBID の processFulltextDocument API に送信し TEI XML を返す。"""
    grobid_url = get_app_settings().grobid_url
    url = f"{grobid_url}/api/processFulltextDocument"
    resp = requests.post(
        url,
        files={"input": ("paper.pdf", pdf_bytes, "application/pdf")},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.text


# ---------------------------------------------------------------------------
# Public: Diff 計算 (Gateway層)
# ---------------------------------------------------------------------------

def _flatten_value(value: Any) -> str:
    """ネストされた値を比較用の文字列に変換する。"""
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value) if value is not None else ""


def compute_structure_diff(
    base: PaperStructure,
    proposed: PaperStructure,
) -> list[FieldDiff]:
    """base と proposed の PaperStructure を比較し、変更があるフィールドのみを返す。

    比較は ``model_dump()`` で辞書化した上で再帰的に行い、
    review_status / paper_id など自動管理フィールドは除外する。

    Parameters
    ----------
    base:
        現在の正典 PaperStructure。
    proposed:
        ユーザー提案の PaperStructure。

    Returns
    -------
    list[FieldDiff]
        変更のあるフィールドのリスト。変更なしの場合は空リスト。
    """
    _SKIP_FIELDS = {"paper_id", "review_status", "reviewer_notes"}

    def _recurse(
        base_dict: dict[str, Any],
        proposed_dict: dict[str, Any],
        prefix: str = "",
    ) -> list[FieldDiff]:
        diffs: list[FieldDiff] = []
        for key in base_dict:
            if key in _SKIP_FIELDS and not prefix:
                continue
            path = f"{prefix}.{key}" if prefix else key
            b_val = base_dict.get(key)
            p_val = proposed_dict.get(key)

            if isinstance(b_val, dict) and isinstance(p_val, dict):
                diffs.extend(_recurse(b_val, p_val, path))
            else:
                b_str = _flatten_value(b_val)
                p_str = _flatten_value(p_val)
                if b_str != p_str:
                    diffs.append(FieldDiff(
                        field_path=path,
                        base_value=b_str,
                        proposed_value=p_str,
                    ))
        return diffs

    return _recurse(base.model_dump(), proposed.model_dump())


# ---------------------------------------------------------------------------
# Public: LLM提案評価・マージ関数 (Gateway層)
# ---------------------------------------------------------------------------

def evaluate_and_merge_proposals(
    base_structure: PaperStructure,
    proposed_structure: PaperStructure,
) -> MergeResult:
    """Reasoningモデルを使って正典構造とユーザー提案をマージ・評価する。

    Diff ベースの最適化:
    1. base と proposed の差分を計算し、変更がなければ早期リターン。
    2. 変更があるフィールドのみを LLM に送信しトークン消費を削減。
    3. LLM が承認した変更のみを base 構造に適用し、未変更フィールドの
       ハルシネーションによる破損を防止する。

    方針: 「ジャンクの中の宝石」を最大限に拾い上げる。
    粗削りな提案であっても有用な洞察・補足・修正を積極的に取り込み、
    正典構造をより良いものに育てる。

    Parameters
    ----------
    base_structure:
        現在の正典 PaperStructure（マージのベースライン）。
    proposed_structure:
        ユーザーが提出した提案 PaperStructure。

    Returns
    -------
    MergeResult
        ``merged_structure`` (更新後の正典) と
        ``evaluation_reasoning`` (マージ方針・却下理由のテキスト) を含む。

    Notes on Reasoning models
    -------------------------
    gpt-5.2 等の Reasoning モデルは ``system`` ロールをサポートしないため、
    すべてのプロンプトを ``user`` ロールで送信する。
    temperature 等のパラメータも指定しない。
    """
    # --- Step 1: Diff 計算 ---
    diffs = compute_structure_diff(base_structure, proposed_structure)

    if not diffs:
        logger.info("No diff detected — returning base structure as-is")
        return MergeResult(
            merged_structure=base_structure.model_copy(),
            evaluation_reasoning="提案構造と正典構造の間に差分がないため、変更なし。",
        )

    logger.info("Diff detected: %d field(s) changed", len(diffs))

    # --- Step 2: 差分のみを LLM に送信 ---
    diff_lines: list[str] = []
    for d in diffs:
        diff_lines.append(
            f"- field: {d.field_path}\n"
            f"  base:     {d.base_value}\n"
            f"  proposed: {d.proposed_value}"
        )
    diff_text = "\n".join(diff_lines)

    prompt = (
        "あなたは論文構造レビュアーです。\n"
        "以下に、正典構造 (base) とユーザー提案 (proposed) の「差分のみ」を示します。\n"
        "差分に含まれないフィールドは変更されていないため、一切触れないでください。\n\n"
        "【マージ方針】\n"
        "提案はジャンクを含む可能性がありますが、その中にある「宝石」（有用な洞察・"
        "補足・修正・新しい視点）を最大限に拾い上げてください。\n"
        "たとえ粗削りな提案であっても、正典構造をより正確・豊かにする部分があれば"
        "積極的に取り込んでください。\n"
        "一方、誤り・無関係・冗長な部分は却下し、その理由を明記してください。\n\n"
        f"--- 差分フィールド ({len(diffs)} 件) ---\n{diff_text}\n\n"
        "【出力形式】\n"
        "各差分フィールドについて、以下の JSON 配列で回答してください:\n"
        '[\n  {"field_path": "...", "action": "accept" or "reject", "final_value": "...", "reason": "..."}\n]\n'
        "action が accept の場合: final_value に採用する値を入れてください（proposed そのままでも base との折衷でも可）。\n"
        "action が reject の場合: final_value は空文字で構いません。base の値が維持されます。\n"
        "JSON 配列のみで回答してください。"
    )

    raw = generate_text(messages=[{"role": "user", "content": prompt}])

    # --- Step 3: LLM 結果をパースして base に差分適用 ---
    decisions = _parse_diff_decisions(raw)
    merged_dict = base_structure.model_dump()
    reasoning_parts: list[str] = []

    for d in diffs:
        decision = decisions.get(d.field_path)
        if decision and decision.get("action") == "accept":
            final_value = decision.get("final_value", d.proposed_value)
            _set_nested_value(merged_dict, d.field_path, final_value)
            reason = decision.get("reason", "")
            reasoning_parts.append(f"[ACCEPT] {d.field_path}: {reason}")
        else:
            reason = decision.get("reason", "差分なし or LLM 未回答") if decision else "LLM 未回答"
            reasoning_parts.append(f"[REJECT] {d.field_path}: {reason}")

    # paper_id は正典のものを必ず引き継ぐ
    merged_dict["paper_id"] = base_structure.paper_id
    merged_structure = PaperStructure.model_validate(merged_dict)

    return MergeResult(
        merged_structure=merged_structure,
        evaluation_reasoning="\n".join(reasoning_parts),
    )


def _parse_diff_decisions(raw: str) -> dict[str, dict[str, str]]:
    """LLM の差分判定レスポンスを field_path → decision dict にパースする。"""
    match = re.search(r"\[[\s\S]*\]", raw)
    if not match:
        return {}
    try:
        items = json.loads(match.group())
    except json.JSONDecodeError:
        return {}
    result: dict[str, dict[str, str]] = {}
    for item in items:
        if isinstance(item, dict) and "field_path" in item:
            result[item["field_path"]] = item
    return result


def _set_nested_value(d: dict[str, Any], path: str, value: Any) -> None:
    """ドット区切りパスで辞書のネストされた値を設定する。

    値の型を元の構造に合わせて復元する（list/dict は JSON パース試行）。
    """
    keys = path.split(".")
    for key in keys[:-1]:
        d = d.setdefault(key, {})
    last_key = keys[-1]
    original = d.get(last_key)

    # 元の値が list/dict で、新しい値が str の場合は JSON パースを試行
    if isinstance(original, (list, dict)) and isinstance(value, str):
        try:
            parsed = json.loads(value)
            d[last_key] = parsed
            return
        except (json.JSONDecodeError, TypeError):
            pass
    d[last_key] = value
