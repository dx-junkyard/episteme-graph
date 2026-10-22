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

from typing import Any, Callable, Mapping

from core.element_vocab import operation_label as _vocab_operation_label
from core.theory_modules.schema import UNCLASSIFIED_EDGE_TYPE, contains_internal_id, plain_math

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

#: 照合の相手として名指しされた論文（レーダーの起点など）の保存行が無いとき（§21 C — 第 4 波。
#: 「見つかっていません」を言う前に、照合できなかった側を名指しする。題名は ``{title}``）。
FACT_RELATED_PEER_UNSAVED = (
    "論文「{title}」の理論モジュールはまだ保存されていないため、この論文とは照合していません。"
)

#: 外枠モジュールはあるが、どれも照合の対象（``identity_eligible`` = 成員が下限以上・汎用でない
#: 工程の型が 2 種以上）に入らないとき（§22 A — 第 5 波。「照合したが相手なし」と畳まない）。
FACT_RELATED_NO_ELIGIBLE = (
    "この論文には、照合の対象になる構造を持つモジュールがありません"
    "（どのモジュールも、工程が少ないか、定義などの単一の工程の型だけでできているため）。"
)
#: 照合の対象になったモジュールと、対象外のモジュールが混ざっているとき（対象外をラベルで名指しする。
#: ``{labels}`` は「、」区切り・最大 ``INELIGIBLE_LABELS_MAX`` 件 + 「ほか」）。
FACT_RELATED_INELIGIBLE_LISTED = (
    "次のモジュールは、工程が少ないか単一の工程の型だけでできているため、照合の対象にしていません: {labels}"
)
#: 対象外モジュールの名指しの上限（数は出さない。超えたら「ほか」）。
INELIGIBLE_LABELS_MAX = 5

