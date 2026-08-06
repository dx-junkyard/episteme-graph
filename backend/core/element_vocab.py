"""統制語彙キー → 日本語表示名の**サーバ側正本**（CP4）。

正本文書: `docs/features/element_context_presentation_redesign.md` §2.4 / §3.2 CP4。
ミラーは `frontend/public/js/element-vocab.js` で、**キー集合と訳語文字列の一致を
`backend/tests/test_element_vocab_mirror.py` が固定する**。この2ファイル以外に訳語を
直書きしない。

方針（RC10 の是正）:

* **統制語彙だけを訳す**。role_in_argument / theory stage / operation / chain_type /
  link_status / definition_status / claim_type / symbol scope が対象。
* **自由文（英語）は訳さない**。summary / description / 逐語根拠は原文のまま表示し、
  出所注記を添えるのは表示側の責務（表示パスに LLM 翻訳を入れない）。
* **未知キーは空文字を返す**（fail-closed）。内部語彙をそのまま画面へ漏らさないため。
  呼び出し側は空文字を「その欄を出さない」判断に使う（既存 ``statusLabel`` と同規約）。

theory stage の訳語はかつて3箇所（`admin-lecture-studio.js` の「理論的前提」/
`core/discuss/opening.py` の「理論の土台」/ 未定義）に分裂していた。オーナー承認済みの
統一語（§9 Q2）は本モジュールの ``THEORY_STAGE_LABELS`` で、"理論的前提" は D層の
assumption（前提）と衝突するため採らない。

本モジュールは純粋なデータ + 純粋関数のみ（FastAPI / DB / LLM を import しない）。
"""
from __future__ import annotations

__all__ = [
    "CHAIN_TYPE_LABELS",
    "CLAIM_TYPE_LABELS",
    "DEFINITION_STATUS_LABELS",
    "EQUATION_ROLE_LABELS",
    "LINK_STATUS_FACTS",
    "LINK_STATUS_LABELS",
    "OPERATION_LABELS",
    "SYMBOL_SCOPE_LABELS",
    "THEORY_STAGE_LABELS",
    "THEORY_STAGE_DISPLAY_TO_KEY",
    "chain_type_label",
    "claim_type_label",
    "definition_missing_fact",
    "definition_status_label",
    "equation_role_label",
    "link_status_fact",
    "link_status_label",
    "operation_label",
    "symbol_scope_label",
    "theory_stage_key",
    "theory_stage_label",
]


def _lookup(table: dict[str, str], key: object) -> str:
    """未知キーは "" を返す（fail-closed。内部語彙を画面へ出さない）。"""
    return table.get(str(key if key is not None else "").strip(), "")


# ── theory stage（src/episteme_graph/agents/component_graph/schema.py の
#    THEORY_STAGES / THEORY_STAGE_LABELS と1対1）───────────────────────────────

THEORY_STAGE_LABELS: dict[str, str] = {
    "theory_basis": "理論の土台",
    "observation_model": "観測モデル",
    "observable_construction": "観測量の構成",
    "equation_system": "式の体系",
    "elimination": "消去",
    "consistency_relation": "整合関係",
    "diagnostic_application": "診断・応用",
}

# A層が main ノードの label に載せる**英語表示名** → stage キーの逆引き
# （`THEORY_STAGE_LABELS`（英語）は agent 側 schema の同名定数）。graph node の
# label を訳語へ変換するときに使う。
THEORY_STAGE_DISPLAY_TO_KEY: dict[str, str] = {
    "theory basis": "theory_basis",
    "observation model": "observation_model",
    "observable construction": "observable_construction",
    "equation system": "equation_system",
    "elimination": "elimination",
    "consistency relation": "consistency_relation",
    "diagnostic / application": "diagnostic_application",
    "diagnostic/application": "diagnostic_application",
}


def theory_stage_label(key: object) -> str:
    return _lookup(THEORY_STAGE_LABELS, key)


