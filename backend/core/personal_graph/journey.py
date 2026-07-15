"""個人知識ネットワークの旅の経路探索（Journey, Phase P-2）。

設計の正本は ``docs/features/personal_knowledge_network_design.md`` §6。
非LLM・決定論的な traversal で、以下の5区間を辿る:

```
本人ノード（起点）
  → [1] 論文ローカルグラフ（theory_component_graphs.graph_json、main layer）
  → [2] element_identity_links（confirmed のみ・PN-6）
  → [3] L層ハブ（library_entries）→ 他インスタンス（当該コース内限定）
  → [4] atlas 骨格 node（topic binding）
  → [5] atlas node 近傍にある本人の別ノード
```

``derive.py`` と同じ流儀で純粋部/DB部を分離する: :func:`build_journey` は
モジュールトップで ``core.personal_graph.schema`` と標準ライブラリのみ import する
純粋関数（fake データだけでユニットテストできる）。:func:`journey_for_node` が
唯一の DB 起点で、``core.personal_graph.queries`` / ``core.personal_graph.derive`` を
関数内で遅延 import する。

境界（PN-5）: ``MAX_FANOUT_PER_SEGMENT`` / ``MAX_STEPS`` で fan-out・深さを固定する。
順序は決定論キー（created_at, id）。乱数・現在時刻に依存しない。

PN-6（リンクは confirmed のみ辿る）: 本モジュールは
``core.deliberation.identity_links.confirmed_links_for_document`` /
``list_for_shared_part``（confirmed のみへ絞り込み済み）を ``queries.py`` 経由でのみ読み、
confirmed 以外の状態にある同一性リンクを traversal の根拠にしない。
"""

from __future__ import annotations

from core.personal_graph.schema import ANCHOR_TYPE_COMPONENT, PersonalNetwork, PersonalNode

# ---------------------------------------------------------------------------
# 境界（PN-5）
# ---------------------------------------------------------------------------

MAX_FANOUT_PER_SEGMENT = 5
MAX_STEPS = 12

# structure_anchor 由来の "claim" は core.personal_graph.schema に定数が無く、
# derive.py でも生文字列のまま使われている（_reconstruction_node 参照）ため、ここでも
# 同じ慣例に合わせて生文字列で扱う。
_ANCHOR_CLAIM = "claim"

# [1]〜[3]（論文ローカルグラフ・同一性リンク・L層ハブ）を辿れるのは、起点アンカーが
# 具体的な論文内インスタンス（component/claim）を指しているときだけ。topic 粒度に
# 縮退したアンカーは document を特定できないため、これらの区間を飛ばして [4] へ直行する
# （設計書 §6 の縮退規則）。
_DOCUMENT_LOCAL_ANCHOR_TYPES = (ANCHOR_TYPE_COMPONENT, _ANCHOR_CLAIM)

_FRONTIER_NOTE = "ここから先はまだ道が無い（この要素の同一性リンクが未確定）"

_NODE_KIND_JA = {
    "tension": "引っかかり",
    "question": "問い",
    "reconstruction": "再構成",
}


def _node_kind_ja(node: PersonalNode) -> str:
    return _NODE_KIND_JA.get(node.node_kind, "要素")


def _sort_key(row: dict) -> tuple:
    """(created_at, id) の決定論順キー（derive.py と同じ規約）。"""
    return (str(row.get("created_at") or ""), str(row.get("id") or ""))


def _find_main_node(nodes: list[dict], anchor_type: str, anchor_id: str) -> dict | None:
    """anchor（component_id / claim_id）を含む main layer node を探す。

    - component: main node 自身の ``component_id`` が anchor と一致するか、
      anchor が main node の ``member_component_ids``（equation_detail 層の子ノード ID
      群。TheoryOperationGraph は式単位の step を theory stage 単位の main node に集約する
      — CLAUDE.md TheoryOperationGraph 節参照）に含まれる場合に一致とみなす。
    - claim: anchor が main node の ``linked_claim_ids`` に含まれる場合に一致とみなす。

    複数一致しうる場合は決定論順（display_order, component_id）で最初の1件を採用する。
    """
    main_nodes = [n for n in nodes if str(n.get("graph_layer") or "main") == "main"]
    main_nodes.sort(key=lambda n: (int(n.get("display_order") or 0), str(n.get("component_id") or "")))
    for node in main_nodes:
        if anchor_type == ANCHOR_TYPE_COMPONENT:
            if str(node.get("component_id") or "") == anchor_id:
                return node
            member_ids = [str(m) for m in (node.get("member_component_ids") or [])]
            if anchor_id in member_ids:
                return node
        else:
            linked_claims = [str(c) for c in (node.get("linked_claim_ids") or [])]
            if anchor_id in linked_claims:
                return node
    return None


