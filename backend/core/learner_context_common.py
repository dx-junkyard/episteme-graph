"""学習者向け文脈 API の共通プリミティブ（非LLM・読み取り専用・FastAPI 非 import）。

``core/component_context.py``（component の rich 投影）と ``core/element_context.py``
（claim / equation の W層レンズ射影）は、生まれた時期が違うために**同じ責務を二度**
持っていた:

- 数値非公開（W8）の ``strip_confidence``
- コース document スコープを SQL の WHERE 句で強制する fail-closed な要素解決
- W層 ITEM を学習者向けに射影するときの遮断層（内部 ID / 生 TeX を出さない、
  ``navigable`` を学習者が実際に開ける型だけに絞り直す）

このうち遮断層は後発の element 側にしか無く、component の graph レーンには
``comp_003`` のような内部 ID ラベルや、学習者向けの取得口が無い型
（figure / evidence / derivation）の ``navigable: true`` が漏れていた。本モジュールを
両者の**正本**に置くことで、遮断は片方だけに実装されない構造にする。

配置の規約:

- 本モジュールは ``core.deliberation``（W層）と ``core.text_excerpt`` を読むだけで、
  ``core.component_context`` / ``core.element_context`` を import しない
  （逆依存を作らない。従来 element_context が component_context から
  ``strip_confidence`` を import していた向きの依存は、本モジュールの新設で解消する）。
- 公開名は両モジュール側から再エクスポートし、既存の import 面・テストの参照面
  （``component_context._strip_confidence`` / ``element_context._project_item`` 等）を
  そのまま維持する。
- 書き込みを行わない（本モジュールに DB への更新経路は無い。SQL 断片を組み立てる
  ``scoped_id_match_sql`` も WHERE 句専用で、実行は呼び出し側の SELECT のみ）。
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Callable

from core.text_excerpt import looks_like_tex_math
from core.deliberation.schema import (
    CONTEXT_STATUS_CANDIDATE,
    ELEMENT_DERIVATION,
    ELEMENT_EQUATION,
    ELEMENT_EVIDENCE,
    ELEMENT_THEORY_CLAIM,
    ELEMENT_THEORY_COMPONENT,
)

# ---------------------------------------------------------------------------
# 共通定数
# ---------------------------------------------------------------------------

# W層 context lens レーンの表示上限（``context_lens.py`` の ``_CONTEXT_LANE_MAX`` と
# 同じ値）。W層が candidate 込みで既に切ったあとに学習者向けフィルタが走るため、
# 実際の表示件数はこれ以下になり得る。
LANE_MAX = 20

# 文脈 DTO の出所ラベル（コース公開時点の凍結成果物を読んでいる）。
PROVENANCE_COURSE_FREEZE = "course_freeze"


# ---------------------------------------------------------------------------
# 小さな型ガード / 正規化
# ---------------------------------------------------------------------------


def is_uuid(value: Any) -> bool:
    """UUID 形の文字列か（DB UUID と agent 側 ID の判別に使う）。"""
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def json_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def json_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def normalized_document_ids(course_document_ids: set[str] | None) -> list[str]:
    """コース document 集合を SQL バインド用の（空要素を除いた）ソート済み list にする。"""
    return sorted({str(d) for d in (course_document_ids or set()) if str(d or "").strip()})


def strip_confidence(value: Any) -> Any:
    """レスポンスを再帰走査して ``"confidence"`` キーを除去する（数値非公開・W8 相当）。

    学習者向け文脈 API 共通のヘルパー。component / claim / equation の各 API が
    同じ規則を使うため、正本はここ1箇所に置く（W8 の実装をコピペしない）。
    """
    if isinstance(value, dict):
        return {k: strip_confidence(v) for k, v in value.items() if k != "confidence"}
    if isinstance(value, list):
        return [strip_confidence(v) for v in value]
    return value


# ---------------------------------------------------------------------------
# コース document スコープを強制する解決 SQL（fail-closed）
# ---------------------------------------------------------------------------


def scoped_id_match_sql(
    element_id: str, document_ids: list[str]
) -> tuple[str, dict[str, Any]]:
    """「DB UUID または ``source_scope.legacy_ids`` 一致」の WHERE 断片と params を返す。

    ``document_id = ANY(:doc_ids)`` を**SQL の WHERE 句に直接含める**（後付けの
    Python フィルタではなく）ことが本ヘルパーの要点である。agent 側 ID
    （``comp_001`` / ``claim_span_001`` 等）は論文ごとに独立採番されるため文書間で
    衝突しうるので、コース外文書の同名要素に誤って一致する余地を SQL の時点で断つ。

    戻り値の ``where_clause`` は ``id`` / ``source_scope`` 列を持つ成果テーブル
    （``theory_components`` / ``theory_claims``）で共通に使える形にしてある。
    呼び出し側は ``WHERE document_id = ANY(:doc_ids) AND ({where_clause})`` の形で
    埋め込む（``params`` には ``raw_id`` / ``doc_ids``、UUID 形のときだけ
    ``uuid_id`` が入る）。
    """
    conditions = ["source_scope->'legacy_ids' ? :raw_id"]
    params: dict[str, Any] = {"raw_id": str(element_id), "doc_ids": document_ids}
    if is_uuid(element_id):
        conditions.append("id = CAST(:uuid_id AS uuid)")
        params["uuid_id"] = str(element_id)
    return " OR ".join(conditions), params


# ---------------------------------------------------------------------------
# 遮断層: 内部 ID ラベル / 生 TeX を学習者に出さない（LE4 / EC3）
# ---------------------------------------------------------------------------
# W層 context_lens はラベル（caption / 本文 / 記号）を引けなかった項目に内部 ID を
# そのまま label として入れる（``_build_claim`` の図 DB UUID・evidence_id・
# subclaim の agent 側 ID、``_build_equation`` の ``synth_claim_0001``、
# ``_build_component`` の ``comp_003``、thesis の ``support:<section>:<idx>`` など）。
# W層は変更しない（LE6）ので、学習者向け射影の時点で「裸の内部 ID 形」を検出し
# 一般ラベルへ置換する。

_UUID_LABEL_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

_INTERNAL_ID_LABEL_RES = (
    re.compile(r"^ev(?:idence)?_[0-9]", re.IGNORECASE),   # evidence_registry: ev_0001
    re.compile(r"^synth_", re.IGNORECASE),               # 合成 claim: synth_claim_0001
    re.compile(r"^claim_", re.IGNORECASE),                # claim 生ID: claim_span_001 / claim_0004
    re.compile(r"^span_[0-9]", re.IGNORECASE),           # rhetorical_role: span_001
    re.compile(r"^support:"),                             # thesis support node: support:<section>:<idx>
    re.compile(r"^node_", re.IGNORECASE),                 # graph node id
)

# ラベル**全体**ではなく「内部 ID を埋め込んだ事実文」も遮る（EC3。
# equation_context_panel_display_design.md §1.5）。W層 ``_derivation_membership_facts``
# は「導出「derivation_eq_tex_b16」のステップ「step_001」」のような文をラベルにするため、
# 先頭一致の ``_INTERNAL_ID_LABEL_RES`` では検出できない。関係の意味（relation_label
# 「の導出に属する」）は保持したまま、ラベルだけ一般ラベルへ置換する。
_EMBEDDED_INTERNAL_ID_RE = re.compile(
    r"derivation_[A-Za-z0-9_]+"
    r"|system_derivation_[0-9]+"
    r"|(?:^|[^A-Za-z0-9])sys_[0-9]+_step_[0-9]+"
    r"|(?:^|[^A-Za-z0-9])step_[0-9]+",
    re.IGNORECASE,
)

# ``eq_2_7`` 形は論文の式番号由来で学習者にも可読なため v1 では置換しない（設計書 §4 の裁定）。
_EQUATION_NUMBER_LABEL_RE = re.compile(r"^eq[_\-.]?[0-9]", re.IGNORECASE)

# element_type 別の一般ラベル（内部 ID を出す代わりの事実文。関係語
# （``relation_label``）は保持するので「図 / を根拠とする」の形で意味は残る）。
_GENERIC_ITEM_LABELS = {
    ELEMENT_THEORY_CLAIM: "関連する主張",
    ELEMENT_THEORY_COMPONENT: "関連する論理要素",
    ELEMENT_EQUATION: "関連する数式",
    "figure": "図",
    "evidence": "本文の根拠箇所",
    "section": "掲載セクション",
    "thesis": "中心命題",
    "derivation": "導出の流れ",
    "symbol": "記号",
    "stage": "理論の段階",
    "part": "構成部品",
}
_GENERIC_ITEM_LABEL_FALLBACK = "関連する要素"

# ``focus.contextual_role`` は上位項目のラベルから合成される（W層
# ``_derive_contextual_role``）ため、内部 ID がそのまま役割文に混ざり得る
# （「synth_claim_0001を定量化する」等）。含まれていたら role をキーごと落とす
# （candidate / unidentified と同じ「推測で穴埋めしない」縮退）。
ROLE_INTERNAL_TOKEN_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"|synth_[A-Za-z0-9_]*[0-9]"
    r"|claim_span_[0-9]"
    r"|claim_[0-9]{3,}"
    r"|ev(?:idence)?_[0-9]{3,}"
    r"|span_[0-9]{3,}"
    r"|support:",
    re.IGNORECASE,
)
# 後方互換 alias（共有契約の公開名は ROLE_INTERNAL_TOKEN_RE）。
_ROLE_INTERNAL_TOKEN_RE = ROLE_INTERNAL_TOKEN_RE

# TheoryOperationGraph のノード ID（``theory_op_0001`` / ``eq_op_0007``）と
# コンポーネントの agent 側 ID。ITEM v2 の ``sublabel`` / ``intrinsic`` の事実文は
# ラベルと違い自由文なので、**文中のどこに現れても**遮断する。
_EXTRA_INTERNAL_TOKEN_RE = re.compile(
    r"(?:^|[^A-Za-z0-9])(?:theory_op|eq_op)_[0-9]"
    r"|(?:^|[^A-Za-z0-9])comp_[0-9]",
    re.IGNORECASE,
)

# W層 ITEM の表示専用 element_type（``deliberation/schema.py`` の解決対象語彙には無い）。
ITEM_TYPE_SYMBOL = "symbol"

# ITEM v2 の ``qualifier``: 式単位の操作ノード（TheoryOperationGraph の
# ``graph_layer='equation_detail'``）の目印。traceability のための層なので**学習者には
# 項目ごと出さない**（CP3。教員向けは折りたたみ「式の詳細層」で残る）。
QUALIFIER_EQUATION_DETAIL = "equation_detail"

# ITEM v2 の ``group``（区画キー。設計書 §4.3）。未知値・空は「関連」区画へ寄せる
# （P4: 項目自体は落とさない）。
ITEM_GROUPS = (
    "stage",
    "thesis",
    "claim",
    "section",
    "symbol_defined",
    "symbol_used",
    "equation_up",
    "equation_down",
    "derivation_in",
    "derivation_out",
    "claim_required",
    "evidence",
    "operation",
    "figure",
    "component",
    "related",
)
ITEM_GROUP_FALLBACK = "related"

# 学習者が「旅の続き」として実際に再フェッチできる element_type。
# theory_claim / equation は claim / equation 文脈 API、theory_component は component
# 文脈 API（``/components/{id}/context``）で開ける。それ以外（figure / section /
# thesis / derivation / symbol / evidence / stage / part）は学習者向けの文脈取得口が
# 無いため ``navigable`` を立てない（W層の ``_NAVIGABLE_ELEMENT_TYPES`` は教員向けの
# 可否なのでそのままでは契約が成立しない）。
LEARNER_NAVIGABLE_ELEMENT_TYPES = (
    ELEMENT_THEORY_CLAIM,
    ELEMENT_EQUATION,
    ELEMENT_THEORY_COMPONENT,
)

# W層設計書 §16 で evidence / derivation が教員向けには navigable になった
# （``context_lens._NAVIGABLE_ELEMENT_TYPES`` に追加）。**学習者の旅の対象は
# claim / equation / component のまま**なので、ホワイトリスト方式に加えて明示的な
# 拒否リストとしても書いておく（W層が語彙を増やしたときに学習者側へ黙って波及しない
# ための二重の fail-closed。学習者向けには対応する文脈取得 API が存在しないため、
# navigable を立てると押しても何も起きない導線になる）。
LEARNER_FORCED_NON_NAVIGABLE_ELEMENT_TYPES = (
    ELEMENT_EVIDENCE,
    ELEMENT_DERIVATION,
)


def is_internal_id_label(
    label: str, element_type: str, element_id: Any, *, include_agent_id_tokens: bool = False
) -> bool:
    """label が「裸の内部 ID」かどうか（LE4 のラベル規則）。

    ``eq_2_7`` のような equation の式番号は論文由来で学習者にも可読なため対象外
    （設計書 §4 の裁定）。既定（``include_agent_id_tokens=False``）の判定は
    element_context の従来実装と**同一**（UUID / 生ID形の先頭一致 / 文中埋め込み /
    label が ITEM の id 生値そのもの）— 共有化は純粋な移設で、claim / equation
    文脈 API の出力は変えない。

    ``include_agent_id_tokens=True`` は **component の graph レーン専用**（§2-5 A-3 の
    裁定範囲）: W層 ``_build_component`` はノードラベルを引けなかったとき ``comp_003`` /
    ``theory_op_0001`` / ``eq_op_0007`` のような agent 側 ID を label に入れ得るため、
    これらも遮断する。claim / equation 経路への同拡張は「同一レーン内で複数の
    ``comp_00X`` が同じ一般ラベルへ collapse する」UX の検討（RC6 と同型）が要るため
    未適用 = オーナー判断待ち。なお ``contains_internal_id``（sublabel / 自由文の判定）
    はこの語彙を**従来から**含む（label 判定との非対称は既存挙動の保存）。
    W層生成側の ``core/deliberation/labels.py::is_internal_id_like`` は同目的の
    別実装（部分重複・非同一）で、統合は W層非改変方針（LE6′）により行わない。
    """
    text = str(label or "").strip()
    if not text:
        return False
    if element_type == ELEMENT_EQUATION and _EQUATION_NUMBER_LABEL_RE.match(text):
        return False
    if _UUID_LABEL_RE.match(text):
        return True
    if any(pattern.match(text) for pattern in _INTERNAL_ID_LABEL_RES):
        return True
    if _EMBEDDED_INTERNAL_ID_RE.search(text):  # EC3: 文中に埋め込まれた内部 ID
        return True
    if include_agent_id_tokens and _EXTRA_INTERNAL_TOKEN_RE.search(text):
        return True  # comp_003 / theory_op_0001 / eq_op_0007（component レーンのみ）
    raw_id = str(element_id or "").strip()
    return bool(raw_id) and text == raw_id


def generic_item_label(element_type: str) -> str:
    """element_type 別の一般ラベル（内部 ID を出す代わりの事実文）。"""
    return _GENERIC_ITEM_LABELS.get(str(element_type or ""), _GENERIC_ITEM_LABEL_FALLBACK)


def contains_internal_id(value: Any) -> bool:
    """自由文（sublabel / 事実文 / 役割文）**のどこか**に内部 ID が含まれるか。

    ``is_internal_id_label`` が「ラベル全体が内部 ID 形か」を見るのに対し、こちらは
    説明文の途中に混ざった ID（「導出「derivation_x」のステップ「step_001」」/
    「synth_claim_0001 を定量化する」）を遮るための判定。W層がラベルラダーで
    生成時点から ID を排除しても（EC3′）、射影側は最後の砦として残す。
    """
    text = str(value or "").strip()
    if not text:
        return False
    if any(pattern.match(text) for pattern in _INTERNAL_ID_LABEL_RES):
        return True
    if _UUID_LABEL_RE.match(text):
        return True
    if _EMBEDDED_INTERNAL_ID_RE.search(text):
        return True
    if ROLE_INTERNAL_TOKEN_RE.search(text):  # UUID / synth_ / ev_ / span_ / support:
        return True
    return bool(_EXTRA_INTERNAL_TOKEN_RE.search(text))


def safe_text(value: Any, *, allow_tex: bool = False) -> str:
    """学習者に出してよい表示文字列だけを通す（内部 ID / 生 TeX を空へ落とす）。

    ``allow_tex=True`` は**記号**専用（``canonical_symbol`` は TeX 形のまま渡し、
    レンダリング可否はフロントの ``looksLikeRenderableTex`` ゲートに委ねる。§5.4）。
    """
    text = str(value or "").strip()
    if not text:
        return ""
    if contains_internal_id(text):
        return ""
    if not allow_tex and looks_like_tex_math(text):
        return ""
    return text


def normalized_group(value: Any) -> str:
    """ITEM の区画キー。未知値・空は「関連」区画へ寄せる（§4.1 / P4）。"""
    group = str(value or "").strip()
    return group if group in ITEM_GROUPS else ITEM_GROUP_FALLBACK


def equation_focus_label(label: str, element_id: str) -> str:
    """数式の見出し（EC1: TeX を出さない）。

    ラベルが TeX なら捨て、``eq_2_7`` のような論文の式番号だけを残す。
    ``eq_tex_b14`` のような合成 ID は見出しにしない（空 = 種別チップのみ）。
    """
    text = str(label or "").strip()
    if text and not looks_like_tex_math(text) and not is_internal_id_label(
        text, ELEMENT_EQUATION, element_id
    ):
        return text
    raw_id = str(element_id or "").strip()
    if raw_id and _EQUATION_NUMBER_LABEL_RE.match(raw_id):
        return raw_id
    return ""


def learner_navigable(element_type: Any, element_id: Any) -> bool:
    """学習者が実際に再フェッチできる型・ID か（W層の ``navigable`` は信用しない）。"""
    kind = str(element_type or "")
    return (
        bool(str(element_id or "").strip())
        and kind in LEARNER_NAVIGABLE_ELEMENT_TYPES
        and kind not in LEARNER_FORCED_NON_NAVIGABLE_ELEMENT_TYPES
    )


# ---------------------------------------------------------------------------
# ITEM 射影（両 API 共通の遮断層。キー集合だけを世代で分ける）
# ---------------------------------------------------------------------------


def project_item(
    item: dict, *, legacy_keys_only: bool = False, include_agent_id_tokens: bool = False
) -> dict | None:
    """W層 ITEM を学習者向けに射影する。``None`` は「学習者に出さない項目」。

    ``evidence_refs``（evidence_id / step_id 等の内部参照）・``relation``（内部語彙
    キー）・``label_source``（来歴 = 教員のみ）は落とし、読み手向けの
    ``relation_label`` を残す。

    - ``qualifier == "equation_detail"``（式単位の操作ノード）は**項目ごと除外**する
      （CP3。traceability 層は学習者に見せない）。
    - ``label`` が裸の内部 ID 形なら element_type 別の一般ラベルへ置換し
      ``unresolved`` を立てる。このとき **``sublabel`` は保持する**（一般ラベルが
      2件並んでも区別できるようにする = RC6 の再発防止）。
    - ``sublabel`` に内部 ID / 生 TeX が混ざっていればその欄だけ空にする（項目は残す）。
    - 生 TeX のラベルは一般ラベルへ落とす（式は再掲しない = EH1。equation は先に
      論文の式番号を試す）。**記号（``symbol``）だけは TeX でも遮断しない** —
      記号は式の再掲ではなく読解の部品で、レンダリング可否はフロントの
      ``looksLikeRenderableTex`` ゲートが判断する（§5.4）。
    - ``navigable`` は学習者が実際に再フェッチできる型かで作り直す（さらに
      evidence / derivation は明示的に拒否する —
      ``LEARNER_FORCED_NON_NAVIGABLE_ELEMENT_TYPES``）。

    ``legacy_keys_only=True`` は component 文脈 API（``graph.upper`` / ``graph.lower``）
    向けで、**遮断は同じだけ効かせたうえでキー集合を旧6キー
    （id / element_type / label / relation_label / relation_status / navigable）に
    留める**。component の DTO に ITEM v2 の ``group`` を足すと、統一パーツカード
    （``element-card.js`` の ``hasGroupedItems``）が1件でも group を見つけた時点で
    4区画描画へ切り替わり、可視の UX 変更になってしまう（キー集合は世代差として
    意図的に残す。遮断だけを共有するのが本引数の目的）。
    """
    element_type = str(item.get("element_type") or "")
    element_id = item.get("element_id")
    qualifier = str(item.get("qualifier") or "").strip()
    if qualifier == QUALIFIER_EQUATION_DETAIL:
        # component レーンにこの qualifier は現れない（``context_lens`` が
        # ``equation_detail`` の ITEM を作るのは equation レンズだけ）ので、
        # legacy_keys_only 経路でも実質 no-op である。
        return None

    label = str(item.get("label") or "")
    unresolved = bool(item.get("unresolved"))
    # EC1/EC2: レーンの相手ラベルにも生 TeX が混ざり得る（equation は式番号があれば
    # それを、無ければ一般ラベルへ）。記号だけは TeX でも遮断しない（§5.4）。
    is_symbol = element_type == ITEM_TYPE_SYMBOL
    if not is_symbol and looks_like_tex_math(label):
        replacement = (
            equation_focus_label("", str(element_id or ""))
            if element_type == ELEMENT_EQUATION
            else ""
        )
        if replacement:
            label = replacement
        else:
            label, unresolved = generic_item_label(element_type), True
    elif is_internal_id_label(
        label, element_type, element_id, include_agent_id_tokens=include_agent_id_tokens
    ):
        label, unresolved = generic_item_label(element_type), True

    if legacy_keys_only:
        return {
            "id": element_id,
            "element_type": item.get("element_type"),
            "label": label,
            "relation_label": item.get("relation_label"),
            "relation_status": item.get("relation_status"),
            "navigable": learner_navigable(element_type, element_id),
        }
    return {
        "id": element_id,
        "element_type": item.get("element_type"),
        "label": label,
        "sublabel": safe_text(item.get("sublabel")),
        "qualifier": qualifier,
        "group": normalized_group(item.get("group")),
        "unresolved": unresolved,
        "relation_label": item.get("relation_label"),
        "relation_status": item.get("relation_status"),
        "navigable": learner_navigable(element_type, element_id),
    }


def is_learner_visible_relation(item: Any) -> bool:
    """その ITEM の関係を学習者に見せてよいか（LE2: AI 候補は出さない）。

    ``relation_status == "candidate"`` は教員が確定していない AI の提案なので、
    component / claim / equation のどの文脈 API でも同じ判定で除外する。
    """
    return isinstance(item, dict) and item.get("relation_status") != CONTEXT_STATUS_CANDIDATE


def visible_lane_items(
    items: Any,
    *,
    legacy_keys_only: bool = False,
    project: Callable[[dict], dict | None] | None = None,
) -> list[dict]:
    """1レーン分の ITEM を candidate 除外 + 射影 + 上限（``LANE_MAX``）で組み立てる。

    ``project`` を渡すと1件の射影をその callable に委ねる（component_context が
    ``_project_context_item`` を注入し、射影 seam を本番経路に載せるための口）。
    省略時は :func:`project_item` を ``legacy_keys_only`` 付きで使う。
    """
    projected: list[dict] = []
    for item in json_list(items):
        if not is_learner_visible_relation(item):
            continue
        entry = (
            project(item)
            if project is not None
            else project_item(item, legacy_keys_only=legacy_keys_only)
        )
        if entry is None:
            continue
        projected.append(entry)
        if len(projected) >= LANE_MAX:
            break
    return projected
