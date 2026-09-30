"""同じ構造のモジュールを持つ論文（``GET .../theory-modules/related`` の core・§13.7）。

設計正本: ``docs/features/theory_module_layer_design.md`` §13.7（不変条項 TM11〜TM15）。

保存行（``knowledge_theory_modules_live``）どうしで**構造の指紋が完全一致**する他 document を、
当該 document の外枠行ごとに集める。「同じ構造」は事実（工程の型と受け渡す式の形が同じ）で
あって同一性の候補ではない（TM13）ので、候補の status は出さず、未確定の候補も確定済みも
同じ扱いで列挙する。ただし**教員が見送った組は出さない**:

- この指紋の構造エントリ（``candidate_key`` = ``core.library.schema.build_structural_candidate_key``）
  が ``dismissed`` なら、そのモジュールの相手を全部外す（構造エントリの見送り）。
- 当該モジュールから構造エントリへのリンクが ``rejected`` なら、そのモジュールの相手を全部外す。
- 相手モジュールから構造エントリへのリンクが ``rejected`` なら、その相手だけを外す。

可視性（相手 document を閲覧できるか）は **route 層が判定して** ``can_view`` として渡す
（core は FastAPI も ``services`` も import しない）。応答はタイトルだけで、数値は
``hidden``（真偽値）以外に出さない。指紋は DTO に載せない（TM12）— ガードレールが
``core.theory_modules.schema.FORBIDDEN_KEYS`` を応答全体へ再帰走査する。

本モジュールは**純関数だけ**を置く（``core/theory_modules/`` は FastAPI / sqlalchemy / SQL を
持たない = TM2 のガードレール）。DB の読み出し（live ビューのみ）は
``core/library/structural_matches.py`` にあり、route が両者をつなぐ。LLM 0 回・書き込みなし・
監査なし。
"""

from __future__ import annotations

from typing import Callable, Mapping

# ---------------------------------------------------------------------------
# 事実文（TM8。数値を入れない）
#
# 設計書 §13.7 は「定数は core/theory_modules/schema.py」とするが、schema.py は保存ステージの
# 担当が同時に編集しているため、related に固有の 3 文は本モジュールに置く（逸脱は §12.5）。
# ---------------------------------------------------------------------------

FACT_RELATED_NOT_SAVED = (
    "この教材の理論モジュールはまだ保存されていないため、同じ構造の論文を照合できません。"
    "再解析すると照合できます。"
)
FACT_RELATED_RULE_VERSION_STALE = (
    "保存済みの理論モジュールは以前の規則で組んだもので、表示中のモジュールと対応が取れません。"
    "再解析すると照合できます。"
)
FACT_RELATED_HIDDEN = "閲覧できない論文にも、同じ構造のモジュールがあります。"
#: 保存行はあるが、どの外枠モジュールにも同じ構造の相手が見つからないとき（閉世界の言明。
#: 列挙が空のまま黙らない — 第 14 周 brain-te03 seq9）。
FACT_RELATED_NONE = "このコーパスの中では、同じ構造のモジュールを持つ他の論文は見つかっていません。"

#: タイトルが空の教材を列挙するときの表示（空文字で黙って欠かさない）。
UNTITLED_DOCUMENT_LABEL = "題名のない教材"

_LEVEL_OUTER = "outer"


# ---------------------------------------------------------------------------
# 組み立て（純関数）
# ---------------------------------------------------------------------------


