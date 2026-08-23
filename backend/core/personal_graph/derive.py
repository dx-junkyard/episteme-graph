"""個人知識ネットワークの導出（設計書 §4）。非LLM・決定論的な純粋関数。

``build_network`` は sqlalchemy 等の DB 依存を一切持たない純粋関数であり、fake rows
（dict のリスト）だけでローカル検証できる。**モジュールトップで ``core.personal_graph.queries``
を import しない** — ローカル環境に sqlalchemy が無くても純粋部を検証できるようにするため
（``derive_personal_network`` 内で遅延 import する）。
"""

from __future__ import annotations

from core.personal_graph.schema import (
    ANCHOR_TYPE_COMPONENT,
    ANCHOR_TYPE_GRAPH_EDGE,
    ANCHOR_TYPE_TOPIC,
    EDGE_KIND_BRIDGE,
    NODE_KIND_QUESTION,
    NODE_KIND_RECONSTRUCTION,
    NODE_KIND_TENSION,
    PersonalAnchor,
    PersonalEdge,
    PersonalNetwork,
    PersonalNode,
)

# TENSION_OWNED_STATUSES の正本は core/tension/schema.py（再定義禁止・CLAUDE.md）。
from core.tension.schema import TENSION_OWNED_STATUSES

# structure_anchor の帰属語彙・status 語彙の正本も core/structure_anchor/schema.py
# （anchor_type の粒度語彙も同モジュールが正本。ここでは同意判定に使う値のみ import する）。
from core.structure_anchor.schema import (
    ANCHOR_STATUS_ACTIVE,
    ATTRIBUTION_CONFIRMED,
    ATTRIBUTION_LEARNER_SELECTED,
)

_TRACE_LABEL_LIMIT = 80

# N2 の「本人確定」とみなす attribution_source（§2「同意の汲み取り」層1: 明示 confirm 必須）
_ATTRIBUTION_OWNED = (ATTRIBUTION_LEARNER_SELECTED, ATTRIBUTION_CONFIRMED)

# N4 で「本人が異議を挟んだ」とみなす self_check（§2「採用しない痕跡」）
_SELF_CHECK_DISAGREE = ("disagreed", "verdict_wrong")

#: N4 のラベルが元 claim 本文から引けないときのフォールバック（従来の固定文字列）。
_RECONSTRUCTION_FALLBACK_LABEL = "claim への再構成"

_BRIDGE_FACT = "この引っかかりを自分でつないだ"
_FACT_REVISED = "改訂を経てたどり着いた再構成"
_FACT_DESCENDED = "原因を絞るため記号まで降りた"


def _truncate(text: str | None, limit: int = _TRACE_LABEL_LIMIT) -> str:
    return (text or "").strip()[:limit]


def _sort_key(row: dict) -> tuple:
    """(created_at, id) の決定論順キー（§4）。両方とも文字列として辞書順比較する。"""
    return (str(row.get("created_at") or ""), str(row.get("id") or ""))


def _tension_label(payload: dict) -> str:
    """payload.learner_text（articulate 済み）優先、無ければ payload.text（evidence_quote 由来）。"""
    learner_text = (payload.get("learner_text") or "").strip()
    if learner_text:
        return _truncate(learner_text)
    return _truncate(payload.get("text"))


def _topic_anchor(
    topic_id: str,
    topic_atlas: dict[str, str],
    topic_labels: dict[str, str] | None,
) -> PersonalAnchor:
    """topic 粒度への縮退アンカーを組み立てる（``_tension_anchor`` / ``_question_anchor`` 共用）。

    ``anchor_label`` には**トピック題名のみ**を入れる（``topic_labels`` は
    ``queries.fetch_topic_labels`` が ``topics[].title`` から決定論的に組み立てた
    {topic_id: title}）。題名が引けない topic（コース削除済み・title 未設定）は
    従来どおり空文字のまま — 発話原文などの別テキストで埋めない（捏造しない・P4）。
    """
    return PersonalAnchor(
        anchor_type=ANCHOR_TYPE_TOPIC,
        anchor_id=topic_id,
        anchor_label=(topic_labels or {}).get(topic_id, ""),
        atlas_node_id=topic_atlas.get(topic_id),
    )


