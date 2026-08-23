"""わたしの地図「いまここの周り」（近傍関係ビュー）の導出。

設計の正本は ``docs/features/personal_map_nearby_design.md``（不変条項 PMN-1〜PMN-7）。
親文書は ``docs/features/personal_knowledge_network_design.md``（PN-1〜PN-7 を全て継承）。

本モジュールが見せるのは**2つの関係**だけである（設計書 §2）:

- **R1 依存の向き**: TheoryOperationGraph の main 層（theory stage のバックボーン）における
  中心ノードの上流（これが前提にしていること）と下流（これに依存していること）。
- **R2 確かめられているか**: ``epistemic_ledger`` の検証状態（記帳の有無を主語にした
  段階ラベル）と、``core/doubt/support_paths.py`` の支持線の事実文。

各ノードには、その理論構成を裏づける**代表 claim の逐語**（``claim_excerpt``）を添える
（:func:`_claim_excerpt`）。main ノードの ``label`` は theory stage 名に固定されており
（CLAUDE.md #308）、``description`` は内部 ID を含むため学習者 DTO には載せられないので、
stage 名だけでは「どの話なのか」が判らない。逐語は**出典から確認できた claim**
（``support_status='source_backed'``）に限り、承認済み review_status を優先する
（未承認・非 source_backed の本文は学習者に出さない = ``claim_excerpt: null``）。

検証状態は**差分になるときだけ**出す（:func:`_suppress_uniform_verification`）。D層の
``ledger_builder`` は main ノードへ ``unknown`` 行をバックフィルするため、素朴に出すと
全ノードに「検証情報なし」が並び、区別を伝えない語が画面を埋める。表示ノードのどれにも
区別を生む status が無ければ、台帳の区別ごと出さず（``ledger_available=False``）事実文
:data:`FACT_NO_VERIFICATION_RECORDS` 1行に畳む。

加えて「広がりの装置」（好奇心の情報設計）のうち2件を点ビュー・範囲ビューに追加する
（正本は同設計書。存在だけを事実として見せ、詳細は本人の明示操作まで伏せる —
``journey.py`` の ``cross_course_hint`` と同じ文法）:

- **装置2（共通部品の糸）**: 点ビューの**中心ノードのみ**について、confirmed 同一性
  リンク（PN-6）経由で他論文にも現れる共通部品を最大3行の事実文で示す
  （:func:`_shared_part_thread_facts`）。範囲ビューには出さない。
- **装置3（検証の晴れ間の近接提示）**: 点ビューで、表示集合に入らなかった main ノードの
  うち台帳 status が ``untested``/``unknown`` のものを閉世界語彙のまま近接提示する
  （:func:`_fog_candidate_labels`）。台帳行が無いノードは対象外（「行が無い＝何も
  主張しない」の既存意味論を維持）。

「名前のある霧」（装置1）と「範囲ビュー→分野の地図の接続行」（装置4）は本モジュールの
責務外 — 装置1は ``core.personal_graph.atlas_fog``、装置4は :func:`build_topic_range` の
``atlas_concept_context`` 引数（呼び出し側 :func:`_nearby_for_topic_anchor` が
``queries.fetch_atlas_concept_context`` で解決して渡す）として実装する。

**位置に意味の無い配置をしない**（PMN-1）: 返すのは「上流／中心／下流」という向きだけで、
座標・順位・距離を返さない。**推測の辺を描かない**（PMN-2): 辺は
``source_backing_status ∈ {source_backed, partially_source_backed}`` のものだけを採用する
（``core/doubt/support_paths.py`` の容量1エッジ条件と同じ規則）。

規約:

- FastAPI / routes / services / core.llm を import しない（core/ 規約）。LLM を呼ばない（PMN-6）。
- DB 読みは ``queries.py`` のプリミティブ経由のみ。書き込みは一切しない。
- 数値（confidence / load_score / 支持経路の本数 / 件数）を返さない（PMN-4）。
- 訳語は ``core/element_vocab.py``（theory stage）・``core/label_vocab.py``（検証状態）・
  ``personal_graph/schema.py``（node_kind）からのみ引く。**新しい訳語表を作らない**。
- 権限判定の実体は呼び出し側（route）が ``can_view_document`` コールバックで注入する
  （journey.py と同じ規約）。
"""

from __future__ import annotations

from collections.abc import Callable

from core.element_vocab import theory_stage_key, theory_stage_label
from core.label_vocab import VERIFICATION_STATUS_LABELS_LEDGER
from core.personal_graph import queries
from core.personal_graph.derive import derive_person_network
from core.personal_graph.schema import (
    ANCHOR_TYPE_COMPONENT,
    ANCHOR_TYPE_TOPIC,
    NODE_KIND_LABELS,
    PersonalNode,
)
from core.reconstruction.schema import APPROVED_REVIEW_STATUSES, SOURCE_BACKED
from core.text_excerpt import excerpt

# ---------------------------------------------------------------------------
# 境界（PN-5: 有界な探索。無制限に広げない）
# ---------------------------------------------------------------------------

#: 上流・下流それぞれの表示上限（journey.MAX_FANOUT_PER_SEGMENT と同じ値・同じ意図）。
MAX_FANOUT = 5

#: 「土台までの道筋」の最大段数。
MAX_ROOT_DEPTH = 6

#: 中心解決のためにコース sources を走査する document の最大数（設計書 §3.2）。
MAX_DOCUMENTS_SCANNED = 5

MODE_NEAR = "near"
MODE_ROOT = "root"
MODES = (MODE_NEAR, MODE_ROOT)

#: レスポンス専用モード（範囲モード）。リクエストの ``mode`` クエリには加えない
#: （``routes/personal_map.py`` の ``NEARBY_MODES`` バリデーションは ``MODES`` のまま）。
MODE_RANGE = "range"

#: 採用する辺の source_backing_status（PMN-2）。
_QUALIFIED_EDGE_BACKING = ("source_backed", "partially_source_backed")

#: 装置2（共通部品の糸）の最大表示行数。4件目以降は黙って切る（件数に言及しない）。
MAX_SHARED_PART_THREADS = 3

#: 装置3（検証の晴れ間の近接提示）の最大列挙数。
MAX_FOG_LABELS = 3

#: 装置3が対象とする台帳 status（閉世界語彙 SL1。台帳行が無いノードは対象外）。
_FOG_VERIFICATION_STATUSES = ("untested", "unknown")

