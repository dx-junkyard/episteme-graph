"""カートリッジの「形の宣言」と適合事実（概念レジストリ P3-7 / §8）。

正本: ``docs/features/concept_registry_design.md`` §8。親文書の診断 F-8
（``particle_physics`` は実質フレーバー物理用で、名前と実内容が乖離しており、
分野との適合が入口で提示されない）への是正。

``backend/cartridges/<id>/shape.json``（任意・無ければ従来どおり）が宣言するのは:

```json
{"covers": ["..."], "does_not_cover": ["..."],
 "expects": {"entry_types": [...], "component_types": [...], "claim_types": [...]},
 "atlas_domain_key": "particle_physics"}
```

不変条項（本モジュールに掛かるもの）:

- **KR1 A層非改変**: ``src/episteme_graph/agents/`` はこのファイルを読まない
  （agent の入力は不変）。読むのは backend 側のこのモジュールだけ。
- **KR5 決定論・非LLM**: ``core.llm`` を import しない。照合は
  :func:`core.atlas_gaps.schema.normalize_label` で正規化したうえで、
  ``src/episteme_graph/agents/alias_matching.py``（P0-2 の正本）の**語境界付き**
  一致だけを使う（``str in str`` の部分一致は F-7 の再発になる）。
- **KR6 数値を見せない**: 一致件数・スコアを返さない。返すのは**名前の列挙**と事実文。
- **KR8 閉世界の正直さ**: 「この分野の論文ではない」と断じない。言えるのは
  「この論文の解析で、この分野の地図に配置があった / 無かった」という事実だけ。
  材料が無ければ「まだ解析されていないため、適合の事実はありません」。

FastAPI を import しない（開発ルール2 / core/ 共通ルール）。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import text as sa_text

from core.atlas_gaps.schema import normalize_label
from core.schema import CLAIM_TYPES, COMPONENT_TYPES
from episteme_graph.agents.alias_matching import text_mentions_alias

logger = logging.getLogger(__name__)

__all__ = [
    "FACT_NO_MATERIAL",
    "FACT_NO_PLACEMENT",
    "FACT_NO_SHAPE",
    "FACT_PLACED",
    "SHAPE_FILENAME",
    "fit_facts",
    "load_shape",
    "validate_shape",
]

SHAPE_FILENAME = "shape.json"

#: 適合の事実文（固定文・言い換えない）。
FACT_NO_SHAPE = "この分野には、扱う範囲の宣言（shape.json）がありません。"
FACT_NO_MATERIAL = "この論文はまだ解析されていないため、適合の事実はありません。"
FACT_PLACED = "この論文には、この分野の地図への配置があります。"
FACT_NO_PLACEMENT = "この論文には、この分野の地図への配置がありません。"

#: 列挙の上限（読み手が1画面で読める長さ。件数は出さない）。
_MAX_LISTED_TERMS = 8

#: 概念語として走査する artifact の本文の上限（プロンプト予算と同じ考え方の定数）。
_MAX_SCAN_CHARS = 20000

#: ``expects`` のキーと、その語彙の正本。
_EXPECTS_VOCABULARIES: dict[str, tuple[str, ...]] = {
    "component_types": COMPONENT_TYPES,
    "claim_types": CLAIM_TYPES,
}


# ---------------------------------------------------------------------------
# 宣言の読み込みと検証
# ---------------------------------------------------------------------------


def _cartridges_root() -> Path:
    """``backend/cartridges/`` の絶対パス（``core/cartridges.py`` と同じ解決）。"""
    from core.cartridges import _cartridges_root as resolve  # 局所 import（循環回避）

    return resolve()


def load_shape(cartridge_id: str | None) -> Optional[dict]:
    """``<cartridge>/shape.json`` を読む。無ければ ``None``（従来どおりの動作）。

    JSON が壊れている / dict でない場合も ``None`` を返して warning を出す
    （宣言の不備で解析の入口を止めない = fail-soft）。
    """
    key = str(cartridge_id or "").strip()
    if not key or "/" in key or "\\" in key or key.startswith("."):
        return None
    path = _cartridges_root() / key / SHAPE_FILENAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        logger.warning("cartridge shape: failed to parse %s", path, exc_info=True)
        return None
    if not isinstance(data, dict):
        logger.warning("cartridge shape: %s must be a JSON object", path)
        return None
    return data


def _entry_type_vocabulary() -> tuple[str, ...]:
    """``LIBRARY_ENTRY_TYPES``（担当 A が ``core/schema.py`` に足す語彙）。

    まだ無い環境では検証をスキップする（語彙表が無いことを「語彙外」と誤報しない）。
    """
    try:
        from core import schema as core_schema

        values = getattr(core_schema, "LIBRARY_ENTRY_TYPES", None)
        if values:
            return tuple(str(v) for v in values)
    except Exception:  # noqa: BLE001
        return ()
    return ()


def validate_shape(shape: Any) -> list[str]:
    """形の宣言の検証。**warning 文のリスト**を返す（例外を投げない）。

    起動時に ``main.py`` の lifespan から fail-open で 1 回呼ぶ（help_kb の
    validator と同じ作法）。語彙外の ``expects`` は「宣言が語彙とずれている」
    という運用上の警告であって、解析を止める理由ではない。
    """
    if shape is None:
        return []
    if not isinstance(shape, dict):
        return ["shape.json は JSON オブジェクトである必要があります。"]

    violations: list[str] = []
    for key in ("covers", "does_not_cover"):
        value = shape.get(key)
        if value is not None and not isinstance(value, list):
            violations.append(f"shape.json の {key} は文字列の配列である必要があります。")

    expects = shape.get("expects")
    if expects is not None and not isinstance(expects, dict):
        return violations + ["shape.json の expects はオブジェクトである必要があります。"]

    vocabularies = dict(_EXPECTS_VOCABULARIES)
    entry_types = _entry_type_vocabulary()
    if entry_types:
        vocabularies["entry_types"] = entry_types

    for key, vocabulary in vocabularies.items():
        declared = (expects or {}).get(key)
        if declared is None:
            continue
        if not isinstance(declared, list):
            violations.append(f"shape.json の expects.{key} は配列である必要があります。")
            continue
        allowed = set(vocabulary)
        unknown = sorted({str(v) for v in declared if str(v) not in allowed})
        if unknown:
            violations.append(
                f"shape.json の expects.{key} に語彙表に無い値があります: " + ", ".join(unknown)
            )
    return violations


def validate_all_shapes() -> list[str]:
    """同梱カートリッジすべての形の宣言を検証する（起動時 validator の入口）。"""
    violations: list[str] = []
    root = _cartridges_root()
    if not root.is_dir():
        return violations
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        shape = load_shape(entry.name)
        if shape is None:
            continue
        violations.extend(f"[{entry.name}] {v}" for v in validate_shape(shape))
    return violations


# ---------------------------------------------------------------------------
# 適合事実
# ---------------------------------------------------------------------------


def _terms(shape: dict, key: str) -> list[str]:
    values = shape.get(key)
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for value in values:
        term = str(value or "").strip()
        if term and term not in out:
            out.append(term)
    return out


def _collect_concept_text(artifacts: dict | None) -> str:
    """``paper_skeleton`` / ``thesis_reconstruction`` artifact の概念語の走査対象。

    抽出するのは agent が**合成した文**とラベルだけ（confidence / reason のような
    メタは入れない）。走査対象は決定論的な順で組み立て、上限で切る。
    """
    if not isinstance(artifacts, dict):
        return ""

    parts: list[str] = []

    def _push(value: Any) -> None:
        if isinstance(value, str):
            text = value.strip()
            if text:
                parts.append(text)
        elif isinstance(value, dict):
            _push(value.get("text"))
            _push(value.get("label"))
            _push(value.get("summary"))
        elif isinstance(value, list):
            for item in value:
                _push(item)

    skeleton = artifacts.get("paper_skeleton")
    if isinstance(skeleton, dict):
        _push(skeleton.get("title"))
        for key in ("paper_goal", "central_question", "headline_claim"):
            _push(skeleton.get(key))
        _push(skeleton.get("supporting_subclaims"))
        for block in skeleton.get("logical_blocks") or []:
            if isinstance(block, dict):
                _push(block.get("label"))
                _push(block.get("summary"))

    thesis = artifacts.get("thesis_reconstruction")
    if isinstance(thesis, dict):
        for key in ("central_question", "headline_claim", "paper_goal"):
            _push(thesis.get(key))
        _push(thesis.get("central_thesis"))
        for section in thesis.get("support_structure") or []:
            if isinstance(section, dict):
                _push(section.get("label"))
                _push(section.get("entries"))
            else:
                _push(section)
        _push(thesis.get("alternative_theses"))

    return "\n".join(parts)[:_MAX_SCAN_CHARS]


def _matched_terms(terms: list[str], haystack: str) -> list[str]:
    """宣言の語のうち、概念語に**語として**現れたものを宣言順で返す。

    比較は ``normalize_label``（NFKC + casefold + 空白畳み）で正規化してから、
    ``alias_matching`` の語境界付きパターンで行う（``SM`` が ``cosmological`` に
    当たる F-7 の再発防止。P0-2 の規律をそのまま流用する）。
    """
    if not terms or not haystack:
        return []
    normalized_haystack = normalize_label(haystack)
    matched: list[str] = []
    for term in terms:
        normalized_term = normalize_label(term)
        if not normalized_term:
            continue
        if text_mentions_alias(normalized_haystack, normalized_term):
            matched.append(term)
        if len(matched) >= _MAX_LISTED_TERMS:
            break
    return matched


def _unplaced_reason(artifacts: dict | None, domain_key: str) -> str:
    """採用 run の ``landscape_placement`` artifact から当該ドメインの理由を取る。"""
    if not isinstance(artifacts, dict) or not domain_key:
        return ""
    payload = artifacts.get("landscape_placement")
    if not isinstance(payload, dict):
        return ""
    for item in payload.get("unplaced_domains") or []:
        if isinstance(item, dict) and str(item.get("domain_key") or "") == domain_key:
            return str(item.get("reason") or "").strip()
    return ""


def _has_live_placement(session: Any, *, document_id: str, domain_key: str) -> Optional[bool]:
    """``landscape_placements`` の live 行の有無。判定不能なら ``None``。"""
    if session is None or not document_id or not domain_key:
        return None
    try:
        row = session.execute(
            sa_text(
                """
                SELECT 1
                  FROM landscape_placements
                 WHERE document_id = CAST(:document_id AS uuid)
                   AND domain_key = :domain_key
                   AND status <> 'superseded'
                 LIMIT 1
                """
            ),
            {"document_id": document_id, "domain_key": domain_key},
        ).fetchone()
    except Exception:  # noqa: BLE001 — 配置が読めなくても他の事実は返す（fail-soft）
        logger.warning("cartridge shape: placement lookup failed", exc_info=True)
        return None
    return row is not None


def fit_facts(
    session: Any,
    *,
    cartridge_id: str,
    document_id: str,
    artifacts: dict | None,
) -> dict:
    """分野と論文の「適合の事実」（§8）。数値なし・事実文と名前の列挙のみ。

    Args:
        session: DB セッション（``landscape_placements`` の live 行の有無だけを読む）。
        cartridge_id: 分野（= ``domain_key`` と同一名前空間）。
        document_id: 論文（``documents.id``）。
        artifacts: ``persistence.document_run_artifacts(document_id, policy="adopted")``
            の戻り値（``{stage: payload}``）。``None`` なら「まだ解析されていない」。

    Returns:
        ``{"cartridge_id", "available", "facts": [...], "covered_terms": [...],
        "out_of_scope_terms": [...], "atlas_domain_key"}``。
    """
    key = str(cartridge_id or "").strip()
    shape = load_shape(key)
    result: dict[str, Any] = {
        "cartridge_id": key,
        "available": False,
        "facts": [],
        "covered_terms": [],
        "out_of_scope_terms": [],
        "atlas_domain_key": "",
    }
    if shape is None:
        result["facts"] = [FACT_NO_SHAPE]
        return result

    domain_key = str(shape.get("atlas_domain_key") or key).strip()
    result["atlas_domain_key"] = domain_key

    facts: list[str] = []

    # (b) 配置の有無（この論文の解析で、この分野の地図に置けたか）。
    placed = _has_live_placement(session, document_id=str(document_id or ""), domain_key=domain_key)
    if placed is True:
        facts.append(FACT_PLACED)
    elif placed is False:
        facts.append(FACT_NO_PLACEMENT)
        # (a) 置けなかった理由（agent が申告した reason。無ければ何も足さない）。
        reason = _unplaced_reason(artifacts, domain_key)
        if reason:
            facts.append("解析での理由: " + reason)

    # (c)(d) 宣言の語と論文の概念語の一致（名前の列挙のみ・件数を出さない）。
    haystack = _collect_concept_text(artifacts)
    covered = _matched_terms(_terms(shape, "covers"), haystack)
    out_of_scope = _matched_terms(_terms(shape, "does_not_cover"), haystack)
    result["covered_terms"] = covered
    result["out_of_scope_terms"] = out_of_scope
    if covered:
        facts.append("この分野が扱うと宣言している主題のうち、この論文に現れたもの: " + "、".join(covered))
    if out_of_scope:
        facts.append(
            "この分野が扱わないと宣言している主題のうち、この論文に現れたもの: " + "、".join(out_of_scope)
        )

    if not facts:
        facts.append(FACT_NO_MATERIAL)
        result["facts"] = facts
        return result

    result["available"] = True
    result["facts"] = facts
    return result
