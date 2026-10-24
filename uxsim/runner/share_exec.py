"""第 25 周「共有・転用の通し」（face=share）の行為の実行と画面の投影。

行為の定義は ``uxsim/actions/share_actions.py``。画面の正本:
- ナレッジライブラリタブ: ``frontend/public/js/admin.js`` の ``renderLibraryIdentityCandidates`` /
  ``_libraryDecideIdentityLink`` / ``decideLibraryEntryReview`` / ``renderLibraryEntriesList`` /
  ``renderLibraryDetail`` + ``renderLibraryRegistrySections`` / ``loadLibraryEntryLabels`` /
  ``loadLibraryEntryRelations`` / ``loadLibraryEntryAtlasLinks`` / ``deriveLibraryAtlasLinks`` /
  ``_libraryPromptDismissReason``
- 深く検討の同一性リンク: ``frontend/public/js/deliberation.js`` の ``_identityLinksSectionHtml`` /
  ``_searchSharedPartCandidates`` / ``_renderSharedPartCandidates`` / ``_createIdentityLink`` /
  ``_identityLinkRowHtml`` / ``_decideIdentityLink``
- 原稿スタジオ「説明・承認の共有」: ``frontend/public/js/admin-lecture-studio.js`` の
  ``lsOpenEndorsementModal`` / ``lsReloadExplanations`` / ``lsRenderExplanationCard`` / ``lsAddExplanation`` /
  ``lsBindExplanationActions`` / ``lsOpenCiteSelector`` / ``lsCallExplanation``
- 教材の共有: ``frontend/public/js/admin.js`` の ``openDocumentShareModal`` / ``renderDocShareCurrent`` /
  ``renderDocShareDropdown`` / ``addDocumentShare``
- 学生の部品チップ: ``frontend/public/js/app.js`` の ``renderComponentContextPanel`` /
  ``renderContextSharedPane`` / ``showComponentExplanations``

投影は**画面と同じ欄を同じ形で**（IK-0634 / IK-0628）。成功した応答は ``session.last_body = {"_ui_fact": …}`` に
置き換え（``state.project_observation`` が最初に読む）、審判は ``HttpTrace.response_excerpt``（生の応答）を読む。
画面が数・内部 ID を出しているところは投影でも隠さない（隠すと審判から欠陥が見えなくなる — §7.3）。

このモジュールは ``actions_exec`` を import しない（``_Ctx`` の形だけを使う。dispatch は指揮者が足す）。
画面が覚えるもの（``PersonaSession.scratch``）: ``share_identity``（同一性の候補）/ ``share_entries``（エントリ一覧）/
``share_entry``（開いたエントリ）/ ``share_relations`` / ``share_atlas_links`` / ``share_element``（深く検討で開いた要素）/
``share_part_hits``（共通部品の検索結果）/ ``share_element_links`` / ``share_components`` / ``share_component`` /
``share_explanations`` / ``share_document``（共有を開いた教材）/ ``share_groups``。
"""
from __future__ import annotations

import re
from typing import Any, Callable, Optional

from uxsim.runner.state import PersonaSession

# ---------------------------------------------------------------------------
# 画面の文言の逐語ミラー（JS が正本。変えたら JS を読んで写し直す）
# ---------------------------------------------------------------------------
# admin.js renderLibraryIdentityCandidates
IDENTITY_EMPTY = "いま確認をお待ちしている同一性の候補はありません。"
IDENTITY_SUPPORT_HEAD = "この候補を支持する論文: "
IDENTITY_HIDDEN_NOTE = "閲覧できない論文由来の候補が別にあります。"
# admin.js _libraryPromptDismissReason
DISMISS_REASON_PROMPT = "「{what}」を見送る理由を入力してください（記録に残ります）"
DISMISS_REASON_REQUIRED = "見送りには理由が必要です。"
# admin.js decideLibraryEntryReview / _libraryDecide* の失敗時 alert の既定
UPDATE_FAIL = "更新に失敗しました"
ADD_FAIL = "追加に失敗しました"
# admin.js deriveLibraryAtlasLinks
DERIVE_NEED_DOMAIN = "先に左の一覧から分野を選んでください。導出は分野ごとに行います。"
DERIVE_DOMAIN_FALLBACK = "選んだ分野"
DERIVE_CONFIRM = ("分野「{domain}」の現在の凍結骨格と、この分野のライブラリエントリを突き合わせて、対応の候補を作ります。"
                  "作られるのは候補だけで、分野の地図（骨格）は書き換わりません。確定は候補ごとの操作で行います。")
DERIVE_FAIL = "導出に失敗しました"
# admin.js renderLibraryEntriesList
ENTRIES_EMPTY = "エントリがありません"
# admin.js renderLibraryRegistrySections の注記
LABELS_NOTE = "別名は検索にも表示にも使われます。隠しラベルは表記ゆれ・OCR のノイズを捨てずに検索だけで拾うための器です。"
RELATIONS_NOTE = "他の概念との関係を記録します。関係はリンクであって統合ではありません（どちらの行も残ります）。"
ATLAS_NOTE = "分野の地図（骨格）のどのノードに当たるかの対応です。対応を確定しても骨格は書き換わりません。"
REVIEW_NOTE = "候補のままのエントリは凍結できず、解析パイプラインや学習者には届きません。"
LABELS_EMPTY = "別名・隠しラベルはまだありません。"
RELATIONS_EMPTY = "関係はまだ記録されていません。"
ATLAS_EMPTY = "この概念に対応づけられたノードはまだありません。"
ATLAS_NOT_CURRENT = "このノードは現在の版の地図にはありません"
# admin.js renderLibraryRegistrySections（第 3 波・IK-0920 / IK-0927）
BOUND_PARTS_HEAD = "結びついた教材の部品"
BOUND_PARTS_NOTE = ("教材の部品のうち、この共通部品と同じものとして結び付けられたもの。"
                    "確定した結び付きは学生の「共通部品として」に出ます。")
BOUND_PARTS_EMPTY = "この共通部品と結び付けられた教材の部品は、まだありません。"
BOUND_PARTS_NO_NAME = "（部品名を引けませんでした）"
BOUND_PARTS_HIDDEN = "あなたが閲覧できない論文に由来する結び付きもあります（中身は表示しません）。"
BOUND_PARTS_STATUS = {"confirmed": "確定", "candidate": "候補", "rejected": "却下"}
ATLAS_MANUAL_PLACEHOLDER = "地図の概念を名前で検索..."
ATLAS_MANUAL_SELECT_EMPTY = "（先に検索してください）"
ATLAS_MANUAL_NONE = "（該当なし）"
ATLAS_MANUAL_NEED_NODE = "先に地図の概念を検索して選んでください。"
ATLAS_MANUAL_ADDED = "対応の候補を付けました。一覧の［確定］で確定します。"
ATLAS_MANUAL_ADD_FAIL = "対応を付けられませんでした"
ATLAS_MANUAL_SEARCH_FAIL = "検索に失敗しました"
RELATION_OBJECT_PLACEHOLDER = "相手の名前・別名で検索..."  # IK-0866 是正後（旧: 相手のエントリ ID...）
RELATION_SELECT_EMPTY = "（先に検索してください）"
RELATION_SEARCH_NONE = "（該当なし）"
RELATION_SEARCH_FAIL = "検索できませんでした。"
ALIASES_MOVED_NOTE = "別名は下の「別名・隠しラベル」区画で足し・外します。"
LIBRARY_REGISTERED = "ライブラリに登録しました"
# admin.js の訳語表（backend/core/library/schema.py の逐語ミラー）
ENTRY_TYPE_LABELS = {
    "apparatus": "装置", "theory_component": "理論コンポーネント", "concept": "概念", "theory": "理論", "method": "方法",
    "observable": "観測量", "assumption": "前提", "quantity": "量", "process": "過程", "instrument_model": "装置モデル",
    "noise_source": "ノイズ源", "signal_processing": "信号処理の工夫",
}
LABEL_KIND_LABELS = {"preferred": "主ラベル", "alternate": "別名", "hidden": "隠しラベル"}
RELATION_KIND_LABELS = {"broader": "上位", "related": "関連", "exact_match": "同じ", "close_match": "近い"}
JUSTIFICATION_LABELS = {
    "manual_curation": "教員の判断", "lexical_match": "表記の一致", "vector_similarity": "意味の近さ",
    "cartridge_declared": "分野の宣言", "corpus_cooccurrence": "コーパス内の共起", "llm_candidate": "AI の候補",
    "structural_match": "構造の一致",
}
JUSTIFICATION_NONE = "根拠の記録なし"
REVIEW_STATUS_LABELS = {"candidate": "候補", "confirmed": "確定", "dismissed": "見送り"}
STANDARDIZATION_LABELS = {"standard": "標準", "field_standard": "分野標準", "emerging_common": "共通化しつつある",
                          "novel": "新規", "unknown": "未評価"}
# element-vocab.js（ElementVocab.elementTypeLabel）— 同一性リンク行の要素型
ELEMENT_TYPE_LABELS = {"theory_component": "論理要素", "theory_claim": "主張", "symbol": "記号",
                       "theory_module": "理論モジュール"}
# deliberation.js
DLB_SECTION_HEAD = "同一性リンク"
DLB_SECTION_NOTE = ("この要素が別の資料・共通部品と同じものだという対応づけです。候補は AI 対話または教員が作成し、"
                    "確定・却下は教員のみが行います。既存の表記は書き換えません（リンクの追加のみ）。")
DLB_LINK_STATUS = {"candidate": "候補（未確定）", "confirmed": "確定済み", "rejected": "却下"}
DLB_SEARCH_EMPTY = "同分野の共通部品が見つかりません。"
# deliberation.js _sharedPartDomainSelectHtml（IK-0890・第 2 波）
DLB_DOMAIN_PLACEHOLDER = "分野を選んでください"
DLB_DOMAIN_NO_NAME = "名前の登録が無い分野"
DLB_SEARCH_FAIL = "候補の検索に失敗しました。"
DLB_CREATED = "候補を作成しました。上の一覧から確定・却下できます。"  # 旧サーバ（facts なし）のときだけ
DLB_INSTANCE_TYPE_FALLBACK = "要素"  # deliberation.js _identityLinkRowHtml（instance_type_label が無いとき）
DLB_INSTANCE_LABEL_FALLBACK = "（名前を読めませんでした）"
DLB_CREATE_FAIL = "リンク候補の作成に失敗しました。"
DLB_DECIDE_FAIL = "操作に失敗しました（既に確定・却下済みの可能性があります）"
DLB_NO_LINKS = "同一性リンクはまだありません。"
# admin-lecture-studio.js
LS_ENDORSE_HEAD = "説明バージョンと承認 — {name}"
LS_ENDORSE_SUB = "承認は説明バージョン単位。承認の厚みは段階ラベルで表示(点数化しない)。"
LS_NO_EXPLANATIONS = "説明バージョンがありません。"
LS_LOAD_FAIL = "読み込みに失敗しました"
LS_ADD_NEED_BODY = "説明本文を入力してください"
LS_ADDED = "独自解釈を追加しました"
LS_ADD_FAIL = "追加に失敗しました"
LS_CALL_FAIL = "処理に失敗しました"
# admin-lecture-studio.js lsBindExplanationActions / lsEndorseExplanation のトースト（第 3 波・IK-0924 の語）
LS_OK = {"share": "共有設定を更新しました", "approve": "学生に公開してよいと査読しました", "endorse": "支持しました",
         "revoke": "支持を取り消しました", "cite": "引用しました"}
#: admin-lecture-studio.js EXPLANATION_AXIS_LABELS / EXPLANATION_REVIEW_STATUS_LABELS / EXPLANATION_ACTION_LABELS の逐語ミラー
EXPLANATION_AXIS_LABELS = {"review": "学生への公開", "support": "教員の支持", "citation": "引用"}
EXPLANATION_REVIEW_STATUS_LABELS = {"teacher_approved": "公開可（査読済み）", "teacher_review_required": "査読待ち",
                                    "needs_revision": "要修正（公開しない）", "rejected": "却下（公開しない）"}
EXPLANATION_ACTION_LABELS = {"approve": "学生に公開してよいと査読する", "endorse": "支持する", "revoke": "支持を取り消す"}
LS_NO_CITE_TARGET = "引用先にできる編集可能な別コースがありません。"
# admin-lecture-studio.js LS_CITATION_INTENT_LABELS（select の表示。先頭は「記録しない」= 送らない）
LS_CITE_INTENT_LABELS = {"uses_as_evidence": "根拠として使う", "extends": "発展させる", "qualifies": "条件を付ける",
                         "contrasts_with": "対比する", "cites_for_background": "背景として引く"}
# admin.js openDocumentShareModal / renderDocShareCurrent / addDocumentShare
DS_HEAD = "教材の共有・開示範囲"
DS_SHARE_NOTE = ("解析成果（理論の部品・グラフ・主張）を指定グループへ個別に共有します。"
                 "「閲覧・引用できる」か「編集・再解析できる」かを選びます。")  # openDocumentShareModal（第 1 周の是正後）
DS_EMPTY = "まだ共有グループは設定されていません。"
DS_NEED_GROUP = "グループを選択してください"
DS_ADDED = "共有しました"
DS_ADD_FAIL = "追加に失敗しました"
DS_LOAD_FAIL = "共有設定の取得に失敗しました"
# app.js renderContextSharedPane / showComponentExplanations
SP_TAB_INSTANCE = "この論文では"
SP_TAB_SHARED = "共通部品として"
SP_EMPTY = "共通部品としての情報はまだ登録されていません。"
EX_EMPTY = "承認済みの説明はまだありません。"
EX_FAIL = "説明を取得できませんでした。"
EX_STANDARD = "標準の説明"

_NUM_HINT = re.compile(r"^\s*(?:#|No\.?\s*)?(\d+)\s*$")


# ---------------------------------------------------------------------------
# 共通
# ---------------------------------------------------------------------------
def _note(session: PersonaSession, note: str) -> None:
    session.scratch.setdefault("runner_notes", []).append(note)


def _fact(ctx, text: str, status: Optional[int] = None) -> None:
    """画面が出す文に置き換える（審判は trace の生応答を読む）。"""
    ctx.session.last_body = {"_ui_fact": text}
    if status is not None:
        ctx.session.last_status = status


