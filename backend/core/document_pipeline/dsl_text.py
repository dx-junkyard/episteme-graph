"""DSLLinkingResult を embedding 用の検索可能テキストに変換する。"""
from __future__ import annotations


def dsl_result_to_search_text(dsl_result, document_id: str) -> str:
    """DSLLinkingResult → embedding 用テキスト。

    nodes / edges を「人間にもLLMにも読みやすい行ベース表現」で並べる。
    polarity と reason を含めることで、検索時に意味的なマッチが取れるようにする。
    """
    lines: list[str] = [f"DSL graph for document {document_id}"]

    nodes = list(getattr(dsl_result, "nodes", []) or [])
    if nodes:
        lines.append("Nodes:")
        for n in nodes:
            value = getattr(n, "node_value", "") or ""
            ntype = getattr(n, "node_type", "") or ""
            reason = getattr(n, "reason", "") or ""
            node_id = getattr(n, "node_id", "") or ""
            line = f"- {node_id} {ntype} {value}".rstrip()
            if reason:
                line = f"{line} :: {reason}"
            lines.append(line)

    edges = list(getattr(dsl_result, "edges", []) or [])
    if edges:
        lines.append("Edges:")
        for e in edges:
            src = getattr(e, "from_node_id", "") or ""
            dst = getattr(e, "to_node_id", "") or ""
            pred = getattr(e, "core_predicate", "") or ""
            verb = getattr(e, "domain_verb", "") or ""
            polarity = getattr(e, "polarity", "") or ""
            reason = getattr(e, "reason", "") or ""
            head = f"- {src} {pred}({verb}, {polarity}) {dst}".rstrip()
            if reason:
                head = f"{head} :: {reason}"
            lines.append(head)

    review_notes = list(getattr(dsl_result, "review_notes", []) or [])
    if review_notes:
        lines.append("Review notes:")
        for note in review_notes:
            lines.append(f"- {note}")

    return "\n".join(lines)
