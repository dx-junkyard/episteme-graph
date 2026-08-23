"""帰り道の景色（REVISIT の構造差分, docs/features/understanding_cycle_design.md §6）。

個人知識ネットワーク（``core.personal_graph``）の過去時点での導出結果と現在の導出結果を
比較し、「前回のあと何が加わったか」を肯定形の事実文として組み立てる。

**personal_graph パッケージ自体は一切変更しない**（読むだけ）。ガードレール
（``test_understanding_cycle_guardrails.py``）が ``core/personal_graph/`` に
``intention`` / ``anchor_mark`` という文字列が現れないことを固定しているため、
本層は独立モジュールとして ``core/cycle/`` に置き、既存の
``core.personal_graph.queries`` / ``core.personal_graph.derive`` を関数呼び出しで
再利用するだけに留める（Option A: パッケージ非改変）。

否定形の断言はしない（「まだ架かっていませんでした」等）— ``interest_traces`` は
in-place 更新されるため、過去時点にその橋が本当に存在しなかったことを保証できない
（status 遷移・payload 更新により同じ行が後から書き換わり得る）。肯定形の
「加わっています」「増えています」のみを事実文にする。数値・件数は含めない（UC9）。
"""

from __future__ import annotations

from core.personal_graph.schema import PersonalEdge, PersonalNetwork

_MAX_MAP_DIFF_FACTS = 2
_LABEL_LIMIT = 60


def _excerpt(text: str | None, limit: int = _LABEL_LIMIT) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def build_network_as_of(user_id: str, course_id: str, until_iso: str) -> PersonalNetwork:
    """指定時刻以前に作られた痕跡だけで個人ネットワークを再導出する（読み時計算・PN-2）。

    ``core.personal_graph.queries`` / ``core.personal_graph.derive`` を遅延 import する
    （personal_graph/derive.py と同じ流儀: sqlalchemy がローカルに無い環境でも
    純粋関数側の検証を壊さないため）。フィルタは Python 側で行う（created_at は
    ISO 文字列。空文字＝タイムスタンプ不明の行は常に until 以前として扱い、
    情報を落とさない）。
    """
    from core.personal_graph import queries
    from core.personal_graph.derive import build_network

    until = until_iso or ""
    traces = [
        t for t in queries.fetch_traces(user_id, course_id)
        if (t.get("created_at") or "") <= until
    ]
    reconstructions = [
        r for r in queries.fetch_reconstructions(user_id, course_id)
        if (r.get("created_at") or "") <= until
    ]
    topic_atlas = queries.fetch_topic_atlas_binding(course_id)
    claim_topic_map = queries.fetch_claim_topic_map(course_id)
    return build_network(traces, reconstructions, topic_atlas, claim_topic_map)


def _bridge_key(edge: PersonalEdge) -> tuple:
    to_ref = edge.to_ref or {}
    return (edge.from_node_id, to_ref.get("ref_type"), to_ref.get("ref_id"))


def build_map_diff_facts(before: PersonalNetwork, after: PersonalNetwork) -> list[str]:
    """before → after の構造差分を肯定形の事実文にする（純粋関数・最大2件・数値なし）。

    見るのは (1) ノードの新規追加 (2) 橋（bridge edge）の新規追加のみ。どちらも
    「無かったものが無くなった／消えた」という否定形の断言は作らない（本人が
    dismiss/supersede した痕跡は個人ネットワーク導出そのものから除外されるため、
    「消えた」ように見えても実際は状態遷移であり、非存在の断言はできない）。
    """
    before_node_ids = {n.id for n in before.nodes}
    before_bridge_keys = {_bridge_key(e) for e in before.edges if e.edge_kind == "bridge"}

    node_by_id = {n.id: n for n in after.nodes}
    facts: list[str] = []

    for node in after.nodes:
        if len(facts) >= _MAX_MAP_DIFF_FACTS:
            return facts
        if node.id in before_node_ids:
            continue
        label = _excerpt(node.label)
        if not label:
            continue
        facts.append(f"前回の問いのあと、あなたの地図に『{label}』が加わっています。")

    for edge in after.edges:
        if len(facts) >= _MAX_MAP_DIFF_FACTS:
            return facts
        if edge.edge_kind != "bridge":
            continue
        key = _bridge_key(edge)
        if key in before_bridge_keys:
            continue
        from_node = node_by_id.get(edge.from_node_id)
        from_label = _excerpt(from_node.label) if from_node else ""
        if not from_label:
            continue
        facts.append(f"前回の問いのあと、『{from_label}』から自分でつないだ橋が増えています。")

    return facts[:_MAX_MAP_DIFF_FACTS]
