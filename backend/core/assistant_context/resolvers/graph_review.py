"""画面文脈アダプター — グラフレビュー画面の解決器（Phase 1・§4.3 / §5）。

``sources = {"paper_layer": <論文層 DTO>}`` を読む。DTO の形の正本は
``docs/features/graph_paper_layer_design.md`` §3 で、生成は
``core/graph_paper_layer/builder.py::build_paper_layer``（読み時導出・非LLM）。

規律:

- 入力（``ctx`` / ``sources``）を **mutate しない**・例外を外へ出さない。
- 数値（``core.graph_paper_layer.schema.FORBIDDEN_KEYS`` の各キーの値）と件数を
  事実文に書かない（SA4）。ガードレールは本ファイルに当該キー名が出現しないことも
  検査するので、キー名を**文字列としても**書かない。
- 内部 ID（``eq_op_`` / ``theory_op_`` / ``ev_`` / ``claim_`` / ノード ID）を
  事実文に書かない。式は印字番号、図表は ``display_label``、章は見出し（SA4 / PL7）。
- 空の部品は黙って飛ばす（「見えないものがある」と言わせない = SA2）。
"""

from __future__ import annotations

from typing import Any, Mapping

from ..registry import register
from ..schema import (
    EQUATION_ROLE_LABELS,
    EXPLANATION_STATUS_APPROVED_LABEL,
    EXPLANATION_STATUS_CANDIDATE_LABEL,
    FACT_NODE_UNLOCATED,
    FACT_SECTION_UNBOUND,
    GRAPH_LAYER_LABELS,
    MAX_COVERAGE_ITEMS,
    MAX_DERIVATION_STEPS,
    MAX_EQUATION_ITEMS,
    MAX_EVIDENCE_ITEMS,
    MAX_FIGURE_ITEMS,
    MAX_PLAIN_TEXT_CHARS,
    MAX_SECTION_LINES,
    MAX_SYMBOL_ITEMS,
    MAX_TEXT_CHARS,
    MAX_THESIS_ROLE_ITEMS,
    MORE_ITEMS_MARK,
    SCREEN_GRAPH_REVIEW,
    SYMBOL_ROLE_LABELS,
    VIEW_LAYER_LABELS,
    ScreenContext,
)

# ---------------------------------------------------------------------------
# 小さな純関数
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


def _paper_layer(sources: Mapping[str, Any]) -> dict:
    return _dict(sources.get("paper_layer")) if isinstance(sources, Mapping) else {}


#: 章見出し・ラベルの表示上限（見出しは短いが、壊れた artifact で長文が入り得る）。
_MAX_LABEL_CHARS = MAX_TEXT_CHARS


def _section_titles(dto: Mapping[str, Any]) -> dict[str, str]:
    """``section_id`` → 見出し（内部 ID を事実文に出さないための対応表）。"""
    titles: dict[str, str] = {}
    for section in _list(_dict(dto.get("paper")).get("sections")):
        row = _dict(section)
        section_id = _text(row.get("section_id"))
        title = _text(row.get("title"), _MAX_LABEL_CHARS)
        if section_id and title:
            titles[section_id] = title
    return titles


def _page_suffix(page: Any) -> str:
    text = _text(page)
    return f"（p.{text}）" if text else ""


def _located(section_id: Any, page: Any, titles: Mapping[str, str]) -> str:
    """章見出し + ページの所在表記（括弧の中に入れる素の形）。

    見出しが引けなければページだけ・どちらも無ければ空文字（内部 ID は出さない）。
    """
    title = titles.get(_text(section_id), "")
    page_text = _text(page)
    parts = [part for part in (title, f"p.{page_text}" if page_text else "") if part]
    return ", ".join(parts)


def _join(items: list[str], *, truncated: bool = False) -> str:
    joined = "／".join(items)
    return f"{joined}／{MORE_ITEMS_MARK}" if truncated else joined


# ---------------------------------------------------------------------------
# kind "graph_node"
# ---------------------------------------------------------------------------


def _node_head_fact(node: Mapping[str, Any]) -> str:
    label = _text(node.get("label"), MAX_TEXT_CHARS)
    layer = GRAPH_LAYER_LABELS.get(_text(node.get("graph_layer")), "")
    if not label:
        return ""
    return f"選択中のノード: 「{label}」（{layer or GRAPH_LAYER_LABELS['main']}）"


def _location_fact(node: Mapping[str, Any], titles: Mapping[str, str]) -> str:
    parts: list[str] = []
    for section in _list(node.get("sections")):
        row = _dict(section)
        title = _text(row.get("title"), _MAX_LABEL_CHARS) or titles.get(
            _text(row.get("section_id")), ""
        )
        if not title:
            continue
        parts.append(f"「{title}」{_page_suffix(row.get('page_start'))}")
    if parts:
        return "論文上の位置: " + _join(parts)
    return FACT_NODE_UNLOCATED