def _blocked(ctx, name: str, text: str) -> None:
    """画面がその場で止めて送らない（理由が空・分野未選択など）。HTTP は出さず precondition として残し、画面の文を出す。"""
    ctx.missing(name)
    ctx.session.last_body = {"_precondition": name, "_ui_fact": text}


def _ok(status: Optional[int]) -> bool:
    return status is not None and 200 <= status < 300


def _detail(body: Any, default: str) -> str:
    d = body.get("detail") if isinstance(body, dict) else None
    if isinstance(d, str) and d.strip():
        return d.strip()
    if d not in (None, ""):
        return str(d)
    return default


def pick(rows: list[dict], hint: Any, keys: tuple[str, ...]) -> Optional[dict]:
    """一覧から番号（1 始まり）か、``keys`` の値の部分一致（大小無視）で 1 件を選ぶ。hint が空なら先頭。"""
    rows = [r for r in rows if isinstance(r, dict)]
    if not rows:
        return None
    text = str(hint or "").strip()
    if not text:
        return rows[0]
    m = _NUM_HINT.match(text)
    if m:
        i = int(m.group(1))
        return rows[i - 1] if 1 <= i <= len(rows) else None
    low = text.lower()
    for r in rows:
        for k in keys:
            v = r
            for part in k.split("."):
                v = v.get(part) if isinstance(v, dict) else None
            if isinstance(v, str) and v and (low in v.lower() or v.lower() in low):
                return r
    return None


def harness_text(inner: str) -> str:
    """runner の注記（画面の文ではない）を「（ハーネス: …）」の形で言う（第 25 周 H23・H29）。"""
    return f"（ハーネス: {str(inner).strip()}。これは画面の文ではない）"


def _norm_name(v: Any) -> str:
    return re.sub(r"\s+", "", str(v or "")).casefold()


def _linked_component_ids(session) -> set[str]:
    """このセッションで同一性リンクを見た（深く検討で開いた）部品の id。"""
    el = session.scratch.get("share_element") or {}
    links = session.scratch.get("share_element_links") or []
    return {str(el.get("element_id") or "")} if el.get("element_id") and links else set()


def pick_component(comps: list[dict], hint: Any, *, linked: Optional[set] = None
                   ) -> tuple[Optional[dict], list[dict]]:
    """原稿スタジオの部品一覧から ``component_hint`` で 1 件を引く（第 25 周 H29）。

    順序: 番号 → 名前の完全一致 → 同一性リンクのある部品の部分一致 → 部分一致が 1 件。部分一致が複数なら
    選ばず ``(None, 候補)`` を返す（「TOV」で先頭の「Define: TOV residual loss」を開いて意図と違った）。
    """
    rows = [c for c in comps if isinstance(c, dict)]
    text = str(hint or "").strip()
    if not rows:
        return None, []
    if not text or _NUM_HINT.match(text):
        return pick(rows, text, ("name",)), []
    key = _norm_name(text)
    exact = [c for c in rows if _norm_name(c.get("name")) == key]
    if exact:
        return exact[0], []
    part = [c for c in rows if key and (key in _norm_name(c.get("name")) or _norm_name(c.get("name")) in key)
            and _norm_name(c.get("name"))]
    if linked:
        hit = [c for c in part if str(c.get("id") or "") in linked]
        if len(hit) == 1:
            return hit[0], []
    if len(part) == 1:
        return part[0], []
    return (None, part) if part else (None, [])


def _label(table: dict, key: Any) -> str:
    k = str(key or "")
    return table.get(k, k)


def _chip(status: Any) -> str:
    """admin.js _libraryReviewChipHtml: 確定（confirmed）と空はチップを出さない。"""
    s = str(status or "")
    if not s or s == "confirmed":
        return ""
    return f" [{_label(REVIEW_STATUS_LABELS, s)}]"


def _justification(j: Any) -> str:
    return f"（{_label(JUSTIFICATION_LABELS, j) if j else JUSTIFICATION_NONE}）"


def _local_expression(le: Any) -> str:
    if not le:
        return ""
    if isinstance(le, str):
        return le.strip()
    if isinstance(le, dict) and le.get("name") is not None:
        return str(le["name"]).strip()
    return ""

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


def _is_pdf(m: dict) -> bool:
    return str(m.get("filename") or "").lower().endswith(".pdf")


def pick_material(rows: list[dict], hint: Any, fmt: Any) -> Optional[dict]:
    """教材一覧（admin.js renderMaterials の行: 題名・ファイル名）から 1 件。format_hint=tex|pdf で版を絞る。"""
    f = str(fmt or "").strip().lower()
    if f in ("pdf",):
        rows = [m for m in rows if _is_pdf(m)]
    elif f in ("tex", "latex", "source", "tar", "tar.gz"):
        rows = [m for m in rows if not _is_pdf(m)]
    return pick(rows, hint, ("title", "filename"))


def target_of_node(node: dict) -> str:
    """admin-graph-review.js ``deliberationTargetId`` の写し（DB UUID → 代表要素 → linked_component_ids の先頭）。"""
    nid = str(node.get("id") or node.get("node_id") or "")
    if nid and _UUID_RE.match(nid):
        return nid
    rep = str(node.get("representative_component_id") or "").strip()
    if rep:
        return rep
    for c in node.get("linked_component_ids") or []:
        if str(c or "").strip():
            return str(c).strip()
    return ""


# ---------------------------------------------------------------------------
# グラフのノード名（admin-lecture-studio.js lsGraphSemanticLabel の写し — グラフレビューは graphView に委譲）
# ---------------------------------------------------------------------------
# admin-lecture-studio.js LS_OPERATION_VERB_JA の逐語ミラー（test_round25_share_harness が JS と突き合わせる）
LS_OPERATION_VERB_JA = {
    "Apply definition": "定義を適用", "Apply criterion": "基準を適用", "Apply constraint": "制約を適用",
    "Apply equation": "式を適用", "Apply measurement": "測定を適用", "Apply independence": "独立性を適用",
    "Infer conclusion": "結論を導出", "Infer intermediate claim": "中間主張を導出", "Derive result": "結果を導出",
    "Derive consistency relation": "整合関係を導出", "Compare": "比較・検証", "Flag limitation": "制約を確認",
    "Eliminate parameter": "パラメータを消去", "Solve linear system": "連立方程式を求解", "State assumption": "仮定を提示",
    "Branch on condition": "条件分岐", "Introduce observable": "観測量を導入", "Define": "定義", "Construct": "構成",
    "Linearize": "線形化", "Normalize": "正規化", "Approximate": "近似", "Substitute": "代入", "Eliminate": "消去",
    "Derive": "導出", "Constrain": "制約", "Diagnose": "診断", "Solve": "求解", "Infer": "推論", "Apply": "適用",
    "Flag": "確認", "State": "提示", "Introduce": "導入",
}
# element-vocab.js THEORY_STAGE_LABELS / THEORY_STAGE_DISPLAY_TO_KEY（正本 backend/core/element_vocab.py）
THEORY_STAGE_LABELS = {"theory_basis": "理論の土台", "observation_model": "観測モデル",
                       "observable_construction": "観測量の構成", "equation_system": "式の体系", "elimination": "消去",
                       "consistency_relation": "整合関係", "diagnostic_application": "診断・応用"}
THEORY_STAGE_DISPLAY_TO_KEY = {"theory basis": "theory_basis", "observation model": "observation_model",
                               "observable construction": "observable_construction",
                               "equation system": "equation_system", "elimination": "elimination",
                               "consistency relation": "consistency_relation",
                               "diagnostic / application": "diagnostic_application",
                               "diagnostic/application": "diagnostic_application"}
LS_MAIN_STAGE_LABELS = ("Theory basis", "Observation model", "Observable construction", "Equation system",
                        "Elimination", "Consistency relation", "Diagnostic / application")


def _theory_stage_label_ja(text: Any) -> str:
    """lsTheoryStageLabelJa（コロン形は ""）。"""
    raw = str(text or "").strip()
    if not raw or ":" in raw:
        return ""
    if raw in THEORY_STAGE_LABELS:
        return THEORY_STAGE_LABELS[raw]
    low = raw.lower()
    key = THEORY_STAGE_DISPLAY_TO_KEY.get(low) or (low.replace(" ", "_") if low.replace(" ", "_") in THEORY_STAGE_LABELS
                                                   else "")
    return THEORY_STAGE_LABELS.get(key, "")


def _stage_label_ja(text: Any) -> str:
    """lsGraphStageLabelJa。"""
    raw = str(text or "").strip()
    if not raw:
        return raw
    if ":" in raw and raw.index(":") > 0:
        head = _theory_stage_label_ja(raw[:raw.index(":")])
        if head:
            rest = raw[raw.index(":") + 1:].strip()
            return f"{head}: {rest}" if rest else head
    return _theory_stage_label_ja(raw) or raw


def _operation_label_ja(verb: Any) -> str:
    """lsGraphOperationLabelJa。"""
    label = str(verb or "").strip()
    if label in LS_OPERATION_VERB_JA:
        return LS_OPERATION_VERB_JA[label]
    stage = _theory_stage_label_ja(label)
    if stage:
        return stage
    parts = label.split(" ")
    if len(parts) >= 2 and parts[0] in LS_OPERATION_VERB_JA:
        return LS_OPERATION_VERB_JA[parts[0]] + ": " + " ".join(parts[1:])
    return label


def _looks_like_internal_id(token: str) -> bool:
    t = re.sub(r"[)\].,;:]+$", "", re.sub(r"^[(\[]+", "", str(token or ""))).strip()
    if not t:
        return False
    if "_" in t and re.search(r"\d", t) and not re.search(r"\s", t):
        return True
    if re.match(r"^eq[\s._-]*\(?\d", t, re.IGNORECASE):
        return True
    return bool(re.match(r"^\(?\d+(?:[._]\d+)+\)?$", t) or re.match(r"^\(\d+\)$", t))


def _strip_internal_ids(text: Any) -> str:
    """lsGraphStripInternalIds。"""
    s = re.sub(r"\beq[\s._-]*\(?\d+(?:[._]\d+)*\)?", " ", str(text or ""), flags=re.IGNORECASE)
    if not s.strip():
        return ""
    sep = re.compile(r"^[—–→←\-<>|,:;./]+$")
    out: list[str] = []
    for tok in [t for t in s.split() if t and not _looks_like_internal_id(t)]:
        if sep.match(tok) and (not out or sep.match(out[-1])):
            continue
        out.append(tok)
    while out and sep.match(out[-1]):
        out.pop()
    joined = re.sub(r"\s{2,}", " ", " ".join(out))
    return re.sub(r"^[\s—–→←<>|,:;]+|[\s—–→←<>|,:;]+$", "", joined).strip()


def _main_stage_label(node: dict) -> str:
    label = str(node.get("label") or node.get("name") or "")
    ctype = str(node.get("component_type") or node.get("type") or "")
    if not (ctype.endswith("OperationNode") or str(node.get("operation") or "").strip()):
        return label
    head = label.split(":", 1)[0].strip().lower() if ":" in label else label.lower()
    return next((x for x in LS_MAIN_STAGE_LABELS if x.lower() == head), label)


def graph_node_label(node: dict) -> str:
    """lsGraphSemanticLabel（visual_label の日本語化 → display_label → 操作 + 対象 → 段 + 対象 → 段 → 操作）。"""
    if not isinstance(node, dict):
        return ""
    visual = str(node.get("visual_label") or "").strip()
    cand = _strip_internal_ids(_operation_label_ja(visual)) if visual else ""
    if not cand and str(node.get("display_label") or "").strip():
        cand = _strip_internal_ids(node["display_label"])
    if not cand:
        op = str(node.get("operation") or "").strip()
        if op:
            human = op.replace("_", " ").strip()
            human = human[:1].upper() + human[1:]
            verb = _strip_internal_ids(_operation_label_ja(human))
            target = _strip_internal_ids(str(node.get("theory_object") or "").strip())
            cand = f"{verb}: {target}" if verb and target and target.lower() != verb.lower() else (verb or target)
    if not cand:
        stage = _main_stage_label(node)
        obj = str(node.get("theory_object") or "").strip()
        full = str(node.get("display_label") or "").strip() or (
            (stage or str(node.get("label") or node.get("name") or "")) if not obj
            else (obj if not stage or obj.lower() == stage.lower() else f"{stage}: {obj}"))
        cand = _strip_internal_ids(full) or _strip_internal_ids(stage)
    return _stage_label_ja(cand or "（無題）")


# ---------------------------------------------------------------------------
# 同一性の候補（ナレッジライブラリタブ上部）
# ---------------------------------------------------------------------------
def identity_candidates_view(body: dict) -> str:
    """admin.js renderLibraryIdentityCandidates の写し。"""
    lines = ["同一性の候補:"]
    for f in body.get("facts") or []:
        lines.append(str(f))
    cands = [c for c in body.get("candidates") or [] if isinstance(c, dict)]
    if not cands:
        # 第 25 周 H10 / H22: 画面（renderLibraryIdentityCandidates・IK-0864 是正後）は候補ゼロのとき
        # サーバの facts だけを描き、固定の空状態文を重ねない（2 回出ると読まれた）
        return "\n".join(lines)
    for i, c in enumerate(cands, 1):
        entry = c.get("entry") if isinstance(c.get("entry"), dict) else {}
        lines.append(f"{i}. {entry.get('name') or ''}{_chip(entry.get('review_status'))}")
        sub = _label(ENTRY_TYPE_LABELS, entry.get("entry_type"))
        if entry.get("domain_label") or entry.get("domain_key"):
            sub += f" ・ {entry.get('domain_label') or entry['domain_key']}"  # IK-0866: 分野は表示名
        lines.append(f"   {sub}")
        titles = [str(t) for t in c.get("supporting_titles") or [] if str(t or "")]
        if titles:
            lines.append("   " + IDENTITY_SUPPORT_HEAD + " / ".join(titles))
        for j, link in enumerate([l for l in c.get("links") or [] if isinstance(l, dict)], 1):
            inst = link.get("instance") if isinstance(link.get("instance"), dict) else {}
            # 画面は題名が空なら document_id（UUID）を出す — 投影でもそのまま出す（隠すと見えない欠陥になる）
            who = str(link.get("document_title") or inst.get("document_id") or "")
            tlabel = ELEMENT_TYPE_LABELS.get(str(inst.get("element_type") or ""), "")
            expr = _local_expression(link.get("local_expression"))
            lines.append(f"   リンク{j}: {who}" + (f" {tlabel}" if tlabel else "") + (f"「{expr}」" if expr else "")
                         + _chip(link.get("status")) + " " + _justification(link.get("mapping_justification"))
                         + " ［確認］［見送り］")
        if c.get("hidden_count"):
            lines.append("   " + IDENTITY_HIDDEN_NOTE)
        lines.append("   概念: ［確定］［見送り］")
    return "\n".join(lines)