#: 近い構造（§22 B）の対の上限と、共有を要求する汎用でない工程の型の種類の下限。
NEAR_STRUCTURE_MAX_PAIRS = 5
NEAR_STRUCTURE_MIN_SHARED_KINDS = 2
#: 近い構造の区画の読み方（表示専用・同一性の候補ではない — TM13）。
FACT_NEAR_STRUCTURE_NOTE = (
    "工程の形が完全には一致しないが、汎用でない工程の型を 2 種類以上共有するモジュールの組です。"
    "照合は工程の型だけで、記号・概念の対応は見ていません。同じ部品の候補にはしていません。"
)

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
    unsaved_peer_titles: tuple[str, ...] = (),
    peer_outer_rows: list[dict] | None = None,
    title_notes: Mapping[str, str] | None = None,
) -> dict:
    """§13.7 の応答を組み立てる（入力を mutate しない・数値キーなし・指紋なし）。

    ``unsaved_peer_titles``: 照合の相手として名指しされた（閲覧できる）論文のうち、理論
    モジュールが保存されていないものの題名（§21 C）。相手が見つからないとき、閉世界の
    「見つかっていません」の**前に**その事実を 1 行ずつ置く。

    Args:
        own_rows: ``structural_matches.load_live_modules`` の結果（当該 document の live 行）。
        matches: ``structural_matches.load_matching_modules`` の結果（他 document の一致行）。
        decisions: ``structural_matches.load_structural_decisions`` の結果。
        rule_version: 現在の規則の版（``core.theory_modules.schema.RULE_VERSION``）。
        can_view: 相手 document の閲覧可否（route 層の fail-closed 判定）。
        titles: 閲覧できる相手 document のタイトル。
        candidate_key_for: 指紋 → 構造エントリの ``candidate_key``。
        title_notes: 相手 document の題名に添える事実文（題名がファイル名のまま・arXiv の題名を
            表示している / 引けていない — ``core.paper_discovery.radar.display_title_for``）。
        peer_outer_rows: 他 document の live 外枠行（``label`` / ``member_steps`` /
            ``structure_fingerprint`` / ``document_id`` 付き）。近い構造（§22 B）の材料で、
            ``None`` なら ``near_structure`` は空。同一性の候補にはしない（TM13）。
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
    ineligible_keys: list[str] = []
    ineligible_labels: list[str] = []
    for row in own_rows:
        if row.get("level") != _LEVEL_OUTER:
            continue
        key = row.get("agent_module_key") or ""
        if not row.get("identity_eligible"):
            # 照合の対象外（成員が下限未満・汎用でない工程の型が 2 種未満）。照合した集合に
            # 入れない（§22 A — completion-defined-by-proxy）。
            if key and key not in ineligible_keys and key not in checked_keys:
                ineligible_keys.append(key)
                label = _display_label(row)
                if label and label not in ineligible_labels:
                    ineligible_labels.append(label)
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
    if not checked_keys:
        # 照合の対象になる外枠が 1 つも無い（§22 A）。「見つかっていません」と言わない。
        facts.append(FACT_RELATED_NO_ELIGIBLE)
    elif hidden:
        facts.append(FACT_RELATED_HIDDEN)
    elif not modules:
        # 照合できなかった側を先に名指しする（§21 C — upstream-loss-masked-downstream）。
        for title in unsaved_peer_titles:
            facts.append(
                FACT_RELATED_PEER_UNSAVED.format(title=title or UNTITLED_DOCUMENT_LABEL)
            )
        # 相手ゼロなら区画全体で事実文 1 行（空欄の行を並べない — 第 15 周 te-02 seq17）。
        facts.append(FACT_RELATED_NONE)
    if checked_keys and ineligible_labels:
        shown = ineligible_labels[:INELIGIBLE_LABELS_MAX]
        text = "、".join(shown) + ("ほか" if len(ineligible_labels) > len(shown) else "")
        facts.append(FACT_RELATED_INELIGIBLE_LISTED.format(labels=text))
    near = build_near_structure(
        own_rows,
        peer_outer_rows or [],
        document_id=document_id,
        rule_version=rule_version,
        can_view=can_view,
        titles=titles,
        title_notes=title_notes,
    )
    return {
        "document_id": document_id,
        "available": True,
        "facts": facts,
        # 相手のある外枠モジュールだけ（内側・相手なしは行にしない）。
        "modules": modules,
        # 照合した外枠の module_key（UI が「照合したが相手なし」と「対応が取れない」を分ける）。
        "checked_module_keys": checked_keys,
        # 照合の対象外だった外枠の module_key（§22 A。UI が「対象外」を別の文で言う）。
        "ineligible_module_keys": ineligible_keys,
        # 近い構造（§22 B）。表示専用・同一性の候補ではない（TM13）・数値なし。
        "near_structure": near,
        "near_structure_note": FACT_NEAR_STRUCTURE_NOTE if near else "",
        "hidden": hidden,
    }


def _display_label(row: Mapping[str, Any]) -> str:
    """保存行の表示ラベル（内部 ID を含むものは出さない — TM10）。

    事実文に載せるラベルは**読み時に** ``schema.plain_math``（正本）で作り直す。保存行の
    ``visual_label`` は保存時の規則で落としたもので、規則を直しても旧行に残る（``textbfx`` /
    ``langle cdots rangle`` — judge-deep-07 ②）。``$…$`` 付きの ``label`` を先に読み、無ければ
    ``visual_label`` を同じ関数に通す（プレーンの文字列には何もしない）。
    """
    for key in ("label", "visual_label"):
        label = plain_math(str(row.get(key) or "").strip())
        if label and not contains_internal_id(label):
            return label
    return ""


def _operation_label(operation: str) -> str:
    op = str(operation or "").strip()
    if not op:
        return ""
    label = _vocab_operation_label(op)
    if label:
        return label
    head = op.lower().replace("-", "_").split("_", 1)[0]
    return _vocab_operation_label(head)


def _process_kinds(row: Mapping[str, Any]) -> dict[str, list[str]]:
    """``{汎用でない edge_type: [その型の工程の訳語, …]}``（保存行の ``member_steps`` から）。"""
    kinds: dict[str, list[str]] = {}
    steps = row.get("member_steps")
    for step in steps if isinstance(steps, list) else []:
        if not isinstance(step, Mapping) or step.get("generic"):
            continue
        edge_type = str(step.get("edge_type") or "").strip()
        if not edge_type or edge_type == UNCLASSIFIED_EDGE_TYPE:
            continue
        labels = kinds.setdefault(edge_type, [])
        label = _operation_label(str(step.get("operation") or ""))
        if label and label not in labels:
            labels.append(label)
    return kinds


def _near_pairs(
    own_rows: list[dict],
    peer_outer_rows: list[dict],
    *,
    document_id: str,
    rule_version: str,
) -> list[tuple]:
    """近い構造の対の素材 ``(-共有種類, -珍しさ, 起点の順, 相手 document, 起点ラベル, 相手ラベル, 訳語, module_key)``。

    珍しさ = 共有する工程の型ごとの「その型を外枠に持つ論文の数（起点を含む）」の逆数の和。
    どの論文にも現れる型（定義・結果を導出）だけの共有を、珍しい型の共有より後ろに並べる
    （judge-deep-07 ④-1: 主題の離れた論文との対が先頭に来ていた）。並べ替えにだけ使い、
    DTO にも文面にも出さない（TM6）。
    """
    own = [
        row for row in own_rows
        if row.get("level") == _LEVEL_OUTER and row.get("rule_version") == rule_version
    ]
    kind_documents: dict[str, set[str]] = {}
    for row in own:
        for edge_type in _process_kinds(row):
            kind_documents.setdefault(edge_type, set()).add(document_id)
    for peer in peer_outer_rows:
        other = str(peer.get("document_id") or "")
        if not other or peer.get("rule_version") != rule_version:
            continue
        if peer.get("level", _LEVEL_OUTER) != _LEVEL_OUTER:
            continue
        for edge_type in _process_kinds(peer):
            kind_documents.setdefault(edge_type, set()).add(other)
    pairs: list[tuple] = []
    seen: set[tuple[str, str, str]] = set()
    for own_index, row in enumerate(own):
        seed_label = _display_label(row)
        seed_kinds = _process_kinds(row)
        if not seed_label or len(seed_kinds) < NEAR_STRUCTURE_MIN_SHARED_KINDS:
            continue
        seed_fp = str(row.get("structure_fingerprint") or "")
        for peer in peer_outer_rows:
            other = str(peer.get("document_id") or "")
            if not other or other == document_id:
                continue
            if peer.get("level", _LEVEL_OUTER) != _LEVEL_OUTER or peer.get("rule_version") != rule_version:
                continue
            if seed_fp and str(peer.get("structure_fingerprint") or "") == seed_fp:
                continue  # 完全一致は modules 側（同一性候補の経路）で扱う
            peer_label = _display_label(peer)
            if not peer_label:
                continue
            peer_kinds = _process_kinds(peer)
            # 起点モジュールの工程の順で並べる（訳語も起点側の工程から取る）。
            shared = [edge_type for edge_type in seed_kinds if edge_type in peer_kinds]
            if len(shared) < NEAR_STRUCTURE_MIN_SHARED_KINDS:
                continue
            labels: list[str] = []
            for edge_type in shared:
                for label in seed_kinds.get(edge_type, []):
                    if label not in labels:
                        labels.append(label)
            if not labels:
                continue  # 訳語の無い型だけの共有は言わない（生の型名を出さない）
            pair_key = (seed_label, other, peer_label)
            if pair_key in seen:
                continue
            seen.add(pair_key)
            rarity = sum(1.0 / max(1, len(kind_documents.get(edge_type, ()))) for edge_type in shared)
            pairs.append((
                -len(shared), -round(rarity, 9), own_index, other, seed_label, peer_label, labels,
                str(row.get("agent_module_key") or ""),
            ))
    return pairs


def near_structure_peer_documents(
    own_rows: list[dict], peer_outer_rows: list[dict], *, document_id: str, rule_version: str
) -> list[str]:
    """近い構造の対に現れる相手 document（タイトル引きの材料。可視性は呼び出し側）。"""
    out: list[str] = []
    for pair in _near_pairs(own_rows, peer_outer_rows, document_id=document_id, rule_version=rule_version):
        if pair[3] not in out:
            out.append(pair[3])
    return out


def build_near_structure(
    own_rows: list[dict],
    peer_outer_rows: list[dict],
    *,
    document_id: str,
    rule_version: str,
    can_view: Callable[[str], bool],
    titles: Mapping[str, str],
    title_notes: Mapping[str, str] | None = None,
) -> list[dict]:
    """近い構造の対（§22 B・純関数・表示専用）。

    指紋が完全一致しない外枠どうしで、汎用でない工程の型を ``NEAR_STRUCTURE_MIN_SHARED_KINDS``
    種類以上共有する対を、ラベルの列挙だけで返す（最大 ``NEAR_STRUCTURE_MAX_PAIRS`` 対）。
    接点の本数・重なりの率・指紋は出さない（TM6 / TM12）。**同一性の候補にはしない**（TM13 /
    O-6 従属）— 本関数は書き込みも候補キーの計算もしない。閲覧できない相手は黙って外す
    （名指しの材料にしない — fail-closed）。
    """
    notes = title_notes or {}
    rows: list[tuple] = []
    for neg_shared, neg_rarity, own_index, other, seed_label, peer_label, labels, module_key in _near_pairs(
        own_rows, peer_outer_rows, document_id=document_id, rule_version=rule_version
    ):
        if not can_view(other):
            continue
        title = titles.get(other) or UNTITLED_DOCUMENT_LABEL
        pair = {
            # 画面のモジュールと突き合わせるための読み時キー（指紋ではない — TM12）。
            "seed_module_key": module_key,
            "seed_module_label": seed_label,
            "peer_module_label": peer_label,
            "peer_document_title": title,
            "shared_operation_labels": labels,
        }
        note = str(notes.get(other) or "").strip()
        if note:
            pair["peer_document_title_note"] = note
        # 並び: 共有する汎用でない型の数 → 型の珍しさ → 題名 → 起点の順 → 相手のラベル（決定論）。
        rows.append((neg_shared, neg_rarity, title, own_index, peer_label, pair))
    rows.sort(key=lambda item: item[:5])
    return [item[5] for item in rows[:NEAR_STRUCTURE_MAX_PAIRS]]


def _unavailable(document_id: str, fact: str) -> dict:
    return {
        "document_id": document_id,
        "available": False,
        "facts": [fact],
        "modules": [],
        "checked_module_keys": [],
        "ineligible_module_keys": [],
        "near_structure": [],
        "near_structure_note": "",
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
