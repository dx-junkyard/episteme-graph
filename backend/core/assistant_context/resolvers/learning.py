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

from typing import Any, Mapping

from core.element_vocab import claim_type_label, theory_stage_key, theory_stage_label
from core.learner_context_common import is_internal_id_label, safe_text

from ..registry import register
from ..schema import (
    EXPLANATION_STATUS_APPROVED_LABEL,
    EXPLANATION_STATUS_CANDIDATE_LABEL,
    LEARNING_DISCUSS_SCOPE_LABELS,
    LEARNING_ELEMENT_TYPE_LABELS,
    LEARNING_ELEMENT_TYPES,
    LEARNING_VIEW_MODE_LABELS,
    MAX_LEARNING_CLAIM_EXCERPT_CHARS,
    MAX_LEARNING_ELEMENT_FACTS,
    MAX_LEARNING_PLACEMENT_FACTS,
    MAX_LEARNING_RETRIEVED_CLAIM_CHARS,
    MAX_LEARNING_RETRIEVED_CLAIMS_PER_SOURCE,
    MAX_LEARNING_RETRIEVED_FACTS,
    MAX_LEARNING_SUPPORT_ITEMS,
    MAX_LEARNING_VERIFICATION_FACTS,
    MAX_TEXT_CHARS,
    MORE_ITEMS_MARK,
    SCREEN_LEARNING,
    ScreenContext,
    infer_selection_kind,
)

# ---------------------------------------------------------------------------
# 小さな純関数（graph_review.py と同じ作法）
# ---------------------------------------------------------------------------


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list:
    return [item for item in value] if isinstance(value, (list, tuple)) else []


def _text(value: Any, limit: int | None = None) -> str:
    text = str(value or "").strip()
    if limit is not None and len(text) > limit:
        return text[:limit]
    return text


def _safe(value: Any, limit: int | None = None) -> str:
    """内部 ID / 生 TeX が混ざった自由文はその欄ごと落とす（学習者射影の最後の砦）。

    遮断の述語は :func:`core.learner_context_common.safe_text` が正本
    （内部 ID + 生 TeX 判定）。ここで**新しい判定を作らない**。V-8 の実測では
    ``Equation (3.55) defines \delta P_{...}.`` のような claim 本文がそのまま
    学習者向けの事実文に出ていた（内部 ID の遮断はあったが TeX の遮断が無かった）。
    """
    text = _text(value, limit)
    if not text:
        return ""
    return safe_text(text)


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
        facts.append(f"「{label}」の論文の流れの中での役割: {role}")
    else:
        summary = _safe(component.get("summary"), MAX_TEXT_CHARS)
        if summary:
            facts.append(f"「{label}」の要約: {summary}")

    supports = _dict(instance.get("supports"))

    texts = [
        _safe(_dict(ref).get("text"), MAX_TEXT_CHARS)
        for ref in _list(supports.get("preconditions"))
    ]
    texts = [text for text in texts if text]
    if texts:
        shown, truncated = _capped(texts, MAX_LEARNING_SUPPORT_ITEMS)
        tail = f"{_quoted(shown)}・{MORE_ITEMS_MARK}" if truncated else _quoted(shown)
        facts.append(f"「{label}」が前提にしているのは{tail}です")

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
        excerpt = _safe(
            row.get("excerpt") or row.get("text"), MAX_LEARNING_CLAIM_EXCERPT_CHARS
        )
        if excerpt:
            facts.append(f"「{label}」を支える主張: 「{excerpt}」")

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
        status_label = (
            EXPLANATION_STATUS_CANDIDATE_LABEL
            if status in ("candidate", "pending", "draft", "review_required")
            else EXPLANATION_STATUS_APPROVED_LABEL
        )
        facts.append(f"この要素の説明（{status_label}）: {body}")

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


def resolve_element(ctx: ScreenContext, sources: Mapping[str, Any]) -> list[str]:
    """選択中のチップ1件を事実文にする（§11.3 kind ``element``）。"""
    element_type, payload = _selected_element(ctx, sources)
    if not element_type:
        return []
    context = _dict(payload.get("context"))
    item = _dict(payload.get("item"))

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

    return ["。".join(parts) + "。"] if parts else []


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
    excerpt = _safe(row.get("text"), MAX_LEARNING_RETRIEVED_CLAIM_CHARS)
    if not excerpt:
        return ""
    fact = f"[出典{index}] の箇所には次の主張が構造化されています: 「{excerpt}」"
    type_label = claim_type_label(row.get("claim_type"))
    if type_label:
        fact += f"（主張の種類: {type_label}）"
    return fact


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
    if detail and detail.lower() != raw_label.lower():
        fact += f"（{detail}）"
    return fact


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
        claims = _list(row.get("claims"))[:MAX_LEARNING_RETRIEVED_CLAIMS_PER_SOURCE]
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