#: 「状態が定まっていない」ことしか言わない台帳 status（表示上の区別を生まない）。
_UNINFORMATIVE_VERIFICATION_STATUS = "unknown"

#: 表示ノード間で**区別を生む**台帳 status。語彙の正本は
#: ``core/label_vocab.VERIFICATION_STATUS_LABELS_LEDGER`` のキーで、そこから
#: :data:`_UNINFORMATIVE_VERIFICATION_STATUS` を除いて導出する（新しい語彙表を作らない）。
DISTINGUISHING_VERIFICATION_STATUSES = tuple(
    status
    for status in VERIFICATION_STATUS_LABELS_LEDGER
    if status != _UNINFORMATIVE_VERIFICATION_STATUS
)

#: ノードの代表 claim 逐語（``claim_excerpt``）の最大文字数。切り詰めの実装は
#: ``core/text_excerpt.excerpt``（CP5: 切り詰めは1実装。素スライスを新規に書かない）。
CLAIM_EXCERPT_LIMIT = 80

# ---------------------------------------------------------------------------
# 事実文（PMN-3 閉世界語彙 / PMN-5 助言しない）。
# 「この分野では未検証」「誰も検証していない」のような分野全体への言及はしない。
# ---------------------------------------------------------------------------

FACT_NO_QUALIFIED_EDGES = "この場所の前後のつながりは、まだ出典から確認できていません。"

#: 表示ノードの検証状態に区別が無いとき、ノードごとのラベルの代わりに1行だけ言う事実文
#: （閉世界語彙 SL1: 主語は「このコーパス」に限る。分野全体には言及しない）。
FACT_NO_VERIFICATION_RECORDS = (
    "これらの理論構成には、このコーパスの中では検証記録がありません。"
)
FACT_NO_UPSTREAM = "この場所より手前の前提は、この論文の中には見つかりません。"
FACT_NO_DOWNSTREAM = "これに依存しているものは、この論文の中には見つかりません。"
NOTICE_UNRESOLVED = "この記録は、まだ論文の理論構成に結びついていません。"

#: 範囲モード（topic アンカーの事実ベース粗表示）専用の事実文・notice。
NOTICE_TOPIC_NO_MAPPING = "このトピックの教材は、まだ理論構成に対応づけられていません。"
FACT_RANGE_UNKNOWN_POINT = "この記録がその中のどこについてのものかは、まだ記録されていません。"
FACT_RANGE_SHARPEN = (
    "教材のテキストを選んで質問するか、帰属カードで確定すると、1点に絞り込まれます。"
)

#: コース範囲フォールバック（トピック⇄claim の対応が引けないときに、コース sources の
#: 解析済み論文の理論構成をそのまま範囲として見せる）の事実文。**粗いことを隠さず、
#: 粗いとラベルして見せる**ための1行（PMN-1 の正直さ。件数・数値は言わない = PMN-4）。
FACT_RANGE_COURSE_FALLBACK = (
    "このトピックと論文の対応はまだ記録されていません。"
    "かわりに、このコースのソース論文の理論構成を表示しています。"
)

#: 装置2（共通部品の糸）の事実文テンプレート。
_SHARED_PART_FACT_TEMPLATE = "共通部品『{name}』は、論文『{title}』にも現れます。"

#: 装置3（検証の晴れ間の近接提示）の事実文の接頭辞（閉世界語彙 SL1）。
FACT_FOG_NEARBY_PREFIX = "この近くには、このコーパスの中では検証記録がない場所があります："

#: 装置4（範囲ビュー→分野の地図の接続行）の事実文テンプレート。
_RANGE_ATLAS_FACT_TEMPLATE = (
    "このトピックは、分野の地図の『{region_label}』にある『{concept_label}』に"
    "対応づけられています。"
)

_ANCHOR_CLAIM = "claim"
_ANCHOR_EQUATION = "equation"
_ANCHOR_DERIVATION_STEP = "derivation_step"
_ANCHOR_STAGE = "stage"

#: 中心として解決できるアンカー種別（設計書 §3.2 の表）。それ以外は available:false。
RESOLVABLE_ANCHOR_TYPES = (
    ANCHOR_TYPE_COMPONENT,
    _ANCHOR_CLAIM,
    _ANCHOR_EQUATION,
    _ANCHOR_DERIVATION_STEP,
    _ANCHOR_STAGE,
)

#: 範囲モード（点として中心解決できないが、トピック粒度に縮退した痕跡を事実ベースで
#: 粗く見せる）を許すアンカー種別。``RESOLVABLE_ANCHOR_TYPES`` とは別枠
#: （点ビューの中心にはしない・PMN-1）。
RANGE_ANCHOR_TYPES = (ANCHOR_TYPE_TOPIC,)


# ---------------------------------------------------------------------------
# グラフ読み（純関数）
# ---------------------------------------------------------------------------


def _as_list(value) -> list:
    return value if isinstance(value, list) else []


def _ids(node: dict, key: str) -> list[str]:
    return [str(v) for v in _as_list(node.get(key)) if v]


def main_nodes(graph: dict) -> list[dict]:
    """main 層ノードを決定論順（display_order, component_id）で返す。

    ``equation_detail`` / ``debug`` 層は使わない（CLAUDE.md TheoryOperationGraph 節。
    fallback / inferred なノードを確定扱いしないため）。
    """
    nodes = [n for n in _as_list(graph.get("nodes")) if isinstance(n, dict)]
    mains = [n for n in nodes if str(n.get("graph_layer") or "main") == "main"]
    mains.sort(
        key=lambda n: (int(n.get("display_order") or 0), str(n.get("component_id") or ""))
    )
    return mains


def node_stage(node: dict) -> str:
    """main ノードの theory stage キーを返す（引けなければ空文字）。

    A層は main ノードの ``label`` に**英語の stage 表示名**を載せる（CLAUDE.md #308）。
    キーへの逆引きは ``core/element_vocab.theory_stage_key``（訳語・逆引きの正本）に委ねる。
    """
    return theory_stage_key(node.get("label"))


def node_display_label(node: dict) -> str:
    """学習者に見せるノード名（日本語の stage 名）。

    stage キーが引けない古い/非正規な main ラベルは、``display_label`` → ``label`` の順で
    そのまま出す（情報を落とさない。P4）。
    """
    stage = node_stage(node)
    if stage:
        label = theory_stage_label(stage)
        if label:
            return label
    for key in ("display_label", "label"):
        value = str(node.get(key) or "").strip()
        if value:
            return value
    return str(node.get("component_id") or "")