def _adjacent_main_nodes(local_graph: dict, main_id: str, nodes_by_id: dict[str, dict]) -> list[dict]:
    """main_id を持つ main node に 1 hop で隣接する、他の main layer node を決定論順で返す。"""
    edges = local_graph.get("edges") if isinstance(local_graph.get("edges"), list) else []
    neighbor_ids: list[str] = []
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        src = str(edge.get("source_component_id") or edge.get("source") or "")
        tgt = str(edge.get("target_component_id") or edge.get("target") or "")
        if src == main_id and tgt and tgt != main_id and tgt not in neighbor_ids:
            neighbor_ids.append(tgt)
        elif tgt == main_id and src and src != main_id and src not in neighbor_ids:
            neighbor_ids.append(src)
    neighbor_ids.sort()
    result: list[dict] = []
    for node_id in neighbor_ids:
        node = nodes_by_id.get(node_id)
        if node is not None and str(node.get("graph_layer") or "main") == "main":
            result.append(node)
    return result


def _resolved_atlas_node_id(node: PersonalNode, topic_atlas: dict[str, str]) -> str | None:
    """[4]/[5] で使う atlas_node_id の解決。

    アンカー自身が atlas_node_id を持つ場合（topic 粒度に縮退した N1/N3）はそれを使い、
    無ければ ``topic_id`` からコース⇄地図バインディングで解決する（component/claim/構造
    アンカー付きの N2 等、アンカー自体には atlas_node_id が乗らないノードのフォールバック）。
    """
    return node.anchor.atlas_node_id or topic_atlas.get(str(node.topic_id or "")) or None