def _tension_anchor(
    trace: dict,
    topic_atlas: dict[str, str],
    *,
    topic_labels: dict[str, str] | None = None,
) -> PersonalAnchor:
    """§2: 本人が connect 操作を経た tension（``status='connected'``）のみ、
    ``payload.connected_refs.component_ids / edge_ids`` をアンカーに使う。

    ``payload.target_refs`` は使わない — tension は LLM 候補生成時点で
    ``target_refs``（component_ids 等）が payload に書かれており（
    ``core/tension/worker.py`` → ``candidate_to_payload``）、``confirm_tension_trace`` は
    confirm 時にその payload をそのまま残して status だけを変える。したがって
    ``status`` を見ずに ``target_refs`` を読むと、本人が接続操作をしていない
    LLM 帰属の component がアンカーになってしまう（PN-3 違反）。

    ``connected_refs`` は ``connect_tension_trace``（``backend/api/services.py``）が
    connect 時に **本人が指定した component_id / edge_id のみ**を書く別キーであり、
    LLM 由来の ``target_refs`` とは区別される。``connected_refs`` を持たない
    connect 済み行（本機能未コミット時点のデータには存在しないが、将来の後方互換のため）は
    topic 粒度へ fail-closed に縮退する。

    topic 粒度へ縮退した場合の ``anchor_label`` はトピック題名（``topic_labels``、既定
    ``None``＝従来どおり空文字）。``_topic_anchor`` 参照。
    """
    payload = trace.get("payload") or {}
    if trace.get("status") == "connected":
        refs = payload.get("connected_refs") or {}
        component_ids = [str(c) for c in (refs.get("component_ids") or []) if c]
        edge_ids = [str(e) for e in (refs.get("edge_ids") or []) if e]
        if component_ids:
            return PersonalAnchor(anchor_type=ANCHOR_TYPE_COMPONENT, anchor_id=component_ids[0])
        if edge_ids:
            return PersonalAnchor(anchor_type=ANCHOR_TYPE_GRAPH_EDGE, anchor_id=edge_ids[0])
    return _topic_anchor(str(trace.get("topic_id") or ""), topic_atlas, topic_labels)


def _tension_node(
    trace: dict,
    topic_atlas: dict[str, str],
    *,
    topic_labels: dict[str, str] | None = None,
) -> PersonalNode:
    payload = trace.get("payload") or {}
    return PersonalNode(
        id=str(trace["id"]),
        node_kind=NODE_KIND_TENSION,
        label=_tension_label(payload),
        anchor=_tension_anchor(trace, topic_atlas, topic_labels=topic_labels),
        topic_id=trace.get("topic_id"),
        created_at=str(trace.get("created_at") or ""),
        facts=[],
        source={"kind": "tension", "status": trace.get("status") or ""},
    )


def _tension_bridge_edges(trace: dict) -> list[PersonalEdge]:
    """§3: status='connected' の tension について ``connected_refs``
    （本人が connect 操作で明示的に指定した component_ids / edge_ids のみ。
    ``_tension_anchor`` と同じ理由で LLM 候補由来の ``target_refs`` は使わない・PN-3）
    の各要素へ橋を張る。"""
    payload = trace.get("payload") or {}
    refs = payload.get("connected_refs") or {}
    trace_id = str(trace["id"])
    edges: list[PersonalEdge] = []
    for cid in refs.get("component_ids") or []:
        if not cid:
            continue
        edges.append(PersonalEdge(
            edge_kind=EDGE_KIND_BRIDGE,
            from_node_id=trace_id,
            to_ref={"ref_type": "component", "ref_id": str(cid)},
            fact=_BRIDGE_FACT,
        ))
    for eid in refs.get("edge_ids") or []:
        if not eid:
            continue
        edges.append(PersonalEdge(
            edge_kind=EDGE_KIND_BRIDGE,
            from_node_id=trace_id,
            to_ref={"ref_type": "graph_edge", "ref_id": str(eid)},
            fact=_BRIDGE_FACT,
        ))
    return edges