def theory_stage_key(value: object) -> str:
    """stage キー・英語表示名・``"<Stage>: 説明"`` 形から stage キーを引く。

    未知は "" を返す（fail-closed）。A層の main ノード label は英語表示名そのもの
    （#308 の規約）だが、旧 run には ``"Theory basis: ..."`` 形も残っているため
    コロン前の見出し部分でも引けるようにする。
    """
    raw = str(value if value is not None else "").strip()
    if not raw:
        return ""
    if raw in THEORY_STAGE_LABELS:
        return raw
    lowered = raw.lower()
    if lowered in THEORY_STAGE_DISPLAY_TO_KEY:
        return THEORY_STAGE_DISPLAY_TO_KEY[lowered]
    if lowered.replace(" ", "_") in THEORY_STAGE_LABELS:
        return lowered.replace(" ", "_")
    head = raw.split(":", 1)[0].strip().lower()
    if head and head in THEORY_STAGE_DISPLAY_TO_KEY:
        return THEORY_STAGE_DISPLAY_TO_KEY[head]
    if head and head.replace(" ", "_") in THEORY_STAGE_LABELS:
        return head.replace(" ", "_")
    return ""


# ── 導出・グラフの操作動詞 ────────────────────────────────────────────────────
# キーは snake_case（derivation_chain/schema.py の OPERATION_ONTOLOGY /
# CONTROLLED_OPERATIONS、component_graph の operation）。訳語は
# `admin-lecture-studio.js` の LS_OPERATION_VERB_JA（issue #337、英語表示形キー）を
# そのまま移植したもので、``operation_label`` が "Apply definition" のような英語表示形も
# snake_case へ正規化して引けるようにしている。

OPERATION_LABELS: dict[str, str] = {
    # LS_OPERATION_VERB_JA からの移植（複合動詞）
    "apply_definition": "定義を適用",
    "apply_criterion": "基準を適用",
    "apply_constraint": "制約を適用",
    "apply_equation": "式を適用",
    "apply_measurement": "測定を適用",
    "apply_independence": "独立性を適用",
    "infer_conclusion": "結論を導出",
    "infer_intermediate_claim": "中間主張を導出",
    "derive_result": "結果を導出",
    "derive_consistency_relation": "整合関係を導出",
    "flag_limitation": "制約を確認",
    "eliminate_parameter": "パラメータを消去",
    "solve_linear_system": "連立方程式を求解",
    "state_assumption": "仮定を提示",
    "branch_on_condition": "条件分岐",
    "introduce_observable": "観測量を導入",
    # LS_OPERATION_VERB_JA からの移植（単独動詞）
    "compare": "比較・検証",
    "define": "定義",
    "construct": "構成",
    "linearize": "線形化",
    "normalize": "正規化",
    "approximate": "近似",
    "substitute": "代入",
    "eliminate": "消去",
    "derive": "導出",
    "constrain": "制約",
    "diagnose": "診断",
    "solve": "求解",
    "infer": "推論",
    "apply": "適用",
    "flag": "確認",
    "state": "提示",
    "introduce": "導入",
    # OPERATION_ONTOLOGY のうち LS 側に無かったもの
    "apply_measurement_or_update": "測定・更新を適用",
    "apply_non_disturbance_or_independence": "独立性・非擾乱を適用",
    "transform": "変換",
    "relate": "関係づけ",
    "integrate": "積分",
    "eliminate_variable": "変数を消去",
}


def operation_label(key: object) -> str:
    """操作動詞の訳。snake_case キーと英語表示形（"Apply definition"）の両方を引ける。"""
    raw = str(key if key is not None else "").strip()
    if not raw:
        return ""
    normalized = raw.lower().replace(" ", "_").replace("-", "_")
    return OPERATION_LABELS.get(normalized, "")


# ── 導出チェーンの種別（derivation_chain/schema.py の chain_type）───────────────

CHAIN_TYPE_LABELS: dict[str, str] = {
    "equation_chain": "式の導出",
    "claim_chain": "主張の導出",
    "mixed_chain": "混在した導出",
    "system_level": "系レベルの操作",
}


def chain_type_label(key: object) -> str:
    return _lookup(CHAIN_TYPE_LABELS, key)


# ── 式の前段リンク状態（equation_semantics/schema.py の LINK_STATUSES）──────────
# ``link_status_label`` は一覧・チップ用の短い名前、``link_status_fact`` は
# 「もとになる式」が空欄である**理由の事実文**（CP10「空は沈黙ではない」）。

LINK_STATUS_LABELS: dict[str, str] = {
    "derived": "前段の式から導出",
    "axiomatic": "出発点として置かれる",
    "external_reference": "本論文の外部を参照",
    "unresolved": "前段は未同定",
}