def _thesis_facts(node: Mapping[str, Any]) -> list[str]:
    facts: list[str] = []
    for role in _list(node.get("thesis_roles"))[:MAX_THESIS_ROLE_ITEMS]:
        row = _dict(role)
        label = _text(row.get("section_label"), _MAX_LABEL_CHARS)
        text = _text(row.get("text"), MAX_TEXT_CHARS)
        if not (label or text):
            continue
        body = f"{label}: {text}" if label and text else (label or text)
        facts.append(f"中心命題での役割 — {body}")
    return facts


def _equation_facts(node: Mapping[str, Any]) -> list[str]:
    facts: list[str] = []
    for equation in _list(node.get("equations"))[:MAX_EQUATION_ITEMS]:
        row = _dict(equation)
        label = _text(row.get("display_label"), _MAX_LABEL_CHARS)
        if not label:
            continue
        role = EQUATION_ROLE_LABELS.get(_text(row.get("role")), "")
        # latex は出さない（読み上げ・プロンプト双方で扱いにくい）。
        body = _text(row.get("plain_text"), MAX_PLAIN_TEXT_CHARS)
        fact = label if label.startswith("式") else f"式 {label}"
        if role:
            fact += f"（{role}）"
        if body:
            fact += f": {body}"
        facts.append(fact)
    return facts


def _evidence_facts(node: Mapping[str, Any], titles: Mapping[str, str]) -> list[str]:
    facts: list[str] = []
    for record in _list(node.get("evidence"))[:MAX_EVIDENCE_ITEMS]:
        row = _dict(record)
        # 逐語引用は切り詰めない（既に ≤200字で射影されている）。
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        where = _located(row.get("section_id"), row.get("page"), titles)
        prefix = f"根拠の逐語引用{f'（{where}）' if where else ''}"
        facts.append(f"{prefix}: 「{text}」")
    return facts


def _figure_facts(node: Mapping[str, Any]) -> list[str]:
    facts: list[str] = []
    items = _list(node.get("figures")) + _list(node.get("tables"))
    for item in items[:MAX_FIGURE_ITEMS]:
        row = _dict(item)
        label = _text(row.get("display_label"), _MAX_LABEL_CHARS)
        if not label:
            continue
        caption = _text(row.get("caption"), MAX_TEXT_CHARS)
        facts.append(f"図・表: {label}" + (f" — {caption}" if caption else ""))
    return facts


def _symbol_facts(node: Mapping[str, Any]) -> list[str]:
    facts: list[str] = []
    for item in _list(node.get("symbols"))[:MAX_SYMBOL_ITEMS]:
        row = _dict(item)
        symbol = _text(row.get("symbol"), _MAX_LABEL_CHARS)
        if not symbol:
            continue
        role = SYMBOL_ROLE_LABELS.get(_text(row.get("role")), "")
        quote = _text(row.get("definition_quote"), MAX_TEXT_CHARS)
        fact = f"記号: {symbol}"
        if role:
            fact += f"（{role}）"
        if quote:
            fact += f" — {quote}"
        facts.append(fact)
    return facts


def _derivation_facts(node: Mapping[str, Any]) -> list[str]:
    facts: list[str] = []
    for chain in _list(node.get("derivations")):
        for step in _list(_dict(chain).get("steps")):
            if len(facts) >= MAX_DERIVATION_STEPS:
                return facts
            row = _dict(step)
            operation = _text(row.get("operation"), _MAX_LABEL_CHARS)
            inputs = [_text(v, _MAX_LABEL_CHARS) for v in _list(row.get("input_labels"))]
            outputs = [_text(v, _MAX_LABEL_CHARS) for v in _list(row.get("output_labels"))]
            inputs = [v for v in inputs if v]
            outputs = [v for v in outputs if v]
            reason = _text(row.get("reason"), MAX_TEXT_CHARS)
            if not (operation or inputs or outputs):
                continue
            fact = f"導出: {operation}" if operation else "導出"
            if inputs or outputs:
                fact += f"（{_join(inputs) or '—'} → {_join(outputs) or '—'}）"
            if reason:
                fact += f" — {reason}"
            facts.append(fact)
    return facts


def _explanation_fact(node: Mapping[str, Any]) -> str:
    row = _dict(node.get("explanation"))
    body = _text(row.get("body"), MAX_TEXT_CHARS)
    if not body:
        return ""
    status = _text(row.get("status"))
    label = (
        EXPLANATION_STATUS_APPROVED_LABEL
        if status == "approved"
        else EXPLANATION_STATUS_CANDIDATE_LABEL
    )
    return f"この要素の説明（{label}）: {body}"