def _question_anchor(
    trace: dict,
    topic_atlas: dict[str, str],
    *,
    topic_labels: dict[str, str] | None = None,
) -> tuple[PersonalAnchor, str]:
    """§2 N2/N3: structure_anchor が本人確定済み（learner_selected/confirmed・active）なら
    そのアンカーを使う（N2）。それ以外（無し・llm_candidate・dismissed）は topic 粒度へ
    縮退する（N3）。**llm_candidate の anchor_id は絶対に使わない**（PN-3）。

    N2 の ``anchor_label`` は structure_anchor 側の値をそのまま使う（トピック題名で
    上書きしない）。N3（topic 縮退）のときだけ ``topic_labels`` からトピック題名を入れる
    （``_topic_anchor`` 参照。既定 ``None`` は従来どおり空文字）。

    戻り値は (anchor, attribution_source_for_source_meta)。
    """
    payload = trace.get("payload") or {}
    structure_anchor = payload.get("structure_anchor")
    if (
        isinstance(structure_anchor, dict)
        and structure_anchor.get("status") == ANCHOR_STATUS_ACTIVE
        and structure_anchor.get("attribution_source") in _ATTRIBUTION_OWNED
    ):
        anchor = PersonalAnchor(
            anchor_type=str(structure_anchor.get("anchor_type") or ANCHOR_TYPE_TOPIC),
            anchor_id=str(structure_anchor.get("anchor_id") or ""),
            anchor_label=str(structure_anchor.get("anchor_label") or ""),
        )
        return anchor, str(structure_anchor.get("attribution_source") or "")

    anchor = _topic_anchor(str(trace.get("topic_id") or ""), topic_atlas, topic_labels)
    attribution = ""
    if isinstance(structure_anchor, dict):
        attribution = str(structure_anchor.get("attribution_source") or "")
    return anchor, attribution


def _question_node(
    trace: dict,
    topic_atlas: dict[str, str],
    *,
    topic_labels: dict[str, str] | None = None,
) -> PersonalNode:
    payload = trace.get("payload") or {}
    anchor, attribution_source = _question_anchor(trace, topic_atlas, topic_labels=topic_labels)
    return PersonalNode(
        id=str(trace["id"]),
        node_kind=NODE_KIND_QUESTION,
        label=_truncate(payload.get("text")),
        anchor=anchor,
        topic_id=trace.get("topic_id"),
        created_at=str(trace.get("created_at") or ""),
        facts=[],
        source={
            "kind": "question",
            "status": trace.get("status") or "",
            "attribution_source": attribution_source,
        },
    )


def _reconstruction_terminal_ids(reconstructions: list[dict]) -> set[str]:
    """revision_of チェーンの終端行（他行の revision_of に自分の id が現れない行）の id 集合。"""
    referenced = {str(r["revision_of"]) for r in reconstructions if r.get("revision_of")}
    return {str(r["id"]) for r in reconstructions if str(r["id"]) not in referenced}


def _reconstruction_chain_facts(root_id: str, by_id: dict[str, dict]) -> list[str]:
    """終端行から revision_of を遡り、改訂・記号降下の事実を集める（回数は出さない。PN-4）。"""
    revised = False
    descended = False
    seen: set[str] = set()
    node = by_id.get(root_id)
    while node is not None and node["id"] not in seen:
        seen.add(str(node["id"]))
        if node.get("descended_to_symbol"):
            descended = True
        prev_id = node.get("revision_of")
        if prev_id:
            revised = True
            node = by_id.get(str(prev_id))
        else:
            node = None

    facts: list[str] = []
    if revised:
        facts.append(_FACT_REVISED)
    if descended:
        facts.append(_FACT_DESCENDED)
    return facts


