"""個人知識ネットワーク（Personal Knowledge Network, Phase P）core パッケージ。

学習者本人が引き受けた痕跡（``interest_traces`` の tension/question・
``learner_reconstructions`` の再構成成功）から、公共構造（theory_components /
theory_component_graphs / atlas 骨格）への参照として個人ネットワークを
**決定論的に導出する**（書かれるのではなく彫り出される）。
``docs/features/personal_knowledge_network_design.md`` が設計の正本
（特に §0 不変条項 PN-1〜7・§2 ノード導出規則・§3 エッジ意味論・§4 導出アルゴリズム）。

Phase P-0 スコープ（本パッケージ現状）:
- ``schema.py``    : node_kind/edge_kind/anchor 語彙・PersonalNode/PersonalEdge/
  PersonalNetwork の正本
- ``queries.py``   : interest_traces / learner_reconstructions / コース atlas binding
  の SQL 読みプリミティブ（本人スコープの DB 読みはここだけ）
- ``derive.py``    : §4 の導出アルゴリズム（純粋関数 ``build_network`` +
  エントリポイント ``derive_personal_network``）
- ``graph_data.py``: ``learning_states.personal_graph`` JSONB の正本アクセサ
- ``bridges.py``   : ビジョン Phase B — 橋候補の k-匿名集約（教員向け・コーススコープ。
  本人スコープの個人ネットワーク導出とは別系統で、集約読みを自前に持つ）
- ``provisional.py``: カテゴリギャップ候補 v1-b — 共有骨格に置けなかった論文の主題を
  本人にだけ見せる「暫定ノード」の読み時導出（``docs/features/category_gap_candidates_design.md``
  §4.4 裁定 / §5.6。共有骨格・共有候補には一切書き込まない）

**開発ルール2**: 本パッケージは FastAPI / routes / services / ``core.llm`` を import しない
（非LLM・決定論であることを import レベルで保証する。PN-5）。受講ゲート等の権限判定は
API 層（``routes/personal_map.py``、Phase P-0 では未実装）の責務。
"""

from core.personal_graph.derive import derive_personal_network
from core.personal_graph.schema import (
    EDGE_KIND_BRIDGE,
    NODE_KIND_QUESTION,
    NODE_KIND_RECONSTRUCTION,
    NODE_KIND_TENSION,
    NODE_KINDS,
    PersonalAnchor,
    PersonalEdge,
    PersonalNetwork,
    PersonalNode,
)

__all__ = [
    "EDGE_KIND_BRIDGE",
    "NODE_KIND_QUESTION",
    "NODE_KIND_RECONSTRUCTION",
    "NODE_KIND_TENSION",
    "NODE_KINDS",
    "PersonalAnchor",
    "PersonalEdge",
    "PersonalNetwork",
    "PersonalNode",
    "derive_personal_network",
]