def _identity_candidates(ctx) -> None:
    s = ctx.session
    params = {}
    if ctx.args.get("domain_key"):
        params["domain_key"] = ctx.args["domain_key"]
    if ctx.args.get("include_dismissed"):
        params["include_dismissed"] = "true"
    status, body = ctx.main(params=params or None)
    if _ok(status) and isinstance(body, dict):
        s.scratch["share_identity"] = [c for c in body.get("candidates") or [] if isinstance(c, dict)]
        _fact(ctx, identity_candidates_view(body))


def _identity_rows(session: PersonaSession) -> list[dict]:
    return [c for c in session.scratch.get("share_identity") or [] if isinstance(c, dict)]


def _decide_link(ctx, link_id: str, decision: str, *, screen: str) -> tuple[Optional[int], Any]:
    decision = "reject" if str(decision or "").strip().lower() in ("reject", "見送り", "却下", "dismiss") else "confirm"
    template = f"/api/admin/deliberation/identity-links/{{link_id}}/{decision}"
    return ctx.call("POST", template, overrides={"link_id": link_id})


def _identity_link_decide(ctx) -> None:
    s = ctx.session
    cand = pick(_identity_rows(s), ctx.args.get("candidate_hint"), ("entry.name",))
    if cand is None:
        return ctx.missing("identity_candidate")
    link = pick(list(cand.get("links") or []), ctx.args.get("link_hint"), ("document_title", "local_expression.name"))
    if link is None or not link.get("link_id"):
        return ctx.missing("identity_link")
    if ctx.args.get("reason"):
        _note(s, "share: リンクの［見送り］の画面には理由の欄が無い（理由を書けなかった — 送られていない）")
    status, body = _decide_link(ctx, str(link["link_id"]), str(ctx.args.get("decision") or ""), screen="library")
    if _ok(status):
        # 画面は一覧を読み直す（_libraryDecideIdentityLink → loadLibraryEntryCandidates）
        st2, body2 = ctx.call("GET", "/api/admin/library/identity-candidates")
        if _ok(st2) and isinstance(body2, dict):
            s.scratch["share_identity"] = [c for c in body2.get("candidates") or [] if isinstance(c, dict)]
            _fact(ctx, identity_candidates_view(body2), status)
    else:
        _fact(ctx, f"（警告）{_detail(body, UPDATE_FAIL)}")


# ---------------------------------------------------------------------------
# エントリ一覧・詳細
# ---------------------------------------------------------------------------
def entries_view(entries: list[dict]) -> str:
    """admin.js renderLibraryEntriesList の写し。"""
    if not entries:
        return ENTRIES_EMPTY
    lines = ["ナレッジライブラリのエントリ:"]
    for i, e in enumerate(entries, 1):
        retired = e.get("status") == "retired"
        line = f"{i}. {e.get('name') or ''}" + (" (廃止)" if retired else "") + _chip(e.get("review_status"))
        # admin.js の一覧行（IK-0866 是正後）: 版は version_line・更新者は updated_by_label（サーバが表示語に解決。無ければ旧形）
        line += f" — {_label(ENTRY_TYPE_LABELS, e.get('entry_type'))} ・ {e.get('version_line') or ('版 ' + str(e.get('latest_version_no') or 0))}"
        who = e.get("updated_by_label") or e.get("updated_by")
        if who:
            line += f" ・ 更新: {who}"
        lines.append(line)
    return "\n".join(lines)


def _entries(ctx) -> None:
    s = ctx.session
    params = {"include_candidates": "true"}
    for k in ("domain_key", "q", "entry_type"):
        if ctx.args.get(k):
            params[k] = ctx.args[k]
    status, body = ctx.main(params=params)
    if _ok(status) and isinstance(body, dict):
        rows = [e for e in body.get("entries") or [] if isinstance(e, dict)]
        s.scratch["share_entries"] = rows
        _fact(ctx, entries_view(rows))


def _entry_pool(session: PersonaSession) -> list[dict]:
    """画面で見えているエントリ（一覧 + 同一性の候補の概念 + 開いた・作ったエントリ）。"""
    pool: list[dict] = []
    seen: set[str] = set()
    opened = session.scratch.get("share_entry")
    for e in ([opened] if isinstance(opened, dict) else []) + list(session.scratch.get("share_entries") or []) + \
            [c.get("entry") for c in _identity_rows(session)]:
        if isinstance(e, dict) and e.get("id") and str(e["id"]) not in seen:
            seen.add(str(e["id"]))
            pool.append(e)
    return pool


def _resolve_entry(ctx) -> Optional[dict]:
    s = ctx.session
    hint = ctx.args.get("entry_hint") or ctx.args.get("candidate_hint")
    if not hint and isinstance(s.scratch.get("share_entry"), dict):
        return s.scratch["share_entry"]
    pool = _entry_pool(s)
    if ctx.args.get("candidate_hint") and not ctx.args.get("entry_hint"):
        cand = pick(_identity_rows(s), ctx.args["candidate_hint"], ("entry.name",))
        if cand and isinstance(cand.get("entry"), dict):
            return cand["entry"]
    return pick(pool, hint, ("name",))


def _labels_lines(labels: list[dict]) -> list[str]:
    if not labels:
        return [LABELS_EMPTY]
    out = []
    for l in labels:
        dismissed = l.get("status") == "dismissed"
        out.append(f"- {l.get('label') or ''} {_label(LABEL_KIND_LABELS, l.get('kind'))}{_chip(l.get('status'))} "
                   f"{_justification(l.get('mapping_justification'))}" + ("" if dismissed else " ［見送り］"))
    return out


def _relations_lines(relations: list[dict], entry_id: str) -> list[str]:
    if not relations:
        return [RELATIONS_EMPTY]
    out = []
    for i, r in enumerate(relations, 1):
        is_subject = r.get("subject_entry_id") == entry_id
        # 画面は名前が空なら相手のエントリ ID を出す
        other = (r.get("object_name") or r.get("object_entry_id")) if is_subject else \
            (r.get("subject_name") or r.get("subject_entry_id"))
        buttons = ("" if r.get("status") == "confirmed" else "［確定］") + ("" if r.get("status") == "dismissed" else "［見送り］")
        out.append(f"{i}. {_label(RELATION_KIND_LABELS, r.get('kind'))}: {other or ''}{_chip(r.get('status'))} "
                   f"{_justification(r.get('mapping_justification'))}" + (f" {r['reason']}" if r.get("reason") else "")
                   + (f" {buttons}" if buttons else ""))
    return out


def _atlas_lines(links: list[dict], version: Any) -> list[str]:
    out = []
    if version:
        out.append(f"分野の地図 版{version} に対する対応です。")
    if not links:
        out.append(ATLAS_EMPTY)
        return out
    for i, l in enumerate(links, 1):
        buttons = ("" if l.get("status") == "confirmed" else "［確定］") + ("" if l.get("status") == "dismissed" else "［見送り］")
        out.append(f"{i}. {l.get('node_label') or l.get('node_id') or ''} {_label(RELATION_KIND_LABELS, l.get('kind'))}"
                   f"{_chip(l.get('status'))} {_justification(l.get('mapping_justification'))}"
                   + (f" {l['nearness_label']}" if l.get("nearness_label") else "")
                   + (f" {ATLAS_NOT_CURRENT}" if l.get("node_in_current_version") is False else "")
                   + (f" {buttons}" if buttons else ""))
    return out


def bound_parts_lines(body: Optional[dict]) -> list[str]:
    """admin.js loadLibraryEntryBoundParts（第 3 波・IK-0920）: 確定 → 候補 → 却下の順に
    「論文『題名』 「部品名」 型ラベル 〔状態〕」。部品名は ``instance_label``（無ければ local_expression の name）、
    型は ``instance_type_label``。``hidden_count`` があれば ``hidden_note``（無ければ固定文）。表示のみ（操作は無い）。"""
    links = [l for l in (body or {}).get("identity_links") or [] if isinstance(l, dict)]
    order = {"confirmed": 0, "candidate": 1, "rejected": 2}
    links = sorted(links, key=lambda l: order.get(l.get("status"), 3))
    out: list[str] = []
    if not links:
        out.append(BOUND_PARTS_EMPTY)
    for link in links:
        part = str(link.get("instance_label") or "").strip()
        if not part:
            le = link.get("local_expression")
            part = (le.strip() if isinstance(le, str) else str((le or {}).get("name") or "").strip()
                    if isinstance(le, dict) else "")
        title = str(link.get("instance_document_title") or "").strip()
        type_label = str(link.get("instance_type_label") or "").strip()
        status_label = BOUND_PARTS_STATUS.get(str(link.get("status") or ""), "")
        out.append("- " + (f"論文『{title}』 " if title else "") + (f"「{part}」" if part else BOUND_PARTS_NO_NAME)
                   + (f" {type_label}" if type_label else "") + (f" 〔{status_label}〕" if status_label else ""))
    if (body or {}).get("hidden_count"):
        out.append(str((body or {}).get("hidden_note") or BOUND_PARTS_HIDDEN))
    return out


def atlas_manual_line(manual: Optional[dict] = None) -> list[str]:
    """admin.js renderLibraryRegistrySections の手動の入口（第 3 波・IK-0927）+ ``_libraryBindManualAtlasLink`` の結果。

    ``manual`` は直前の検索・追加の結果 ``{nodes: [...], facts: [...]}``（無ければ入口だけ）。選択肢の表示は
    「ラベル（領域名）」。"""
    manual = manual or {}
    nodes = manual.get("nodes")
    if nodes is None:
        select = ATLAS_MANUAL_SELECT_EMPTY
    elif nodes:
        select = " / ".join(f"{n.get('label') or ''}" + (f"（{n['region_label']}）" if n.get("region_label") else "")
                            for n in nodes if isinstance(n, dict))
    else:
        select = ATLAS_MANUAL_NONE
    out = [f"手で対応を付ける: 地図の概念の欄（{ATLAS_MANUAL_PLACEHOLDER}）［検索］ 概念の選択（{select}）［対応を付ける］"]
    facts = " ".join(str(f) for f in manual.get("facts") or [] if f)
    if facts:
        out.append(facts)
    return out


def _outcome_line(outcomes: Optional[dict], key: str) -> list[str]:
    facts = [str(f) for f in ((outcomes or {}).get(key) or []) if f]
    return [" ".join(facts)] if facts else []


def entry_detail_view(entry: dict, labels: Optional[list], relations: Optional[list], atlas: Optional[list],
                      version: Any, outcomes: Optional[dict] = None, *, bound_parts: Optional[dict] = None,
                      atlas_manual: Optional[dict] = None) -> str:
    """admin.js renderLibraryDetail + renderLibraryRegistrySections の写し（4 区画）。

    区画の取得に失敗したときは画面と同じく区画ごと畳む（``_libraryHideSection``）。"""
    # renderLibraryDetail（IK-0866/0867 の是正後）: 分野は domain_label・版の行は version_line + 更新者 updated_by_label・
    # 別名の入力欄は無く「別名・隠しラベル」区画への案内 1 行（旧サーバは domain_key / 版 N にフォールバック）
    head = f"{entry.get('domain_label') or entry.get('domain_key') or ''} ・ {_label(ENTRY_TYPE_LABELS, entry.get('entry_type'))}"
    if entry.get("status") == "retired":
        head += " ・ 廃止済み"
    version_line = entry.get("version_line") if "version_line" in entry else f"版 {entry.get('latest_version_no') or 0}"
    if entry.get("updated_by_label"):
        version_line = f"{version_line or ''} ・ 更新: {entry['updated_by_label']}"
    lines = [head] + ([version_line] if version_line else []) + [
             f"標準化度: {_label(STANDARDIZATION_LABELS, entry.get('standardization_status') or 'unknown')}",
             f"名称: {entry.get('name') or ''}", ALIASES_MOVED_NOTE,
             f"要約: {entry.get('summary') or ''}"]
    if labels is not None:
        # admin.js _libraryShowOutcomeFacts（IK-0897・第 2 波）: 追加の結果の事実文を一覧の直前に 1 行
        lines += ["別名・隠しラベル", f"（{LABELS_NOTE}）"] + _outcome_line(outcomes, "labels") + _labels_lines(labels)
    if relations is not None:
        lines += ["関係", f"（{RELATIONS_NOTE}）"] + _outcome_line(outcomes, "relations") + \
                 _relations_lines(relations, str(entry.get("id") or "")) + \
                 [f"追加: 種類［上位/関連/同じ/近い］ 相手の欄（{RELATION_OBJECT_PLACEHOLDER}）［検索］"
                  f" 相手の選択（{RELATION_SELECT_EMPTY}）［追加］"]
    if bound_parts is not None:
        # 取得に失敗した区画は畳む（_libraryHideSection）— 呼び手が None を渡す
        lines += [BOUND_PARTS_HEAD, f"（{BOUND_PARTS_NOTE}）"] + bound_parts_lines(bound_parts)
    if atlas is not None:
        lines += ["分野の地図との対応", f"（{ATLAS_NOTE}）"] + _atlas_lines(atlas, version) + atlas_manual_line(atlas_manual)
    status_label = _label(REVIEW_STATUS_LABELS, entry.get("review_status") or "confirmed")
    lines += ["この概念の扱い", f"（{REVIEW_NOTE}）", f"いまの扱い: {status_label}"
              + (f" 理由: {entry['review_note']}" if entry.get("review_note") else "")
              + (f" {entry['review_status_note']}" if entry.get("review_status_note") else "") + " ［確定にする］［見送る］"]
    return "\n".join(lines)


