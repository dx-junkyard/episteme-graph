"""画面文脈アダプター — 学習チャットの解決器（Phase 4・設計書 §11.3）。

``sources`` は route が**権限ゲート付きの学習者射影**で組み立てて渡す。本モジュールは
渡された DTO しか見ない（DB も LLM も触らない = SA3）:

``sources = {``
    ``"element": {"element_type": ..., "context": <学習者射影 DTO>, "item": <evidence item>},``
    ``"ledger": <学習者向け台帳行>,``
    ``"landscape": <学習者向け landscape DTO（選択要素の論文に絞ったもの）>,``
``}``

規律（Phase 1 から継承し、学習側で強める）:

- **生テーブルを引かない**。学習者射影（``component_context`` / ``element_context`` /
  台帳の学習者向け投影 / ``landscape.projection.learner_landscape_dto``）が既に持つ
  遮断（数値除去・内部 ID 遮断・``ANY(:doc_ids)`` の scope 強制）を**再実装しない**。
- 数値を書かない（SA4 / LS4 / PN-4）。ガードレールはこのファイルに数値キーの名前が
  **文字列としても**現れないことを検査するので、キー名を書かない。
- 内部 ID を書かない。式は論文の印字番号（``eq_2_7`` 形は可読なので通す —
  ``learner_context_common`` の既定裁定）、主張は本文の先頭抜粋、図は caption。
- **出所ラベルを剥がさない**（§11.13-1 のオーナー判断）。AI 推定の配置は
  「AIによる推定（未確認）」を事実文の中に含めたまま渡す。
- 解決できないものは**黙って何も出さない**（「見えないものがある」と言わせない = SA2）。
- 入力を mutate しない・例外を外へ出さない。
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from core.display_projection import contains_internal_id as _display_internal_id
from core.element_vocab import claim_type_label, theory_stage_key, theory_stage_label
from core.label_vocab import ATLAS_PILL_NOTES, learner_atlas_wording
from core.learner_context_common import contains_internal_id, is_internal_id_label, safe_text
from core.simulation_bridge.schema import FACT_NOT_VERIFICATION, RUN_STATUS_SUCCEEDED
from core.text_excerpt import excerpt as _excerpt
from core.text_hygiene import strip_control_sequences

from ..registry import register
from ..schema import (
    EXPLANATION_STATUS_APPROVED_LABEL,
    EXPLANATION_STATUS_CANDIDATE_LABEL,
    LEARNER_EXPLANATION_SECTION_LABEL,
    LEARNING_DISCUSS_SCOPE_LABELS,
    LEARNING_ELEMENT_TYPE_LABELS,
    LEARNING_ELEMENT_TYPES,
    LEARNING_PERSONAL_MAP_SCREEN_FACT,
    LEARNING_PERSONAL_MAP_CENTER_TEMPLATE,
    LEARNING_PERSONAL_MAP_DOWNSTREAM_TEMPLATE,
    LEARNING_PERSONAL_MAP_FACT_LINE_TEMPLATE,
    LEARNING_PERSONAL_MAP_RANGE_DOCUMENT_TEMPLATE,
    LEARNING_PERSONAL_MAP_RANGE_FACT,
    LEARNING_PERSONAL_MAP_TOUCHED_MARK,
    LEARNING_PERSONAL_MAP_UPSTREAM_TEMPLATE,
    MAX_PERSONAL_MAP_DOCUMENTS,
    MAX_PERSONAL_MAP_DTO_FACTS,
    MAX_PERSONAL_MAP_NODES,
    LEARNING_ATLAS_NODE_FACT_TEMPLATE,
    LEARNING_ATLAS_NO_PAPERS_FACT,
    LEARNING_ATLAS_PANEL_LINE_TEMPLATE,
    LEARNING_ATLAS_PAPERS_TEMPLATE,
    LEARNING_ATLAS_PILL_NOTE_TEMPLATE,
    LEARNING_VIEW_MODE_LABELS,
    MAX_LEARNING_CLAIM_EXCERPT_CHARS,
    MAX_LEARNING_ELEMENT_FACTS,
    MAX_LEARNING_PLACEMENT_FACTS,
    MAX_LEARNING_RETRIEVED_CLAIM_CHARS,
    MAX_LEARNING_RETRIEVED_CLAIMS_PER_SOURCE,
    MAX_LEARNING_RETRIEVED_FACTS,
    MAX_LEARNING_SIMULATION_FACT_LINE_CHARS,
    MAX_LEARNING_SIMULATION_FACTS,
    MAX_LEARNING_SIMULATION_SYMBOL_CHARS,
    MAX_LEARNING_SIMULATION_SYMBOLS,
    MAX_LEARNING_SIMULATION_VECTOR_ITEMS,
    MAX_LEARNING_SUPPORT_ITEMS,
    MAX_LEARNING_VERIFICATION_FACTS,
    MAX_TEXT_CHARS,
    MORE_ITEMS_MARK,
    SCREEN_LEARNING,
    ScreenContext,
    infer_selection_kind,
    selected_simulation_run_id,
)

# ---------------------------------------------------------------------------
# 小さな純関数（graph_review.py と同じ作法）
# ---------------------------------------------------------------------------


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list:
    return [item for item in value] if isinstance(value, (list, tuple)) else []


def _text(value: Any, limit: int | None = None) -> str:
    """前後の空白を除いた文字列。``limit`` を超えるときは語境界・文境界で切る（IK-0393）。

    素のスライス（``text[:limit]``）は英文を語の途中で切り（``…indicating tha``）、
    プロンプトへ壊れた語を渡していた。切り詰めの正本は ``core.text_excerpt.excerpt``。
    """
    text = str(value or "").strip()
    if limit is not None and len(text) > limit:
        return _excerpt(text, limit)
    return text


def _safe(value: Any, limit: int | None = None) -> str:
    r"""内部 ID / 生 TeX が混ざった自由文はその欄ごと落とす（学習者射影の最後の砦）。

    遮断の述語は :func:`core.learner_context_common.safe_text` が正本
    （内部 ID + 生 TeX 判定）。ここで**新しい判定を作らない**。V-8 の実測では
    ``Equation (3.55) defines \delta P_{...}.`` のような claim 本文がそのまま
    学習者向けの事実文に出ていた（内部 ID の遮断はあったが TeX の遮断が無かった）。
    """
    text = _text(value, limit)
    if not text:
        return ""
    return safe_text(text)


def is_fragment_claim_text(value: Any) -> bool:
    """主張の本文が数値・記号の断片だけか（IK-0393 (c)）。

    ``≈9.3`` / ``(2)`` / ``B = 3`` のような断片は、主張行として A層に残っていても
    （P4 = 行は消さない）学習者向けの構造の手がかりにはしない。判定は「字母（ラテン
    文字・漢字・かな等、``str.isalpha``）が2字未満」だけ（短い日本語の主張や略語は残す）。
    """
    text = str(value or "").strip()
    return sum(1 for ch in text if ch.isalpha()) < 2


def _join(items: list[str], *, truncated: bool = False) -> str:
    joined = "・".join(items)
    return f"{joined}・{MORE_ITEMS_MARK}" if truncated else joined


def _quoted(items: list[str]) -> str:
    return "".join(f"「{item}」" for item in items)


def _capped(items: list[str], limit: int) -> tuple[list[str], bool]:
    return items[:limit], len(items) > limit


# ---------------------------------------------------------------------------
# kind "element" — 選択中のチップ1件の中身
# ---------------------------------------------------------------------------


def _selected_element(ctx: ScreenContext, sources: Mapping[str, Any]) -> tuple[str, dict]:
    """``(element_type, sources["element"])``。対象外なら ``("", {})``。"""
    if infer_selection_kind(ctx.selection) != "element":
        return "", {}
    payload = _dict(sources.get("element"))
    element_type = _text(ctx.selection.get("element_type")) or _text(
        payload.get("element_type")
    )
    if element_type not in LEARNING_ELEMENT_TYPES:
        return "", {}
    return element_type, payload


#: 確定した同一性リンクの事実文（IK-0920。新しい kind を足さず element 解決器が添える）。
SHARED_PART_CONFIRMED_FACT = "教員がこの部品を共通部品「{name}」と同じものとして確かめています"
#: 同じ共通部品と確かめられた部品を持つ、学生が読めるほかの論文（題名の列挙・件数なし）。
SHARED_PART_OTHER_DOCUMENTS_FACT = "共通部品「{name}」と同じものとして教員が確かめた部品は、{titles}にもあります"


def _shared_part_facts(context: Mapping[str, Any]) -> list[str]:
    """``shared_part``（確定した同一性リンク → active な共通部品）を事実 1〜2 行にする。"""
    shared = _dict(_dict(context).get("shared_part"))
    entry = _dict(shared.get("entry"))
    name = _safe(entry.get("name"), MAX_TEXT_CHARS)
    if not name:
        return []
    facts = [SHARED_PART_CONFIRMED_FACT.format(name=name)]
    titles = [
        _safe(_dict(doc).get("title"), MAX_TEXT_CHARS)
        for doc in _list(entry.get("other_documents"))
    ]
    titles = [title for title in titles if title]
    if titles:
        shown, truncated = _capped(titles, MAX_LEARNING_SUPPORT_ITEMS)
        quoted = "・".join(f"論文『{t}』" for t in shown)
        if truncated or entry.get("other_documents_truncated") is True:
            quoted = f"{quoted}・{MORE_ITEMS_MARK}"
        facts.append(SHARED_PART_OTHER_DOCUMENTS_FACT.format(name=name, titles=quoted))
    return facts


#: 第 25 周 第 10 波（製品頭脳 B 4315・IK-0755 の AI 側）: 解析が英語で生成した文（役割・要約・前提）は
#: 保存データのままなので、出所を添えて渡す（学習者には日本語で言い換えさせる。文を書き換えない）。
GENERATED_ENGLISH_NOTE = "（この文は論文の解析が英語で生成したものです。学習者には日本語で言い換えて伝えてください）"


def _generated_english_note(text: str) -> str:
    return GENERATED_ENGLISH_NOTE if _is_english_sentence(text) else ""


def _component_facts(context: Mapping[str, Any]) -> list[str]:
    instance = _dict(_dict(context).get("instance"))
    component = _dict(instance.get("component"))
    label = _safe(component.get("label"), MAX_TEXT_CHARS)
    if not label:
        return []

    facts: list[str] = []
    in_paper = _dict(instance.get("in_paper"))
    title = _safe(_dict(in_paper.get("document")).get("title"), MAX_TEXT_CHARS)
    head = f"学習者が選んでいるのは論理要素「{label}」です"
    if title:
        head = f"学習者が選んでいるのは論理要素「{label}」で、論文『{title}』に由来します"
    facts.append(head)

    # 論文の流れの中での役割。引けなければ要約で代替する（どちらも無ければ行ごと省く）。
    role = _safe(in_paper.get("narrative_role"), MAX_TEXT_CHARS)
    if role:
        facts.append(f"「{label}」の論文の流れの中での役割{_generated_english_note(role)}: {role}")
    else:
        summary = _safe(component.get("summary"), MAX_TEXT_CHARS)
        if summary:
            facts.append(f"「{label}」の要約{_generated_english_note(summary)}: {summary}")

    # IK-0920: 教員が確定した共通部品（上限で切られないよう要約の直後に置く）。
    facts.extend(_shared_part_facts(context))

    supports = _dict(instance.get("supports"))

    texts = [
        _safe(_dict(ref).get("text"), MAX_TEXT_CHARS)
        for ref in _list(supports.get("preconditions"))
    ]
    texts = [text for text in texts if text]
    if texts:
        shown, truncated = _capped(texts, MAX_LEARNING_SUPPORT_ITEMS)
        tail = f"{_quoted(shown)}・{MORE_ITEMS_MARK}" if truncated else _quoted(shown)
        english = any(_is_english_sentence(text) for text in shown)
        facts.append(
            f"「{label}」が前提にしているのは{tail}です"
            + (GENERATED_ENGLISH_NOTE if english else "")
        )

    equations: list[str] = []
    for equation in _list(supports.get("equations")):
        row = _dict(equation)
        equation_label = _text(row.get("label"), MAX_TEXT_CHARS)
        # ラベルが引けず裸の内部 ID に落ちている式は出さない（論文の式番号は通る）。
        if not equation_label or is_internal_id_label(
            equation_label, "equation", row.get("id")
        ):
            continue
        equations.append(
            equation_label if equation_label.startswith("式") else f"式 {equation_label}"
        )
    if equations:
        shown, truncated = _capped(equations, MAX_LEARNING_SUPPORT_ITEMS)
        facts.append(f"「{label}」が使う式は {_join(shown, truncated=truncated)} です")

    for claim in _list(supports.get("claims"))[:MAX_LEARNING_SUPPORT_ITEMS]:
        row = _dict(claim)
        if is_fragment_claim_text(row.get("excerpt") or row.get("text")):
            continue
        claim_text = _safe(
            row.get("excerpt") or row.get("text"), MAX_LEARNING_CLAIM_EXCERPT_CHARS
        )
        if claim_text:
            facts.append(f"「{label}」を支える主張: 「{claim_text}」")

    explanation = _dict(instance.get("explanation"))
    body = _safe(explanation.get("body") or explanation.get("text"), MAX_TEXT_CHARS)
    if body:
        # 出所ラベル: 学習者向け component 文脈 DTO の ``instance.explanation`` は
        # route（``_first_approved_component_explanation``）が **teacher_approved の
        # 説明だけ**を充填し、``status`` キーを持たない。したがって status 不在は
        # 「承認済み」であり、``candidate`` 系の status が明示されたときだけ
        # 「候補（未承認）」を付ける（未承認を承認済みと言わない側の誤りを防ぐため、
        # ``candidate`` / ``pending`` / ``draft`` は候補扱い）。
        status = _text(explanation.get("status") or explanation.get("review_status"))
        if status in ("candidate", "pending", "draft", "review_required"):
            facts.append(f"この要素の説明（{EXPLANATION_STATUS_CANDIDATE_LABEL}）: {body}")
        else:
            # 第 25 周 第 5 波（IK-0986）: 学習画面の区画名（「教員の説明」）で指す。
            facts.append(
                f"学習者の画面に出ているこの要素の「{LEARNER_EXPLANATION_SECTION_LABEL}」"
                f"（{EXPLANATION_STATUS_APPROVED_LABEL}）: {body}"
            )
            # IK-1064: 説明の出所（誰が書いたか）を問われたときに答えられるよう、学習者の画面に
            # 出ている書いた教員の名前（本人が登録した名前だけ）を添える。標準の説明は名前なし。
            author = _safe(explanation.get("author_name"), 40)
            if author and str(explanation.get("kind") or "") != "standard":
                facts.append(f"この「{LEARNER_EXPLANATION_SECTION_LABEL}」を書いた教員: {author}")
    elif instance:
        # IK-1064: 説明が無いことも画面に出ている事実（「承認済みの説明がまだありません」）。
        facts.append(
            f"学習者の画面のこの要素には、承認済みの「{LEARNER_EXPLANATION_SECTION_LABEL}」はまだありません。"
        )

    return facts


def _lens_facts(context: Mapping[str, Any], element_type: str) -> list[str]:
    """claim / equation の W層レンズ射影（``element_context``）を事実文にする。"""
    dto = _dict(context)
    if dto.get("available") is not True:
        return []
    focus = _dict(dto.get("focus"))
    type_label = LEARNING_ELEMENT_TYPE_LABELS.get(element_type, "要素")
    label = _safe(focus.get("headline") or focus.get("label"), MAX_TEXT_CHARS)
    if not label:
        return []

    facts = [f"学習者が選んでいるのは{type_label}「{label}」です"]

    summary = _safe(focus.get("intrinsic_summary"), MAX_TEXT_CHARS)
    if summary:
        facts.append(f"「{label}」の要約: {summary}")

    for key, phrase in (("upper", "上位にあたるのは"), ("lower", "下位にあたるのは")):
        labels: list[str] = []
        for item in _list(dto.get(key)):
            row = _dict(item)
            item_label = _safe(row.get("label"), MAX_TEXT_CHARS)
            if item_label and item_label not in labels:
                labels.append(item_label)
        if not labels:
            continue
        shown, truncated = _capped(labels, MAX_LEARNING_SUPPORT_ITEMS)
        tail = f"{_quoted(shown)}・{MORE_ITEMS_MARK}" if truncated else _quoted(shown)
        facts.append(f"「{label}」の{phrase}{tail}です")

    for note in _list(dto.get("notes")):
        text = _safe(note, MAX_TEXT_CHARS)
        if text:
            facts.append(text)

    return facts


def _figure_facts(item: Mapping[str, Any]) -> list[str]:
    row = _dict(item)
    caption = _safe(row.get("caption"), MAX_TEXT_CHARS)
    title = _safe(row.get("title"), MAX_TEXT_CHARS)
    if caption:
        return [f"学習者が選んでいるのは図です: {caption}"]
    if title:
        return [f"学習者が選んでいるのは図です: {title}"]
    return []


def _superseded_component_facts(context: Mapping[str, Any], item: Mapping[str, Any]) -> list[str]:
    """後継の決まらない凍結の ⚓（IK-1075）。名前と、パネルと同じ事実文（``frozen_label_line``）。"""
    row = _dict(context)
    label = _safe(row.get("frozen_label"), MAX_TEXT_CHARS) or _safe(
        _dict(item).get("title"), MAX_TEXT_CHARS
    )
    facts: list[str] = []
    if label and not contains_internal_id(label):
        facts.append(f"学習者が選んでいるのは論理要素「{label}」です")
    line = _safe(row.get("frozen_label_line"), MAX_TEXT_CHARS) or _safe(row.get("note"), MAX_TEXT_CHARS)
    if line and not contains_internal_id(line):
        facts.append(line.rstrip("。"))
    return facts[:MAX_LEARNING_ELEMENT_FACTS]


def resolve_element(ctx: ScreenContext, sources: Mapping[str, Any]) -> list[str]:
    """選択中のチップ1件を事実文にする（§11.3 kind ``element``）。"""
    element_type, payload = _selected_element(ctx, sources)
    if not element_type:
        return []
    context = _dict(payload.get("context"))
    item = _dict(payload.get("item"))

    if element_type == "component" and context.get("available") is False:
        # IK-1075: 凍結の ⚓ の後継が決まらない（パネルと同じ解決点の結果）。別の部品の役割・式・
        # 検証を語らせないため、凍結時の名前と画面と同じ事実文だけを渡す（題名の縮退にも落とさない）。
        return _superseded_component_facts(context, item)
    if element_type == "component":
        facts = _component_facts(context)
    elif element_type == "figure":
        facts = _figure_facts(item)
    else:
        facts = _lens_facts(context, element_type)

    if not facts and item:
        # 射影が引けなかったときの最小限の縮退（題名だけ。本文は載せない）。
        title = _safe(item.get("title"), MAX_TEXT_CHARS)
        type_label = LEARNING_ELEMENT_TYPE_LABELS.get(element_type, "要素")
        if title:
            facts = [f"学習者が選んでいるのは{type_label}「{title}」です"]

    return [fact for fact in facts if fact][:MAX_LEARNING_ELEMENT_FACTS]


# ---------------------------------------------------------------------------
# kind "verification" — 台帳の検証事実（閉世界語彙のまま）
# ---------------------------------------------------------------------------


def resolve_verification(ctx: ScreenContext, sources: Mapping[str, Any]) -> list[str]:
    """台帳の事実文を**そのまま**渡す（§11.3 kind ``verification``）。

    SL1 の閉世界語彙は台帳の投影側が既に固定しているので、ここで言い換えない。
    記帳者・スコープの生 dict は載せない（学習者射影が既に落としているものを
    復活させない）。
    """
    ledger = _dict(sources.get("ledger"))
    if not ledger:
        return []

    facts: list[str] = []
    fact_line = _safe(ledger.get("fact_line"))
    if fact_line:
        facts.append(fact_line)
    support_fact_line = _safe(ledger.get("support_fact_line"))
    if support_fact_line:
        facts.append(support_fact_line)

    for condition in _list(ledger.get("falsification_conditions")):
        row = _dict(condition)
        statement = _safe(row.get("statement"), MAX_TEXT_CHARS)
        if not statement:
            continue
        kind_label = _safe(row.get("kind_label"), MAX_TEXT_CHARS)
        prefix = f"覆る条件（{kind_label}）" if kind_label else "覆る条件"
        facts.append(f"{prefix}: 「{statement}」")

    return facts[:MAX_LEARNING_VERIFICATION_FACTS]


# ---------------------------------------------------------------------------
# kind "placement" — 分野の地図の中での位置づけ
# ---------------------------------------------------------------------------


def _region_labels(landscape: Mapping[str, Any]) -> dict[str, str]:
    """``region_id`` → 領域名。配置の region 行（``node_kind == "region"``）から作る。

    学習者向け DTO は骨格そのものを持たないので、引けないことは普通に起きる。
    引けなければ領域名を**書かない**（内部 ID を代わりに出さない = SA4 / PL7）。
    """
    labels: dict[str, str] = {}
    for document in _list(_dict(landscape).get("documents")):
        for placement in _list(_dict(document).get("placements")):
            row = _dict(placement)
            if _text(row.get("node_kind")) != "region":
                continue
            node_id = _text(row.get("node_id"))
            label = _safe(row.get("node_label"), MAX_TEXT_CHARS)
            if node_id and label:
                labels.setdefault(node_id, label)
    return labels


def resolve_placement(ctx: ScreenContext, sources: Mapping[str, Any]) -> list[str]:
    """論文が分野の地図のどこに置かれているかを事実文にする（§11.3 kind ``placement``）。

    出所ラベル（「教員確認済み」／「AIによる推定（未確認）」）は**剥がさない**
    （§11.13-1 のオーナー判断）。骨格版が引ければ明示する（VA8 / LS5）。
    """
    landscape = _dict(sources.get("landscape"))
    documents = _list(landscape.get("documents"))
    if not documents:
        return []

    versions = {
        _text(_dict(entry).get("domain_key")): _text(_dict(entry).get("frozen_version"))
        for entry in _list(landscape.get("domains"))
    }
    regions = _region_labels(landscape)

    document = _dict(documents[0])
    title = _safe(document.get("title"), MAX_TEXT_CHARS)
    facts: list[str] = []
    for placement in _list(document.get("placements")):
        if len(facts) >= MAX_LEARNING_PLACEMENT_FACTS:
            break
        row = _dict(placement)
        node_label = _safe(row.get("node_label"), MAX_TEXT_CHARS)
        if not node_label:
            continue
        region_label = _safe(row.get("region_label"), MAX_TEXT_CHARS) or regions.get(
            _text(row.get("region_id")), ""
        )
        place = f"「{region_label}／{node_label}」" if region_label else f"「{node_label}」"
        version = versions.get(_text(row.get("domain_key")), "")
        map_name = f"分野の地図（版 {version}）" if version else "分野の地図"
        subject = f"論文『{title}』" if title else "この論文"
        provenance = _safe(row.get("provenance_label"), MAX_TEXT_CHARS)
        fact = f"{subject}は、{map_name}の{place}に置かれています"
        if provenance:
            fact += f"（{provenance}）"
        # 第 25 周 第 9 波: 観点（perspective）だけが違う配置は同じ文になる — 同じ事実を 2 行渡さない。
        if fact not in facts:
            facts.append(fact)
    return facts


# ---------------------------------------------------------------------------
# kind "view" — 表示モードの事実1行
# ---------------------------------------------------------------------------


def resolve_view(ctx: ScreenContext, sources: Mapping[str, Any]) -> list[str]:
    """表示モードの事実1行（§11.3 kind ``view``）。

    ここで書いてよい数値はスライド番号だけ（画面の位置であって指標ではない）。
    """
    parts: list[str] = []
    mode_label = LEARNING_VIEW_MODE_LABELS.get(_text(ctx.view.get("mode")), "")
    precision = bool(ctx.view.get("precision_reading"))
    segment = _text(ctx.selection.get("segment_id"))

    if mode_label:
        head = f"学習者は{mode_label}を見ています"
        if precision:
            head = f"学習者は精読モードで{mode_label}を見ています"
        parts.append(head)
    elif precision:
        parts.append("学習者は精読モードです")

    if segment:
        parts.append(f"表示中の区画: スライド{segment}")

    scope_label = LEARNING_DISCUSS_SCOPE_LABELS.get(_text(ctx.view.get("discuss_scope")), "")
    if scope_label:
        parts.append(f"検索範囲: {scope_label}")

    facts = ["。".join(parts) + "。"] if parts else []
    # IK-0907: 地図を開いたことがあるとき、地図に何が出て何が出ないかの事実を添える
    # （AI が地図に無い「記帳の条件」へ案内しない — 画面の案内は画面の事実に従わせる）。
    if ctx.view.get("personal_map_seen") is True:
        facts.append(LEARNING_PERSONAL_MAP_SCREEN_FACT + "。")
    facts.extend(_atlas_node_facts(ctx.view, sources))
    facts.extend(_personal_map_node_facts(ctx.selection, sources))
    return facts


def _personal_map_node_label(node: Mapping[str, Any], *, with_touched: bool = False) -> str:
    label = _safe(node.get("label"), 40)
    if not label or contains_internal_id(label) or _display_internal_id(label):
        return ""
    text = f"『{label}』"
    verification = _dict(node.get("verification"))
    ver_label = _safe(verification.get("label"), 40)
    if ver_label:
        scope = _safe(verification.get("scope_line"), MAX_TEXT_CHARS)
        text += f"（{ver_label}" + (f"・記録された条件: {scope.rstrip('。')}" if scope else "") + "）"
    if with_touched and node.get("touched") is True:
        text += LEARNING_PERSONAL_MAP_TOUCHED_MARK
    return text


def _personal_map_node_facts(selection: Mapping[str, Any], sources: Mapping[str, Any] | None) -> list[str]:
    """「わたしの地図」で見ている場所の事実（第 25 周 第 10 波・IK-1113）。

    材料は route が本人ゲート・閲覧可否ゲートの内側で引いた近傍関係ビュー（``sources["personal_map"]``）で、
    画面の ``selection.personal_map_node_id`` と一致するときだけ使う。範囲ビューは範囲の論文と理論構成、
    点ビューは中心と前後 1 階層。検証の札と記録された条件は地図の札と同じ学習者の語（数値なし）。
    """
    node_id = _text(_dict(selection).get("personal_map_node_id"))
    data = _dict(_dict(sources).get("personal_map"))
    if not node_id or _text(data.get("node_id")) != node_id:
        return []
    view = _dict(data.get("view"))
    if view.get("available") is not True:
        return []
    facts: list[str] = []
    center = _dict(view.get("center"))
    if view.get("mode") == "range" or not center:
        facts.append(LEARNING_PERSONAL_MAP_RANGE_FACT + "。")
        for document in _list(view.get("range_documents"))[:MAX_PERSONAL_MAP_DOCUMENTS]:
            row = _dict(document)
            title = _safe(row.get("title"), MAX_TEXT_CHARS)
            if not title or contains_internal_id(title):
                continue
            labels = [
                text for text in (
                    _personal_map_node_label(_dict(n), with_touched=True)
                    for n in _list(row.get("nodes"))[:MAX_PERSONAL_MAP_NODES]
                ) if text
            ]
            if labels:
                facts.append(
                    LEARNING_PERSONAL_MAP_RANGE_DOCUMENT_TEMPLATE.format(title=title, nodes="、".join(labels)) + "。"
                )
    else:
        center_text = _personal_map_node_label(center)
        if not center_text:
            return []
        facts.append(LEARNING_PERSONAL_MAP_CENTER_TEMPLATE.format(label=center_text) + "。")
        for key, template in (("upstream", LEARNING_PERSONAL_MAP_UPSTREAM_TEMPLATE),
                              ("downstream", LEARNING_PERSONAL_MAP_DOWNSTREAM_TEMPLATE)):
            labels = [
                text for text in (
                    _personal_map_node_label(_dict(n)) for n in _list(view.get(key))[:MAX_PERSONAL_MAP_NODES]
                ) if text
            ]
            if labels:
                facts.append(template.format(labels="、".join(labels)) + "。")
    for line in _list(view.get("facts"))[:MAX_PERSONAL_MAP_DTO_FACTS]:
        text = _safe(line, MAX_TEXT_CHARS)
        if text and not contains_internal_id(text):
            facts.append(LEARNING_PERSONAL_MAP_FACT_LINE_TEMPLATE.format(line=text.rstrip("。")) + "。")
    return facts


def resolve_selected_atlas_node(ctx: ScreenContext, sources: Mapping[str, Any]) -> list[str]:
    """利用者が分野の地図で選んだ場所の事実だけ（IK-1049・2026-10-28 第 7 波）。

    音声・casual の往復は表示モード・台帳・位置づけを渡さない（§11.5）が、利用者が**明示に
    選んだもの**（地図の場所の名前と札の文・詳細パネルの検証と承認の行・置かれた論文の題名）は
    要素と同じく渡す（2026-10-28 オーナー判断 = 「選択」は要素に限らず明示の選択全般）。
    推定は増やさない — 画面が申告した ``view.atlas_node_*`` と、route が権限ゲート内で引いた
    ``sources["atlas_node"]`` だけを読む。kind としては登録しない（登録 kind 集合は不変）。
    """
    # 第 10 波（IK-1113）: わたしの地図で見ている場所も利用者の明示の選択（地図の場所と同じ扱い）。
    return _atlas_node_facts(ctx.view, sources) + _personal_map_node_facts(ctx.selection, sources)


def _atlas_node_facts(view: Mapping[str, Any], sources: Mapping[str, Any] | None = None) -> list[str]:
    """分野の地図で最後に開いた場所の事実 1〜2 行（IK-0907 第 4 周）。

    名前は画面の題名（内部 ID・TeX は出さない）、札の意味は ``label_vocab.ATLAS_PILL_NOTES``
    の正本（詳細パネルと同じ文）を表示状態 → 台帳の状態の順で引く。どちらも引けなければ出さない。
    """
    label = _safe(view.get("atlas_node_label"), 40)
    if not label or contains_internal_id(label):
        return []
    facts = [LEARNING_ATLAS_NODE_FACT_TEMPLATE.format(label=label) + "。"]
    note = ATLAS_PILL_NOTES.get(_text(view.get("atlas_node_status"))) or ATLAS_PILL_NOTES.get(
        _text(view.get("atlas_node_ledger_status"))
    )
    if note:
        # IK-1059 残り（第 9 波）: 札の説明もパネル（routes/atlas_view.py）と同じ学習者の語に読み替える
        # （「…記録は台帳にありません」を AI の材料に入れない — 英語の回答で ledger と訳される）。
        facts.append(LEARNING_ATLAS_PILL_NOTE_TEMPLATE.format(note=learner_atlas_wording(note)))
    facts.extend(_atlas_node_panel_facts(view, sources))
    return facts


def _atlas_node_panel_facts(view: Mapping[str, Any], sources: Mapping[str, Any] | None) -> list[str]:
    """同じ場所の詳細パネルの検証・承認の行と、そこに置かれた論文（IK-1031）。

    材料はルートが権限ゲート内で引いた ``sources["atlas_node"]``（画面の ``view.atlas_node_id``
    と一致するときだけ使う）。文はサーバの状態キャッシュの行そのまま・題名は最大 3。
    """
    node = _dict(_dict(sources).get("atlas_node"))
    node_id = _text(view.get("atlas_node_id"))
    if not node or not node_id or _text(node.get("node_id")) != node_id:
        return []
    facts: list[str] = []
    for key in ("verify_line", "endorse_line"):
        line = _safe(node.get(key), MAX_TEXT_CHARS)
        if line and not contains_internal_id(line):
            facts.append(LEARNING_ATLAS_PANEL_LINE_TEMPLATE.format(line=line.rstrip("。")) + "。")
    papers = []
    for paper in _list(node.get("papers"))[:3]:
        row = _dict(paper)
        title = _safe(row.get("title"), MAX_TEXT_CHARS)
        if not title or contains_internal_id(title):
            continue
        provenance = _safe(row.get("provenance_label"), MAX_TEXT_CHARS)
        papers.append(f"『{title}』" + (f"（{provenance}）" if provenance else ""))
    if papers:
        facts.append(LEARNING_ATLAS_PAPERS_TEMPLATE.format(titles="、".join(papers)) + "。")
    elif node.get("papers_resolved") is True:
        facts.append(LEARNING_ATLAS_NO_PAPERS_FACT + "。")
    return facts


# ---------------------------------------------------------------------------
# kind "retrieved_structure" — 検索で当たった箇所の構造 1 hop（P4-2）
#
# 正本: ``docs/features/knowledge_transfer_design.md`` §5。入口は画面の申告ではなく
# **回答に採用した出典**（``cited_sources``）なので、``screen_context`` が無いターンでも
# 働く。route が「chunk → 主張 → 理論の骨格の main ノード」を権限ゲート内で解決し、
# ここへ DTO として渡す（本モジュールは DB を引かない = KT3 / SA3）。
# ---------------------------------------------------------------------------


def _retrieved_claim_fact(index: str, claim: Mapping[str, Any]) -> str:
    """1つの主張の事実文（出典番号に結ぶ）。出せないものは空文字。"""
    row = _dict(claim)
    if is_fragment_claim_text(row.get("text")):
        return ""
    claim_text = _safe(row.get("text"), MAX_LEARNING_RETRIEVED_CLAIM_CHARS)
    if not claim_text:
        return ""
    fact = f"[出典{index}] の箇所には次の主張が構造化されています: 「{claim_text}」"
    # ``claim_type='unknown'`` は「種類が決まっていない」という記録で、学習者に渡しても
    # 情報が無い（「主張の種類: 不明」は雑音 — IK-0500）。種類の行ごと出さない。
    claim_type = str(row.get("claim_type") or "").strip().lower()
    type_label = "" if claim_type in _UNINFORMATIVE_CLAIM_TYPES else claim_type_label(claim_type)
    if type_label:
        fact += f"（主張の種類: {type_label}）"
    return fact


#: 種類の行を出さない claim_type（「決まっていない」ことしか言えない値）。
_UNINFORMATIVE_CLAIM_TYPES = frozenset({"", "unknown"})

_CLAIM_TEXT_TRAILING = " \t.。．…"


def _claim_dedupe_key(value: Any) -> str:
    """主張本文の包含判定キー（空白を畳み casefold・末尾の句読点と省略記号を落とす）。"""
    text = " ".join(str(value or "").split()).casefold()
    while text.endswith("..."):
        text = text[:-3]
    return text.rstrip(_CLAIM_TEXT_TRAILING)


def _dedupe_claims(claims: list[Any]) -> list[dict]:
    """同じ出典の主張のうち、本文が他の主張に丸ごと含まれるものを畳む（IK-0501）。

    同じ引用文の全文と途中で切れた版が並ぶと、同じことを 2 度言う事実文になる。
    含む側（長い方）を**先に出た位置**に残す。行は消さない（この 1 回の表示から外すだけ）。
    """
    kept: list[tuple[str, dict]] = []
    for claim in claims:
        row = _dict(claim)
        key = _claim_dedupe_key(row.get("text"))
        if not key:
            kept.append((key, row))
            continue
        absorbed = False
        for position, (kept_key, _kept_row) in enumerate(kept):
            if not kept_key:
                continue
            if key in kept_key:
                absorbed = True
                break
            if kept_key in key:
                kept[position] = (key, row)
                absorbed = True
                break
        if not absorbed:
            kept.append((key, row))
    return [row for _, row in kept]


def _retrieved_node_fact(claim: Mapping[str, Any]) -> str:
    """主張が理論の骨格（main 層）のどの段階に置かれているかの事実文。

    main ノードの ``label`` は #308 の規約で **theory stage の英語表示名**そのもの
    （``display_label`` は ``"<Stage>: <理論対象>"`` 形）。段階名は訳語表
    （``element_vocab.THEORY_STAGE_LABELS``）で日本語にし、stage を引けないノードは
    **何も出さない**（英語の内部表示名をそのまま学習者へ渡さない = SA4 / PL7）。
    """
    row = _dict(claim)
    node = _dict(row.get("node"))
    if not node:
        return ""
    raw_label = _text(node.get("label"), MAX_TEXT_CHARS)
    stage = theory_stage_label(theory_stage_key(raw_label))
    if not stage:
        return ""
    fact = f"この主張は、理論の骨格では『{stage}』の段階に置かれています"
    # ``display_label`` の「: 」以降（理論対象）が引ければ括弧で添える。引けない・
    # 内部 ID 形なら段階名だけで止める（推測で埋めない）。
    detail_source = _text(node.get("display_label"), MAX_TEXT_CHARS) or raw_label
    detail = detail_source.split(":", 1)[1].strip() if ":" in detail_source else ""
    detail = _safe(detail, MAX_TEXT_CHARS)
    # 第 25 周 第 6 波: 理論対象が英語の文（解析層の生成した説明。日本語を含まず語が並ぶ）なら
    # 括弧ごと添えない。上限で切ると「…as a conti」のような途中切りの英語が学習者に届く
    # （IK-0571 と同じ根 — 英語の生成文を学習者向けの事実文にしない）。短い対象名は残す。
    if detail and _is_english_sentence(detail):
        detail = ""
    if detail and detail.lower() != raw_label.lower():
        fact += f"（{detail}）"
    return fact


_JAPANESE_CHAR_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uff66-\uff9f]")
_ENGLISH_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]*")
#: 英語の文とみなす語数の下限（短い対象名「TOV equations」は残す）。
_ENGLISH_SENTENCE_MIN_WORDS = 4


def _is_english_sentence(text: str) -> bool:
    """日本語を含まず英単語が並ぶ（文の形の）文字列か。数式（``$``）を含むものは対象外。"""
    value = str(text or "")
    if not value or "$" in value or _JAPANESE_CHAR_RE.search(value):
        return False
    return len(_ENGLISH_WORD_RE.findall(value)) >= _ENGLISH_SENTENCE_MIN_WORDS


def resolve_retrieved_structure(
    ctx: ScreenContext, sources: Mapping[str, Any]
) -> list[str]:
    """採用した出典の箇所に結ばれた主張と理論の骨格上の位置を事実文にする（P4-2）。

    ``sources["retrieved_structure"]["sources"]`` は route が組んだ
    ``[{"index": 1, "claims": [{"text", "claim_type", "node": {...}}]}]``。
    数値（一致度・件数）は載せない（KT7）。
    """
    payload = _dict(sources.get("retrieved_structure"))
    entries = _list(payload.get("sources"))
    if not entries:
        return []

    facts: list[str] = []
    for entry in entries:
        row = _dict(entry)
        index = _text(row.get("index"))
        if not index:
            continue
        claims = _dedupe_claims(_list(row.get("claims")))[:MAX_LEARNING_RETRIEVED_CLAIMS_PER_SOURCE]
        for claim in claims:
            if len(facts) >= MAX_LEARNING_RETRIEVED_FACTS:
                return facts
            claim_fact = _retrieved_claim_fact(index, claim)
            if not claim_fact:
                continue
            facts.append(claim_fact)
            if len(facts) >= MAX_LEARNING_RETRIEVED_FACTS:
                return facts
            node_fact = _retrieved_node_fact(claim)
            if node_fact:
                facts.append(node_fact)
    return facts[:MAX_LEARNING_RETRIEVED_FACTS]


# ---------------------------------------------------------------------------
# kind "simulation" — 学習者が開いている自分の検証シミュレーションの実行記録（4-e）
#
# 正本: ``docs/features/simulation_bridge_design.md`` §14（SB11〜SB15）と本層の §11.17。
# route が**本人所有**かつ grounding の document 範囲内の run だけを
# ``sources["simulation"]`` に入れる（他人の run・不在は渡さない = 黙って空）。
# 本人の run なので入力・出力・模擬観測の値は載せてよい（SB6 の読み替え・§14.1）。
# 予想は並べるだけで当否を書かない（SB14）。``FACT_NOT_VERIFICATION`` は成功した run で
# 必ず 1 行として載せ、route はそれがブロックに載ったときだけ出力側の拘束を足す。
# ---------------------------------------------------------------------------

_MODULE_LABEL_PATTERN = "理論モジュール『"


def _simulation_module_label(statement: str) -> str:
    """``statement``（閉世界の 1 文）から理論モジュール名を取り出す（無ければ ``""``）。"""
    start = statement.find(_MODULE_LABEL_PATTERN)
    if start < 0:
        return ""
    rest = statement[start + len(_MODULE_LABEL_PATTERN):]
    end = rest.find("』")
    return _safe(rest[:end], MAX_TEXT_CHARS) if end > 0 else ""


def _simulation_number(value: Any) -> str:
    """数値（とベクトル）を短い表記にする。数値でないものは ``""``（載せない）。"""
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value:  # NaN
            return ""
        return f"{value:.4g}"
    if isinstance(value, (list, tuple)):
        items = [_simulation_number(v) for v in value[:MAX_LEARNING_SIMULATION_VECTOR_ITEMS]]
        items = [item for item in items if item]
        if not items:
            return ""
        more = "、…" if len(value) > MAX_LEARNING_SIMULATION_VECTOR_ITEMS else ""
        return "(" + "、".join(items) + more + ")"
    return ""


def _simulation_symbol(value: Any) -> str:
    text = strip_control_sequences(str(value or "")).strip()
    # 内部 ID の語彙は表示投影層（統合語彙）と学習者射影の両方で見る（再定義しない）。
    if not text or contains_internal_id(text) or _display_internal_id(text):
        return ""
    return text[:MAX_LEARNING_SIMULATION_SYMBOL_CHARS]


def _simulation_pairs(mapping: Any) -> list[tuple[str, Any]]:
    """``{sym: v}`` と ``[{symbol, value}]`` の両形を ``[(sym, v)]``（記号の昇順）にする。"""
    pairs: list[tuple[str, Any]] = []
    if isinstance(mapping, Mapping):
        pairs = [(str(k), mapping[k]) for k in mapping]
    else:
        for item in _list(mapping):
            row = _dict(item)
            if "symbol" in row:
                pairs.append((str(row.get("symbol")), row.get("value")))
    return sorted(pairs, key=lambda kv: kv[0])


def _simulation_value_line(title: str, mapping: Any) -> str:
    items: list[str] = []
    for symbol, value in _simulation_pairs(mapping):
        name = _simulation_symbol(symbol)
        shown = _simulation_number(value)
        if name and shown:
            items.append(f"{name} = {shown}")
    if not items:
        return ""
    shown_items, truncated = _capped(items, MAX_LEARNING_SIMULATION_SYMBOLS)
    return f"{title}: {_join(shown_items, truncated=truncated)}"


def _simulation_observed_line(mapping: Any) -> str:
    items: list[str] = []
    for symbol, entry in _simulation_pairs(mapping):
        name = _simulation_symbol(symbol)
        if not name:
            continue
        if isinstance(entry, Mapping):
            mean = _simulation_number(entry.get("mean"))
            std = _simulation_number(entry.get("std"))
        else:
            mean, std = _simulation_number(entry), ""
        if not mean:
            continue
        items.append(f"{name} = {mean} ± {std}" if std else f"{name} = {mean}")
    if not items:
        return ""
    shown_items, truncated = _capped(items, MAX_LEARNING_SIMULATION_SYMBOLS)
    return f"模擬観測（実験機のモデルを通した値）: {_join(shown_items, truncated=truncated)}"


def _simulation_reference_line(reference: Any) -> str:
    """入力した論文の記載値（記号・値・単位・出所）。第 22 周 (i): 単位が AI の文脈に無く、換算の誤りを疑わせた。"""
    from core.simulation_bridge.schema import reference_input_fact  # 遅延 import（解決器の純粋性検査の経路を変えない）

    mapping = reference
    if isinstance(reference, list):  # DTO の list 形 [{symbol, value, unit, source_note}] も受ける
        mapping = {
            _text(item.get("symbol")): item for item in reference
            if isinstance(item, dict) and _text(item.get("symbol"))
        }
    if not isinstance(mapping, dict) or not mapping:
        return ""
    return safe_text(reference_input_fact(mapping))


def resolve_simulation(ctx: ScreenContext, sources: Mapping[str, Any]) -> list[str]:
    """学習者が開いている自分の実行記録を事実文にする（4-e・最大 8 行）。

    ``sources["simulation"]`` は route が本人所有を確かめた run 行（store の dict）。
    画面の ``selection.simulation_run_id`` と run の ID が一致しないときは何も出さない。
    """
    run_id = selected_simulation_run_id(ctx.selection)
    run = _dict(sources.get("simulation"))
    if not run_id or not run or _text(run.get("id")).lower() != run_id:
        return []

    result = _dict(run.get("result"))
    inputs = _dict(run.get("inputs"))
    statement = _text(result.get("statement"))
    module_label = _simulation_module_label(statement)
    wiring_name = _safe(result.get("wiring_name"), MAX_TEXT_CHARS)
    succeeded = _text(run.get("status")) == RUN_STATUS_SUCCEEDED

    head = (
        f"学習者が開いている検証シミュレーションの実行記録（本人の実行）: "
        f"理論モジュール『{module_label}』を動かした記録です"
        if module_label
        else "学習者が開いている検証シミュレーションの実行記録（本人の実行）です"
    )
    head += f"（配線『{wiring_name}』）" if wiring_name else "（配線なし）"
    if not succeeded:
        head += "。比較まで終わっていない実行です"
    facts: list[str] = [head + "。"]

    for line in (
        _simulation_value_line("入力", inputs.get("values")),
        _simulation_reference_line(inputs.get("reference")),
        _simulation_value_line("理論の出力", result.get("theory_outputs")),
        _simulation_observed_line(result.get("observed")),
        _simulation_value_line(
            "あなたの予想（学習者が実行前に書いた値。当否は判定していません）",
            run.get("prediction"),
        ),
    ):
        if line:
            facts.append(line)

    labels = [
        _safe(value, MAX_TEXT_CHARS)
        for value in _dict(result.get("labels")).values()
        if _safe(value, MAX_TEXT_CHARS)
    ]
    if labels:
        facts.append("段階ラベル: " + "／".join(labels))

    # SB1: 成功した run では「一致は検証ではない」を必ず 1 行で渡す。比較の事実より**前**に
    # 置く（``render_block`` は予算超過で末尾から落とすので、比較の事実だけが残って
    # この 1 行が落ちる並びにしない）。
    if succeeded:
        facts.append(FACT_NOT_VERIFICATION)

    # 第 22 周 (j): 「この実行を共有すると何が起きるか」を実行の事実として渡す（英語の問いにも実行の事実から答えられる）。
    from core.simulation_bridge.schema import FACT_SHARE_MEANING_SHARED, FACT_SHARE_MEANING_UNSHARED  # 遅延 import

    facts.append(FACT_SHARE_MEANING_SHARED if run.get("shared_at") else FACT_SHARE_MEANING_UNSHARED)
    decision = _dict(run.get("ledger_decision"))
    if _safe(decision.get("fact")):
        facts.append(_safe(decision.get("fact")))

    other_facts: list[str] = []
    for fact in _list(result.get("facts")):
        text = _safe(fact)
        if text and text != FACT_NOT_VERIFICATION and text not in other_facts:
            other_facts.append(text)
    if other_facts:
        joined = "".join(f if f.endswith("。") else f + "。" for f in other_facts)
        facts.append("比較の事実: " + _text(joined, MAX_LEARNING_SIMULATION_FACT_LINE_CHARS))
    return facts[:MAX_LEARNING_SIMULATION_FACTS]


# 登録順がそのまま予算の優先順位（``render_block`` は行境界で末尾から落とす）。
# 具体的なもの（学習者が明示的に選んだ要素）から順に登録する。
# ``retrieved_structure`` は**別ブロック**（別ヘッダ・別予算）として描画されるため
# 末尾に置く — 画面文脈ブロックの解決（``kinds=None``）では
# ``sources["retrieved_structure"]`` が渡らないので何も出さない。
register(SCREEN_LEARNING, "element", resolve_element)
register(SCREEN_LEARNING, "verification", resolve_verification)
register(SCREEN_LEARNING, "placement", resolve_placement)
register(SCREEN_LEARNING, "view", resolve_view)
register(SCREEN_LEARNING, "retrieved_structure", resolve_retrieved_structure)
# 4-e（検証シミュレーション）は ``selection.simulation_run_id`` があるときだけ事実を出す。
# 画面文脈ブロックの末尾に載る（学習者が要素も同時に選んでいれば要素の事実が先）。
register(SCREEN_LEARNING, "simulation", resolve_simulation)
