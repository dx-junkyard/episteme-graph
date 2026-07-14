"""個人知識ネットワーク（Personal Knowledge Network, Phase P-0）の語彙・dataclass 正本。

設計の正本は ``docs/features/personal_knowledge_network_design.md``（特に §2 ノード導出規則・
§3 エッジ意味論）。FastAPI には依存しない（core/ 規約）。全 dataclass は JSON シリアライズ可能。

ノードは「公共構造への参照（アンカー）+ 本人の関与の種別」であり、独立した私的語彙のノードは
作らない（アンカー ID 交差を well-defined にするため。ビジョン §3 修正①）。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

# ---------------------------------------------------------------------------
# node_kind（§2 の表 N1〜N4 に対応）
# ---------------------------------------------------------------------------

NODE_KIND_TENSION = "tension"                  # N1: 引っかかり（本人が引き受けた status のみ）
NODE_KIND_QUESTION = "question"                # N2/N3: 構造帰属付き/帰属なしの問い
NODE_KIND_RECONSTRUCTION = "reconstruction"    # N4: 再構成の成功（改訂チェーンの終端行）

NODE_KINDS = (NODE_KIND_TENSION, NODE_KIND_QUESTION, NODE_KIND_RECONSTRUCTION)

# ---------------------------------------------------------------------------
# edge_kind（§3。P-0 実装ノートにより実体化するのは bridge のみ。
# revision / descend はノードの facts フィールドに事実文として保持する）
# ---------------------------------------------------------------------------

EDGE_KIND_BRIDGE = "bridge"

EDGE_KINDS = (EDGE_KIND_BRIDGE,)

# ---------------------------------------------------------------------------
# anchor_type（§2 アンカー解決）。structure_anchor の語彙
# （claim | equation | derivation_step | concept | stage | chunk | segment。
# 正本は core.structure_anchor.schema.ANCHOR_TYPES）に加え、Phase P 固有の3語彙を持つ。
# ---------------------------------------------------------------------------

ANCHOR_TYPE_COMPONENT = "component"    # 接続済み tension が theory_components を指す場合
ANCHOR_TYPE_GRAPH_EDGE = "graph_edge"  # 接続済み tension が TheoryOperationGraph の edge を指す場合
ANCHOR_TYPE_TOPIC = "topic"            # アンカーが取れない/粗い場合の縮退先（コース⇄地図バインディング経由）

PERSONAL_ANCHOR_TYPES = (ANCHOR_TYPE_COMPONENT, ANCHOR_TYPE_GRAPH_EDGE, ANCHOR_TYPE_TOPIC)


@dataclass
class PersonalAnchor:
    """ノードが指し示す公共構造上の位置。

    ``anchor_type`` は structure_anchor 語彙（claim|equation|derivation_step|concept|
    stage|chunk|segment）+ Phase P 固有語彙（component|graph_edge|topic）のいずれか。
    ``atlas_node_id`` は topic 由来の binding があるときのみ設定する（P-0）。
    """

    anchor_type: str
    anchor_id: str
    anchor_label: str = ""
    atlas_node_id: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PersonalNode:
    """個人ネットワークのノード1件。

    ``id`` は由来行の UUID（``interest_traces.id`` / ``learner_reconstructions.id``）を
    そのまま使う。``label`` は本人の言葉の抜粋（80字で切る）。``facts`` は事実文のみを
    保持し、件数・回数などの数値は入れない（PN-4）。``source`` はデバッグ・UI 詳細用の
    出所メタ（kind / status / attribution_source 等）。
    """

    id: str
    node_kind: str
    label: str
    anchor: PersonalAnchor
    topic_id: str | None
    created_at: str
    facts: list[str]
    source: dict

    def to_dict(self) -> dict:
        d = asdict(self)
        d["anchor"] = self.anchor.to_dict()
        return d


@dataclass
class PersonalEdge:
    """個人ネットワークの辺1件（P-0 は ``bridge`` のみ）。

    ``to_ref`` は ``{"ref_type": "component"|"graph_edge", "ref_id": str}``。
    ``fact`` は本人の行為または DB 上の事実を述べる事実文（推測エッジは作らない。KN-3）。
    """

    edge_kind: str
    from_node_id: str
    to_ref: dict
    fact: str

    def to_dict(self) -> dict:
        return {
            "edge_kind": self.edge_kind,
            "from_node_id": self.from_node_id,
            "to_ref": dict(self.to_ref),
            "fact": self.fact,
        }


@dataclass
class PersonalNetwork:
    """導出結果全体。集計数値フィールドを持たない（PN-4）。"""

    nodes: list[PersonalNode] = field(default_factory=list)
    edges: list[PersonalEdge] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
        }