def qualified_edges(graph: dict) -> list[tuple[str, str]]:
    """出典から確認できる辺だけを ``(source, target)`` で返す（PMN-2）。

    ``source_backing_status`` が ``source_backed`` / ``partially_source_backed`` の辺のみ。
    ``inferred`` / ``review_required`` / 未分類（空）の辺は**採用しない** — 推測の
    依存関係を「前提」として学習者に見せないため。向きは ComponentGraphAgent の規約どおり
    ``source_component_id``（前提側）→ ``target_component_id``（結果側）。
    """
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for edge in _as_list(graph.get("edges")):
        if not isinstance(edge, dict):
            continue
        backing = str(edge.get("source_backing_status") or "").strip().lower()
        if backing not in _QUALIFIED_EDGE_BACKING:
            continue
        src = str(edge.get("source_component_id") or edge.get("source") or "")
        tgt = str(edge.get("target_component_id") or edge.get("target") or "")
        if not src or not tgt or src == tgt:
            continue
        key = (src, tgt)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    out.sort()
    return out


def node_matches_anchor(node: dict, anchor_type: str, anchor_id: str) -> bool:
    """main ノードが本人のアンカーを束ねているかを判定する（設計書 §3.2 の表）。"""
    if not anchor_id:
        return False
    if anchor_type == ANCHOR_TYPE_COMPONENT:
        return (
            str(node.get("component_id") or "") == anchor_id
            or anchor_id in _ids(node, "member_component_ids")
        )
    if anchor_type == _ANCHOR_CLAIM:
        return anchor_id in _ids(node, "linked_claim_ids")
    if anchor_type == _ANCHOR_EQUATION:
        return anchor_id in _ids(node, "linked_equation_ids")
    if anchor_type == _ANCHOR_DERIVATION_STEP:
        return anchor_id in _ids(node, "linked_derivation_ids")
    if anchor_type == _ANCHOR_STAGE:
        return node_stage(node) == anchor_id
    return False


def find_center_node(graph: dict, anchor_type: str, anchor_id: str) -> dict | None:
    """アンカーを束ねる main ノードを決定論順で1件返す（無ければ None）。"""
    for node in main_nodes(graph):
        if node_matches_anchor(node, anchor_type, anchor_id):
            return node
    return None


def _upstream_ids(edges: list[tuple[str, str]], center_id: str) -> list[str]:
    return [src for src, tgt in edges if tgt == center_id]


def _downstream_ids(edges: list[tuple[str, str]], center_id: str) -> list[str]:
    return [tgt for src, tgt in edges if src == center_id]


def root_path_ids(edges: list[tuple[str, str]], center_id: str) -> list[str]:
    """中心から土台までの一直線（土台側が先頭）。

    各段で上流の決定論順（``qualified_edges`` のソート順）先頭を採る。閉路と
    ``MAX_ROOT_DEPTH`` で打ち切る。中心自身は含めない。
    """
    chain: list[str] = []
    seen = {center_id}
    cursor = center_id
    while len(chain) < MAX_ROOT_DEPTH:
        ups = [i for i in _upstream_ids(edges, cursor) if i not in seen]
        if not ups:
            break
        nxt = ups[0]
        chain.insert(0, nxt)
        seen.add(nxt)
        cursor = nxt
    return chain


# ---------------------------------------------------------------------------
# DTO 組み立て（純関数。数値を載せない = PMN-4）
# ---------------------------------------------------------------------------


def _mine_for_node(node: dict, personal_nodes: list[PersonalNode]) -> list[dict]:
    """この main ノードに紐づく本人の痕跡（新しい順）。candidate は含まない（PN-3）。"""
    hits = [
        p
        for p in personal_nodes
        if node_matches_anchor(node, p.anchor.anchor_type, p.anchor.anchor_id)
    ]
    hits.sort(key=lambda p: (p.created_at or "", p.id), reverse=True)
    return [
        {
            "trace_id": p.id,
            "kind": p.node_kind,
            "kind_label": NODE_KIND_LABELS.get(p.node_kind, ""),
            "text": p.label,
            "course_id": p.course_id,
            "created_at": p.created_at,
        }
        for p in hits
    ]


def _verification(component_id: str, ledger: dict[str, str]) -> dict | None:
    """検証状態の段階ラベル（台帳行が無ければ None）。生数値は載せない（PMN-4）。"""
    status = str(ledger.get(component_id) or "").strip()
    if not status:
        return None
    label = VERIFICATION_STATUS_LABELS_LEDGER.get(status, "")
    if not label:
        return None
    return {"status": status, "label": label}


def _claim_excerpt(node: dict, claim_summaries: dict[str, dict] | None) -> str | None:
    """ノードの代表 claim の逐語（80字切り詰め）。出せない場合は ``None``。

    選定規則（決定論。AI 推定・要約を挟まない = PMN-6）:

    1. 候補は ``linked_claim_ids`` のうち本文が非空で
       ``support_status == 'source_backed'`` のもの（出典から確認できた claim のみ）。
    2. そのうち承認済み ``review_status``（``core/reconstruction/schema.py`` の
       ``APPROVED_REVIEW_STATUSES``。claim の承認語彙の既存正本）を優先する。
    3. 承認済みが無ければ ``source_backed`` の中から採る。
    4. ``source_backed`` が1件も無ければ ``None`` — 未承認・非 source_backed の本文を
       学習者に出さない（R層が「未検証の構造で学習者を試さない」のと同じ線）。

    同順位内は ``claim_id`` の昇順で先頭を採る（表示の揺れを作らない）。切り詰めは
    ``core/text_excerpt.excerpt``（CP5 の唯一の実装。TeX トークンの途中で切らない。
    安全に切れる位置が無ければ空文字を返すので、その場合も ``None`` に倒す）。
    """
    if not claim_summaries:
        return None
    approved: list[str] = []
    backed: list[str] = []
    for claim_id in sorted(set(_ids(node, "linked_claim_ids"))):
        summary = claim_summaries.get(claim_id)
        if not isinstance(summary, dict):
            continue
        text = str(summary.get("text") or "").strip()
        if not text:
            continue
        if str(summary.get("support_status") or "") != SOURCE_BACKED:
            continue
        if str(summary.get("review_status") or "") in APPROVED_REVIEW_STATUSES:
            approved.append(text)
        else:
            backed.append(text)
    for bucket in (approved, backed):
        if bucket:
            return excerpt(bucket[0], CLAIM_EXCERPT_LIMIT) or None
    return None