def build_journey(
    start_node: PersonalNode,
    network: PersonalNetwork,
    local_graph: dict,
    identity_links: list[dict],
    library_entries: dict[str, dict],
    topic_atlas: dict[str, str],
    course_document_ids: set,
) -> dict:
    """§6 の旅の traversal 本体（純粋関数・非LLM・決定論）。

    引数はすべて事前に読み込み済みのプレーンなデータ（dict/list/set）で、DB へは一切
    アクセスしない。呼び出し側（:func:`journey_for_node`）の責務:

    - ``local_graph``: 起点アンカーが component/claim のときの、対応する document の
      theory_component_graphs.graph_json（無関係なら ``{}``）。
    - ``identity_links``: 起点要素（component/claim）に対応する confirmed 同一性リンクのみ
      （PN-6。confirmed 以外の状態のものは含まれない）。既に決定論順・fan-out 上限で絞り込み済み
      だが、本関数側でも再度上限を適用する（fake データのみで fan-out の切り詰めを
      ユニットテストできるようにするため）。
    - ``library_entries``: ``identity_links`` に現れる shared_part_id →
      ``{"name": str, "other_documents": [{"document_id": str, "title": str}, ...]}``。
      ``other_documents`` はコース内限定でフィルタ**する前**の候補（コース内限定の
      fail-closed フィルタは本関数が ``course_document_ids`` を使って行う）。
    - ``topic_atlas``: ``queries.fetch_topic_atlas_binding`` と同じ形（topic_id → atlas_node_id）。
    - ``course_document_ids``: 当該コースの sources に含まれる document_id 集合
      （[3] のコース内限定フィルタに使う。§6 の権限規則のサブセット = v1 は当該コース内に限定する）。

    戻り値は ``{"steps": [...], "frontier_note": str | None, "truncated": bool}``。
    """
    steps: list[dict] = []
    frontier_note: str | None = None

    anchor = start_node.anchor
    anchor_type = anchor.anchor_type
    anchor_id = anchor.anchor_id
    kind_ja = _node_kind_ja(start_node)

    # ---- [1] 論文ローカルグラフ -------------------------------------------------
    if anchor_type in _DOCUMENT_LOCAL_ANCHOR_TYPES and isinstance(local_graph, dict):
        all_nodes = [
            n for n in (local_graph.get("nodes") or [])
            if isinstance(n, dict) and n.get("component_id")
        ]
        nodes_by_id = {str(n["component_id"]): n for n in all_nodes}
        main_node = _find_main_node(all_nodes, anchor_type, anchor_id)
        if main_node is not None:
            main_id = str(main_node.get("component_id") or "")
            main_label = str(main_node.get("label") or main_id)
            steps.append({
                "fact": f"この{kind_ja}は理論構成『{main_label}』の一部です",
                "ref": {"kind": "graph_node", "id": main_id, "label": main_label},
            })
            neighbors = _adjacent_main_nodes(local_graph, main_id, nodes_by_id)
            for neighbor in neighbors[:MAX_FANOUT_PER_SEGMENT]:
                n_label = str(neighbor.get("label") or neighbor.get("component_id") or "")
                steps.append({
                    "fact": f"『{main_label}』は『{n_label}』ともつながっています",
                    "ref": {
                        "kind": "graph_node",
                        "id": str(neighbor.get("component_id") or ""),
                        "label": n_label,
                    },
                })

    # ---- [2] 同一性リンク（confirmed のみ・PN-6） + [3] L層ハブ → 他インスタンス ----
    if anchor_type in _DOCUMENT_LOCAL_ANCHOR_TYPES:
        capped_links = sorted(identity_links, key=_sort_key)[:MAX_FANOUT_PER_SEGMENT]

        shared_part_ids_in_order: list[str] = []
        for link in capped_links:
            shared_part_id = str(link.get("shared_part_id") or "")
            if not shared_part_id:
                continue
            entry = library_entries.get(shared_part_id)
            if entry is None:
                # active な library_entry としてハブ化できなかった shared_part
                # （retired・存在しない）。§6/§10「L層ハブとして使うのは active 行のみ」の
                # 前提条件を満たさないため、この shared_part を経由した hop は一切生成しない
                # （generic な「共通部品」名へのフォールバックもしない）。
                continue
            name = entry.get("name") or "共通部品"
            steps.append({
                "fact": f"この{kind_ja}は共通部品『{name}』の教材での表れとして確認されています",
                "ref": {"kind": "shared_part", "id": shared_part_id, "label": name},
            })
            if shared_part_id not in shared_part_ids_in_order:
                shared_part_ids_in_order.append(shared_part_id)

        if not shared_part_ids_in_order:
            # 同一性リンク区間から有効な（active な）ハブが1件も得られなかったときだけ、
            # 経路が途切れたことを正直に伝える（リンクが空だった場合と、リンクはあるが
            # すべて retired だった場合の両方をここに集約する。エラー・警告文体にしない。
            # D層「空欄は発見」と同族）。
            frontier_note = _FRONTIER_NOTE

        # [3]: 各 shared_part の他インスタンス。当該コースの sources に含まれる
        # document のみ（fail-closed: コース外は件数にも言及せず省く）。区間全体の
        # fan-out 上限は shared_part をまたいで合算する（1 shared_part あたりではない）。
        hub_hits: list[tuple[str, str, str]] = []  # (shared_part_name, document_id, title)
        for shared_part_id in shared_part_ids_in_order:
            entry = library_entries.get(shared_part_id) or {}
            name = entry.get("name") or "共通部品"
            for doc in entry.get("other_documents") or []:
                if isinstance(doc, dict):
                    doc_id = str(doc.get("document_id") or "")
                    title = str(doc.get("title") or "")
                else:
                    doc_id = str(doc or "")
                    title = ""
                if not doc_id or doc_id not in course_document_ids:
                    continue
                hub_hits.append((name, doc_id, title))
        for name, doc_id, title in hub_hits[:MAX_FANOUT_PER_SEGMENT]:
            label = title or "別の教材"
            steps.append({
                "fact": f"『{name}』は{label}にも現れます",
                "ref": {"kind": "document", "id": doc_id, "label": label},
            })

    # ---- [4] atlas 骨格 node ------------------------------------------------------
    atlas_node_id = _resolved_atlas_node_id(start_node, topic_atlas)
    if atlas_node_id:
        label = anchor.anchor_label or ""
        fact = f"地図の『{label}』にあります" if label else "地図の対応する場所にあります"
        steps.append({
            "fact": fact,
            "ref": {"kind": "atlas_node", "id": str(atlas_node_id), "label": label},
        })

        # ---- [5] atlas node 近傍にある本人の別ノード ------------------------------
        siblings = [
            other for other in network.nodes
            if other.id != start_node.id
            and _resolved_atlas_node_id(other, topic_atlas) == atlas_node_id
        ]
        siblings.sort(key=lambda n: (n.created_at, n.id))
        for sibling in siblings[:MAX_FANOUT_PER_SEGMENT]:
            steps.append({
                "fact": f"あなたの別の{_node_kind_ja(sibling)}『{sibling.label}』もこの近くにあります",
                "ref": {"kind": "personal_node", "id": sibling.id, "label": sibling.label},
            })

    truncated = len(steps) > MAX_STEPS
    if truncated:
        steps = steps[:MAX_STEPS]

    return {"steps": steps, "frontier_note": frontier_note, "truncated": truncated}