def _reconstruction_node(
    row: dict,
    by_id: dict[str, dict],
    claim_topic_map: dict[str, str] | None = None,
    topic_atlas: dict[str, str] | None = None,
) -> PersonalNode:
    """N4 ノードを組み立てる。

    ``topic_id`` は既定 ``None``（claim アンカーは topic 粒度の帰属を持たないため）だが、
    N16 是正として ``claim_topic_map``（``queries.fetch_claim_topic_map`` が
    ``topics[].linked_claim_ids`` から逆引きした {claim_id: topic_id}）で claim が
    実際にいずれかのトピック教材へ組み込まれていることが分かれば、そのトピックを
    ``topic_id`` に設定する。さらにそのトピックがコース⇄地図バインディング
    （``topic_atlas``）を持てば ``anchor.atlas_node_id`` にも反映する（アンカー自体は
    claim のまま — atlas_node_id は topic 由来の binding からの導出という P-0 の意味論を
    維持する）。これにより journey の [4]/[5]（atlas 骨格・コーススコープ近傍）区間と
    「わたしの地図」のドット配置が reconstruction ノードでも成立する（決定論・非LLM。
    ``course_content_builder.py`` が component 経由で書き込んだ既存マッピングを読むだけ）。
    解決できない claim（どのトピックにも組み込まれていない）は従来どおり ``None``
    のまま — 「まだ地図にない」トレイに残る（P4: 情報を落とさない）。

    ``label`` は元 claim の本文（``row["claim_text"]`` = ``theory_claims.text``。
    ``queries.fetch_reconstructions{,_for_user}`` が LEFT JOIN で併読する）を他ノードと
    同じ80字規則で切ったもの。本人が REVEAL（出典開示）まで見た成功終端行だけがここに
    来るため、claim 本文の提示は伏せフィールド（R層 item の ``response_space`` /
    ``expected``）の漏洩には当たらない。claim 本文が引けない行（列が無い古い呼び出し・
    claim 削除済み）は従来の固定ラベルへフォールバックする（後方互換）。
    """
    row_id = str(row["id"])
    claim_id = str(row.get("claim_id") or "")
    topic_id = (claim_topic_map or {}).get(claim_id)
    atlas_node_id = (topic_atlas or {}).get(str(topic_id)) if topic_id else None
    label = _truncate(row.get("claim_text")) or _RECONSTRUCTION_FALLBACK_LABEL
    return PersonalNode(
        id=row_id,
        node_kind=NODE_KIND_RECONSTRUCTION,
        label=label,
        anchor=PersonalAnchor(
            anchor_type="claim", anchor_id=claim_id, atlas_node_id=atlas_node_id,
        ),
        topic_id=topic_id,
        created_at=str(row.get("created_at") or ""),
        facts=_reconstruction_chain_facts(row_id, by_id),
        source={
            "kind": "reconstruction",
            "item_id": str(row.get("item_id") or ""),
            "machine_verdict": row.get("machine_verdict") or "",
            "self_check": row.get("self_check"),
        },
    )


def build_network(
    traces: list[dict],
    reconstructions: list[dict],
    topic_atlas: dict[str, str],
    claim_topic_map: dict[str, str] | None = None,
    *,
    topic_labels: dict[str, str] | None = None,
) -> PersonalNetwork:
    """§4 の導出アルゴリズム本体（純粋関数・非LLM・決定論）。

    ``claim_topic_map``（N16 是正, 既定 None＝後方互換）は {claim_id: topic_id} で、
    reconstruction ノードの ``topic_id`` 解決に使う（``_reconstruction_node`` 参照）。
    省略時は従来どおり全 reconstruction ノードの ``topic_id`` が ``None`` になる。

    ``topic_labels``（既定 None＝後方互換）は {topic_id: トピック題名}
    （``queries.fetch_topic_labels``）。topic 粒度へ縮退したアンカー（N3 と未接続 tension）の
    ``anchor_label`` にトピック題名を入れるためだけに使う。省略時は従来どおり空文字
    （題名の無い topic も空のまま。発話原文で埋めない・P4）。

    - N1 tension: ``kind='tension'`` かつ ``status in TENSION_OWNED_STATUSES`` のみ採用。
      ``status='connected'`` の行だけ bridge 辺を張る。
    - N2/N3 question: ``kind='question'`` かつ ``status != 'superseded'`` は必ずノード化する
      （structure_anchor の確定状況によって N2/N3 いずれかのアンカーへ解決するだけで、
      「帰属なし」は棄却理由にならない）。
    - N4 reconstruction: revision_of チェーンの終端行のみを対象に、
      ``machine_verdict='match'`` かつ ``self_check`` が異議シグナル
      （disagreed/verdict_wrong）でなければ採用する（NULL は異議なしとして含める）。
    - ``payload.map_excluded`` が truthy な trace（tension/question 両方）は本人が
      「地図には反映しない」を選んだ痕跡として導出から除外する（提案書 §6。行自体は
      保持され dismiss/supersede と同様に導出対象から外れるだけ・P4。書き込み側は別
      エージェントが実装する既存/将来 API の責務で、本関数はこのキーを読むだけ）。
    - 出力順は nodes・edges とも (created_at, id) の決定論順。
    """
    nodes: list[PersonalNode] = []
    edges: list[PersonalEdge] = []

    traces_sorted = sorted(traces, key=_sort_key)
    for trace in traces_sorted:
        kind = trace.get("kind")
        status = trace.get("status")
        payload = trace.get("payload") or {}
        if payload.get("map_excluded"):
            # 本人が「地図には反映しない」を選んだ痕跡（提案書 §6）。行は保持されるが
            # 個人ネットワークのノードにはしない（P4: 削除ではなく導出対象から外すだけ）。
            continue
        if kind == "tension":
            if status not in TENSION_OWNED_STATUSES:
                continue
            nodes.append(_tension_node(trace, topic_atlas, topic_labels=topic_labels))
            if status == "connected":
                edges.extend(_tension_bridge_edges(trace))
        elif kind == "question":
            if status == "superseded":
                continue
            nodes.append(_question_node(trace, topic_atlas, topic_labels=topic_labels))

    recon_sorted = sorted(reconstructions, key=_sort_key)
    by_id = {str(r["id"]): r for r in recon_sorted}
    terminal_ids = _reconstruction_terminal_ids(recon_sorted)
    for row in recon_sorted:
        row_id = str(row["id"])
        if row_id not in terminal_ids:
            continue
        if row.get("machine_verdict") != "match":
            continue
        if row.get("self_check") in _SELF_CHECK_DISAGREE:
            continue
        nodes.append(_reconstruction_node(row, by_id, claim_topic_map, topic_atlas))

    nodes.sort(key=lambda n: (n.created_at, n.id))
    return PersonalNetwork(nodes=nodes, edges=edges)