def _load_detail(ctx, entry_id: str, *, status: Optional[int] = None, head: str = "",
                 outcomes: Optional[dict] = None, atlas_manual: Optional[dict] = None) -> None:
    s = ctx.session
    st, entry = ctx.call("GET", "/api/admin/library/entries/{entry_id}", overrides={"entry_id": entry_id})
    if not _ok(st) or not isinstance(entry, dict):
        _fact(ctx, (head + "\n" if head else "") + "エントリの読み込みに失敗しました", status)
        return
    s.scratch["share_entry"] = entry
    stl, bl = ctx.call("GET", "/api/admin/library/entries/{entry_id}/labels", overrides={"entry_id": entry_id})
    labels = [x for x in (bl or {}).get("labels") or [] if isinstance(x, dict)] if _ok(stl) and isinstance(bl, dict) else None
    s.scratch["share_entry_labels"] = labels or []
    str_, br = ctx.call("GET", "/api/admin/library/relations", params={"entry_id": entry_id})
    relations = [x for x in (br or {}).get("relations") or [] if isinstance(x, dict)] if _ok(str_) and isinstance(br, dict) else None
    sta, ba = ctx.call("GET", "/api/admin/library/atlas-links", params={"domain_key": entry.get("domain_key") or ""})
    atlas, version = None, ""
    if _ok(sta) and isinstance(ba, dict):
        atlas = [x for x in ba.get("links") or [] if isinstance(x, dict) and x.get("entry_id") == entry_id]
        version = ba.get("skeleton_version") or ""
    s.scratch["share_relations"] = relations or []
    s.scratch["share_atlas_links"] = atlas or []
    # 第 3 波（IK-0920）: 結びついた教材の部品（loadLibraryEntryBoundParts）。失敗・404（旧サーバ）は区画ごと畳む
    stb, bb = ctx.call("GET", "/api/admin/deliberation/shared-parts/{entry_id}/identity-links",
                       overrides={"entry_id": entry_id})
    bound = bb if _ok(stb) and isinstance(bb, dict) else None
    text = entry_detail_view(entry, labels, relations, atlas, version, outcomes, bound_parts=bound,
                             atlas_manual=atlas_manual)
    _fact(ctx, (head + "\n" if head else "") + text, status if status is not None else st)


def _entry_open(ctx) -> None:
    entry = _resolve_entry(ctx)
    if entry is None or not entry.get("id"):
        return ctx.missing("entry")
    _load_detail(ctx, str(entry["id"]))


def _entry_create(ctx) -> None:
    s = ctx.session
    name = str(ctx.args.get("name") or "").strip()
    if not name:
        return ctx.missing("name")
    same = next((e for e in s.scratch.get("share_entries") or [] if isinstance(e, dict) and e.get("id")
                 and re.sub(r"\s+", "", str(e.get("name") or "")).casefold() == re.sub(r"\s+", "", name).casefold()), None)
    if same is not None:
        # 一覧に同じ名前のエントリが見えている（前の周・前の手で作ったもの）。場面は「無ければ作る」なので作らずに開く
        _note(s, f"runner: 一覧に同じ名前のエントリ「{same.get('name')}」があるので、新規作成せずにそれを開いた")
        s.scratch["share_entry"] = same
        # 第 25 周 H23: 観察の先頭で「作っていない」と言う（作成後の詳細に見えて頭脳が「作った」と誤読した）
        _load_detail(ctx, str(same["id"]), head=harness_text(
            f"一覧に同じ名前のエントリ「{same.get('name')}」があるので、新規作成していない（［登録］は押していない）。"
            "開いたのは既存のエントリの詳細"))
        return
    domain = str(ctx.args.get("domain_key") or "").strip()
    if not domain:
        domain = "astrophysics"
        _note(s, "runner_default: 新規エントリの分野を astrophysics にした（画面の分野の選択欄の既定ではない）")
    payload = {"domain_key": domain, "entry_type": str(ctx.args.get("entry_type") or "theory_component"),
               "name": name, "aliases": [str(a) for a in ctx.args.get("aliases") or [] if str(a or "").strip()],
               "summary": str(ctx.args.get("summary") or ""), "body": {}, "source_component_ids": [],
               "source_document_ids": [], "exemplar_images": []}
    status, body = ctx.main(json=payload)
    if _ok(status) and isinstance(body, dict) and body.get("id"):
        s.scratch["share_entry"] = body
        _load_detail(ctx, str(body["id"]), status=status, head=LIBRARY_REGISTERED)
    elif status is not None:
        _fact(ctx, f"（エラー）{_detail(body, '登録に失敗しました')}")


def _entry_review(ctx) -> None:
    s = ctx.session
    entry = _resolve_entry(ctx)
    if entry is None or not entry.get("id"):
        return ctx.missing("entry")
    status_arg = str(ctx.args.get("status") or "").strip().lower()
    target = "dismissed" if status_arg in ("dismissed", "dismiss", "見送り", "見送る", "reject") else "confirmed"
    note = str(ctx.args.get("review_note") or "").strip()
    if target == "dismissed" and not note:
        # 画面は理由の入力ダイアログで空のまま OK すると alert で止まり、送らない
        return _blocked(ctx, "dismiss_reason", DISMISS_REASON_PROMPT.format(what=entry.get("name") or "この候補")
                        + " → （空欄）\n" + DISMISS_REASON_REQUIRED)
    payload: dict = {"status": target}
    if note:
        payload["review_note"] = note
    st, body = ctx.main(json=payload, overrides={"entry_id": str(entry["id"])})
    if _ok(st):
        _load_detail(ctx, str(entry["id"]), status=st)
    else:
        _fact(ctx, f"（警告）{_detail(body, UPDATE_FAIL)}")


def _label_add(ctx) -> None:
    entry = _resolve_entry(ctx)
    if entry is None or not entry.get("id"):
        return ctx.missing("entry")
    label = str(ctx.args.get("label") or "").strip()
    if not label:
        return ctx.missing("label")
    kind = "hidden" if str(ctx.args.get("kind") or "").strip() in ("hidden", "隠しラベル") else "alternate"
    st, body = ctx.main(json={"kind": kind, "label": label}, overrides={"entry_id": str(entry["id"])})
    if _ok(st):
        facts = body.get("facts") if isinstance(body, dict) else None
        _load_detail(ctx, str(entry["id"]), status=st, outcomes={"labels": facts or []})
    else:
        _fact(ctx, f"（警告）{_detail(body, ADD_FAIL)}")


def _label_dismiss(ctx) -> None:
    """admin.js loadLibraryEntryLabels の［見送り］→ 理由の入力（空なら送らない）→ POST …/labels/{id}/dismiss → 読み直し。"""
    s = ctx.session
    entry = _resolve_entry(ctx)
    if entry is None or not entry.get("id"):
        return ctx.missing("entry")
    if str((s.scratch.get("share_entry") or {}).get("id") or "") != str(entry["id"]):
        _load_detail(ctx, str(entry["id"]))
    open_labels = [l for l in s.scratch.get("share_entry_labels") or [] if isinstance(l, dict)
                   and l.get("id") and l.get("status") != "dismissed"]
    label = pick(open_labels, ctx.args.get("label"), ("label",))
    if label is None:
        return _blocked(ctx, "label", harness_text("見送る対象のラベルが「別名・隠しラベル」に無い（［見送り］のボタンが"
                                                   "出ているラベルだけを見送れる）"))
    note = str(ctx.args.get("review_note") or "").strip()
    if not note:
        return _blocked(ctx, "dismiss_reason", DISMISS_REASON_PROMPT.format(what=label.get("label") or "このラベル"))
    st, body = ctx.call("POST", "/api/admin/library/entries/{entry_id}/labels/{label_id}/dismiss",
                        json={"review_note": note},
                        overrides={"entry_id": str(entry["id"]), "label_id": str(label["id"])})
    if _ok(st):
        _load_detail(ctx, str(entry["id"]), status=st)
    else:
        _fact(ctx, f"（警告）{_detail(body, '更新に失敗しました')}")


def _relation_add(ctx) -> None:
    s = ctx.session
    entry = _resolve_entry(ctx)
    if entry is None or not entry.get("id"):
        return ctx.missing("entry")
    obj = str(ctx.args.get("object_entry_id") or "").strip()
    picked_line = ""
    if not obj and ctx.args.get("object_hint"):
        # admin.js searchLibraryRelationCounterparts（IK-0866 の是正後）: 相手の名前・別名で検索 → 選択欄の先頭が選ばれる →
        # ［追加］。検索結果が無ければ選択欄は「（該当なし）」で追加は送られない（facts があれば下に出る）
        hint = str(ctx.args["object_hint"]).strip()
        st0, body0 = ctx.call("GET", "/api/admin/library/entries", params={"q": hint})
        if not _ok(st0) or not isinstance(body0, dict):
            return _fact(ctx, f"相手の欄に「{hint}」を入れて［検索］ → {RELATION_SEARCH_FAIL}", st0)
        found = [e for e in body0.get("entries") or [] if isinstance(e, dict) and e.get("id")
                 and str(e.get("id")) != str(entry["id"])]
        facts = [str(f) for f in body0.get("facts") or [] if f]
        if not found:
            return _fact(ctx, "\n".join([f"相手の欄に「{hint}」を入れて［検索］ → 相手の選択: {RELATION_SEARCH_NONE}"]
                                       + facts + ["（［追加］は押しても何も送られない）"]), st0)
        hit = pick(found, hint, ("name",)) or found[0]
        if hit is not found[0]:
            _note(s, f"share: 検索結果の選択欄の先頭ではなく「{hit.get('name')}」を選んだ（選択欄で選び直した）")
        obj = str(hit["id"])
        meta = " ・ ".join(x for x in (_label(ENTRY_TYPE_LABELS, hit.get("entry_type")),
                                       str(hit.get("domain_label") or hit.get("domain_key") or "")) if x)
        picked_line = f"相手の欄に「{hint}」を入れて［検索］ → 相手の選択: {hit.get('name') or ''}" + \
            (f"（{meta}）" if meta else "")
    if not obj:
        return ctx.missing("relation_object")
    kind = str(ctx.args.get("kind") or "related").strip()
    payload = {"subject_entry_id": str(entry["id"]), "object_entry_id": obj, "kind": kind}
    if ctx.args.get("reason"):
        _note(s, "share: 画面の関係の追加には理由の欄が無い（reason は送っていない）")
    st, body = ctx.main(json=payload)
    if _ok(st):
        facts = body.get("facts") if isinstance(body, dict) else None
        _load_detail(ctx, str(entry["id"]), status=st, head=picked_line, outcomes={"relations": facts or []})
    else:
        _fact(ctx, (picked_line + "\n" if picked_line else "") + f"（警告）{_detail(body, ADD_FAIL)}")


def _decide_with_reason(ctx, template: str, hole: str, row: dict, label: str, reload_entry: Optional[dict]) -> None:
    s = ctx.session
    status_arg = str(ctx.args.get("status") or "").strip().lower()
    target = "dismissed" if status_arg in ("dismissed", "dismiss", "見送り", "見送る", "reject") else "confirmed"
    note = str(ctx.args.get("review_note") or "").strip()
    if target == "dismissed" and not note:
        return _blocked(ctx, "dismiss_reason", DISMISS_REASON_PROMPT.format(what=label) + " → （空欄）\n"
                        + DISMISS_REASON_REQUIRED)
    payload: dict = {"status": target}
    if note:
        payload["review_note"] = note
    st, body = ctx.call("POST", template, json=payload, overrides={hole: str(row["id"])})
    if _ok(st) and reload_entry and reload_entry.get("id"):
        _load_detail(ctx, str(reload_entry["id"]), status=st)
    elif not _ok(st):
        _fact(ctx, f"（警告）{_detail(body, UPDATE_FAIL)}")


def _relation_decide(ctx) -> None:
    s = ctx.session
    rows = [r for r in s.scratch.get("share_relations") or [] if isinstance(r, dict)]
    row = pick(rows, ctx.args.get("relation_hint"), ("object_name", "subject_name"))
    if row is None or not row.get("id"):
        return ctx.missing("relation")
    label = f"{_label(RELATION_KIND_LABELS, row.get('kind'))}: {row.get('object_name') or row.get('subject_name') or ''}"
    _decide_with_reason(ctx, "/api/admin/library/relations/{relation_id}/decide", "relation_id", row, label,
                        s.scratch.get("share_entry"))


def _atlas_link_decide(ctx) -> None:
    s = ctx.session
    if ctx.args.get("entry_hint"):
        entry = _resolve_entry(ctx)
        if entry and entry.get("id"):
            _load_detail(ctx, str(entry["id"]))
    rows = [r for r in s.scratch.get("share_atlas_links") or [] if isinstance(r, dict)]
    row = pick(rows, ctx.args.get("link_hint"), ("node_label", "node_id"))
    if row is None or not row.get("id"):
        return ctx.missing("atlas_link")
    _decide_with_reason(ctx, "/api/admin/library/atlas-links/{link_id}/decide", "link_id", row,
                        str(row.get("node_label") or row.get("node_id") or "この対応"), s.scratch.get("share_entry"))


def _atlas_derive(ctx) -> None:
    s = ctx.session
    domain = str(ctx.args.get("domain_key") or "").strip()
    if not domain:
        # 画面は「すべての分野」のままだと alert で止まる（deriveLibraryAtlasLinks）
        return _blocked(ctx, "domain_key", DERIVE_NEED_DOMAIN)
    st, body = ctx.main(json={"domain_key": domain})
    # admin.js deriveLibraryAtlasLinks（IK-0866・第 2 波）: 確認文の分野は表示名（_libraryTabState.domainLabels）。
    # 引けなければ「選んだ分野」。runner は分野一覧を読まないので、読んだエントリの domain_label で引く（近似）
    label = next((str(e.get("domain_label")) for e in s.scratch.get("share_entries") or []
                  if isinstance(e, dict) and e.get("domain_key") == domain and e.get("domain_label")), "") or DERIVE_DOMAIN_FALLBACK
    lines = ["確認: 地図との対応を導出 — " + DERIVE_CONFIRM.format(domain=label) + " → 導出する"]
    if _ok(st):
        # deriveLibraryAtlasLinks（IK-0866 の是正後）: 応答の facts をボタンの下（#library-atlas-derive-facts）に出し、
        # 一覧と同一性の候補を読み直す（旧サーバは facts が無く何も出ない）
        lines += [str(f) for f in (body.get("facts") if isinstance(body, dict) else None) or [] if f]
        st2, body2 = ctx.call("GET", "/api/admin/library/entries", params={"include_candidates": "true",
                                                                         "domain_key": domain})
        if _ok(st2) and isinstance(body2, dict):
            rows = [e for e in body2.get("entries") or [] if isinstance(e, dict)]
            s.scratch["share_entries"] = rows
            lines.append(entries_view(rows))
        st3, body3 = ctx.call("GET", "/api/admin/library/identity-candidates", params={"domain_key": domain})
        if _ok(st3) and isinstance(body3, dict):
            s.scratch["share_identity"] = [c for c in body3.get("candidates") or [] if isinstance(c, dict)]
            lines.append(identity_candidates_view(body3))
        _fact(ctx, "\n".join(lines), st)
    else:
        _fact(ctx, "\n".join(lines + [f"（警告）{_detail(body, DERIVE_FAIL)}"]))