def _component_fact(node: Mapping[str, Any]) -> str:
    summary = _text(_dict(node.get("component")).get("summary"), MAX_TEXT_CHARS)
    return f"コンポーネントの要約: {summary}" if summary else ""


def resolve_graph_node(ctx: ScreenContext, sources: Mapping[str, Any]) -> list[str]:
    """選択中ノードの「論文側の顔」を事実文にする（§4.3 kind ``graph_node``）。"""
    node_id = _text(ctx.selection.get("node_id"))
    if not node_id:
        return []
    dto = _paper_layer(sources)
    node = _dict(_dict(dto.get("nodes")).get(node_id))
    if not node:
        return []

    titles = _section_titles(dto)
    facts: list[str] = []

    head = _node_head_fact(node)
    if head:
        facts.append(head)

    narrative_role = _text(node.get("narrative_role"), MAX_TEXT_CHARS)
    if narrative_role:
        facts.append(f"論文の流れの中での役割: {narrative_role}")

    facts.append(_location_fact(node, titles))
    facts.extend(_thesis_facts(node))
    facts.extend(_equation_facts(node))
    facts.extend(_evidence_facts(node, titles))
    facts.extend(_figure_facts(node))
    facts.extend(_symbol_facts(node))
    facts.extend(_derivation_facts(node))

    explanation = _explanation_fact(node)
    if explanation:
        facts.append(explanation)
    component = _component_fact(node)
    if component:
        facts.append(component)

    return [fact for fact in facts if fact]


# ---------------------------------------------------------------------------
# kind "document_graph"
# ---------------------------------------------------------------------------


def _coverage_fact(dto: Mapping[str, Any], key: str, label_key: str, prefix: str) -> str:
    rows = _list(_dict(dto.get("coverage")).get(key))
    labels: list[str] = []
    for row in rows:
        text = _text(_dict(row).get(label_key), _MAX_LABEL_CHARS)
        if text:
            labels.append(text)
    if not labels:
        return ""
    truncated = len(labels) > MAX_COVERAGE_ITEMS
    return f"{prefix}: " + _join(labels[:MAX_COVERAGE_ITEMS], truncated=truncated)


def resolve_document_graph(ctx: ScreenContext, sources: Mapping[str, Any]) -> list[str]:
    """論文順の背骨（章 → ノード）と被覆を事実文にする（§4.3 kind ``document_graph``）。"""
    dto = _paper_layer(sources)
    if not dto:
        return []

    facts: list[str] = []

    # PL8 の事実文（グラフ未構築・artifact 欠落）は逐語でそのまま渡す。
    for fact in _list(dto.get("facts")):
        text = _text(fact, MAX_TEXT_CHARS)
        if text:
            facts.append(text)
    if dto.get("available") is False:
        return facts

    labels = {
        node_id: _text(_dict(node).get("label"), _MAX_LABEL_CHARS)
        for node_id, node in _dict(dto.get("nodes")).items()
    }
    for section in _list(_dict(dto.get("paper")).get("sections"))[:MAX_SECTION_LINES]:
        row = _dict(section)
        title = _text(row.get("title"), _MAX_LABEL_CHARS)
        if not title:
            continue
        node_labels: list[str] = []
        for node_id in _list(row.get("node_ids")):
            label = labels.get(_text(node_id), "")
            if label and label not in node_labels:
                node_labels.append(label)
        if node_labels:
            facts.append(f"章「{title}」 → ノード: " + _join(node_labels))
        else:
            facts.append(f"章「{title}」 → {FACT_SECTION_UNBOUND}")

    for key, label_key, prefix in (
        ("unbound_sections", "title", "フレームに掛かっていない章"),
        ("unbound_equations", "display_label", "フレームに掛かっていない式"),
        ("unbound_figures", "display_label", "フレームに掛かっていない図"),
    ):
        fact = _coverage_fact(dto, key, label_key, prefix)
        if fact:
            facts.append(fact)

    return facts


# ---------------------------------------------------------------------------
# kind "view"
# ---------------------------------------------------------------------------


def resolve_view(ctx: ScreenContext, sources: Mapping[str, Any]) -> list[str]:
    """表示モードの事実1行（§4.3 kind ``view``）。"""
    mode = _text(ctx.view.get("mode"))
    if mode == "paper":
        return ["教員はいま「論文の順」ビューを見ています"]
    if mode == "graph":
        layer = VIEW_LAYER_LABELS.get(_text(ctx.view.get("layer")), "")
        if layer:
            return [f"教員はいまグラフ表示を見ています（表示層: {layer}）"]
        return ["教員はいまグラフ表示を見ています"]
    return []


register(SCREEN_GRAPH_REVIEW, "graph_node", resolve_graph_node)
register(SCREEN_GRAPH_REVIEW, "document_graph", resolve_document_graph)
register(SCREEN_GRAPH_REVIEW, "view", resolve_view)