def derive_personal_network(user_id: str, course_id: str) -> PersonalNetwork:
    """エントリポイント（設計書 §4）。DB 読み取りは queries.py に委譲する。

    ``queries`` はここで遅延 import する（モジュールトップで import すると sqlalchemy が
    ローカル環境に無い場合に ``build_network`` 単体の検証まで巻き込んで壊れるため）。
    """
    from core.personal_graph import queries  # 遅延 import（理由は docstring 参照）

    traces = queries.fetch_traces(user_id, course_id)
    reconstructions = queries.fetch_reconstructions(user_id, course_id)
    topic_atlas = queries.fetch_topic_atlas_binding(course_id)
    claim_topic_map = queries.fetch_claim_topic_map(course_id)
    topic_labels = queries.fetch_topic_labels(course_id)
    return build_network(
        traces, reconstructions, topic_atlas, claim_topic_map, topic_labels=topic_labels,
    )


# ---------------------------------------------------------------------------
# Phase P-0.5（意味論移行, 設計書 §5.1〜5.3）
#
# ``build_network``（上記）は単一コーススコープの導出であり、既存の
# ``derive_personal_network`` / ``GET /api/learning/courses/{course_id}/personal-network``
# はそのままコースビューとして維持する（互換）。以下は本人スコープ（``/api/me/...``）の
# 正本導出で、複数コースの痕跡を1つの個人ネットワークへ束ねる。
# ---------------------------------------------------------------------------