def _atlas_link_manual(ctx) -> None:
    """admin.js ``_libraryBindManualAtlasLink``（第 3 波・IK-0927）: 地図の概念を名前で［検索］→ 選んで［対応を付ける］。

    ``q`` で ``GET /atlas-links/nodes?domain_key=&q=`` を読み、``node_hint`` があればその概念へ
    ``POST /entries/{id}/atlas-links``（作られるのは manual_curation の候補）。``node_hint`` が無ければ検索だけ。
    結果の文（facts・成功の固定文・失敗の detail）は画面と同じく入口の下に 1 行。"""
    s = ctx.session
    entry = _resolve_entry(ctx)
    if entry is None or not entry.get("id"):
        return ctx.missing("entry")
    entry_id = str(entry["id"])
    domain = str(entry.get("domain_key") or "")
    q = str(ctx.args.get("q") or "").strip()
    st, body = ctx.call("GET", "/api/admin/library/atlas-links/nodes", params={"domain_key": domain, "q": q})
    if not _ok(st) or not isinstance(body, dict):
        _load_detail(ctx, entry_id, status=st,
                     atlas_manual={"facts": [_detail(body, ATLAS_MANUAL_SEARCH_FAIL)]})
        return
    nodes = [n for n in body.get("nodes") or [] if isinstance(n, dict)]
    s.scratch["share_atlas_nodes"] = nodes
    manual: dict = {"nodes": nodes, "facts": [str(f) for f in body.get("facts") or [] if f]}
    hint = ctx.args.get("node_hint")
    if hint in (None, ""):
        _load_detail(ctx, entry_id, status=st, atlas_manual=manual)
        return
    node = pick(nodes, hint, ("label", "region_label"))
    if node is None or not node.get("node_id"):
        manual["facts"] = [ATLAS_MANUAL_NEED_NODE]
        _load_detail(ctx, entry_id, status=st, atlas_manual=manual)
        return
    st2, body2 = ctx.main(json={"domain_key": domain, "node_id": node["node_id"]},
                          overrides={"entry_id": entry_id})
    manual["facts"] = [ATLAS_MANUAL_ADDED] if _ok(st2) else [_detail(body2, ATLAS_MANUAL_ADD_FAIL)]
    _load_detail(ctx, entry_id, status=st2, atlas_manual=manual)


# ---------------------------------------------------------------------------
# 深く検討の同一性リンク
# ---------------------------------------------------------------------------
def _component_pool(session: PersonaSession) -> list[dict]:
    """画面で選べる部品: グラフレビューのノード（ラベル + 代表要素）と原稿スタジオの理論コンポーネント一覧。"""
    pool: list[dict] = []
    for n in session.scratch.get("graph_nodes") or []:
        if isinstance(n, dict) and n.get("target"):
            pool.append({"id": n["target"], "name": " / ".join(n.get("labels") or []),
                         "document_id": session.latest("graph_documents")})
    for c in session.scratch.get("share_components") or []:
        if isinstance(c, dict) and c.get("id"):
            pool.append({"id": c["id"], "name": c.get("name") or "",
                         "document_id": ((c.get("source_scope") or {}).get("document_id") if isinstance(c.get("source_scope"), dict) else "") or ""})
    return pool


def identity_link_row(i: int, link: dict) -> str:
    """deliberation.js _identityLinkRowHtml の 1 行（第 3 波・IK-0916 / IK-0917 / IK-0918）。

    結んだ相手（部品側）は ``instance_type_label: instance_label｜instance_summary（論文『instance_document_title』）``
    の下に「入力: … ／ …」「出力: … ／ …」。要素型名・UUID は title 属性だけ（本文に出ない）。``provenance_label`` →
    ``confidence_label``（AI・照合由来の未確定だけ）→ 未確定の行だけ ``confirm_note``。旧サーバ（instance_* なし）では
    要素の型ラベルを「要素」、名前を「（名前を読めませんでした）」と出す（JS のフォールバックと同じ）。"""
    row = f"{i}. {_label(DLB_LINK_STATUS, link.get('status'))}"
    le = link.get("local_expression") if isinstance(link.get("local_expression"), dict) else {}
    expr = le.get("label") or le.get("notation") or ""
    if expr:
        row += f" {expr}"
    if link.get("shared_part_id"):
        row += f" 共通部品: {link.get('shared_part_name') or link.get('shared_part_id')}"
        if link.get("shared_part_summary"):
            row += f"｜{link['shared_part_summary']}"
    sub: list[str] = []
    if link.get("instance_element_type"):
        inst = f" {link.get('instance_type_label') or DLB_INSTANCE_TYPE_FALLBACK}: " \
               f"{link.get('instance_label') or DLB_INSTANCE_LABEL_FALLBACK}"
        if link.get("instance_summary"):
            inst += f"｜{link['instance_summary']}"
        if link.get("instance_document_title"):
            inst += f"（論文『{link['instance_document_title']}』）"
        row += inst
        ins = [str(x) for x in link.get("instance_inputs") or [] if x]
        outs = [str(x) for x in link.get("instance_outputs") or [] if x]
        if ins:
            sub.append("   入力: " + " ／ ".join(ins))
        if outs:
            sub.append("   出力: " + " ／ ".join(outs))
    tail = ""
    if link.get("reason"):
        tail += f" {link['reason']}"
    if link.get("provenance_label"):
        tail += f" 〔{link['provenance_label']}〕"
    if link.get("confidence_label"):
        tail += f" {link['confidence_label']}"
    pending = link.get("status") == "candidate"
    if sub:
        out = [row] + sub + (["  " + tail.strip()] if tail else [])
    else:
        out = [row + tail]
    if pending and link.get("confirm_note"):
        out.append(f"   {link['confirm_note']}")
    if pending:
        out.append("   ［確定］［却下］")
    return "\n".join(out)


def identity_section_view(element_label: str, links: list[dict], *, created: bool = False,
                          created_note: Optional[str] = None) -> str:
    """deliberation.js _identityLinksSectionHtml + _identityLinkRowHtml の写し。

    作成の後の注記（``#deliberation-identity-search-note``）は応答の ``facts`` の素通し（IK-0918・``_createIdentityLink``）。
    旧サーバ（facts なし）では従来の固定文。"""
    lines = [f"深く検討: {element_label}", DLB_SECTION_HEAD, f"（{DLB_SECTION_NOTE}）"]
    if not links:
        lines.append(DLB_NO_LINKS)
    for i, link in enumerate(links, 1):
        lines.append(identity_link_row(i, link))
    lines.append("［共通部品と結びつける］")
    if created_note:
        lines.append(created_note)
    elif created:
        lines.append(DLB_CREATED)
    return "\n".join(lines)


def create_outcome_note(body: Any) -> str:
    """deliberation.js ``_createIdentityLink``: ``result.facts`` を空白で連結して注記に置く（第 3 波・IK-0918）。
    facts の無い旧サーバの応答は空（呼び手が固定文へフォールバック）。"""
    if not isinstance(body, dict):
        return ""
    return " ".join(str(f) for f in body.get("facts") or [] if f)


def _load_element_links(ctx, *, status: Optional[int] = None, created: bool = False,
                        created_note: Optional[str] = None) -> None:
    s = ctx.session
    el = s.scratch.get("share_element") or {}
    params = {"document_id": el.get("document_id")} if el.get("document_id") else None
    st, body = ctx.call("GET", "/api/admin/deliberation/elements/{element_type}/{element_id}/identity-links",
                        params=params, overrides={"element_type": el.get("element_type") or "theory_component",
                                                  "element_id": el.get("element_id") or ""})
    if _ok(st) and isinstance(body, dict):
        links = [l for l in body.get("identity_links") or [] if isinstance(l, dict)]
        s.scratch["share_element_links"] = links
        _fact(ctx, identity_section_view(str(el.get("label") or ""), links, created=created,
                                    created_note=created_note),
              status if status is not None else st)


def _identity_open(ctx) -> None:
    s = ctx.session
    eid = str(ctx.args.get("element_id") or "").strip()
    doc = str(ctx.args.get("document_id") or "").strip()
    label = ""
    pool = _component_pool(s)
    if not eid and ctx.args.get("material_hint"):
        # 教材行 → グラフレビュー → ノード（admin-graph-review.js のノード一覧）
        st, body = ctx.call("GET", "/api/admin/materials")
        rows = [m for m in body or [] if isinstance(m, dict)] if _ok(st) and isinstance(body, list) else []
        mat = pick_material(rows, ctx.args.get("material_hint"), ctx.args.get("format_hint"))
        if mat is None or not mat.get("document_id"):
            return ctx.missing("material")
        doc = str(mat["document_id"])
        st, body = ctx.call("GET", "/api/admin/documents/{document_id}/component-graph", overrides={"document_id": doc})
        if not _ok(st) or not isinstance(body, dict):
            return
        graph = body.get("graph") if isinstance(body.get("graph"), dict) else body
        pool = []
        for n in graph.get("nodes") or [] if isinstance(graph, dict) else []:
            if isinstance(n, dict) and target_of_node(n):
                # ノード名は画面と同じ lsGraphSemanticLabel の 1 つだけ（第 25 周 share H4: label と visual_label を
                # " / " で連結し、ノード間の区切りも " / " だったので 1 ノードが 2 つに見えた）
                pool.append({"id": target_of_node(n), "document_id": doc, "name": graph_node_label(n)})
        s.remember("graph_documents", doc)
        s.scratch["share_material_title"] = str(mat.get("title") or mat.get("filename") or "")
        hint = str(ctx.args.get("component_hint") or "").strip()
        if hint and pick(pool, hint, ("name",)) is None:
            # グラフのノードに無い部品は、同じ教材行の「検出要素」（要素インベントリ）から選ぶ（理論コンポーネントの行。
            # 深く検討はその UUID で開く）— 主グラフは段の集約で、個々の部品名はノードに出ない
            st, inv = ctx.call("GET", "/api/admin/deliberation/documents/{document_id}/elements",
                               overrides={"document_id": doc})
            comps = [e for e in (inv.get("elements") or [] if _ok(st) and isinstance(inv, dict) else [])
                     if isinstance(e, dict) and e.get("element_type") == "theory_component" and e.get("element_id")]
            inv_pool = [{"id": str(e["element_id"]), "document_id": doc, "name": str(e.get("label") or "")}
                        for e in comps]
            if pick(inv_pool, hint, ("name",)) is not None:
                _note(s, "share: グラフのノードに部品名が無いので、同じ教材行の［検出要素］の理論コンポーネントから選んだ")
                pool = inv_pool
            elif inv_pool:
                # 第 25 周 H24: 合わないときの選択肢は［検出要素］の理論コンポーネント（深く検討で開ける部品）だけ。
                # グラフのノード（式の詳細層の「制約を適用」等）は深く検討の対象ではない
                pool = inv_pool
    if not eid:
        hit = pick(pool, ctx.args.get("component_hint"), ("name",)) if pool else None
        if hit is None and pool and ctx.args.get("component_hint"):
            # 第 25 周 H24: 選べなかったら深く検討の対象を前の部品のまま残さない（続く検索・作成が前の論文の部品に向いた）
            s.scratch.pop("share_element", None)
            s.scratch.pop("share_element_links", None)
            _fact(ctx, f"「{ctx.args.get('component_hint')}」に合う部品が無い。［検出要素］の理論コンポーネント:\n"
                  + "\n".join(f"- {p.get('name') or ''}" for p in pool[:30]))
            return ctx.missing("component")
        if hit is None and s.latest("components"):
            hit = {"id": s.latest("components"), "name": "", "document_id": s.latest("graph_documents")}
        if hit is None:
            return ctx.missing("component")
        eid, doc, label = str(hit["id"]), doc or str(hit.get("document_id") or ""), str(hit.get("name") or "")
    # 画面は深く検討を開いて内訳を読む（agent 側 ID も受ける）→ 正準の要素 ID で同一性リンクを読む
    st, body = ctx.call("GET", "/api/admin/deliberation/elements/{element_type}/{element_id}/overview",
                        params={"document_id": doc} if doc else None,
                        overrides={"element_type": "theory_component", "element_id": eid})
    if not _ok(st) or not isinstance(body, dict):
        return
    ref = body.get("ref") if isinstance(body.get("ref"), dict) else {}
    bd = body.get("decomposition") if isinstance(body.get("decomposition"), dict) else {}
    s.scratch["share_element"] = {
        "element_type": ref.get("element_type") or "theory_component",
        "element_id": ref.get("element_id") or eid,
        "document_id": ref.get("document_id") or doc,
        "label": bd.get("label") or label,
    }
    _load_element_links(ctx)


def shared_part_domain_line(body: dict) -> str:
    """deliberation.js _sharedPartDomainSelectHtml（IK-0890・第 2 波）: サーバが ``domains`` を返したときだけ
    「探す分野:」の選択欄。表示名は ``domain_label`` だけ（無ければ「名前の登録が無い分野」）。"""
    domains = [d for d in body.get("domains") or [] if isinstance(d, dict)]
    if not domains:
        return ""
    current = str(body.get("domain_key") or "")
    names = [str(d.get("domain_label") or DLB_DOMAIN_NO_NAME) for d in domains]
    chosen = next((str(d.get("domain_label") or DLB_DOMAIN_NO_NAME) for d in domains
                   if str(d.get("domain_key") or "") == current), "") if current else ""
    return f"探す分野: [{chosen or DLB_DOMAIN_PLACEHOLDER} ▼]（選択肢: {' / '.join(names)}）"