def build_related_payload(
    document_id: str,
    *,
    own_rows: list[dict],
    matches: list[dict],
    decisions: Mapping[str, dict],
    rule_version: str,
    can_view: Callable[[str], bool],
    titles: Mapping[str, str],
    candidate_key_for: Callable[[str], str],
) -> dict:
    """§13.7 の応答を組み立てる（入力を mutate しない・数値キーなし・指紋なし）。

    Args:
        own_rows: ``structural_matches.load_live_modules`` の結果（当該 document の live 行）。
        matches: ``structural_matches.load_matching_modules`` の結果（他 document の一致行）。
        decisions: ``structural_matches.load_structural_decisions`` の結果。
        rule_version: 現在の規則の版（``core.theory_modules.schema.RULE_VERSION``）。
        can_view: 相手 document の閲覧可否（route 層の fail-closed 判定）。
        titles: 閲覧できる相手 document のタイトル。
        candidate_key_for: 指紋 → 構造エントリの ``candidate_key``。
    """
    if not own_rows:
        return _unavailable(document_id, FACT_RELATED_NOT_SAVED)
    if any(row.get("rule_version") != rule_version for row in own_rows):
        return _unavailable(document_id, FACT_RELATED_RULE_VERSION_STALE)

    matches_by_fingerprint: dict[str, list[dict]] = {}
    for match in matches:
        matches_by_fingerprint.setdefault(match.get("structure_fingerprint", ""), []).append(match)

    hidden = False
    modules: list[dict] = []
    checked_keys: list[str] = []
    for row in own_rows:
        if row.get("level") != _LEVEL_OUTER:
            continue
        documents: list[dict] = []
        fingerprint = row.get("structure_fingerprint") or ""
        if row.get("identity_eligible") and fingerprint:
            decision = decisions.get(candidate_key_for(fingerprint)) or {}
            rejected = decision.get("rejected_module_ids") or set()
            if not decision.get("dismissed") and row.get("id") not in rejected:
                seen: set[str] = set()
                for match in matches_by_fingerprint.get(fingerprint, []):
                    if match.get("id") in rejected:
                        continue
                    other = match.get("document_id") or ""
                    if not other or other == document_id or other in seen:
                        continue
                    seen.add(other)
                    if not can_view(other):
                        hidden = True
                        continue
                    documents.append(
                        {"title": titles.get(other) or UNTITLED_DOCUMENT_LABEL}
                    )
        key = row.get("agent_module_key") or ""
        if key and key not in checked_keys:
            checked_keys.append(key)
        if not documents:
            continue
        # 外枠 1 つ = 1 行（同じ module_key の保存行が複数あっても 1 行に畳む。第 15 周 te-02/03）。
        existing = next((m for m in modules if key and m["module_key"] == key), None)
        if existing is not None:
            for doc in documents:
                if doc not in existing["documents"]:
                    existing["documents"].append(doc)
            continue
        modules.append({"module_key": key, "documents": documents})

    facts: list[str] = []
    if hidden:
        facts.append(FACT_RELATED_HIDDEN)
    elif not modules:
        # 相手ゼロなら区画全体で事実文 1 行（空欄の行を並べない — 第 15 周 te-02 seq17）。
        facts.append(FACT_RELATED_NONE)
    return {
        "document_id": document_id,
        "available": True,
        "facts": facts,
        # 相手のある外枠モジュールだけ（内側・相手なしは行にしない）。
        "modules": modules,
        # 照合した外枠の module_key（UI が「照合したが相手なし」と「対応が取れない」を分ける）。
        "checked_module_keys": checked_keys,
        "hidden": hidden,
    }


def _unavailable(document_id: str, fact: str) -> dict:
    return {
        "document_id": document_id,
        "available": False,
        "facts": [fact],
        "modules": [],
        "checked_module_keys": [],
        "hidden": False,
    }


def eligible_fingerprints(own_rows: list[dict], *, rule_version: str) -> list[str]:
    """照合に使う指紋（外枠・候補の対象・現在の版・空でない）。重複は除く・順序保持。"""
    out: list[str] = []
    for row in own_rows:
        fingerprint = row.get("structure_fingerprint") or ""
        if (
            row.get("level") == _LEVEL_OUTER
            and row.get("identity_eligible")
            and row.get("rule_version") == rule_version
            and fingerprint
            and fingerprint not in out
        ):
            out.append(fingerprint)
    return out