# ``derived`` は空欄にならないので事実文を持たない（キーを載せない = None を返す）。
LINK_STATUS_FACTS: dict[str, str] = {
    "axiomatic": "この式は定義であり、前段の式を持ちません。",
    "external_reference": "前段の式は本論文の外部にあります。",
    "unresolved": "前段の式は同定できていません。",
}


def link_status_label(key: object) -> str:
    return _lookup(LINK_STATUS_LABELS, key)


def link_status_fact(key: object) -> str | None:
    """空欄の理由の事実文。``derived`` と未知キーは None（述べることが無い）。"""
    return LINK_STATUS_FACTS.get(str(key if key is not None else "").strip()) or None


# ── 記号の定義状態・スコープ（symbol_registry/schema.py）───────────────────────
# ``definition_missing`` は SymbolRecord.review_reasons の語彙だが、読者にとっては
# 「論文中に定義が無い」という有用な事実なので定義状態と同じ表で扱う。

DEFINITION_STATUS_LABELS: dict[str, str] = {
    "defined": "この論文で定義",
    "redefined": "この論文で再定義",
    "used": "使用のみ（定義は別の箇所）",
    "definition_missing": "定義なし",
    "unknown": "不明",
}

_DEFINITION_MISSING_FACT = "論文中に明示的な定義が見つかりません"


def definition_status_label(key: object) -> str:
    return _lookup(DEFINITION_STATUS_LABELS, key)


def definition_missing_fact() -> str:
    """定義が見つからないことの事実文（CP10。隠さず・推測もしない）。"""
    return _DEFINITION_MISSING_FACT


SYMBOL_SCOPE_LABELS: dict[str, str] = {
    "equation_local": "この式の中だけ",
    "section": "この節の中",
    "document": "論文全体",
}


def symbol_scope_label(key: object) -> str:
    return _lookup(SYMBOL_SCOPE_LABELS, key)


# ── 主張の種別（claim_object_builder の CLAIM_TYPE_ONTOLOGY ∪
#    theory_claims.claim_type の CHECK 語彙）──────────────────────────────────

CLAIM_TYPE_LABELS: dict[str, str] = {
    "definition": "定義",
    "criterion": "判定基準",
    "assumption": "仮定",
    "approximation": "近似",
    "setup": "設定",
    "observable_definition": "観測量の定義",
    "operator_relation": "演算子の関係",
    "equation_definition": "式による定義",
    "equation_relation": "式の関係",
    "equation_transformation": "式の変形",
    "equation_approximation": "式の近似",
    "equation_constraint": "式の制約",
    "equation": "数式",
    "relation": "関係",
    "derivation_step": "導出ステップ",
    "derivation_result": "導出結果",
    "measurement_or_update": "測定・更新",
    "causal_or_dependency_claim": "因果・依存関係",
    "incompatibility_or_constraint": "非両立・制約",
    "comparison": "比較",
    "result": "結果",
    "main_result": "主要な結果",
    "conclusion": "結論",
    "limitation": "限界",
    "correction": "補正",
    "uncertainty": "不確かさ",
    "diagnostic_claim": "診断的主張",
    "method_choice": "手法の選択",
    "method": "手法",
    "method_motivation": "手法の動機",
    "background": "背景",
    "prior_work": "先行研究",
    "meta": "メタ情報",
    "problem_statement": "問題設定",
    "theory_encoding": "理論の定式化",
    "structural_property": "構造的性質",
    "interpretation": "解釈",
    "unknown": "不明",
}


def claim_type_label(key: object) -> str:
    return _lookup(CLAIM_TYPE_LABELS, key)


# ── 数式の論証における役割（equation_semantics の ROLE_IN_ARGUMENT_VOCAB）───────
# 既存の `element-vocab.js` の EQUATION_ROLE_LABELS と**同じ文字列**（ミラーテストで固定）。

EQUATION_ROLE_LABELS: dict[str, str] = {
    "premise": "前提",
    "definition": "定義",
    "derived": "導出結果",
    "result": "結果",
    "constraint": "制約",
}


def equation_role_label(key: object) -> str:
    return _lookup(EQUATION_ROLE_LABELS, key)