def _shared_part_search(ctx) -> None:
    s = ctx.session
    el = s.scratch.get("share_element") or {}
    if not el.get("element_id"):
        return _blocked(ctx, "deliberation_element", "（ハーネス: 深く検討で開いている部品がまだ無いので、共通部品は"
                        "探していない。これは画面の文ではない）")
    params: dict = {}
    if el.get("document_id"):
        params["document_id"] = el["document_id"]
    if str(ctx.args.get("q") or "").strip():
        params["q"] = str(ctx.args["q"]).strip()
        s.scratch["share_part_query"] = params["q"]
    elif getattr(ctx.action, "id", "") == "admin.deliberation.shared_part_domain" and s.scratch.get("share_part_query"):
        params["q"] = s.scratch["share_part_query"]  # 分野を選び直すと画面は同じ検索文で探し直す
    if s.scratch.get("share_part_domain"):
        params["domain_key"] = s.scratch["share_part_domain"]  # IK-0890: 教員の選択はサーバの推定より優先
    st, body = ctx.main(params=params or None, overrides={"element_type": el.get("element_type") or "theory_component",
                                                          "element_id": el["element_id"]})
    if _ok(st) and isinstance(body, dict):
        hits = [e for e in body.get("entries") or [] if isinstance(e, dict)]
        s.scratch["share_part_hits"] = hits
        s.scratch["share_part_domains"] = [d for d in body.get("domains") or [] if isinstance(d, dict)]
        domain_line = shared_part_domain_line(body)
        if not hits:
            _fact(ctx, "\n".join(x for x in (domain_line,
                                               "共通部品の検索結果: " + str(body.get("note") or DLB_SEARCH_EMPTY)) if x))
            return
        lines = ([domain_line] if domain_line else []) + ["共通部品の検索結果:"]
        for i, e in enumerate(hits, 1):
            aliases = [a for a in e.get("aliases") or [] if a]
            lines.append(f"{i}. {e.get('name') or '(無名)'}" + (f"（別名: {'、'.join(aliases)}）" if aliases else "")
                         + (f" — {e['summary']}" if e.get("summary") else "") + " ［この部品と結びつける（候補を作成）］")
        _fact(ctx, "\n".join(lines))
    elif st is not None:
        _fact(ctx, "共通部品の検索結果: " + _detail(body, DLB_SEARCH_FAIL))


def _shared_part_domain(ctx) -> None:
    """deliberation.js _bindSharedPartDomainSelect: 分野の選択欄を変えると ``domain_key`` 付きで探し直す。"""
    s = ctx.session
    domains = list(s.scratch.get("share_part_domains") or [])
    if not domains:
        return _blocked(ctx, "shared_part_domain_select", "（ハーネス: 共通部品の検索結果に「探す分野:」の選択欄が"
                        "出ていないので、分野は選んでいない。これは画面の文ではない）")
    hit = pick(domains, ctx.args.get("domain_hint"), ("domain_label", "domain_key"))
    if hit is None or not hit.get("domain_key"):
        return ctx.missing("shared_part_domain")
    s.scratch["share_part_domain"] = str(hit["domain_key"])
    _shared_part_search(ctx)


def _identity_link_create(ctx) -> None:
    s = ctx.session
    el = s.scratch.get("share_element") or {}
    hit = pick(list(s.scratch.get("share_part_hits") or []), ctx.args.get("part_hint"), ("name",))
    if not el.get("element_id") or hit is None or not hit.get("shared_part_id"):
        return ctx.missing("shared_part")
    payload = {"instance_element_type": el.get("element_type") or "theory_component",
               "instance_element_id": el["element_id"], "shared_part_id": hit["shared_part_id"],
               "reason": str(ctx.args.get("reason") or "").strip()}
    if el.get("document_id"):
        payload["document_id"] = el["document_id"]
    st, body = ctx.main(json=payload)
    if _ok(st):
        _load_element_links(ctx, status=st, created=True, created_note=create_outcome_note(body))
    else:
        _fact(ctx, f"{hit.get('name') or ''}: {_detail(body, DLB_CREATE_FAIL)}")


def _dlb_link_decide(ctx) -> None:
    s = ctx.session
    rows = [l for l in s.scratch.get("share_element_links") or [] if isinstance(l, dict) and l.get("status") == "candidate"]
    row = pick(rows, ctx.args.get("link_hint"), ("shared_part_name",))
    if row is None or not row.get("id"):
        return ctx.missing("identity_link")
    st, body = _decide_link(ctx, str(row["id"]), str(ctx.args.get("decision") or ""), screen="deliberation")
    if _ok(st):
        _load_element_links(ctx, status=st)
    else:
        _fact(ctx, _detail(body, DLB_DECIDE_FAIL))


# ---------------------------------------------------------------------------
# 原稿スタジオ「説明・承認の共有」（C層）
# ---------------------------------------------------------------------------
LS_COURSE_SELECT_EMPTY = "コースを選択してください"  # admin-lecture-studio.js（コース未選択の一覧欄）


def _course_for_studio(ctx) -> str:
    """原稿スタジオのコース選択（admin-lecture-studio.js lsLoadCourses: ``/api/admin/courses`` の行だけが選べる）。

    第 25 周 share H5: 学生用の固定コースをそのまま使い、教員が開けないコースで 404 になっていた。指定（course_id /
    course_hint）・セッションのコース・固定コースは、教員の一覧にあるときだけ使う。一覧が空なら画面のコース選択は
    空のままで、何も開けない（precondition:studio_course）。
    """
    s = ctx.session
    st, body = ctx.call("GET", "/api/admin/courses")
    rows = [c for c in body or [] if isinstance(c, dict) and c.get("id")] if _ok(st) and isinstance(body, list) else []
    if not rows:
        _blocked(ctx, "studio_course", f"原稿スタジオ: コースの選択肢がない（{LS_COURSE_SELECT_EMPTY}）")
        return ""
    ids = [str(c["id"]) for c in rows]
    hint = str(ctx.args.get("course_hint") or "").strip()
    if hint:
        hit = pick(rows, hint, ("title",))
        if hit is not None:
            return str(hit["id"])
    for cand in (ctx.args.get("course_id"), s.course_id, s.scratch.get("pinned_course_id")):
        c = str(cand or "").strip()
        if c and c in ids:
            return c
        if c and len(c) >= 8 and any(i.startswith(c) for i in ids):  # 短縮 id（campaign の 1bad7d9f 等）
            return next(i for i in ids if i.startswith(c))
    _note(s, f"runner_default: 原稿スタジオのコースを一覧の先頭「{rows[0].get('title', '')}」にした"
             "（指定・固定のコースはこの教員のコース選択に無い）")
    return ids[0]


def explanation_citation_lines(exp: dict) -> list[str]:
    """admin-lecture-studio.js ``lsRenderExplanationCitations``（第 3 波・IK-0924）: 引用は件数ではなく
    「『コース題名』（意図） 引用者・日付」の列挙。無ければ何も出さない（``citation_count`` は描かない）。"""
    items = [c for c in exp.get("citations") or [] if isinstance(c, dict)]
    if not items:
        return []
    out = [f"   {EXPLANATION_AXIS_LABELS['citation']}:"]
    for c in items:
        course = f"『{c['course_title']}』" if c.get("course_title") else ""
        intent = f"（{c['citation_intent_label']}）" if c.get("citation_intent_label") else ""
        who = "・".join(v for v in (str(c.get("citer_name") or ""), str(c.get("cited_at") or "")[:10]) if v)
        out.append(f"   - {course}{intent}" + (f" {who}" if who else ""))
    return out


def explanation_card_lines(i: int, exp: dict) -> list[str]:
    """admin-lecture-studio.js lsRenderExplanationCard の写し（第 3 波・IK-0924 / IK-0925）。

    頭 = 「教員の支持: {endorsement_label}」、状態行 = 「学生への公開: {査読の語} / 共有: ON|OFF」（引用の件数は出さない）
    → サーバの事実文（body_note / delivery_note / support_note）→ 引用の列挙 → 根拠 claim → 折りたたみ →
    ``can_edit`` が偽なら ``edit_note`` → ボタン（［学生に公開してよいと査読する］［共有ON/OFF］は ``can_edit`` が真の行だけ）。
    旧サーバ（can_edit なし）は JS と同じく偽とみなす。"""
    kind = EX_STANDARD if exp.get("kind") == "standard" else f"教員: {exp.get('author_name') or '不明'}"
    review = EXPLANATION_REVIEW_STATUS_LABELS.get(str(exp.get("review_status") or ""),
                                                  EXPLANATION_REVIEW_STATUS_LABELS["teacher_review_required"])
    lines = [f"{i}. [{kind}] {exp.get('title') or '(無題)'} — {EXPLANATION_AXIS_LABELS['support']}: "
             f"{exp.get('endorsement_label') or ''}",
             f"   {exp.get('body') or ''}",
             f"   {EXPLANATION_AXIS_LABELS['review']}: {review} / 共有: {'ON' if exp.get('shared') else 'OFF'}"]
    lines += [f"   {exp[k]}" for k in ("body_note", "delivery_note", "support_note") if exp.get(k)]
    lines += explanation_citation_lines(exp)
    claims = [b for b in exp.get("backing_claims") or []]
    if claims:
        lines.append("   根拠claim（AI候補。確定・却下は教員のみ。却下しても記録は保持されます）: "
                     + ", ".join(str((b if isinstance(b, dict) else {"claim_id": b}).get("claim_id") or "") for b in claims))
    # 第 25 周 H28: カードの折りたたみ「支持した教員の一覧」（開くと読み込む — admin.explanations.endorsers）
    lines.append("   ▸ 支持した教員の一覧（開くと読み込みます）")
    can_edit = bool(exp.get("can_edit"))
    if not can_edit and exp.get("edit_note"):
        lines.append(f"   {exp['edit_note']}")
    edit = (f"［{EXPLANATION_ACTION_LABELS['approve']}］［{'共有OFF' if exp.get('shared') else '共有ON'}］"
            if can_edit else "")
    lines.append(f"   ［{EXPLANATION_ACTION_LABELS['endorse']}（{' / '.join(EXPLANATION_SUPPORT_LEVEL_LABELS.values())}"
                 f"・専門タグ）］［{EXPLANATION_ACTION_LABELS['revoke']}］{edit}［別コースで引用］")
    return lines


def _reload_explanations(ctx, *, status: Optional[int] = None, head: str = "") -> None:
    s = ctx.session
    comp = s.scratch.get("share_component") or {}
    st, body = ctx.call("GET", "/api/admin/theory-components/{component_id}/explanations",
                        overrides={"component_id": str(comp.get("id") or "")})
    lines = [LS_ENDORSE_HEAD.format(name=comp.get("name") or "無題"), LS_ENDORSE_SUB]
    if head:
        lines.append(head)
    if _ok(st) and isinstance(body, list):
        items = [e for e in body if isinstance(e, dict)]
        s.scratch["share_explanations"] = items
        # admin-lecture-studio.js lsResponseJsonOrDetail / lsReloadExplanations（IK-0891・第 2 波）: 一覧の
        # X-Explanation-Fact ヘッダ（percent-encode）の事実文を一覧の上に描く
        fact = explanation_fact_header(getattr(ctx.client, "last_headers", None))
        if fact:
            lines.append(fact)
        if not items:
            lines.append(LS_NO_EXPLANATIONS)
        for i, e in enumerate(items, 1):
            lines.extend(explanation_card_lines(i, e))
    else:
        s.scratch["share_explanations"] = []
        # 画面（lsReloadExplanations の catch）はサーバの detail（日本語の事実文）、無ければ「読み込みに失敗しました」
        detail = body.get("detail") if isinstance(body, dict) else None
        lines.append(detail if isinstance(detail, str) and detail else LS_LOAD_FAIL)
    lines += ["独自解釈を追加 ［この説明を追加］", "受講者質問からコンポーネント候補を生成 (AI候補→教員確定) ［候補を生成］"]
    _fact(ctx, "\n".join(lines), status if status is not None else st)


def explanation_fact_header(headers: Any) -> str:
    """``X-Explanation-Fact``（percent-encode）を読む。読めなければ空。"""
    from urllib.parse import unquote

    if not isinstance(headers, dict):
        return ""
    raw = headers.get("x-explanation-fact") or headers.get("X-Explanation-Fact") or ""
    try:
        return unquote(str(raw)) if raw else ""
    except Exception:  # noqa: BLE001 — 画面も decode に失敗したら空にする
        return ""


def _explanations_open(ctx) -> None:
    s = ctx.session
    cid = _course_for_studio(ctx)
    if not cid:
        return  # 画面のコース選択が空（_course_for_studio が precondition と画面の文を残した）
    st, body = ctx.call("GET", "/api/admin/courses/{course_id}/theory-components", overrides={"course_id": cid})
    if not _ok(st) or not isinstance(body, list):
        return
    comps = [c for c in body if isinstance(c, dict) and c.get("id")]
    s.scratch["share_components"] = comps
    s.scratch["share_course_id"] = cid
    comp, many = pick_component(comps, ctx.args.get("component_hint"), linked=_linked_component_ids(s))
    if many:
        # 第 25 周 H29: 部分一致が複数 — runner が先頭を選ばず、画面の部品一覧（名前）から選び直させる
        _fact(ctx, harness_text(f"「{ctx.args.get('component_hint') or ''}」は部品一覧の複数の名前に当たるので、"
                                "どれも開いていない")
              + "\n部品一覧（名前で選ぶ）:\n" + "\n".join(f"- {c.get('name') or ''}" for c in many[:20]))
        return ctx.missing("component")
    if comp is None:
        names = " / ".join(str(c.get("name") or "") for c in comps[:20])
        _fact(ctx, f"理論コンポーネント一覧に「{ctx.args.get('component_hint') or ''}」が無い。一覧: {names or '（空）'}")
        return
    s.scratch["share_component"] = comp
    _reload_explanations(ctx)


def _explanation_target(ctx) -> Optional[dict]:
    return pick(list(ctx.session.scratch.get("share_explanations") or []), ctx.args.get("explanation_hint"),
                ("title", "author_name", "body"))


def _explanations_add(ctx) -> None:
    s = ctx.session
    comp = s.scratch.get("share_component") or {}
    if not comp.get("id"):
        return ctx.missing("component")
    body_text = str(ctx.args.get("body") or "")
    if not body_text.strip():
        return _blocked(ctx, "body", LS_ADD_NEED_BODY)
    # admin-lecture-studio.js lsAddExplanation: course_id は書いたコースの出所（IK-0861）
    st, body = ctx.main(json={"title": str(ctx.args.get("title") or ""), "body": body_text, "shared": False,
                              "course_id": str(s.scratch.get("share_course_id") or "")},
                        overrides={"component_id": str(comp["id"])})
    if _ok(st):
        _reload_explanations(ctx, status=st, head=LS_ADDED)
    else:
        # 第 2 波（IK-0891）: 画面はサーバの detail（日本語の事実文）を出す。無ければ固定文
        detail = body.get("detail") if isinstance(body, dict) else None
        _fact(ctx, detail if isinstance(detail, str) and detail else LS_ADD_FAIL)