def _suppress_uniform_verification(
    node_dtos: list[dict], ledger: dict[str, str]
) -> bool:
    """表示ノードの検証状態に**区別**が無いなら、区別ごと出さないと決める。

    ``ledger`` が1行も無いとき（D層未導入・未バックフィル）は従来どおり ``False`` —
    台帳の不在は「検証記録がない」という主張ではないので、既存の「台帳ゼロ」表示
    （何も言わない）をそのまま維持する。台帳行はあるが表示ノードのどれも
    :data:`DISTINGUISHING_VERIFICATION_STATUSES` を持たない場合に ``True`` を返し、
    呼び出し側が per-node ラベルを畳んで事実文1行
    （:data:`FACT_NO_VERIFICATION_RECORDS`）に置き換える。
    """
    if not ledger:
        return False
    for dto in node_dtos:
        verification = dto.get("verification")
        if not isinstance(verification, dict):
            continue
        if str(verification.get("status") or "") in DISTINGUISHING_VERIFICATION_STATUSES:
            return False
    return True


def _blank_verification(node_dtos: list[dict]) -> None:
    """per-node の検証ラベルを落とす（台帳ゼロと同じ見え方へ畳む）。"""
    for dto in node_dtos:
        dto["verification"] = None


def _node_dto(
    node: dict,
    *,
    ledger: dict[str, str],
    personal_nodes: list[PersonalNode],
    is_center: bool,
    claim_summaries: dict[str, dict] | None = None,
) -> dict:
    component_id = str(node.get("component_id") or "")
    return {
        "component_id": component_id,
        "label": node_display_label(node),
        "stage": node_stage(node),
        "verification": _verification(component_id, ledger),
        # 点ビュー・範囲ビュー共通のノード形（フロントが描くかは別問題として、
        # DTO のキー集合は揃える）。解決できなければ null（捏造しない）。
        "claim_excerpt": _claim_excerpt(node, claim_summaries),
        "mine": _mine_for_node(node, personal_nodes),
        "is_center": is_center,
    }


def _enumerate(labels: list[str]) -> str:
    return "、".join(f"『{label}』" for label in labels)


def _fog_candidate_labels(
    graph: dict, *, shown_ids: set[str], ledger: dict[str, str]
) -> list[str]:
    """装置3: 表示集合の外にある main ノードのうち、台帳 status が

    ``untested``/``unknown`` のものを main_nodes の決定論順で列挙する（重複ラベル除去）。
    台帳行が無いノード（``ledger`` にキーが無い）は対象外 — 「行が無い＝何も主張しない」
    という既存の台帳意味論を崩さない。
    """
    labels: list[str] = []
    seen: set[str] = set()
    for node in main_nodes(graph):
        node_id = str(node.get("component_id") or "")
        if not node_id or node_id in shown_ids:
            continue
        status = str(ledger.get(node_id) or "").strip()
        if status not in _FOG_VERIFICATION_STATUSES:
            continue
        label = node_display_label(node)
        if label in seen:
            continue
        seen.add(label)
        labels.append(label)
    return labels


def _link_matches_node(link: dict, node: dict) -> bool:
    """同一性リンク1件が main ノードを束ねる要素を指しているか判定する（装置2）。

    ``element_identity_links.instance_element_type``（``theory_component`` /
    ``theory_claim`` / ``equation`` / ``figure``）別に、ノードの ``component_id`` /
    ``member_component_ids`` / ``linked_claim_ids`` / ``linked_equation_ids`` と
    突合する（``node_matches_anchor`` の突合方法を同一性リンクの語彙へ写した鏡写し）。
    ``figure`` はノードが持つフィールドに対応が無いため常に不一致。
    """
    element_id = str(link.get("instance_element_id") or "")
    if not element_id:
        return False
    element_type = str(link.get("instance_element_type") or "")
    if element_type == "theory_component":
        return (
            element_id == str(node.get("component_id") or "")
            or element_id in _ids(node, "member_component_ids")
        )
    if element_type == "theory_claim":
        return element_id in _ids(node, "linked_claim_ids")
    if element_type == "equation":
        return element_id in _ids(node, "linked_equation_ids")
    return False


def _shared_part_thread_facts(
    center_node: dict,
    *,
    document_id: str,
    can_view_document: Callable[[str, str], bool] | None,
    user_id: str,
) -> list[str]:
    """装置2（共通部品の糸）: 中心ノードのみが対象の点ビュー専用の事実文（最大3行）。

    ``journey.py`` の [2][3] 区間（confirmed 同一性リンク → active な library_entry →
    他 document への confirmed リンク）と同じ規則を鏡写しにする:

    - confirmed 同一性リンクのみを辿る（PN-6。candidate/rejected は使わない）。
    - active でない/名前が引けない library_entry はその糸ごと生成しない（journey と
      同じ「generic フォールバックしない」原則。``fetch_library_entry_names`` が
      既に active のみを返す）。
    - 他 document は現在の document を除外したうえで ``can_view_document`` で
      fail-closed に絞り込む。``can_view_document`` が callable でなければ
      糸を一切出さない（journey より厳格 — journey は省略時に判定をスキップする
      後方互換動作を持つが、この糸は新しい導線なので後方互換を優先しない）。

    (part_name, title) の組で重複除去し、決定論順（辞書順）でソートしたうえで
    最大 ``MAX_SHARED_PART_THREADS`` 行だけ返す（4件目以降は黙って切る — 件数に
    言及すると PN-4 に反するため）。例外はすべて握って空リストへ倒す（fail-soft、
    ``fetch_center_support_fact_line`` と同じ精神）。
    """
    if not document_id or not callable(can_view_document):
        return []
    try:
        raw_links = queries.fetch_confirmed_identity_links(document_id)
        matched = [link for link in raw_links if _link_matches_node(link, center_node)]
        if not matched:
            return []

        shared_part_ids: list[str] = []
        for link in matched:
            shared_part_id = str(link.get("shared_part_id") or "")
            if shared_part_id and shared_part_id not in shared_part_ids:
                shared_part_ids.append(shared_part_id)
        if not shared_part_ids:
            return []

        names = queries.fetch_library_entry_names(shared_part_ids)  # active のみ
        doc_ids_by_part: dict[str, set[str]] = {}
        all_other_doc_ids: set[str] = set()
        for shared_part_id in shared_part_ids:
            if not names.get(shared_part_id):
                # active でない/名前が引けない → この糸は生成しない。
                continue
            other_links = queries.fetch_confirmed_links_for_shared_part(shared_part_id)
            docs = {
                str(row.get("instance_document_id") or "")
                for row in other_links
                if str(row.get("instance_document_id") or "")
                and str(row.get("instance_document_id") or "") != document_id
            }
            if not docs:
                continue
            doc_ids_by_part[shared_part_id] = docs
            all_other_doc_ids.update(docs)

        if not all_other_doc_ids:
            return []

        viewable = {d for d in all_other_doc_ids if can_view_document(user_id, d)}
        if not viewable:
            return []

        titles = queries.fetch_document_titles(sorted(viewable))
        pairs: set[tuple[str, str]] = set()
        for shared_part_id, docs in doc_ids_by_part.items():
            name = names.get(shared_part_id, "")
            for doc_id in docs:
                if doc_id not in viewable:
                    continue
                title = titles.get(doc_id) or "別の教材"
                pairs.add((name, title))

        if not pairs:
            return []
        ordered = sorted(pairs)[:MAX_SHARED_PART_THREADS]
        return [_SHARED_PART_FACT_TEMPLATE.format(name=name, title=title) for name, title in ordered]
    except Exception:
        return []


