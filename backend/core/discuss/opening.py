"""discuss モード「開幕画面」の投影（設計書 §3.3 / §6.3）。

投影の是正は `docs/features/discuss_opening_authoring_design.md` §3（Phase 0）と
§2 最下段（Phase 0b: `course_focus`）が正本。見出しは**主語ごとに固定**する
（論文 / システム（解析） / AI の推測 / 教員）。混ぜない。


白紙のチャット欄で始めないための3要素（中心命題・支持構造・最も脆い一手／理論の
バックボーン／最初の一手は最初の一手のチップのみフロント側で描く）を、A層成果物
（thesis_reconstruction artifact・TheoryOperationGraph の main 層）と D層台帳の投影
（``core.doubt.open_assumptions.compile_open_assumptions``）から**非LLM**で組み立てる。

設計原則:
- 純粋投影部（``project_thesis`` / ``project_backbone`` / ``project_fragile_points`` と
  その下請け）は fake の dict を渡すだけで単体テストできる（``core/personal_graph/derive.py``
  の「純粋関数と DB 読み出しの分離」を踏襲。参照: ``test_discuss_opening.py``）。
- 「議論のきっかけ」（``discuss_opening_authoring_design.md`` §7、Phase 3）だけは投影では
  なく、パイプラインが生成し**教員が承認した**素材（``element_explanations`` の
  ``status='approved'`` / ``role='discussion_seed'`` 行）の配信である。承認済み以外は
  一切載せない（OA2）。承認済みが無い document は投影のまま（OA4）。ここでも LLM は
  呼ばない（読み出しのみ・OA3）。
- DB 読み出し部（``_document_titles`` / ``_load_graph_nodes`` / `_claim_label_index`` /
  ``_load_approved_discussion_seeds`` / ``_compile_open_assumptions``）はこのモジュール内で
  完結させ、都度セッションを開いて
  ``finally`` で閉じる（``core/component_context.py`` と同じ流儀）。1文書の読み出し失敗が
  画面全体を壊さないよう、呼び出し側で fail-soft に握る。
- confidence / load_score 等の生数値は一切レスポンスに含めない（DM6/W8）。射影する
  フィールドをホワイトリストで組み立てたうえ、念のため再帰的にも除去する。
- 本モジュールは FastAPI を import しない。
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from core.deliberation.refs import document_run_artifacts, equation_records
from core.element_explanations import (
    ELEMENT_TYPE_DOCUMENT,
    ROLE_DISCUSSION_SEED,
    STATUS_APPROVED,
    list_for_document,
)
from core import element_vocab
from core.label_vocab import SUPPORT_SECTION_LABELS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 上限（設計書 §3.3 / §6.3 の契約どおり）
# ---------------------------------------------------------------------------

_MAX_CENTRAL_ITEMS = 5
_MAX_SUPPORT_ITEMS_PER_SECTION = 5
_MAX_SUPPORT_ENTRIES_PER_SECTION = 5
_MAX_BACKBONE_NODES = 12
_MAX_FRAGILE_POINTS = 8
# [D-6] 2種類の脆い箇所（主語が違う）の枠。合計は ``_MAX_FRAGILE_POINTS`` のまま。
# 単純に「assumption を先に積んで末尾を切る」と、台帳行が多い document では
# backbone（subject=system）区画が丸ごと空になり「この画面に出ない情報がどこにも無い
# 状態は作らない」（OA7）に反する。片方が枠を使い切らないときは残枠を融通する。
_FRAGILE_BACKBONE_QUOTA = 3
# 「別の見方（AI の提示）」（discuss_opening_authoring_design.md §2）の上限。
_MAX_ALTERNATIVES = 3
# 「議論のきっかけ」（同 §2 / §7、承認済み素材の配信）の上限。生成側の
# ``DISCUSS_OPENING_MAX_ITEMS_PER_DOCUMENT``（既定4）と同じ桁に揃える。再解析を跨いで
# 承認済み行が積み上がっても開幕画面が長くならないようにするための表示上限で、
# 切ったこと自体を示す独立フラグは持たない（``project_thesis`` と同じ方針:
# 設計契約に無いフィールドを増やさない）。
_MAX_DISCUSSION_SEEDS = 4

# alternative_theses は thesis_reconstruction artifact の中で唯一 claim_ids /
# evidence_block_ids を持たない（agents/thesis_reconstruction/schema.py: text /
# reason / confidence のみ）。したがって論文まで辿れない。DM1 / OA7 に従い
# 「出さない」のではなく「そう表示する」ため、出所ラベルをサーバ側から必ず添える。
_ALTERNATIVE_ATTRIBUTION_LABEL = "AI が提示した別の定式化（出典との対応は未確認）"

# 承認済み素材（``element_explanations`` の ``status='approved'`` 行）に添える出所表示
# （discuss_opening_authoring_design.md §7 の文言そのもの）。**投影のみの区画には
# 付けない** — 署名は「人が見た」ことの表明なので、AI 投影に流用すると意味が壊れる。
# 出所ラベルの持ち方は ``_ALTERNATIVE_ATTRIBUTION_LABEL`` と同じ「サーバ側の定数を
# DTO に添える」方式に揃える（フロントの固定文にしない = 出所表示の正本を1箇所にする）。
_AUTHORED_BY_LABEL = "この説明は、論文の解析結果をもとに担当教員が確認したものです。"

# fragile point の主語（discuss_opening_authoring_design.md §2 / §3）。
# 「論文についての言明」と「システム（解析）についての言明」を混ぜないための構造的な区別。
# フロントはこの値（または kind）で区画を分け、1つの見出しに積まない。
FRAGILE_SUBJECT_PAPER = "paper"
FRAGILE_SUBJECT_SYSTEM = "system"

# ---------------------------------------------------------------------------
# theory stage 語彙（domain-neutral・A層コードを import しない写し）。
# 順序の正本: core/atlas_state.py::_STAGE_ORDER（L3 導出チェーンが同じ並びを使う）。
# stage コードの正本: src/episteme_graph/agents/component_graph/schema.py::THEORY_STAGES。
#
# 表示名は**学習者向けの日本語**にする（A層 agents 側の THEORY_STAGE_LABELS は英語）。
# 開幕画面は学習者が最初に見る画面で、`Theory basis` `Equation system` のような内部語彙を
# そのまま出すと理論の骨格ではなく分類名の羅列に見えてしまうため日本語表示名を使う。
# stage コード自体は domain-neutral のまま変えない。
# ---------------------------------------------------------------------------

_STAGE_ORDER = (
    "theory_basis",
    "observation_model",
    "observable_construction",
    "equation_system",
    "elimination",
    "consistency_relation",
    "diagnostic_application",
)

# 日本語表示名の正本は core/element_vocab.py の THEORY_STAGE_LABELS（オーナー承認済みの
# 統一語彙 §9 Q2）。かつて本モジュールが独自表を持ち `equation_system` だけ「方程式系」に
# 分裂していた（他6キーは完全一致）ため、2026-08-14 に表ごと正本へ委譲した —
# 学習者に見える変化は「方程式系」→「式の体系」の1語のみ。
_STAGE_LABELS = element_vocab.THEORY_STAGE_LABELS


def _stage_label(stage: str) -> str:
    """stage コード → 学習者向け日本語表示ラベル。

    未知の stage は機械的な整形（``snake_case`` → 先頭大文字の空白区切り。正本
    ``theory_stage_label()``（agents 側）と同じ縮退規則）に落とす。日本語訳を持たない
    stage が増えてもコードそのものが読める形で残る（情報を落とさない）。
    """
    key = str(stage or "").strip()
    if not key:
        return ""
    return _STAGE_LABELS.get(key, key.replace("_", " ").capitalize())


# ThesisReconstructionAgent.SUPPORT_SECTIONS の日本語ラベル。
# 訳語の正本は core/label_vocab.py::SUPPORT_SECTION_LABELS（かつては
# positioning.py の private 定数を再掲していた）。語彙自体は
# agents/thesis_reconstruction/schema.py の SUPPORT_SECTIONS が正本。
_SUPPORT_SECTION_LABELS = SUPPORT_SECTION_LABELS

# TheoryOperationGraph の review_reasons 語彙（CLAUDE.md「TheoryOperationGraph」節が正本）
# の事実文化。未知の reason コードはコードそのものを表示する（情報を落とさない）。
#
# 文言は**学習者向けに平易化**する（discuss_opening_authoring_design.md §2 / §3）。
# `atomic claim` / `リンク` / `出典に裏付けられて` のような内部語彙をそのまま出すと、
# 「システムの解析状態」が「論文の弱点」に読み替えられてしまう。主語がシステム
# （解析）であることは `_backbone_fact_line` の前置きが担い、ここは何が取れていない
# かだけを平易に述べる。reason コードそのものは変えない（A層・graph 側の正本）。
_REVIEW_REASON_FACT_PHRASES = {
    "missing_atomic_claim": "根拠となる文をまだ特定できていません",
    "missing_evidence_link": "根拠となる箇所とのつながりを記録できていません",
    "missing_equation_link": "関係する式とのつながりを記録できていません",
    "missing_derivation_link": "導出の過程とのつながりを記録できていません",
    "equation_needs_math_review": "式の内容をまだ確認できていません",
    "edge_not_source_backed": "つながりの根拠を論文の中で確認できていません",
    "fallback_or_inferred_node": "解析が推定で補った箇所です",
    "source_span_missing": "元の文章のどこにあたるかを特定できていません",
}

# 「まだ確認できていないところ」の前置き。主語がシステム（解析）であることを文面で明示する
# （旧「レビュー待ちの箇所です」は、誰が何を待っているのかが学習者に分からなかった）。
_SYSTEM_UNCONFIRMED_PREFIX = "解析がこの箇所の裏付けをまだ取れていません"

# 数値を一切見せない（W8/DM6）。射影後も念のため再帰的に除去するキー。
_FORBIDDEN_NUMERIC_KEYS = ("confidence", "load_score", "score")


# ---------------------------------------------------------------------------
# 純粋ヘルパ（DB非依存・fake データで単体テスト可能）
# ---------------------------------------------------------------------------


def _dedupe_ids(raw_ids: Any) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in raw_ids or []:
        key = str(raw or "").strip()
        if key and key not in seen:
            seen.add(key)
            ordered.append(key)
    return ordered


def _claim_ref_item(claim_id: str, claim_label_index: dict[str, str]) -> dict[str, str]:
    """claim_id → {"id","label"}。解決できない id は id 文字列そのものを label にする
    （設計書: 情報を落とさない）。
    """
    label = str(claim_label_index.get(claim_id) or "").strip() or claim_id
    return {"id": claim_id, "label": label}


def _equation_label(record: dict[str, Any] | None) -> str:
    """equation record → 表示ラベル（``core/component_context.py::_equation_label`` /
    ``core/deliberation/context_lens.py::_equation_label`` と同型。equation は独立
    テーブルを持たないため、各モジュールがこの小さな整形をそれぞれ持つのが既存の慣行）。
    """
    if not isinstance(record, dict):
        return ""
    src = record.get("source_extraction") if isinstance(record.get("source_extraction"), dict) else {}
    rec = record.get("reconstruction") if isinstance(record.get("reconstruction"), dict) else {}
    text = (
        rec.get("plain_text") or rec.get("latex")
        or src.get("plain_text") or src.get("latex")
        or record.get("label") or record.get("equation_id") or ""
    )
    return str(text)[:80]


def _equation_ref_item(equation_id: str, equations_by_id: dict[str, dict]) -> dict[str, str]:
    record = equations_by_id.get(equation_id)
    label = _equation_label(record) if record else ""
    return {"id": equation_id, "label": label or equation_id}


def _skeleton_entry_text(skeleton: dict[str, Any] | None, key: str) -> str:
    """paper_skeleton artifact の1エントリ（``{"text","evidence_block_ids","reason",
    "confidence"}``）から本文だけを取り出す。素の文字列で来る形にも耐える。

    ``paper_goal`` は thesis_reconstruction artifact には無く（``ThesisLLMInput`` の
    入力側にしか現れない）、``paper_skeleton`` artifact が正本のため、開幕画面の
    「この論文が答えようとした問い」は2つの artifact を併読して組み立てる。
    """
    if not isinstance(skeleton, dict):
        return ""
    entry = skeleton.get(key)
    if isinstance(entry, dict):
        return str(entry.get("text") or "").strip()
    return str(entry or "").strip()


def project_thesis(
    thesis: dict[str, Any] | None,
    claim_label_index: dict[str, str],
    equations_by_id: dict[str, dict],
    skeleton: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """thesis_reconstruction artifact → 開幕画面の thesis DTO（設計書 §3.3 の契約）。

    artifact 自体が無ければ ``None``。central_thesis / support_structure の
    claim_ids・equation_ids をラベル解決込みで射影する。各リストは上限で切るが、
    切ったこと自体を示す独立フラグは持たない（documents[].truncated は backbone 専用。
    設計契約に無いフィールドを増やさない）。

    ``skeleton``（paper_skeleton artifact、任意）を渡すと ``paper_goal`` /
    ``central_question`` の縮退先として併読する。

    投影の是正（``discuss_opening_authoring_design.md`` §3）で、agent が**合成した文**を
    捨てずに出すようになったフィールド:

    - ``central_question`` / ``paper_goal`` — 問いから始める（export の
      バリデーションゲートは ``central_question`` 不在を error にしているのに、
      学習者向け画面では使われていなかった）。
    - ``central_thesis_text`` — ``central_thesis.text``。従来は claim_ids →
      claim の生ラベル（論文原文）だけを出していた。
    - ``support_sections[].entries[].text`` — ``SupportEntry.text``。
    - ``alternatives`` — ``alternative_theses`` の text のみ。出典を持たない artifact
      なので ``attribution_label`` を必ず添える（OA7: 選別せずラベルで区別する）。

    **言語は変換しない**（A層のプロンプトに言語指定が無いため英語で保存されている
    ことがある）。Phase 0 で直すのは構成と主語だけで、和訳・要約はしない（DM8）。
    """
    if not isinstance(thesis, dict):
        return None

    central = thesis.get("central_thesis")
    central = central if isinstance(central, dict) else {}
    central_claims = [
        _claim_ref_item(cid, claim_label_index)
        for cid in _dedupe_ids(central.get("claim_ids"))[:_MAX_CENTRAL_ITEMS]
    ]
    central_equations = [
        _equation_ref_item(eid, equations_by_id)
        for eid in _dedupe_ids(central.get("equation_ids"))[:_MAX_CENTRAL_ITEMS]
    ]

    support_structure = thesis.get("support_structure")
    support_structure = support_structure if isinstance(support_structure, dict) else {}
    support_sections: list[dict[str, Any]] = []
    for section_key, entries in support_structure.items():
        if not isinstance(entries, list):
            continue
        items: list[dict[str, str]] = []
        # entries[] は agent が合成した1文（SupportEntry.text）とその参照の組。
        # 従来の flat な items[] は残したまま（既存フロントのチップ表示が使う）、
        # 合成文を落とさないための投影を並置する。
        projected_entries: list[dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry_text = str(entry.get("text") or "").strip()
            entry_items: list[dict[str, str]] = []
            for cid in _dedupe_ids(entry.get("claim_ids")):
                if len(entry_items) >= _MAX_SUPPORT_ITEMS_PER_SECTION:
                    break
                entry_items.append({"type": "claim", **_claim_ref_item(cid, claim_label_index)})
            for eid in _dedupe_ids(entry.get("equation_ids")):
                if len(entry_items) >= _MAX_SUPPORT_ITEMS_PER_SECTION:
                    break
                entry_items.append({"type": "equation", **_equation_ref_item(eid, equations_by_id)})
            if (entry_text or entry_items) and len(projected_entries) < _MAX_SUPPORT_ENTRIES_PER_SECTION:
                projected_entries.append({"text": entry_text, "items": entry_items})
            for item in entry_items:
                if len(items) >= _MAX_SUPPORT_ITEMS_PER_SECTION:
                    break
                items.append(item)
        if not items and not projected_entries:
            continue
        support_sections.append(
            {
                "key": str(section_key),
                "label": _SUPPORT_SECTION_LABELS.get(str(section_key), str(section_key)),
                "items": items,
                "entries": projected_entries,
            }
        )

    alternatives: list[dict[str, str]] = []
    for alt in thesis.get("alternative_theses") or []:
        if len(alternatives) >= _MAX_ALTERNATIVES:
            break
        text = str(alt.get("text") or "").strip() if isinstance(alt, dict) else str(alt or "").strip()
        if not text:
            continue
        alternatives.append({"text": text, "attribution_label": _ALTERNATIVE_ATTRIBUTION_LABEL})

    return {
        # 「この論文が答えようとした問い」（主語=論文）。thesis artifact の
        # central_question を優先し、無ければ paper_skeleton の同名エントリへ縮退する。
        "central_question": (
            str(thesis.get("central_question") or "").strip()
            or _skeleton_entry_text(skeleton, "central_question")
        ),
        "paper_goal": _skeleton_entry_text(skeleton, "paper_goal"),
        # 「この論文の主張」（主語=論文）。agent が合成した命題文。無ければ
        # headline_claim（同じく合成値）へ縮退する。claim の生ラベルは
        # central_claims 側に従来どおり残す（置き換えない）。
        "central_thesis_text": (
            str(central.get("text") or "").strip() or str(thesis.get("headline_claim") or "").strip()
        ),
        "central_claims": central_claims,
        "central_equations": central_equations,
        "support_sections": support_sections,
        # 「別の見方（AI の提示）」。出典を持たないため attribution_label 付き（OA7）。
        "alternatives": alternatives,
    }


def _thesis_is_empty(thesis: dict[str, Any] | None) -> bool:
    """投影された thesis DTO が空か（``available`` 判定の下請け）。

    投影の是正で追加した「問い」「合成命題文」「別の見方」も中身として数える
    （claim リンクが1つも無くても、問いと命題文があれば開幕画面は成立する）。
    """
    if not thesis:
        return True
    return not (
        thesis.get("central_claims")
        or thesis.get("central_equations")
        or thesis.get("support_sections")
        or thesis.get("central_question")
        or thesis.get("paper_goal")
        or thesis.get("central_thesis_text")
        or thesis.get("alternatives")
    )


def _seed_tiebreak_key(row: dict[str, Any]) -> tuple[str, str]:
    """``created_at`` が同値の承認済み素材どうしの決定論的な並び（body → id）。

    ``list_for_document`` の ``ORDER BY created_at DESC`` だけでは、同一トランザクションで
    投入された兄弟行（1 document に 2〜3 件）の ``created_at`` が PostgreSQL の ``now()``
    により**同値**になるため順序が決まらない。表示順が読み込みごとに揺れないよう、
    ここで全順序に固定する。
    """
    return (str(row.get("body") or ""), str(row.get("id") or ""))


def _sort_seed_rows(rows: list[dict[str, Any]]) -> None:
    """新しい承認済み素材が先（``created_at`` の降順、同値なら body → id の昇順）。

    降順にするのは表示上限（:data:`_MAX_DISCUSSION_SEEDS`）との組み合わせのため —
    昇順で切ると、再解析後に新しく承認した素材が古い承認済み素材に押し出されて
    永久に画面へ出なくなる。Python の sort は安定なので2段で書く。
    """
    rows.sort(key=_seed_tiebreak_key)
    rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)


def project_discussion_seeds(
    rows: list[dict[str, Any]] | None,
    *,
    limit: int = _MAX_DISCUSSION_SEEDS,
) -> list[dict[str, Any]]:
    """``element_explanations`` 行 → 開幕画面「議論のきっかけ」DTO（設計書 §2 / §7）。

    この区画だけは投影ではなく、解析パイプラインが生成し**教員が承認した**素材を配信する。
    純粋関数（fake dict で単体テスト可）で、DB 読み出しは
    :func:`_load_approved_discussion_seeds` 側に閉じる。

    - **OA2**: ``status='approved'`` かつ ``role='discussion_seed'`` かつ
      ``element_type='document'`` の行だけを通す。読み出し SQL 側でも同じ条件で絞るが、
      candidate / dismissed / superseded を学習者に出さない保証を SQL の 1 条件だけに
      依存させない（二重の関門）。
    - **OA6**: ``evidence`` から取るのは ``evidence_quote`` のみ。``confidence`` /
      ``reason`` 等は射影しない（``_strip_numeric_keys`` は最後の安全網であって
      1次防壁にしない）。``evidence_quote`` は「論文まで辿れる」ことの明示なので出す（DM1）。
    - 各件に ``authored`` / ``authored_by_label`` を添える（設計書 §7 の出所表示）。
      本文が空の行は出さない（表示できる中身が無いので）。
    """
    usable = [
        row
        for row in (rows or [])
        if isinstance(row, dict)
        and str(row.get("status") or "") == STATUS_APPROVED
        and str(row.get("role") or "") == ROLE_DISCUSSION_SEED
        and str(row.get("element_type") or "") == ELEMENT_TYPE_DOCUMENT
        and str(row.get("body") or "").strip()
    ]
    _sort_seed_rows(usable)

    projected: list[dict[str, Any]] = []
    for row in usable[: max(0, int(limit))]:
        evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
        projected.append(
            {
                "body": str(row.get("body") or "").strip(),
                "evidence_quote": str(evidence.get("evidence_quote") or "").strip(),
                "authored": True,
                "authored_by_label": _AUTHORED_BY_LABEL,
            }
        )
    return projected


def project_backbone(
    graph_nodes: list[dict[str, Any]] | None, *, limit: int = _MAX_BACKBONE_NODES
) -> tuple[list[dict[str, Any]], bool]:
    """TheoryOperationGraph の main 層ノード → 開幕画面のバックボーン投影。

    ``graph_layer == "main"`` のみを対象にし（式単位の equation_detail / debug 層は
    集約ラベルを持たないため対象外。CLAUDE.md「TheoryOperationGraph」節）、
    theory stage 順（``_STAGE_ORDER``。無ければ末尾）→ node_id の安定ソートで並べ、
    上限で切って ``truncated`` を正直に返す（先例:
    ``core/atlas_state.py::build_derivation_chain`` / ``core/deliberation/decomposition.py::
    _graph_node_for_component`` の id キー探索を踏襲し、``component_id`` / ``id`` /
    ``node_id`` のいずれの表記も受理する）。
    """
    main_nodes: list[dict[str, Any]] = []
    for node in graph_nodes or []:
        if not isinstance(node, dict):
            continue
        layer = str(node.get("graph_layer") or "main")
        if layer != "main":
            continue
        node_id = str(node.get("component_id") or node.get("id") or node.get("node_id") or "").strip()
        if not node_id:
            continue
        main_nodes.append(node)

    def _node_id(node: dict[str, Any]) -> str:
        return str(node.get("component_id") or node.get("id") or node.get("node_id") or "")

    def _sort_key(node: dict[str, Any]) -> tuple[int, str]:
        stage = str(node.get("stage") or "")
        idx = _STAGE_ORDER.index(stage) if stage in _STAGE_ORDER else len(_STAGE_ORDER)
        return (idx, _node_id(node))

    main_nodes.sort(key=_sort_key)
    truncated = len(main_nodes) > limit

    projected: list[dict[str, Any]] = []
    for node in main_nodes[:limit]:
        stage = str(node.get("stage") or "")
        node_id = _node_id(node)
        review_reasons = [str(r) for r in (node.get("review_reasons") or []) if str(r or "").strip()]
        projected.append(
            {
                "node_id": node_id,
                "label": str(node.get("label") or "").strip() or _stage_label(stage) or node_id,
                "stage": stage,
                "stage_label": _stage_label(stage),
                "description": str(node.get("description") or ""),
                "review_status": str(node.get("review_status") or ""),
                "source_backing_status": str(node.get("source_backing_status") or ""),
                "review_reasons": review_reasons,
            }
        )
    return projected, truncated


def _is_fragile_backbone_node(node: dict[str, Any]) -> bool:
    """backbone ノードのうち「最も脆い一手」候補か（review_required 系）。

    CLAUDE.md: review_status は source_backing_status から導出されるが、念のため
    両方を見て判定する（一方が欠けていても他方で拾う。P4）。
    """
    if str(node.get("review_status") or "") == "review_required":
        return True
    return str(node.get("source_backing_status") or "") in ("inferred", "review_required")


def _backbone_fact_line(node: dict[str, Any]) -> str:
    """review_required なバックボーンノードの事実文（煽らない・断定しない）。

    **主語はシステム（解析）**。これは論文の弱点ではなく、解析が裏付けを取れていない
    箇所である（discuss_opening_authoring_design.md §0 欠陥1）。
    """
    reasons = node.get("review_reasons") or []
    phrases = [_REVIEW_REASON_FACT_PHRASES.get(str(r), str(r)) for r in reasons if str(r or "").strip()]
    if phrases:
        return _SYSTEM_UNCONFIRMED_PREFIX + ": " + "、".join(phrases)
    return _SYSTEM_UNCONFIRMED_PREFIX + "。"


def _join_fact_sentences(base: str, addition: str) -> str:
    """事実文を「。」区切りで連結する（末尾の句点を重複させない）。"""
    base = (base or "").rstrip()
    if not base:
        return addition
    if not base.endswith("。"):
        base += "。"
    return base + addition


def _assumption_fact_line(item: dict[str, Any]) -> str:
    """未検証合意リスト項目（``compile_open_assumptions`` の1件）の事実文。

    **主語は論文**（この論文が確かめていないこと）。``routes/doubt.py::_learner_fact_line``
    と同じ「検証済みも未記帳も同じ精度で併記する」思想（§8-1/8-2）を、開幕画面向けの
    短い一文に凝縮したもの。内部語彙（記帳・スコープ）は学習者向けに平易化する。

    SL-1（賭け金の台帳, §7）: ``compile_open_assumptions`` が付与する
    ``has_falsification_condition`` / ``falsification_not_formulable`` キーが**存在する**
    ときだけ、覆る条件の記帳状況を後段に「。」区切りで連結する（設計書 §7 の3文言を
    逐語で使う）。これらのキーを持たない旧形状の item（SL 結線前の単体テスト等）は
    従来どおりの文だけを返す（後方互換・情報を落とさない）。
    """
    if bool(item.get("scope_count_is_zero")):
        base = "どの範囲で確かめたかが記録されていません。"
    else:
        status = str(item.get("verification_status") or "unknown")
        if status in ("untested", "unknown"):
            base = "検証の記録がない前提です。"
        elif status == "refuted":
            base = "反証の記録がある前提です。"
        else:
            base = "検証状況の記録がある前提です。"

    if "has_falsification_condition" not in item and "falsification_not_formulable" not in item:
        return base

    if bool(item.get("has_falsification_condition")):
        return _join_fact_sentences(base, "何が起これば覆るかが記帳されている前提です。")
    if bool(item.get("falsification_not_formulable")):
        return _join_fact_sentences(base, "反証条件を定式化できないと記帳されている前提です。")
    return _join_fact_sentences(base, "覆る条件はまだ定式化されていません。")


def project_fragile_points(
    assumption_items: list[dict[str, Any]],
    backbone_by_document: dict[str, list[dict[str, Any]]] | Iterable[tuple[str, list[dict[str, Any]]]],
    *,
    limit: int = _MAX_FRAGILE_POINTS,
) -> tuple[list[dict[str, Any]], bool]:
    """脆い箇所の投影 = 未検証合意（D層台帳の投影）+ backbone の review_required ノード。

    **2種類は主語が違う**ので、``kind`` に加えて ``subject`` を明示して返す
    （``assumption`` → ``paper``: この論文が確かめていないこと /
    ``backbone_node`` → ``system``: 解析がまだ裏付けを取れていないところ）。
    フロントはこの区別で見出しを分け、1つの区画に積まない
    （discuss_opening_authoring_design.md §2 / §3）。

    順序は「台帳の投影（開幕画面が最初に見せるべき、教材横断の一次情報）→ document 順の
    backbone ノード」の決定論的な並びに固定する（賞レース化しない=順位づけの演出はしない。
    D3-2 の「並び順は負荷段階→依存数」の精神を踏襲し、ここでは情報源の種類で1段階だけ分ける）。
    同一対象が両方の経路から出ても id 体系が異なる（台帳 target_id と backbone node_id）ため
    unify はせず両方保持する（情報を落とさない, P4）。

    上限（``limit``）は **kind ごとに枠を確保して**切る（[D-6]）: 一方の kind だけで
    上限に達しても他方が丸ごと消えないようにし、両方に候補があるときは両方から必ず
    1件以上残す。余った枠は他方へ融通する（台帳の投影を優先）。``truncated`` の意味は
    従来どおり「候補が上限を超えて切られた」。
    """
    assumption_points: list[dict[str, Any]] = []
    for item in assumption_items or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("statement") or item.get("target_id") or "").strip()
        if not label:
            continue
        assumption_points.append(
            {
                "kind": "assumption",
                "subject": FRAGILE_SUBJECT_PAPER,
                "label": label,
                "fact_line": _assumption_fact_line(item),
                "document_id": None,
            }
        )

    items_view = (
        backbone_by_document.items() if isinstance(backbone_by_document, dict) else backbone_by_document
    )
    backbone_points: list[dict[str, Any]] = []
    for document_id, nodes in items_view or []:
        for node in nodes or []:
            if not isinstance(node, dict) or not _is_fragile_backbone_node(node):
                continue
            label = str(node.get("label") or node.get("node_id") or "").strip()
            if not label:
                continue
            backbone_points.append(
                {
                    "kind": "backbone_node",
                    "subject": FRAGILE_SUBJECT_SYSTEM,
                    "label": label,
                    "fact_line": _backbone_fact_line(node),
                    "document_id": str(document_id),
                }
            )

    limit = max(0, int(limit))
    take_assumption, take_backbone = _allocate_fragile_quota(
        len(assumption_points), len(backbone_points), limit
    )
    points = assumption_points[:take_assumption] + backbone_points[:take_backbone]
    truncated = (len(assumption_points) + len(backbone_points)) > len(points)
    return points, truncated


def _allocate_fragile_quota(
    assumption_count: int, backbone_count: int, limit: int
) -> tuple[int, int]:
    """[D-6] kind 別の枠配分（決定論的）。

    backbone に ``_FRAGILE_BACKBONE_QUOTA``（既定3、``limit`` が小さいときは半分まで）、
    残りを assumption に割り当て、使われなかった枠は他方へ融通する（assumption 優先）。
    両方に候補があり ``limit >= 2`` なら両方から必ず1件以上取る。
    """
    if limit <= 0:
        return 0, 0
    backbone_quota = min(_FRAGILE_BACKBONE_QUOTA, max(0, limit // 2))
    assumption_quota = limit - backbone_quota
    take_assumption = min(assumption_count, assumption_quota)
    take_backbone = min(backbone_count, backbone_quota)
    spare = limit - take_assumption - take_backbone
    if spare > 0:
        extra = min(assumption_count - take_assumption, spare)
        take_assumption += extra
        spare -= extra
    if spare > 0:
        take_backbone += min(backbone_count - take_backbone, spare)
    return take_assumption, take_backbone


def _is_available(
    documents: list[dict[str, Any]],
    fragile_points: list[dict[str, Any]],
    course_focus: str = "",
) -> bool:
    """開幕画面を出せるか。

    ``course_focus``（教員が入力した「このコースで議論したいこと」、Phase 0b）が
    あるだけでも画面は成立する — 教員が書いたものを A層成果の有無で黙って落とさない
    （投影が空だからといって教員の入力を捨てない）。
    """
    if str(course_focus or "").strip():
        return True
    for doc in documents:
        if not _thesis_is_empty(doc.get("thesis")):
            return True
        if doc.get("backbone"):
            return True
    return bool(fragile_points)


def _strip_numeric_keys(value: Any) -> Any:
    """レスポンスを再帰走査して confidence/load_score/score 系キーを除去する
    （W8/DM6 相当。``core/component_context.py::_strip_confidence`` と同型の安全網。
    射影関数はそもそもホワイトリストで組み立てているため通常は何も落とさない）。
    """
    if isinstance(value, dict):
        return {
            k: _strip_numeric_keys(v) for k, v in value.items() if k not in _FORBIDDEN_NUMERIC_KEYS
        }
    if isinstance(value, list):
        return [_strip_numeric_keys(v) for v in value]
    return value


# ---------------------------------------------------------------------------
# DB 読み出し部（都度セッションを開いて閉じる。core/component_context.py と同じ流儀）
# ---------------------------------------------------------------------------


def _document_titles(document_ids: list[str]) -> dict[str, str]:
    """documents.title（無ければ source_path）を id → title でまとめて解決する
    （N+1 回避。先例: core/personal_graph/queries.py::fetch_document_titles と同じ
    プレースホルダ方式 — ``id = ANY(:ids)`` は uuid 列と text 配列の比較で型不一致に
    なりうるため使わない）。
    """
    ids = sorted({str(d) for d in document_ids if str(d or "").strip()})
    if not ids:
        return {}
    from sqlalchemy import text as sa_text

    from core.postgres import get_session

    session = get_session()
    try:
        placeholders = ", ".join(f"CAST(:id_{i} AS uuid)" for i in range(len(ids)))
        rows = session.execute(
            sa_text(f"SELECT id::text AS id, title, source_path FROM documents WHERE id IN ({placeholders})"),
            {f"id_{i}": doc_id for i, doc_id in enumerate(ids)},
        ).mappings().fetchall()
    except Exception:  # noqa: BLE001
        logger.warning("discuss opening: document title lookup failed", exc_info=True)
        return {}
    finally:
        session.close()
    return {str(r["id"]): str(r.get("title") or r.get("source_path") or "") for r in rows}


def _load_graph_nodes(document_id: str) -> list[dict[str, Any]]:
    """document の最新 theory_component_graphs 行から graph_json.nodes[] を返す
    （best-effort。先例: core/component_context.py::_load_graph_narrative /
    core/deliberation/decomposition.py::_graph_node_for_component）。
    """
    from sqlalchemy import text as sa_text

    from core.postgres import get_session

    session = get_session()
    try:
        row = session.execute(
            sa_text(
                """
                SELECT graph_json FROM theory_component_graphs
                WHERE document_id = :doc ORDER BY updated_at DESC LIMIT 1
                """
            ),
            {"doc": document_id},
        ).fetchone()
    except Exception:  # noqa: BLE001
        logger.warning("discuss opening: graph load failed for %s", document_id, exc_info=True)
        return []
    finally:
        session.close()
    graph = row[0] if row and isinstance(row[0], dict) else {}
    nodes = graph.get("nodes") if isinstance(graph, dict) else None
    return [n for n in nodes if isinstance(n, dict)] if isinstance(nodes, list) else []


def _claim_label_index(document_id: str, artifacts: dict[str, Any]) -> dict[str, str]:
    """claim_id（DB UUID / agent 側 legacy id・span id の両方）→ 本文ラベルの索引。

    ``core/deliberation/context_lens.py`` の2つの下請け（``_claim_id_lookup_from_rows``
    が theory_claims.source_scope.legacy_ids / span_id から id 変換表を作る規約、
    ``_artifact_claim_text_index`` が claim_object_builder artifact の text で
    agent 側 sub-claim id を補う規約）を、ここでは「id → 本文」まで一段で持つ索引に
    合成している（本モジュール専用の投影のため、ラベルまで持たない汎用の id 変換表
    より扱いやすい）。theory_claims に無い sub-claim id は artifact 側の text/
    normalized_text で補い、どちらにも無ければ呼び出し側が id 文字列をそのまま表示する。
    """
    index: dict[str, str] = {}

    from sqlalchemy import text as sa_text

    from core.postgres import get_session

    session = get_session()
    try:
        rows = session.execute(
            sa_text(
                "SELECT id::text AS id, text, normalized_text, source_scope "
                "FROM theory_claims WHERE document_id = :doc"
            ),
            {"doc": document_id},
        ).mappings().fetchall()
    except Exception:  # noqa: BLE001
        logger.warning("discuss opening: theory_claims label lookup failed for %s", document_id, exc_info=True)
        rows = []
    finally:
        session.close()

    for row in rows:
        db_id = str(row["id"])
        label = str(row.get("text") or "").strip() or str(row.get("normalized_text") or "").strip()
        if not label:
            continue
        index.setdefault(db_id, label)
        scope = row.get("source_scope") if isinstance(row.get("source_scope"), dict) else {}
        for legacy_id in scope.get("legacy_ids") or []:
            key = str(legacy_id or "").strip()
            if key:
                index.setdefault(key, label)
        span_id = scope.get("span_id")
        if span_id:
            index.setdefault(str(span_id), label)

    claim_builder = artifacts.get("claim_object_builder") if isinstance(artifacts, dict) else None
    claims = claim_builder.get("claims") if isinstance(claim_builder, dict) else None
    for c in claims or []:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("claim_id") or "").strip()
        if not cid or cid in index:
            continue
        label = str(c.get("text") or "").strip() or str(c.get("normalized_text") or "").strip()
        if label:
            index[cid] = label
    return index


def _equations_index_for(
    document_id: str, artifacts: dict[str, Any] | None = None
) -> dict[str, dict[str, Any]]:
    """document の equation_id → record 索引。

    ``artifacts`` を渡すと ``equation_records`` に素通しし、呼び出し側が既に取得済みの
    ``document_run_artifacts`` を再利用する（同一 document で ``document_analysis_runs``
    を2回 SELECT しない。省略時は従来どおり自前で取得する）。
    """
    return {
        str(r.get("equation_id")): r
        for r in equation_records(document_id, artifacts=artifacts)
        if isinstance(r, dict) and r.get("equation_id")
    }


def _load_approved_discussion_seeds(document_id: str) -> list[dict[str, Any]]:
    """document の**承認済み**「議論のきっかけ」行を読む（設計書 §7）。

    ``element_explanations``（migration 062: ``element_type='document'`` /
    ``role='discussion_seed'``、``element_id`` は ``document_id`` と同値）の
    ``status='approved'`` 行だけを引く。``kind`` では絞らない — 素材の役割は ``role``
    が担っており、``kind`` の語彙が将来増えても配信が黙って止まらないようにする。

    未承認しか無い document・そもそも生成されていない document では空リストになり、
    呼び出し側は投影のまま（Phase 0 の画面）に縮退する（OA4: 承認されるまで画面が
    出ない設計にしない）。読み出し失敗も空リストで返す fail-soft（1 document の
    読み出し失敗で画面全体を壊さない。``_load_graph_nodes`` 等と同じ流儀）。
    """
    from core.postgres import get_session

    session = None
    try:
        session = get_session()
        return list_for_document(
            session,
            document_id,
            element_type=ELEMENT_TYPE_DOCUMENT,
            status=STATUS_APPROVED,
            role=ROLE_DISCUSSION_SEED,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "discuss opening: approved discussion seed read failed for %s",
            document_id, exc_info=True,
        )
        return []
    finally:
        if session is not None:
            session.close()


def _compile_open_assumptions(course_id: str) -> list[dict[str, Any]]:
    """D層台帳の未検証合意リスト（``core.doubt.open_assumptions.compile_open_assumptions``）
    を学習者向け設定（``include_challenger_names=False``。既存の学習者向け
    ``GET /api/learning/courses/{course_id}/open-assumptions`` と同じ呼び出し方）で読む。
    台帳未記帳コースでは空リスト（fail-closed。エラーにしない）。
    """
    from core.postgres import get_session

    from core.doubt.open_assumptions import compile_open_assumptions

    session = get_session()
    try:
        return compile_open_assumptions(session, course_id, include_challenger_names=False)
    except Exception:  # noqa: BLE001
        logger.warning("discuss opening: open assumptions read failed for course %s", course_id, exc_info=True)
        return []
    finally:
        session.close()


# ---------------------------------------------------------------------------
# 公開インターフェース
# ---------------------------------------------------------------------------


def build_opening(
    course_id: str,
    document_ids: Iterable[str],
    course_focus: str = "",
) -> dict[str, Any]:
    """discuss モード開幕画面の DTO を組み立てる（設計書 §3.3 / §6.3）。

    呼び出し側（``routes/learning.py``）が受講ゲート（``get_course_data``）を通した後、
    ``services.list_course_source_document_ids(course_data)`` の結果を ``document_ids``
    として渡す。document 単位の読み出し失敗は fail-soft に握り、その文書だけ
    thesis=None / backbone=[] に縮退させて画面全体は返す。

    ``documents[].discussion_seeds``（設計書 §7）は**教員が承認した**素材だけを載せる。
    承認済みが1件も無い document ではキー自体を付けず、投影のまま返す（OA4）。
    ``available`` の判定はこの素材の有無で変えない（設計書 §7: 開幕画面を出せるかは
    従来どおり投影と教員入力だけで決める。承認待ちのあいだ画面が消えたり、
    承認によって突然出現したりしない）。

    ``course_focus``（Phase 0b、``discuss_opening_authoring_design.md`` §2 最下段
    「このコースで議論したいこと」）は教員の任意入力で、AI 生成は一切関与しない。
    呼び出し側が ``core.course_data.course_focus(course_data)`` で読んで渡す
    （本モジュールは course_data の構造を知らない）。未入力なら空文字のまま返し、
    フロントは区画ごと非表示にする。
    """
    ids = sorted({str(d) for d in document_ids if str(d or "").strip()})
    titles = _document_titles(ids)

    documents: list[dict[str, Any]] = []
    backbone_by_document: dict[str, list[dict[str, Any]]] = {}

    for document_id in ids:
        try:
            artifacts = document_run_artifacts(document_id)
            artifacts = artifacts if isinstance(artifacts, dict) else {}
        except Exception:  # noqa: BLE001
            logger.warning("discuss opening: artifact read failed for %s", document_id, exc_info=True)
            artifacts = {}

        thesis_artifact = artifacts.get("thesis_reconstruction")
        # paper_goal / central_question の縮退先（paper_goal は thesis artifact に
        # 存在しない — paper_skeleton artifact が正本）。同じ artifacts dict から読むので
        # document_analysis_runs の追加 SELECT は発生しない。
        skeleton_artifact = artifacts.get("paper_skeleton")
        skeleton_artifact = skeleton_artifact if isinstance(skeleton_artifact, dict) else None

        try:
            equations_by_id = _equations_index_for(document_id, artifacts)
        except Exception:  # noqa: BLE001
            logger.warning("discuss opening: equation index failed for %s", document_id, exc_info=True)
            equations_by_id = {}

        claim_label_index = _claim_label_index(document_id, artifacts)
        thesis_dto = (
            project_thesis(thesis_artifact, claim_label_index, equations_by_id, skeleton_artifact)
            if isinstance(thesis_artifact, dict)
            else None
        )

        graph_nodes = _load_graph_nodes(document_id)
        backbone_nodes, backbone_truncated = project_backbone(graph_nodes)
        backbone_by_document[document_id] = backbone_nodes

        document_dto: dict[str, Any] = {
            "document_id": document_id,
            "title": titles.get(document_id, ""),
            "thesis": thesis_dto,
            "backbone": backbone_nodes,
            "truncated": backbone_truncated,
        }

        # 承認済み素材の配信（設計書 §7）。role 単位の**部分適用**なので、承認済みが
        # 1件も無い document は投影のまま（＝Phase 0 の DTO と完全一致）にする —
        # 空配列のキーすら足さない（劣化しない, OA4）。
        seeds = project_discussion_seeds(_load_approved_discussion_seeds(document_id))
        if seeds:
            document_dto["discussion_seeds"] = seeds

        documents.append(document_dto)

    assumption_items = _compile_open_assumptions(course_id)
    fragile_points, fragile_truncated = project_fragile_points(assumption_items, backbone_by_document)

    focus = str(course_focus or "").strip()
    result = {
        "course_id": course_id,
        "available": _is_available(documents, fragile_points, focus),
        # 教員の任意入力（主語=教員）。未入力なら空文字（フロントは区画ごと非表示）。
        "course_focus": focus,
        "documents": documents,
        "fragile_points": fragile_points,
        "truncated": fragile_truncated,
    }
    return _strip_numeric_keys(result)