def _call_and_reload(ctx, method: str, template: str, exp: dict, payload: Optional[dict], ok_key: str) -> None:
    st, body = ctx.call(method, template, json=payload, overrides={"explanation_id": str(exp["id"])})
    if _ok(st):
        _reload_explanations(ctx, status=st, head=LS_OK[ok_key])
    else:
        _fact(ctx, _detail(body, LS_CALL_FAIL))


def _explanations_update(ctx) -> None:
    exp = _explanation_target(ctx)
    if exp is None or not exp.get("id"):
        return ctx.missing("explanation")
    what = str(ctx.args.get("what") or "").strip().lower()
    if not exp.get("can_edit"):
        # 第 3 波（IK-0925）: can_edit が偽の行には［学生に公開してよいと査読する］［共有ON/OFF］が出ない
        return _blocked(ctx, "explanation_can_edit", harness_text(
            "この説明のカードには［学生に公開してよいと査読する］［共有ON/OFF］のボタンが出ていないので押していない"
            + (f"。カードの文: {exp['edit_note']}" if exp.get("edit_note") else "")))
    if what in ("approve", "査読承認"):
        _call_and_reload(ctx, "PATCH", "/api/admin/explanations/{explanation_id}", exp,
                         {"review_status": "teacher_approved"}, "approve")
    elif what in ("share_on", "share_off", "共有on", "共有off", "share"):
        turn_on = what in ("share_on", "共有on") or (what == "share" and not exp.get("shared"))
        _call_and_reload(ctx, "PATCH", "/api/admin/explanations/{explanation_id}", exp, {"shared": turn_on}, "share")
    else:
        return ctx.missing("what")


def _explanations_endorse(ctx) -> None:
    exp = _explanation_target(ctx)
    if exp is None or not exp.get("id"):
        return ctx.missing("explanation")
    level = str(ctx.args.get("level") or "endorsed").strip()
    _call_and_reload(ctx, "POST", "/api/admin/explanations/{explanation_id}/endorse", exp,
                     {"level": level, "expertise_tag": str(ctx.args.get("expertise_tag") or ""), "note": ""}, "endorse")


def _explanations_revoke(ctx) -> None:
    """説明カードの［支持を取り消す］（第 3 波・IK-0924 の語。``DELETE /explanations/{id}/endorse``）。"""
    exp = _explanation_target(ctx)
    if exp is None or not exp.get("id"):
        return ctx.missing("explanation")
    _call_and_reload(ctx, "DELETE", "/api/admin/explanations/{explanation_id}/endorse", exp, None, "revoke")


#: admin-lecture-studio.js EXPLANATION_SUPPORT_LEVEL_LABELS の逐語ミラー
EXPLANATION_SUPPORT_LEVEL_LABELS = {"provisional": "暫定の支持", "endorsed": "支持", "strong": "強い支持"}
ENDORSERS_EMPTY = "支持した教員はまだいません。"
ENDORSERS_FAIL = "支持した教員の一覧の取得に失敗しました"


def endorsers_view(title: str, body: Any) -> str:
    """admin-lecture-studio.js lsLoadEndorsers の写し（名前・専門タグ・段階・著者自身の印・support_note）。"""
    lines = [f"「{title or '(無題)'}」の支持した教員の一覧:"]
    items = [e for e in (body.get("endorsements") if isinstance(body, dict) else None) or [] if isinstance(e, dict)]
    if not items:
        lines.append(ENDORSERS_EMPTY)
        return "\n".join(lines)
    for en in items:
        tag = f"（専門: {en['expertise_tag']}）" if en.get("expertise_tag") else ""
        level = EXPLANATION_SUPPORT_LEVEL_LABELS.get(str(en.get("level") or ""), EXPLANATION_SUPPORT_LEVEL_LABELS["endorsed"])
        role = f" {en['role_label']}" if en.get("role_label") else ""
        lines.append(f"- {en.get('endorser_name') or '不明'}{tag} [{level}]{role}")
    if isinstance(body, dict) and body.get("support_note"):
        lines.append(str(body["support_note"]))
    return "\n".join(lines)


def _explanations_endorsers(ctx) -> None:
    exp = _explanation_target(ctx)
    if exp is None or not exp.get("id"):
        return ctx.missing("explanation")
    st, body = ctx.call("GET", "/api/admin/explanations/{explanation_id}/endorsements",
                        overrides={"explanation_id": str(exp["id"])})
    if not _ok(st):
        return _fact(ctx, ENDORSERS_FAIL)
    _fact(ctx, endorsers_view(str(exp.get("title") or ""), body), st)


def _explanations_cite(ctx) -> None:
    s = ctx.session
    exp = _explanation_target(ctx)
    if exp is None or not exp.get("id"):
        return ctx.missing("explanation")
    comp = s.scratch.get("share_component") or {}
    st, body = ctx.call("GET", "/api/admin/courses")
    if not _ok(st) or not isinstance(body, list):
        _fact(ctx, "コース一覧の取得に失敗しました")
        return
    # lsOpenCiteSelector: 引用元と同じコースを除き、owner / editor のコースだけ
    options = [c for c in body if isinstance(c, dict) and str(c.get("id")) != str(comp.get("course_id") or "")
               and c.get("role") in ("owner", "editor")]
    if not options:
        return _blocked(ctx, "cite_target", LS_NO_CITE_TARGET)
    target = pick(options, ctx.args.get("course_hint"), ("title",))
    if target is None:
        return _blocked(ctx, "cite_target", "引用先コース: " + " / ".join(str(c.get("title") or c.get("id"))
                                                                       for c in options))
    payload: dict = {"citing_course_id": str(target["id"])}
    intent = str(ctx.args.get("citation_intent") or "").strip()
    intent = next((k for k, v in LS_CITE_INTENT_LABELS.items() if intent in (k, v)), intent)
    if intent in ("記録しない",):
        intent = ""
    if intent:
        payload["citation_intent"] = intent
    _call_and_reload(ctx, "POST", "/api/admin/explanations/{explanation_id}/cite", exp, payload, "cite")


# ---------------------------------------------------------------------------
# 教材の行「共有」
# ---------------------------------------------------------------------------
def _share_modal_view(title: str, perms: list[dict], groups: list[dict], head: str = "") -> str:
    lines = [DS_HEAD, f"教材「{title}」の設定です。"]
    if head:
        lines.append(head)
    lines += ["解析成果のグループ共有", f"（{DS_SHARE_NOTE}）", "現在の共有設定"]
    if not perms:
        lines.append(DS_EMPTY)
    for p in perms:
        # renderDocumentGroupPerms（第 1 周の是正後）: 権限はサーバの permission_label（旧サーバは内部値）
        lines.append(f"- {p.get('group_name') or p.get('group_id') or ''} "
                     f"{p.get('permission_label') or p.get('permission') or ''} ［削除］")
    assigned = {str(p.get("group_id")) for p in perms}
    opts = [f"{g.get('name') or ''}" + (" (設定済み)" if str(g.get("id")) in assigned else "") for g in groups]
    lines.append("グループを追加: ［グループを選択...］ " + (" / ".join(opts) if opts else "（選べるグループが無い）")
                 + " ［閲覧・引用できる / 編集・再解析できる］ ［追加］")
    return "\n".join(lines)


def _load_share_modal(ctx, *, status: Optional[int] = None, head: str = "") -> None:
    s = ctx.session
    doc = s.scratch.get("share_document") or {}
    st1, perms = ctx.call("GET", "/api/admin/documents/{document_id}/groups", overrides={"document_id": doc.get("document_id") or ""})
    st2, groups = ctx.call("GET", "/api/groups")
    if not (_ok(st1) and _ok(st2)):
        _fact(ctx, DS_HEAD + "\n" + DS_LOAD_FAIL, status)
        return
    perm_rows = [p for p in perms or [] if isinstance(p, dict)] if isinstance(perms, list) else []
    group_rows = [g for g in groups or [] if isinstance(g, dict)] if isinstance(groups, list) else []
    s.scratch["share_groups"] = group_rows
    _fact(ctx, _share_modal_view(str(doc.get("title") or ""), perm_rows, group_rows, head),
          status if status is not None else st1)


def _document_share_open(ctx) -> None:
    s = ctx.session
    doc_id = str(ctx.args.get("document_id") or "").strip()
    title = ""
    if not doc_id or ctx.args.get("material_hint"):
        st, body = ctx.call("GET", "/api/admin/materials")
        rows = [m for m in body or [] if isinstance(m, dict)] if _ok(st) and isinstance(body, list) else []
        hit = pick_material(rows, ctx.args.get("material_hint"), ctx.args.get("format_hint")) if rows else None
        if hit is None and not doc_id:
            return ctx.missing("material")
        if hit is not None:
            doc_id, title = str(hit.get("document_id") or doc_id), str(hit.get("title") or hit.get("filename") or "")
    if not doc_id:
        return ctx.missing("document_id")
    s.scratch["share_document"] = {"document_id": doc_id, "title": title}
    s.remember("documents", doc_id)
    _load_share_modal(ctx)


def _document_share_add(ctx) -> None:
    s = ctx.session
    doc = s.scratch.get("share_document") or {}
    if not doc.get("document_id"):
        return ctx.missing("document_share")
    groups = [g for g in s.scratch.get("share_groups") or [] if isinstance(g, dict)]
    hint = ctx.args.get("group_hint")
    grp = pick(groups, hint, ("name",)) if hint else None
    if grp is None:
        return _blocked(ctx, "group", DS_NEED_GROUP)
    perm = "editor" if str(ctx.args.get("permission") or "").strip() == "editor" else "viewer"
    st, body = ctx.main(json={"group_id": str(grp["id"]), "permission": perm},
                        overrides={"document_id": str(doc["document_id"])})
    if _ok(st):
        _load_share_modal(ctx, status=st, head=DS_ADDED)
    else:
        # 画面（addDocumentShare）は detail をそのまま状態欄に出す
        _fact(ctx, f"（エラー）{_detail(body, DS_ADD_FAIL)}")


# ---------------------------------------------------------------------------
# 学生: 部品チップの「共通部品として」・説明バージョン
# ---------------------------------------------------------------------------
def shared_pane_view(body: dict) -> str:
    """app.js renderComponentContextPanel のタブ + renderContextSharedPane の写し。"""
    instance = body.get("instance") if isinstance(body.get("instance"), dict) else {}
    comp = instance.get("component") if isinstance(instance.get("component"), dict) else {}
    sp = body.get("shared_part")
    tabs = f"タブ: ［{SP_TAB_INSTANCE}］" + (f"［{SP_TAB_SHARED}］" if sp else "")
    lines = [f"部品: {comp.get('label') or ''}", tabs]
    if not sp:
        lines.append(f"（「{SP_TAB_SHARED}」のタブは出ていない）")
        if body.get("shared_part_note"):
            # IK-0863: 「共通部品として」が無いときはサーバの事実文がパネルの末尾に出る（旧サーバは無い）
            lines.append(str(body["shared_part_note"]))
        return "\n".join(lines)
    from uxsim.runner.state import shared_pane_lines  # renderContextSharedPane の写しの正本（第 3 波）

    pane = shared_pane_lines(sp) if isinstance(sp, dict) else []
    lines.append(f"「{SP_TAB_SHARED}」:")
    lines.extend(pane or [SP_EMPTY])
    return "\n".join(lines)


def _component_target(ctx) -> str:
    s = ctx.session
    cid = str(ctx.args.get("component_id") or "").strip()
    ref = cid or str(ctx.args.get("element_ref") or "").strip()
    if ref:
        from uxsim.runner.state import material_anchors  # 遅延 import（state の ⚓ 一覧の正本）

        m = re.fullmatch(r"(?:⚓\s*)?(\d+)", ref)
        hit = next((a for a in material_anchors(s.material) if (m and a["no"] == int(m.group(1))) or a["id"] == ref), None)
        if hit is not None:
            return hit["id"] if hit.get("kind") in ("component", "theory_component") else ""
        if m:
            return ""
        return cid
    return str(s.scratch.get("component_center") or s.latest("components") or "")


def _component_shared_part(ctx) -> None:
    s = ctx.session
    cid = _component_target(ctx)
    if not cid:
        return ctx.missing("component_anchor")
    status, body = ctx.main(overrides={"component_id": cid},
                            params={"topic_id": s.topic_id} if getattr(s, "topic_id", None) else None)
    if _ok(status) and isinstance(body, dict):
        s.scratch["component_center"] = cid
        _fact(ctx, shared_pane_view(body))


def explanations_view(body: dict) -> str:
    """app.js showComponentExplanations の写し。"""
    items = [e for e in body.get("explanations") or [] if isinstance(e, dict)]
    if not items:
        return str(body.get("note") or EX_EMPTY)  # IK-0861: 空の理由はサーバの note（旧サーバは固定文）
    lines = []
    for e in items:
        who = EX_STANDARD if e.get("kind") == "standard" else f"教員: {e.get('author_name') or '不明'}"
        lines.append(who + (f" ・ {e['endorsement_label']}" if e.get("endorsement_label") else ""))
        if e.get("title"):
            lines.append(str(e["title"]))
        lines.append(str(e.get("body") or ""))
    return "\n".join(lines)


def _component_explanations(ctx) -> None:
    s = ctx.session
    cid = _component_target(ctx)
    if not cid:
        return ctx.missing("component_anchor")
    from uxsim.runner.state import learner_focus_params

    # app.js showComponentExplanations（第 2 波）: ?topic_id=&document_id=（焦点論文）を付ける
    status, body = ctx.main(params=learner_focus_params(s) or None, overrides={"component_id": cid})
    if _ok(status) and isinstance(body, dict):
        _fact(ctx, explanations_view(body))
    elif status is not None:
        # 画面は応答が ok でなければ固定文 1 行。ただし 404 は（"Course not found" を除き）サーバの detail
        # （このコースの教材に無い部品 — IK-0861 の是正後の showComponentExplanations）
        detail = body.get("detail") if isinstance(body, dict) else None
        _fact(ctx, detail if (status == 404 and isinstance(detail, str) and detail and detail != "Course not found")
              else EX_FAIL)