def build_nearby(
    graph: dict,
    *,
    center_node: dict,
    personal_nodes: list[PersonalNode],
    ledger: dict[str, str],
    support_fact_line: str = "",
    shared_part_fact_lines: list[str] | tuple[str, ...] = (),
    claim_summaries: dict[str, dict] | None = None,
    mode: str = MODE_NEAR,
) -> dict:
    """近傍関係ビューの DTO を組み立てる（純関数・DB 非依存）。

    ``mode=root`` では上流1階層のかわりに「土台までの道筋」を返す（下流1階層は共通）。
    ``edges`` は返したノード集合の内側にある採用済みの辺のみ（描かれないノードへ向かう辺は
    返さない）。

    ``shared_part_fact_lines``（装置2）は呼び出し側（DB 経路）が
    :func:`_shared_part_thread_facts` で事前に解決した事実文（最大 ``MAX_SHARED_PART_THREADS``
    行）をそのまま素通しする。装置3（検証の晴れ間の近接提示）は :func:`_fog_candidate_labels`
    を使って本関数内で導出する（表示集合 ``shown_ids`` を必要とするため純関数の中で組み立てる）。

    ``claim_summaries``（``{claim_id: {text, support_status, review_status}}``）は
    呼び出し側（DB 経路）が ``queries.fetch_claim_summaries`` で1回だけ解決して渡す
    （:func:`_claim_excerpt` の材料。省略時は全ノードの ``claim_excerpt`` が ``None``）。

    ``ledger_available`` は「台帳行があり、かつ表示ノードの検証状態に区別がある」ことを
    意味する（:func:`_suppress_uniform_verification`）。区別が無いときは per-node ラベルを
    落として :data:`FACT_NO_VERIFICATION_RECORDS` 1行に畳む。装置3（晴れ間）は生の
    ``ledger`` を直接読むため、この畳み込みとは独立に動く（表示集合の**外**が主語なので、
    畳み込みで消してはならない）。
    """
    nodes_by_id = {str(n.get("component_id") or ""): n for n in main_nodes(graph)}
    edges = qualified_edges(graph)
    center_id = str(center_node.get("component_id") or "")

    def _dto_list(ids: list[str]) -> list[dict]:
        out = []
        for node_id in ids:
            node = nodes_by_id.get(node_id)
            if node is None:
                continue
            out.append(
                _node_dto(
                    node,
                    ledger=ledger,
                    personal_nodes=personal_nodes,
                    is_center=False,
                    claim_summaries=claim_summaries,
                )
            )
        return out

    downstream = _dto_list(_downstream_ids(edges, center_id)[:MAX_FANOUT])
    if mode == MODE_ROOT:
        root_ids = root_path_ids(edges, center_id)
        root_path = _dto_list(root_ids)
        upstream: list[dict] = []
    else:
        root_path = []
        upstream = _dto_list(_upstream_ids(edges, center_id)[:MAX_FANOUT])

    center = _node_dto(
        center_node,
        ledger=ledger,
        personal_nodes=personal_nodes,
        is_center=True,
        claim_summaries=claim_summaries,
    )

    # 検証状態の差分表示: 表示ノードに区別が無ければ per-node ラベルを畳む。
    verification_suppressed = _suppress_uniform_verification(
        [center] + upstream + downstream + root_path, ledger
    )
    if verification_suppressed:
        _blank_verification([center] + upstream + downstream + root_path)

    shown_ids = {center_id}
    for group in (upstream, downstream, root_path):
        shown_ids.update(n["component_id"] for n in group)
    shown_edges = [
        {"from": src, "to": tgt}
        for src, tgt in edges
        if src in shown_ids and tgt in shown_ids
    ]

    facts: list[str] = []
    if support_fact_line:
        facts.append(support_fact_line)
    if not edges:
        facts.append(FACT_NO_QUALIFIED_EDGES)
    if downstream:
        facts.append("これに依存していること：" + _enumerate([n["label"] for n in downstream]))
    else:
        facts.append(FACT_NO_DOWNSTREAM)
    context_above = root_path if mode == MODE_ROOT else upstream
    if context_above:
        facts.append(
            "これが前提にしていること：" + _enumerate([n["label"] for n in context_above])
        )
    else:
        facts.append(FACT_NO_UPSTREAM)

    if verification_suppressed:
        facts.append(FACT_NO_VERIFICATION_RECORDS)

    # 装置2（共通部品の糸）: 中心ノード限定・DB 経路が事前解決した事実文をそのまま素通し。
    for line in shared_part_fact_lines:
        if line:
            facts.append(line)

    # 装置3（検証の晴れ間の近接提示）: 表示集合の外にある main ノードのみが対象。
    if ledger:
        fog_labels = _fog_candidate_labels(graph, shown_ids=shown_ids, ledger=ledger)
        if fog_labels:
            if len(fog_labels) <= MAX_FOG_LABELS:
                facts.append(FACT_FOG_NEARBY_PREFIX + _enumerate(fog_labels) + "。")
            else:
                facts.append(
                    FACT_FOG_NEARBY_PREFIX + _enumerate(fog_labels[:MAX_FOG_LABELS]) + " など。"
                )

    return {
        "available": True,
        "mode": mode,
        "ledger_available": bool(ledger) and not verification_suppressed,
        "center": center,
        "upstream": upstream,
        "downstream": downstream,
        "root_path": root_path,
        "edges": shown_edges,
        "facts": facts,
        "notice": None,
    }