def journey_for_node(
    user_id: str,
    course_id: str,
    node_id: str,
    can_view_document=None,
) -> dict | None:
    """エントリポイント（設計書 §6/§7）。

    本人の個人ネットワークを導出し、``node_id`` が指すノードを起点に旅の traversal を行う。
    ``node_id`` が本人の個人ネットワークに存在しなければ ``None``（呼び出し側が 404 に写像）。

    DB 読み取りはすべて ``core.personal_graph.queries`` 経由（``core.personal_graph`` の中で
    DB を直接知るのはそのファイルだけ、という queries.py 自身の docstring の規約を維持する）。

    ``can_view_document``: ``(user_id, document_id) -> bool`` の権限判定コールバック
    （省略可）。``core/personal_graph/`` は FastAPI / services を import しない規約
    （PN-5・ガードレール）なので、``services.user_can_view_document`` 等の実体は
    呼び出し側（``routes/personal_map.py``）が注入する。起点アンカーから document を
    解決できても、この判定が False を返す場合は document が見つからなかった場合と
    同じ扱い（[1]〜[3] 区間を丸ごと省き topic 粒度相当へ縮退）にする（PN-7: 学習者が
    閲覧できない document への hop は黙って省く）。省略時（None）は後方互換のため
    従来どおり判定をスキップする——呼び出し側は必ず渡すこと。
    """
    from core.personal_graph import queries  # 遅延 import（derive.py と同じ理由）
    from core.personal_graph.derive import derive_personal_network

    network = derive_personal_network(user_id, course_id)
    start_node = next((n for n in network.nodes if n.id == node_id), None)
    if start_node is None:
        return None

    anchor = start_node.anchor
    local_graph: dict = {}
    matched_links: list[dict] = []
    library_entries: dict[str, dict] = {}

    if anchor.anchor_type in _DOCUMENT_LOCAL_ANCHOR_TYPES:
        if anchor.anchor_type == ANCHOR_TYPE_COMPONENT:
            document_id = queries.fetch_component_document_id(anchor.anchor_id)
        else:
            document_id = queries.fetch_claim_document_id(anchor.anchor_id)

        # PN-7 fail-closed: 起点アンカーが解決した document 自体を学習者が閲覧できなければ、
        # ローカルグラフ・同一性リンクを一切読まず黙って [1]〜[3] を省く（document が
        # 見つからなかった場合と同じ経路。件数にも言及しない）。
        if document_id and callable(can_view_document) and not can_view_document(user_id, document_id):
            document_id = None

        if document_id:
            local_graph = queries.fetch_component_graph(document_id)

            raw_links = queries.fetch_confirmed_identity_links(document_id)
            instance_element_type = (
                "theory_component" if anchor.anchor_type == ANCHOR_TYPE_COMPONENT else "theory_claim"
            )
            matched_links = sorted(
                (
                    row for row in raw_links
                    if row.get("instance_element_type") == instance_element_type
                    and str(row.get("instance_element_id") or "") == str(anchor.anchor_id)
                ),
                key=_sort_key,
            )[:MAX_FANOUT_PER_SEGMENT]

            shared_part_ids: list[str] = []
            for row in matched_links:
                shared_part_id = str(row.get("shared_part_id") or "")
                if shared_part_id and shared_part_id not in shared_part_ids:
                    shared_part_ids.append(shared_part_id)

            if shared_part_ids:
                # fetch_library_entry_names は status='active' の行のみ返す。retired・存在
                # しない shared_part はここで名前が取れず、`library_entries` に一切キーを
                # 積まない（`build_journey` 側は entry が無いことを「active でない＝ハブに
                # しない」の判定に使う。§6/§10 の active-only 前提を journey_for_node と
                # build_journey の双方で守る）。
                names = queries.fetch_library_entry_names(shared_part_ids)
                for shared_part_id in shared_part_ids:
                    if shared_part_id not in names:
                        continue
                    other_doc_ids: set[str] = set()
                    for link_row in queries.fetch_confirmed_links_for_shared_part(shared_part_id):
                        other_doc = str(link_row.get("instance_document_id") or "")
                        if other_doc and other_doc != document_id:
                            other_doc_ids.add(other_doc)
                    for src_doc in queries.fetch_library_entry_source_document_ids(shared_part_id):
                        src_doc = str(src_doc or "")
                        if src_doc and src_doc != document_id:
                            other_doc_ids.add(src_doc)
                    sorted_other = sorted(other_doc_ids)
                    titles = queries.fetch_document_titles(sorted_other)
                    library_entries[shared_part_id] = {
                        "name": names.get(shared_part_id, ""),
                        "other_documents": [
                            {"document_id": d, "title": titles.get(d, "")}
                            for d in sorted_other
                        ],
                    }

    topic_atlas = queries.fetch_topic_atlas_binding(course_id)
    course_document_ids = queries.fetch_course_document_ids(course_id)

    return build_journey(
        start_node,
        network,
        local_graph,
        matched_links,
        library_entries,
        topic_atlas,
        course_document_ids,
    )