def build_person_network(
    traces: list[dict],
    reconstructions: list[dict],
    atlas_by_course: dict[str, dict[str, str]],
    claim_topic_by_course: dict[str, dict[str, str]] | None = None,
    *,
    topic_labels_by_course: dict[str, dict[str, str]] | None = None,
) -> PersonalNetwork:
    """本人スコープの導出（純粋関数・非LLM・決定論）。設計書 §5.1/§5.2。

    rows を ``course_id`` でグルーピングし、各コースについて既存 ``build_network`` を
    そのコースの topic_atlas（``atlas_by_course.get(course_id, {})``）で呼び、返った
    ノードに ``course_id`` をスタンプしてマージする。

    ``claim_topic_by_course``（N16 是正, 既定 None＝後方互換）は
    ``{course_id: {claim_id: topic_id}}``。各コースの ``build_network`` 呼び出しへ
    そのコース分の claim_topic_map を渡し、reconstruction ノードの topic 解決に使う。
    ``topic_labels_by_course``（既定 None＝後方互換）は ``{course_id: {topic_id: title}}``
    で、同様にそのコース分だけを ``build_network`` へ渡す（topic 縮退アンカーの
    ``anchor_label``。コース題名ではなくトピック題名であることに注意）。最後に nodes を (created_at, id) で
    再ソートする。edges はコースIDの辞書順に連結する（決定論。edges 自体は再ソートしない
    — ``build_network`` が既にコース内で決定論順に組み立てている）。

    ``course_id`` は所有境界ではなく provenance（設計書 §5.1）: 同じユーザーの複数コースの
    痕跡が1つのネットワークにマージされる（KN-1 egocentric の自然な帰結）。

    reconstruction の revision チェーンはコース内で閉じている前提（コースをまたぐ
    ``revision_of`` は現実に発生しない — 再構成は常に同一コースの同一 ELICIT item への
    提出であり、item 自体がコーススコープで生成される。したがって
    ``_reconstruction_terminal_ids`` / ``_reconstruction_chain_facts`` の遡行をコース単位
    ``build_network`` 呼び出しに閉じ込めても、チェーンが分断される実害は無い）。
    """
    traces_by_course: dict[str, list[dict]] = {}
    for trace in traces:
        course_id = str(trace.get("course_id") or "")
        traces_by_course.setdefault(course_id, []).append(trace)

    recon_by_course: dict[str, list[dict]] = {}
    for row in reconstructions:
        course_id = str(row.get("course_id") or "")
        recon_by_course.setdefault(course_id, []).append(row)

    course_ids = sorted(set(traces_by_course) | set(recon_by_course))

    all_nodes: list[PersonalNode] = []
    all_edges: list[PersonalEdge] = []
    for course_id in course_ids:
        topic_atlas = atlas_by_course.get(course_id, {})
        claim_topic_map = (claim_topic_by_course or {}).get(course_id)
        sub_network = build_network(
            traces_by_course.get(course_id, []),
            recon_by_course.get(course_id, []),
            topic_atlas,
            claim_topic_map,
            topic_labels=(topic_labels_by_course or {}).get(course_id),
        )
        for node in sub_network.nodes:
            node.course_id = course_id
        all_nodes.extend(sub_network.nodes)
        all_edges.extend(sub_network.edges)

    all_nodes.sort(key=lambda n: (n.created_at, n.id))
    return PersonalNetwork(nodes=all_nodes, edges=all_edges)


def group_nodes_by_anchor(network: PersonalNetwork) -> list[dict]:
    """同じ公共アンカーをコースごとに複製しないためのレスポンス表現（設計書 §5.2）。

    キーは ``(anchor_type, anchor_id)``（``anchor_id`` が非空のノードのみ対象。topic
    アンカーが未解決で anchor_id が空文字のノードはどのグループにも属さない）。

    各グループは ``{"anchor": {...}, "node_ids": [...], "course_ids": [出現順ユニーク]}``。
    ``anchor`` はそのグループの先頭ノード（``network.nodes`` の並び=決定論順で最初に
    現れたノード）の ``anchor.to_dict()``。件数フィールドは付けない（PN-4）。

    戻り値は ``(anchor_type, anchor_id)`` の辞書順にソートする（決定論）。
    """
    groups: dict[tuple[str, str], dict] = {}
    for node in network.nodes:
        anchor_id = node.anchor.anchor_id
        if not anchor_id:
            continue
        key = (node.anchor.anchor_type, anchor_id)
        entry = groups.get(key)
        if entry is None:
            entry = {
                "anchor": node.anchor.to_dict(),
                "node_ids": [],
                "course_ids": [],
            }
            groups[key] = entry
        entry["node_ids"].append(node.id)
        course_id = node.course_id
        if course_id and course_id not in entry["course_ids"]:
            entry["course_ids"].append(course_id)

    return [groups[key] for key in sorted(groups.keys())]


def derive_person_network(user_id: str) -> PersonalNetwork:
    """本人スコープのエントリポイント（設計書 §5、Phase P-0.5）。

    ``derive_personal_network``（コーススコープ, Phase P-0）と同じ遅延 import 流儀。
    本人の全痕跡を course_id 条件なしで読み、``build_person_network`` へ渡す。
    """
    from core.personal_graph import queries  # 遅延 import（理由は derive_personal_network 参照）

    traces = queries.fetch_traces_for_user(user_id)
    reconstructions = queries.fetch_reconstructions_for_user(user_id)
    course_ids = sorted({
        str(row.get("course_id") or "")
        for row in (*traces, *reconstructions)
        if row.get("course_id")
    })
    atlas_by_course = queries.fetch_topic_atlas_binding_for_courses(course_ids)
    claim_topic_by_course = queries.fetch_claim_topic_map_for_courses(course_ids)
    topic_labels_by_course = queries.fetch_topic_labels_for_courses(course_ids)
    return build_person_network(
        traces,
        reconstructions,
        atlas_by_course,
        claim_topic_by_course,
        topic_labels_by_course=topic_labels_by_course,
    )