def build_topic_range(
    documents: list[dict],
    *,
    personal_nodes: list[PersonalNode],
    ledger: dict[str, str],
    topic_label: str,
    atlas_concept_context: dict | None = None,
    fallback_fact: str = "",
    claim_summaries: dict[str, dict] | None = None,
) -> dict:
    """範囲モード（topic アンカーの事実ベース粗表示）の DTO を組み立てる（純関数）。

    ``documents`` は ``[{"title": str, "graph": dict, "touched_claim_ids": set[str]}]``。
    中心を持たず、そのトピックの教材が触れている main ノード群を ``touched`` フラグ付きで
    document 単位に列挙する（PMN-1: 座標・順位・距離は返さない。「触れているか」の事実のみ）。
    ``mine`` は ``_node_dto`` 経由でそのまま ``_mine_for_node`` を使う —
    ``node_matches_anchor`` は topic アンカーに常に ``False`` を返すため、topic に縮退した
    痕跡そのものが範囲内の1ノードに載る偽精度は構造的に起きない。

    ``fallback_fact`` はコース範囲フォールバック（トピック⇄claim の対応が引けず、コース
    sources の理論構成をそのまま範囲として見せた場合）に呼び出し側が渡す事実文
    （:data:`FACT_RANGE_COURSE_FALLBACK`）。非空のときは topic 行の直後に置き、
    ``FACT_RANGE_UNKNOWN_POINT``（「その中のどこについてか」）は**出さない** — 対応自体が
    無い状態で「その中のどこか」を語ると、無い対応があるように読めるため。文言はすべて
    サーバ側定数で、空の列挙・重複文は組み立てない。

    ``atlas_concept_context``（装置4）は呼び出し側（DB 経路）が
    ``queries.fetch_atlas_concept_context`` で事前解決した dict
    （``{"region_label", "concept_label", ...}``）をそのまま受け取る。``region_label`` /
    ``concept_label`` の両方が非空のときだけ、facts の**末尾**（``FACT_RANGE_SHARPEN`` の
    後）に分野の地図への接続行を1件追加する。binding が無い・骨格突合不能などは
    呼び出し側が ``None`` を渡すだけで、この関数は事実文を1行減らすだけで済む（fail-soft）。

    ``claim_summaries`` と ``ledger_available`` の意味論は :func:`build_nearby` と同じ
    （代表 claim の逐語は :func:`_claim_excerpt`、検証状態の差分表示は
    :func:`_suppress_uniform_verification`。stage 名しか出せない範囲ビューでは、この2つが
    「どの話で・何が確かめられているか」を運ぶ唯一の情報になる）。
    """
    range_documents: list[dict] = []
    seen_labels: set[str] = set()
    ordered_labels: list[str] = []
    all_node_dtos: list[dict] = []

    for doc in documents:
        graph = doc.get("graph") or {}
        touched_claim_ids = doc.get("touched_claim_ids") or set()
        nodes_out: list[dict] = []
        for node in main_nodes(graph):
            dto = _node_dto(
                node,
                ledger=ledger,
                personal_nodes=personal_nodes,
                is_center=False,
                claim_summaries=claim_summaries,
            )
            touched = bool(touched_claim_ids & set(_ids(node, "linked_claim_ids")))
            dto["touched"] = touched
            nodes_out.append(dto)
            all_node_dtos.append(dto)
            if touched and dto["label"] not in seen_labels:
                seen_labels.add(dto["label"])
                ordered_labels.append(dto["label"])
        main_ids = {n["component_id"] for n in nodes_out}
        edges_out = [
            {"from": src, "to": tgt}
            for src, tgt in qualified_edges(graph)
            if src in main_ids and tgt in main_ids
        ]
        range_documents.append(
            {"title": str(doc.get("title") or ""), "nodes": nodes_out, "edges": edges_out}
        )

    verification_suppressed = _suppress_uniform_verification(all_node_dtos, ledger)
    if verification_suppressed:
        _blank_verification(all_node_dtos)

    facts: list[str] = []
    if topic_label:
        facts.append(f"この記録は、トピック『{topic_label}』での記録です。")
    if fallback_fact:
        facts.append(fallback_fact)
    # 触れている理論構成は**列挙できるときだけ**言う（空の「：」で終わる欠けた文を出さない）。
    if ordered_labels:
        facts.append("このトピックの教材が触れている理論構成：" + _enumerate(ordered_labels))
    if not fallback_fact:
        facts.append(FACT_RANGE_UNKNOWN_POINT)
    if verification_suppressed:
        facts.append(FACT_NO_VERIFICATION_RECORDS)
    facts.append(FACT_RANGE_SHARPEN)

    if atlas_concept_context:
        region_label = str(atlas_concept_context.get("region_label") or "")
        concept_label = str(atlas_concept_context.get("concept_label") or "")
        if region_label and concept_label:
            facts.append(
                _RANGE_ATLAS_FACT_TEMPLATE.format(
                    region_label=region_label, concept_label=concept_label
                )
            )

    return {
        "available": True,
        "mode": MODE_RANGE,
        "ledger_available": bool(ledger) and not verification_suppressed,
        "center": None,
        "upstream": [],
        "downstream": [],
        "root_path": [],
        "edges": [],
        "topic_label": topic_label,
        # コース範囲フォールバックかどうか（UI が見出しを「この話題が触れている範囲」から
        # 「コースのソース論文の理論構成」へ切り替えるための区別。事実文と見出しの主張を
        # 一致させる = 出所の正直さ。数値ではなく状態の真偽のみ）。
        "range_fallback": bool(fallback_fact),
        "range_documents": range_documents,
        "facts": facts,
        "notice": None,
    }