# ---------------------------------------------------------------------------
# 記号の「直前の定義」（学生の教材 — app.js openSymbolLookup / renderSymbolLookupPopover / symbolTextFromToken）
# ---------------------------------------------------------------------------
# app.js SYMBOL_TOKEN_RE（基底の文字が 1〜3 文字のラテン文字・ギリシャ文字・∂∇ℏÅ のときだけタップの対象）
SYMBOL_TOKEN_RE = re.compile(r"^[A-Za-zͰ-Ͽ∂∇ℏÅ]{1,3}$")
SYMBOL_NOT_FOUND = "この論文には、この記号の記述が見つかりませんでした。"  # app.js の facts が空のときの固定文
SYMBOL_LHS_NOTE = "symbol_lhs:"  # runner_note の印（審判 A9 が読む — 教材の式の左辺に現れる記号）
_GREEK = {"alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ϵ", "varepsilon": "ε", "zeta": "ζ",
          "eta": "η", "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν",
          "xi": "ξ", "pi": "π", "varpi": "ϖ", "rho": "ρ", "varrho": "ϱ", "sigma": "σ", "varsigma": "ς", "tau": "τ",
          "upsilon": "υ", "phi": "ϕ", "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω", "Gamma": "Γ",
          "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ", "Upsilon": "Υ",
          "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω", "partial": "∂", "nabla": "∇", "hbar": "ℏ", "ell": "ℓ"}
_FONT_CMDS = ("mathbf", "mathrm", "mathcal", "mathit", "mathsf", "mathtt", "mathbb", "mathfrak", "boldsymbol",
              "bm", "text", "textrm", "operatorname", "hat", "tilde", "bar", "vec", "dot", "ddot", "widetilde",
              "widehat", "overline")
_SPACING = re.compile(r"\\[!,;:> ]|\\quad|\\qquad")


def _tex_group(src: str, i: int) -> tuple[str, int]:
    """``src[i]`` から 1 つの引数（{...} / 制御語 / 1 文字）を取り出す。"""
    while i < len(src) and src[i] == " ":
        i += 1
    if i >= len(src):
        return "", i
    if src[i] == "{":
        depth, j = 0, i
        while j < len(src):
            if src[j] == "{":
                depth += 1
            elif src[j] == "}":
                depth -= 1
                if depth == 0:
                    return src[i + 1:j], j + 1
            j += 1
        return src[i + 1:], len(src)
    if src[i] == "\\":
        m = re.match(r"\\[A-Za-z]+|\\.", src[i:])
        tok = m.group(0) if m else "\\"
        if tok in _TEX_ARG_COMMANDS:
            # 第 25 周 H26: 波括弧の無い添字の書体命令（``_\mathrm{BC}`` / ``_\rm X``）は引数ごと 1 つの添字。
            # 命令だけを取ると添字が空になり ``\mathcal{L}_\mathrm{BC}`` が ``L`` に落ちて索引に載らなかった
            arg, j = _tex_group(src, i + len(tok))
            return tok + "{" + arg + "}", j
        return tok, i + len(tok)
    return src[i], i + 1


#: 引数を 1 つ取る書体・文字命令（添字の位置で波括弧なしに書かれても引数ごと読む）
_TEX_ARG_COMMANDS = frozenset({"\\mathrm", "\\rm", "\\mathbf", "\\bf", "\\mathit", "\\it", "\\mathcal",
                               "\\cal", "\\mathsf", "\\mathtt", "\\mathbb", "\\text", "\\textrm",
                               "\\textit", "\\textbf", "\\operatorname", "\\boldsymbol", "\\bm"})


def _tex_plain(src: str) -> str:
    """KaTeX が描いたときの文字（textContent）に近い平文: 書体命令は中身だけ・ギリシャ文字は字に・空白命令と括弧は落とす。"""
    out, i = [], 0
    src = _SPACING.sub("", src)
    while i < len(src):
        c = src[i]
        if c == "\\":
            m = re.match(r"\\([A-Za-z]+)", src[i:])
            if not m:
                i += 2
                continue
            name = m.group(1)
            i += len(m.group(0))
            if name in _FONT_CMDS:
                inner, i = _tex_group(src, i)
                out.append(_tex_plain(inner))
            elif name in _GREEK:
                out.append(_GREEK[name])
            continue
        if c in "{} ":
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _scan_tex_symbols(latex: str) -> list[str]:
    """式の LaTeX から、画面でタップできる記号の文字列（app.js symbolTextFromToken の形: 基底 + "_" + 添字の字）を
    出現順に返す。添字は**下付きだけ**（IK-0869 の是正後の app.js symbolTextFromToken: msupsub の vlist で上付き
    （べき）の段を落とす — r^2 → "r"・c_s^2 → "c_s"）。"""
    out: list[str] = []
    src = _SPACING.sub("", latex or "")
    i = 0
    while i < len(src):
        c = src[i]
        base = ""
        if c == "\\":
            m = re.match(r"\\([A-Za-z]+)", src[i:])
            if not m:
                i += 2
                continue
            name = m.group(1)
            i += len(m.group(0))
            if name in _FONT_CMDS:
                inner, i = _tex_group(src, i)
                base = _tex_plain(inner)
            elif name in _GREEK:
                base = _GREEK[name]
            else:
                continue
        elif c.isalpha():
            base, i = c, i + 1
        else:
            i += 1
            continue
        sub = sup = ""
        while i < len(src) and src[i] in "_^":
            mark = src[i]
            grp, i = _tex_group(src, i + 1)
            if mark == "_":
                sub += _tex_plain(grp)
            else:
                sup += _tex_plain(grp)
        if not SYMBOL_TOKEN_RE.match(base):
            continue
        del sup  # 上付きは送らない（IK-0869）
        out.append(f"{base}_{sub}" if sub else base)
    return out


def symbol_text(raw: Any) -> str:
    """頭脳が渡した記号（LaTeX の断片か平文）を、画面が送る形（基底 + "_" + 添字の字）に正規化する。"""
    t = str(raw or "").strip().strip("$").strip()
    t = re.sub(r"\(.*\)$", "", t).strip()  # m(r) → m（括弧の引数はタップの対象ではない）
    if "\\" in t or "{" in t or "^" in t:
        found = _scan_tex_symbols(t)
        return found[0] if found else ""
    if "_" in t:
        base, _, tail = t.partition("_")
        return f"{base}_{tail.replace('_', '')}" if base and tail else base
    return t


def material_symbol_index(material: Any) -> list[dict]:
    """教材の区画ごとの式から、タップできる記号の並び。``equation_id`` は ``![[equation:id]]`` の数式カードのとき
    だけ（app.js の data-equation-id の担体）。``lhs`` = 式の左辺の先頭の記号。"""
    from uxsim.runner.state import _norm_evidence_id  # 遅延 import（画面の ID 正規化の正本）

    out: list[dict] = []
    for ch in (material.get("chunks") if isinstance(material, dict) else None) or []:
        if not isinstance(ch, dict):
            continue
        formulas = [f for f in ch.get("formulas") or [] if isinstance(f, dict)]
        # id の鍵が先・並び順の鍵は空いているときだけ（app.js renderMaterialChunk・第 3 波 IK-0944）
        by_id = {_norm_evidence_id(f["id"]): f for f in formulas if f.get("id")}
        for i, f in enumerate(formulas):
            by_id.setdefault(f"FORMULA_{i}", f)
        ev = {_norm_evidence_id(e.get("id")): e for e in ch.get("evidence_items") or []
              if isinstance(e, dict) and e.get("kind") == "equation"}
        text = str(ch.get("text") or "")
        sources: list[tuple[str, str]] = []  # (latex, equation_id)
        for m in re.finditer(r"!?\[\[equation:\s*(?:\[\[)?([^\]]+?)(?:\]\])?\s*\]\]", text):
            eid = _norm_evidence_id(m.group(1))
            f = by_id.get(eid) or ev.get(eid) or {}
            if f.get("latex"):
                sources.append((str(f["latex"]), eid))
        for m in re.finditer(r"\[\[(FORMULA_\d+|[^\[\]:]+)\]\]", text):
            f = by_id.get(_norm_evidence_id(m.group(1))) or {}
            if f.get("latex"):
                sources.append((str(f["latex"]), ""))
        for m in re.finditer(r"\$\$?([^$]+)\$\$?", text):
            sources.append((m.group(1), ""))
        for latex, eid in sources:
            syms = _scan_tex_symbols(latex)
            lhs = ""
            if "=" in latex:
                left = _scan_tex_symbols(latex.split("=", 1)[0])
                lhs = left[0] if left else ""
            for sym in dict.fromkeys(syms):
                out.append({"symbol": sym, "equation_id": eid, "chunk_id": str(ch.get("id") or ""),
                            "lhs": sym == lhs, "latex": latex})
    return out


def symbol_mention_lines(body: dict) -> list[str]:
    """app.js renderSymbolLookupPopover（IK-0893・第 2 波）: 定義の文が無いとき、この論文の主張での使われ方を
    「…」で並べる（出所の一文はサーバの facts — 「定義の文ではありません」）。"""
    return [f"「{m['text']}」" for m in body.get("mentions") or [] if isinstance(m, dict) and m.get("text")]


def symbol_popover_view(status: Optional[int], body: Any, sent: str) -> str:
    """app.js openSymbolLookup → renderSymbolLookupPopover の写し（応答が ok でなければ何も出さない）。"""
    if not _ok(status) or not isinstance(body, dict):
        return f"記号「{sent}」をタップしたが、吹き出しは出なかった。"
    if not body.get("available"):
        facts = [f for f in body.get("facts") or [] if isinstance(f, str) and f]
        return "\n".join([f"記号の吹き出し: {body.get('symbol') or sent}"] + (facts or [SYMBOL_NOT_FOUND])
                         + symbol_mention_lines(body))
    head = [str(body.get("symbol") or sent)] + [str(body[k]) for k in ("scope_label", "unit") if body.get(k)]
    lines = ["記号の吹き出し: " + " ・ ".join(head)]
    definition = body.get("definition") if isinstance(body.get("definition"), dict) else {}
    if definition.get("text"):
        lines.append(f"定義: {definition['text']}")
        if definition.get("latex"):
            lines.append(f"${definition['latex']}$")  # IK-0869: 式だけで定義された記号は定義式を数式で
        if definition.get("equation_label"):
            lines.append(str(definition["equation_label"]))
    lines += [str(f) for f in body.get("facts") or [] if isinstance(f, str) and f]
    lines += symbol_mention_lines(body)
    for eq in body.get("equations") or []:  # IK-0870: 事実文が名指す式を呼び名と式本文で
        if isinstance(eq, dict) and eq.get("latex"):
            lines += [str(eq.get("name") or ""), f"${eq['latex']}$"]
    ref = body.get("concept_ref") if isinstance(body.get("concept_ref"), dict) else {}
    if ref.get("name"):
        lines.append(f"概念: {ref['name']}（登録済み）")
    if body.get("source"):
        lines.append(f"出典: {body['source']}")
    return "\n".join(lines)


def symbol_lookup(ctx) -> None:
    """``learning.symbol.lookup``（``actions_exec._symbol_lookup`` から委譲）。画面でタップできるのは教材の式の中の記号だけ
    （第 25 周 share H3/H8: 頭脳が本文に無い記号や LaTeX の断片をそのまま送っていた）。"""
    s = ctx.session
    sent = symbol_text(ctx.args.get("symbol"))
    if not sent:
        return ctx.missing("symbol")
    index = material_symbol_index(s.material)
    hits = [h for h in index if h["symbol"] == sent]
    if index and not hits:
        return _blocked(ctx, "symbol_in_material",
                        f"（ハーネス: 記号「{sent}」は開いている教材の式に見当たらないので、タップしていない。"
                        "これは画面の文ではない）")
    want_eq = str(ctx.args.get("equation_id") or "").strip()
    hit = next((h for h in hits if want_eq and h["equation_id"] == want_eq), None) or (hits[0] if hits else None)
    params: dict = {"symbol": sent}
    if getattr(s, "topic_id", None):
        params["topic_id"] = s.topic_id
    if hit and hit["equation_id"]:
        params["equation_id"] = hit["equation_id"]
    if hit and hit["chunk_id"]:
        params["chunk_id"] = hit["chunk_id"]
    if any(h["lhs"] for h in hits):
        _note(s, f"{SYMBOL_LHS_NOTE}{sent}（教材の式の左辺に現れる記号）")
    st, body = ctx.main(params=params)
    _fact(ctx, symbol_popover_view(st, body, sent), st)


HANDLERS: dict[str, Callable[[Any], None]] = {
    "admin.library.identity_candidates": _identity_candidates,
    "admin.library.identity_link_decide": _identity_link_decide,
    "admin.library.entries": _entries,
    "admin.library.entry_open": _entry_open,
    "admin.library.entry_create": _entry_create,
    "admin.library.entry_review": _entry_review,
    "admin.library.label_add": _label_add,
    "admin.library.relation_add": _relation_add,
    "admin.library.relation_decide": _relation_decide,
    "admin.library.atlas_links_derive": _atlas_derive,
    "admin.library.atlas_link_decide": _atlas_link_decide,
    "admin.library.atlas_link_manual": _atlas_link_manual,  # 第 3 波（IK-0927）
    "admin.deliberation.identity_open": _identity_open,
    "admin.deliberation.shared_part_search": _shared_part_search,
    "admin.deliberation.shared_part_domain": _shared_part_domain,  # IK-0890
    "admin.deliberation.identity_link_create": _identity_link_create,
    "admin.deliberation.identity_link_decide": _dlb_link_decide,
    "admin.explanations.open": _explanations_open,
    "admin.explanations.add": _explanations_add,
    "admin.explanations.update": _explanations_update,
    "admin.explanations.endorse": _explanations_endorse,
    "admin.explanations.cite": _explanations_cite,
    "admin.explanations.endorsers": _explanations_endorsers,  # 第 25 周 H28
    "admin.explanations.revoke": _explanations_revoke,  # 第 3 波（IK-0924）
    "admin.library.label_dismiss": _label_dismiss,  # 第 25 周 第 4 周の前（IK-0897）
    "admin.document_share.open": _document_share_open,
    "admin.document_share.add": _document_share_add,
    "learning.component.shared_part": _component_shared_part,
    "learning.component.explanations": _component_explanations,
}


def execute(action_id: str, ctx) -> bool:
    """扱った行為なら実行して True（``actions_exec`` の dispatch から呼ぶ）。"""
    handler = HANDLERS.get(action_id)
    if handler is None:
        return False
    handler(ctx)
    return True