def unavailable(
    mode: str = MODE_NEAR,
    notice: str = NOTICE_UNRESOLVED,
    facts: list[str] | tuple[str, ...] = (),
) -> dict:
    """中心が解決できなかったときの DTO（200 で返す。欠落を異常として演出しない・P4）。

    ``facts`` は「では何をすれば結びつくのか」を示す**出口案内**の事実文
    （呼び出し側が :data:`FACT_RANGE_SHARPEN` 等のサーバ側定数を渡す）。欠落を
    行き止まりにしないための1行で、助言・評価はしない（PMN-5）。
    """
    return {
        "available": False,
        "mode": mode,
        "ledger_available": False,
        "center": None,
        "upstream": [],
        "downstream": [],
        "root_path": [],
        "edges": [],
        "facts": [f for f in facts if f],
        "notice": notice,
    }


# ---------------------------------------------------------------------------
# DB 経路（route から呼ばれる唯一の入口）
# ---------------------------------------------------------------------------


def _documents_for_anchor(
    node: PersonalNode, *, can_view_document: Callable[[str, str], bool] | None, user_id: str
) -> list[str]:
    """中心解決の候補 document_id を決定論順で返す（閲覧可能なものだけ）。

    component / claim アンカーは直接 document を引ける。それ以外（equation /
    derivation_step / stage）は痕跡のコース sources を走査する（設計書 §3.2）。
    """
    candidates: list[str] = []
    anchor_type = node.anchor.anchor_type
    anchor_id = node.anchor.anchor_id
    if anchor_type == ANCHOR_TYPE_COMPONENT:
        doc_id = queries.fetch_component_document_id(anchor_id)
        if doc_id:
            candidates.append(doc_id)
    elif anchor_type == _ANCHOR_CLAIM:
        doc_id = queries.fetch_claim_document_id(anchor_id)
        if doc_id:
            candidates.append(doc_id)
    if not candidates and node.course_id:
        candidates = sorted(queries.fetch_course_document_ids(str(node.course_id)))[
            :MAX_DOCUMENTS_SCANNED
        ]
    if can_view_document is None:
        return candidates
    return [d for d in candidates if can_view_document(user_id, d)]


def _claim_summaries_for_graphs(entries: list[tuple[str, dict]]) -> dict[str, dict]:
    """表示対象グラフの main ノード ``linked_claim_ids`` の**和集合を1回**で解決する。

    ``entries`` は ``[(document_id, graph), ...]``。claim 参照が無ければクエリを発行
    しない（``{}``）。例外はすべて握って ``{}`` に倒す（fail-soft: 逐語が出ないだけで
    依存の向きの表示は成立させる）。
    """
    document_ids: list[str] = []
    claim_ids: list[str] = []
    seen: set[str] = set()
    for document_id, graph in entries:
        doc_id = str(document_id or "")
        if doc_id and doc_id not in document_ids:
            document_ids.append(doc_id)
        for node in main_nodes(graph or {}):
            for claim_id in _ids(node, "linked_claim_ids"):
                if claim_id not in seen:
                    seen.add(claim_id)
                    claim_ids.append(claim_id)
    if not claim_ids:
        return {}
    try:
        return queries.fetch_claim_summaries(claim_ids, document_ids=document_ids)
    except Exception:
        return {}


def _resolve_range_atlas_context(course_id: str, topic_id: str) -> dict | None:
    """装置4: 範囲ビューの topic → 分野の地図の接続文脈を解決する（fail-soft）。

    ``topics[].atlas_node_id``（コース⇄地図バインディング）→ コースの**明示**
    cartridge_id（``fetch_course_cartridge_id``。導出カートリッジへはフォールバック
    しない）→ 凍結骨格上の概念文脈（``fetch_atlas_concept_context``）の順に解決する。
    binding が無い・cartridge_id が空・骨格突合不能・例外はすべて ``None``
    （呼び出し側は事実文を1行減らすだけで済む）。
    """
    try:
        topic_atlas = queries.fetch_topic_atlas_binding(course_id)
        atlas_node_id = topic_atlas.get(str(topic_id))
        if not atlas_node_id:
            return None
        cartridge_id = queries.fetch_course_cartridge_id(course_id)
        if not cartridge_id:
            return None
        return queries.fetch_atlas_concept_context(cartridge_id, atlas_node_id)
    except Exception:
        return None


def _nearby_for_topic_anchor(
    start: PersonalNode,
    network,
    *,
    mode: str,
    center_component_id: str | None,
    can_view_document: Callable[[str, str], bool] | None,
    user_id: str,
) -> dict | None:
    """topic アンカーの範囲モード（+ 中心移動）を解決する（設計書『範囲モード』拡張）。

    ``center_component_id`` が指定されたときは、通常の点ビュー（``build_nearby``、
    要求された ``mode``）へ切り替える — topic はどの main ノードにも直接対応しないため、
    ``node_matches_anchor`` に頼らずコース sources を直接走査して指定 ID を main 層から
    探す。見つからなければ ``None``（route が 404 化）。

    指定が無ければ、``topics[].linked_claim_ids`` から claim を解決し、それらが実際に
    存在する document の main 層を「触れている（touched）」フラグ付きで列挙する範囲モードを
    返す（PMN-1: 位置の意味は主張しない。事実の集合のみ）。

    トピック⇄claim の対応が引けない（``linked_claim_ids`` 無し / claim が document に
    解決できない / 解決した document にグラフが無い）ときは、notice 1行で終わらせず
    **コース範囲フォールバック**に落ちる: コース sources の解析済み論文の main 層を
    ``touched`` なしで範囲表示し、粗いことを ``FACT_RANGE_COURSE_FALLBACK`` で明示する。
    「偽精度の禁止」は粗い対応を隠すことではなく、粗いとラベルして見せることなので、
    ``touched`` を推測で立てない限りこの表示は事実のままである。
    """
    if not start.course_id:
        return unavailable(mode, facts=[FACT_RANGE_SHARPEN])

    if center_component_id:
        for document_id in _documents_for_anchor(
            start, can_view_document=can_view_document, user_id=user_id
        ):
            graph = queries.fetch_component_graph(document_id)
            if not graph:
                continue
            moved = next(
                (
                    n
                    for n in main_nodes(graph)
                    if str(n.get("component_id") or "") == center_component_id
                ),
                None,
            )
            if moved is None:
                continue
            mains = main_nodes(graph)
            ledger = queries.fetch_component_ledger_statuses(
                [str(n.get("component_id") or "") for n in mains]
            )
            support_fact_line = queries.fetch_center_support_fact_line(
                center_component_id,
                document_id=document_id,
                course_id=str(start.course_id or ""),
            )
            shared_part_fact_lines = _shared_part_thread_facts(
                moved,
                document_id=document_id,
                can_view_document=can_view_document,
                user_id=user_id,
            )
            return build_nearby(
                graph,
                center_node=moved,
                personal_nodes=network.nodes,
                ledger=ledger,
                support_fact_line=support_fact_line,
                shared_part_fact_lines=shared_part_fact_lines,
                claim_summaries=_claim_summaries_for_graphs([(document_id, graph)]),
                mode=mode,
            )
        return None

    binding = queries.fetch_topic_claim_binding(
        str(start.course_id), topic_id=start.anchor.anchor_id
    )
    claim_ids = binding.get("claim_ids") or []
    topic_label = str(binding.get("topic_label") or "")

    doc_to_claims: dict[str, set[str]] = {}
    for claim_id in claim_ids:
        document_id = queries.fetch_claim_document_id(claim_id)
        if not document_id:
            continue
        doc_to_claims.setdefault(document_id, set()).add(claim_id)

    document_ids = sorted(doc_to_claims)[:MAX_DOCUMENTS_SCANNED]
    if can_view_document is not None:
        document_ids = [d for d in document_ids if can_view_document(user_id, d)]

    documents: list[dict] = []
    all_main_ids: list[str] = []
    for document_id in document_ids:
        graph = queries.fetch_component_graph(document_id)
        if not graph:
            continue
        # 交差ゼロでも表示する（touched は交差したノードにだけ点く）。claim が document に
        # 解決できた事実は、その論文の理論構成を範囲として見せるのに十分である。
        all_main_ids.extend(
            str(n.get("component_id") or "") for n in main_nodes(graph)
        )
        documents.append(
            {
                "document_id": document_id,
                "graph": graph,
                "touched_claim_ids": doc_to_claims[document_id],
            }
        )

    fallback_fact = ""
    if not documents:
        # コース範囲フォールバック: touched は立てず、粗いことを事実文で明示する。
        fallback_fact = FACT_RANGE_COURSE_FALLBACK
        for document_id in _documents_for_anchor(
            start, can_view_document=can_view_document, user_id=user_id
        ):
            graph = queries.fetch_component_graph(document_id)
            if not graph:
                continue
            all_main_ids.extend(
                str(n.get("component_id") or "") for n in main_nodes(graph)
            )
            documents.append(
                {
                    "document_id": document_id,
                    "graph": graph,
                    "touched_claim_ids": set(),
                }
            )

    if not documents:
        return unavailable(mode, NOTICE_TOPIC_NO_MAPPING, facts=[FACT_RANGE_SHARPEN])

    titles = queries.fetch_document_titles([d["document_id"] for d in documents])
    for doc in documents:
        doc["title"] = titles.get(doc["document_id"], "")

    ledger = queries.fetch_component_ledger_statuses(all_main_ids)
    atlas_concept_context = _resolve_range_atlas_context(
        str(start.course_id or ""), str(start.anchor.anchor_id or "")
    )
    claim_summaries = _claim_summaries_for_graphs(
        [(doc["document_id"], doc["graph"]) for doc in documents]
    )
    return build_topic_range(
        documents,
        personal_nodes=network.nodes,
        ledger=ledger,
        topic_label=topic_label,
        atlas_concept_context=atlas_concept_context,
        fallback_fact=fallback_fact,
        claim_summaries=claim_summaries,
    )


def nearby_for_person_node(
    user_id: str,
    node_id: str,
    *,
    mode: str = MODE_NEAR,
    center_component_id: str | None = None,
    can_view_document: Callable[[str, str], bool] | None = None,
) -> dict | None:
    """本人の痕跡ノードを中心にした近傍関係ビュー（読み取り専用・非LLM・DB 非変更）。

    Returns:
        DTO。``node_id`` が本人の個人ネットワークに無ければ ``None``（route が 404 にする）。
        中心が理論構成に解決できない場合は ``unavailable()``（200 + notice）。
        ``center_component_id`` が当該 document の main 層に無い場合も ``None``
        （存在しない場所を中心にできない = fail-closed）。``topic`` アンカーは範囲モード
        （``build_topic_range``）に縮退する（``_nearby_for_topic_anchor`` 参照）。
    """
    mode = mode if mode in MODES else MODE_NEAR
    network = derive_person_network(user_id)
    start = next((n for n in network.nodes if n.id == node_id), None)
    if start is None:
        return None

    anchor_type = start.anchor.anchor_type
    if anchor_type not in RESOLVABLE_ANCHOR_TYPES and anchor_type not in RANGE_ANCHOR_TYPES:
        return unavailable(mode, facts=[FACT_RANGE_SHARPEN])

    if anchor_type in RANGE_ANCHOR_TYPES:
        return _nearby_for_topic_anchor(
            start,
            network,
            mode=mode,
            center_component_id=center_component_id,
            can_view_document=can_view_document,
            user_id=user_id,
        )

    for document_id in _documents_for_anchor(
        start, can_view_document=can_view_document, user_id=user_id
    ):
        graph = queries.fetch_component_graph(document_id)
        if not graph:
            continue
        center_node = find_center_node(
            graph, start.anchor.anchor_type, start.anchor.anchor_id
        )
        if center_node is None:
            continue
        if center_component_id:
            moved = next(
                (
                    n
                    for n in main_nodes(graph)
                    if str(n.get("component_id") or "") == center_component_id
                ),
                None,
            )
            if moved is None:
                return None
            center_node = moved

        mains = main_nodes(graph)
        ledger = queries.fetch_component_ledger_statuses(
            [str(n.get("component_id") or "") for n in mains]
        )
        support_fact_line = queries.fetch_center_support_fact_line(
            str(center_node.get("component_id") or ""),
            document_id=document_id,
            course_id=str(start.course_id or ""),
        )
        shared_part_fact_lines = _shared_part_thread_facts(
            center_node,
            document_id=document_id,
            can_view_document=can_view_document,
            user_id=user_id,
        )
        return build_nearby(
            graph,
            center_node=center_node,
            personal_nodes=network.nodes,
            ledger=ledger,
            support_fact_line=support_fact_line,
            shared_part_fact_lines=shared_part_fact_lines,
            claim_summaries=_claim_summaries_for_graphs([(document_id, graph)]),
            mode=mode,
        )

    return unavailable(mode, facts=[FACT_RANGE_SHARPEN])
