"""第 25 周 面 C「分野の地図の運用」の行為の実行と画面の投影。

行為 id と引数は ``uxsim/actions/atlas_actions.py``。画面の正本（JS）:

- 教員: ``frontend/public/js/admin.js`` の分野の地図タブ — ``loadState`` / ``renderState`` /
  ``renderAtlasOverview``（骨格の状態）、``buildGapsGroup`` / ``renderGapCandidates`` /
  ``_gapCandidateCardHtml`` / ``gapDecide`` / ``gapRegisterAlias`` / ``gapCreateDraftFromFrozen`` /
  ``gapIncorporate`` / ``openGapIncorporateConfirm`` / ``gapApplyIncorporation``（論文の解析から見つかった候補）、
  ``renderEdgeCandidates`` / ``_edgeCandidateCardHtml`` / ``edgeDecide`` / ``openEdgeIncorporateConfirm`` /
  ``edgeApplyIncorporation``（関係（辺）の候補）、``renderVectorStatus`` / ``vectorRefreshFactText`` /
  ``renderAliases``（ベクトル索引と別名）、``saveDraft`` / ``applyAssistProposal``（下書きの保存）、
  ``#atlas-freeze`` の click / ``_freezeCorrespondenceHtml`` / ``_freezeCollectIdMigrations`` /
  ``openFreezeImpactModal``（凍結）。
- 学生: ``atlas-threads-layer.js``（``factText`` / ``threadsData``）、``landscape-layer.js``
  （``groupByNode`` / ``domainFactLines`` / ``corpusFactText`` / ``appendPlacementRow``）、``corpus-sea.js``
  （``selectDomain`` / ``densityIndex`` / ``regionDensityLevel`` / ``anchorRadius`` / ``renderEdges`` / ``renderPapers``）。

投影は**画面と同じ欄を同じ形で**出す（IK-0634 / IK-0628）。画面が出さない数・内部 ID は投影でも出さない
（教員画面が描く件数 — 公開前チェックの検証エラー件数 — は画面どおりに出す。索引の件数は第 2 波で画面から消えた）。
投影は ``session.last_body = {"_ui_fact": 文}`` に置く（``state.project_observation`` がそのまま返す）。
審判（``uxsim/oracles/atlas.py``）が読む要約は各 trace の ``digest["atlas"]`` に置く（抜粋の 2000 字では
骨格・地図の応答が切れるため。値は ID・版・指紋（sha256 の先頭）・状態の語彙だけで数値の値を持たない）。

画面の文言は JS の逐語ミラー（``uxsim/tests/test_round25_atlas_harness.py`` が JS の原文に現れることを固定）。
このモジュールは ``actions_exec`` を import しない（指揮者が ``actions_exec._HANDLERS.update(HANDLERS)`` で取り込む）。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable, Optional

from uxsim.runner.state import PersonaSession

# ---------------------------------------------------------------------------
# 画面の文言の逐語ミラー（admin.js / atlas-threads-layer.js / landscape-layer.js / corpus-sea.js が正本）
# ---------------------------------------------------------------------------
# admin.js 論文の解析から見つかった候補
GAP_GROUP_TITLE = "論文の解析から見つかった候補"
# 第 3 波（IK-0943）: 区画の説明は定義の文
GAP_GROUP_INTRO = "2 本以上の論文が、この地図にまだ無い同じ項目に触れたとき、ここに候補として出ます。"
GAP_ORIGIN_LABEL = "AIによる検出（未確認）"
GAP_ACCEPT_NOTE = "[採用] は「カテゴリとして妥当」という判断だけを記録します。次版の下書きはこの操作では変わりません。"
GAP_DISMISS_NOTE = "この分野で同じ名前の候補は今後表示されません。「見送り済み」フィルタから戻せます。"
GAP_AT_CAPACITY_TEXT = "この領域の概念は上限（6件）に達しています。追加するには次版で既存概念の整理が必要です。"
GAP_NO_DRAFT_TEXT = "次版の下書きがまだありません。「現在の版から次版の下書きを作る」を実行すると取り込めます。"
GAP_NOT_ACCEPTED_TEXT = "[採用] を押すと、次版の下書きに取り込めます。"
GAP_EMPTY_TEXT = "いまレビューする候補はありません。"
GAP_DISMISS_REASON_REQUIRED = "却下には理由が必要です"
GAP_DOCUMENTS_HEAD = "この項目に触れている論文"
GAP_DOCUMENTS_HIDDEN_TEXT = "閲覧できない論文も根拠に含まれています。"  # admin.js GAP_DOCUMENTS_HIDDEN_TEXT（IK-0937）
GAP_ACCEPT_MEMO_PROMPT = "採用のメモ（任意）\nほかの教員が公開（凍結）するときに、採用の理由として表示されます。"
EDGE_ACCEPT_MEMO_PROMPT = "採用のメモ（任意）\nほかの教員が公開（凍結）するときに、採用の理由として表示されます。"
GAP_RESTORE_REASON_PROMPT = "候補へ戻す理由\nほかの教員が採用した候補を戻すときは必須です（記録に残ります）。"
GAP_ALIAS_NOTE_HEAD = "既存概念『"
GAP_ALIAS_NOTE_TAIL = "』の別名として登録"
GAP_FREEZE_PENDING_TEXT = "採用済みでまだ次版に反映されていない候補が残っています"
GAP_FREEZE_REANALYSIS_TEXT = "既存論文の配置は再解析するまで変わりません"
# 第 3 波（IK-0939）: 後ろにサーバの timing_line（「下書きの間は表示されない／公開すると出る」）を続ける
GAP_SAVE_TO_DRAFT = "この内容を次版の下書きに保存します。"
# admin.js _gapOrphanedHtml / _edgeOrphanedHtml（IK-0884・第 2 波）
GAP_ORPHANED_TITLE = "候補の条件から外れた採用済みの判断"
EDGE_ORPHANED_TITLE = "候補の条件から外れた採用済みの関係"
# admin.js 関係（辺）の候補
EDGE_GROUP_TITLE = "関係（辺）の候補"
EDGE_GROUP_INTRO = ("項目の説明文どうしが近いと推定された組（プロトタイプ近傍）や、同じ論文が両方の項目に置かれている組"
                    "（共起）が、ここに関係の候補として出ます。採用して次版の下書きに反映し、公開するまで地図の関係は変わりません。")
EDGE_KIND_LABELS = {"adjacent": "隣接", "depends": "依存", "related": "関連"}
EDGE_ORIGIN_VECTOR_LABEL = "プロトタイプ近傍"
EDGE_ORIGIN_CO_OCCURRENCE_LABEL = "共起"
EDGE_ORIGIN_NOTE = "AIによる推定（未確認）"
EDGE_KIND_REQUIRED = "採用するには関係の種類を選んでください"
EDGE_DISMISS_REASON_REQUIRED = "見送りには理由が必要です"
EDGE_ACCEPT_NOTE = "[採用] は「この関係は妥当」という判断だけを記録します。次版の下書きはこの操作では変わりません。"
EDGE_DISMISS_NOTE = "見送った関係は既定の一覧に出なくなります。「見送り済みも表示」から戻せます。"
EDGE_NO_DRAFT_TEXT = "次版の下書きがまだありません。この区画の先頭の［現在の版から次版の下書きを作る］を押すと反映できます。"  # admin.js（IK-1044・第 7 波）
# admin.js _edgeDraftCreateHtml（IK-1044・第 7 波）: 関係の区画の先頭と「採用済み・未反映」の区画の先頭の下書きの入口
EDGE_DRAFT_CREATE_NOTE = ("編集中の次版の下書きはありません。採用した関係を地図に加えるには、先に公開中の版を複製して次版の"
                          "下書きを作ります（AI は使いません）。")
EDGE_DRAFT_CREATE_RETIRED_NOTE = "この分野は廃止済みのため、次版の下書きは作れません。「復帰する」で戻すと作れます。"
EDGE_DRAFT_CREATE_BUTTON = "現在の版から次版の下書きを作る"
EDGE_NOT_ACCEPTED_TEXT = "[採用] を押すと、次版の下書きへ反映できます。"
# 第 8 波（IK-1044 残り・sync_keys_wave8 面 C）: 下書きが無いときの未採用の関係カードの案内（admin.js EDGE_NOT_ACCEPTED_NO_DRAFT_TEXT）
EDGE_NOT_ACCEPTED_NO_DRAFT_TEXT = "[採用] を押し、この区画の先頭の［現在の版から次版の下書きを作る］で下書きを作ると反映できます。"


def decision_note_head(decision: dict) -> str:
    """admin.js（第 8 波）: 見送った判断のメモの見出しは「見送りの理由」、採用・候補は「判断のメモ」（4 箇所共通）。"""
    return "見送りの理由" if str((decision or {}).get("status") or "") == "dismissed" else "判断のメモ"
EDGE_EMPTY_TEXT = "いまレビューする関係の候補はありません。"
EDGE_DOCUMENTS_HEAD = "両方の項目に配置がある論文"
EDGE_FREEZE_PENDING_TEXT = "採用済みでまだ次版に反映されていない関係（辺）の候補が残っています"
# admin.js ベクトル索引と別名
VECTOR_GROUP_TITLE = "ベクトル索引"
VECTOR_NO_SKELETON_TEXT = "凍結済みの骨格がありません"
VECTOR_STALE_TEXT = "骨格が更新されています。再構築してください。"
VECTOR_LIMIT_TEXT = "本日の再構築回数の上限に達しました"
VECTOR_DONE_TEXT = "索引を作り直しました"
# admin.js _appendDraftRelationsSection（IK-0886・第 2 波）
DRAFT_RELATIONS_TITLE = "関係（辺）"
DRAFT_RELATIONS_EMPTY = "この下書きには関係（辺）がありません。"
PUBLISHED_RELATIONS_EMPTY = "この版には関係（辺）がありません。"  # 第 5 波（IK-0992）
DRAFT_RELATION_NEW = "— 次版で加わる関係"
# admin.js openFreezeImpactModal(blocked=true)（IK-0885・第 2 波）
FREEZE_BLOCKED_GOTO = "止めている候補を見る"
FREEZE_BLOCKED_BUTTON = "公開できません"
EDGE_SHOW_ALL_BUTTON = "すべて表示"
ALIAS_GROUP_TITLE = "登録済みの別名"
ALIAS_EMPTY_TEXT = "登録された別名はまだありません。"
ALIAS_DISMISS_NOTE = "「見送り」は行を消す操作ではありません。同じ表記をもう一度登録すると戻ります。"
# admin.js 凍結
FREEZE_CORRESPONDENCE_TITLE = "前の版のノードとの対応"
FREEZE_CORRESPONDENCE_NOTE = "チェックした対応だけが次の版に記録されます。チェックを外したままでも公開はできます。"
FREEZE_CORRESPONDENCE_UNMATCHED_TITLE = "対応の候補が見つからなかった項目"
FREEZE_CORRESPONDENCE_ALREADY_NOTE = "次版の下書きに既に記録されている対応:"
FREEZE_QUESTION = "この次版を学習者向けに公開しますか？取り消せません（修正は次版で行います）。"
NO_FROZEN_TEXT = "公開中の分野マップはまだありません（学習者には地図が表示されません）。"
# atlas-threads-layer.js
THREADS_PROVENANCE_TEXT = "AIによる推定（未確認）"
# 分野の地図の版の呼び名（IK-0942・第 3 波）。正本 backend/core/label_vocab.py::ATLAS_VERSION_PREFIX、
# admin.js / atlas-threads-layer.js / landscape-layer.js / app.js の逐語ミラー
ATLAS_VERSION_PREFIX = "地図 版 "


def atlas_version_text(version: Any) -> str:
    """admin.js atlasVersionText の写し（版が空なら空文字）。"""
    v = "" if version is None else str(version).strip()
    return ATLAS_VERSION_PREFIX + v if v else ""
# landscape-layer.js
LANDSCAPE_TOGGLE_LABEL = "論文の位置"
# corpus-sea.js
DENSITY_LEGEND_TITLE = "閲覧できる論文の厚み"
DENSITY_LEGEND_LABELS = ("なし", "少しある", "蓄積あり", "厚い")
REGION_DENSITY_THRESHOLDS = (1, 3, 6)
ANCHOR_RADIUS_THRESHOLDS = (1, 2, 4)
SEA_PAPERS_HEAD = "閲覧できる論文（新しい順）"
SEA_NO_PAPERS_TEXT = "この分野で閲覧できる論文はまだありません。"
SEA_NOT_PLACED_TEXT = "この論文は、現在の分野の地図には置かれていません。"
SEA_NO_DOMAINS_TEXT = "いま閲覧できる分野の地図はありません。"
SEA_DOMAIN_DOT_TITLE = "閲覧できる論文が置かれています"
ANCHOR_SIZE_WORDS = ("小", "中", "大")  # ANCHOR_RADII = [6, 9, 12] を言葉にした投影（画面は円の大きさだけを描く）

KIND_WORDS = {"隣接": "adjacent", "依存": "depends", "関連": "related",
              "adjacent": "adjacent", "depends": "depends", "related": "related"}

_CART = "/api/admin/cartridges/{cartridge_id}/atlas"


# ---------------------------------------------------------------------------
# 共通
# ---------------------------------------------------------------------------
def _runner_note(session: PersonaSession, note: str) -> None:
    session.scratch.setdefault("runner_notes", []).append(note)


HARNESS_NOTE_HEAD = "（ハーネス: "
HARNESS_NOTE_TAIL = "。これは画面の文ではない）"


def harness_note(inner: str) -> str:
    """runner の注記（第 25 周 H20）: 画面の文ではないことを必ず「（ハーネス: …）」の形で言う。

    行為の対象が一覧に無い・ボタンが出ていない等、製品が文を出さない場面の runner の地の文。頭脳が製品の
    画面の文として読まないよう、印を付けずに括弧だけで出さない。
    """
    body = str(inner or "").strip()
    if body.startswith("（") and body.endswith("）"):
        body = body[1:-1]
    return HARNESS_NOTE_HEAD + body + HARNESS_NOTE_TAIL


def _fact(ctx, text: str, status: Optional[int] = None) -> None:
    """画面の投影を置く（status は最後の HTTP の値のまま — 4xx なら頭脳の判断にも効く）。"""
    s = ctx.session
    if status is not None:
        s.last_status = status
    s.last_body = {"_ui_fact": text.strip() or "（画面に何も表示されなかった）"}


def _annotate(ctx, payload: dict) -> None:
    """直前の trace の digest に審判用の要約を置く（``digest["atlas"]``）。"""
    traces = getattr(ctx.client, "traces", None)
    if not traces:
        return
    digest = getattr(traces[-1], "digest", None)
    if isinstance(digest, dict):
        digest["atlas"] = payload


def _sha(obj: Any) -> str:
    try:
        raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        raw = str(obj)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _detail(body: Any) -> str:
    """admin.js ``_gapResponse``: サーバの detail（事実文）をそのまま。取れなければ HTTP 状態。"""
    if isinstance(body, dict):
        d = body.get("detail")
        if isinstance(d, str):
            return d
        if isinstance(d, dict) and d.get("message"):
            return str(d["message"])
    return ""


def _domain(ctx) -> str:
    return str(ctx.args.get("cartridge_id") or ctx.session.scratch.get("atlas_domain") or "")


def _call(ctx, method: str, path: str, **kw) -> tuple[Optional[int], Any]:
    return ctx.call(method, path, overrides={"cartridge_id": _domain(ctx), **(kw.pop("overrides", None) or {})}, **kw)


def _need_domain(ctx) -> bool:
    if _domain(ctx):
        return True
    ctx.missing("cartridge_id")
    _fact(ctx, "分野を選択してください")  # admin.js loadState（select.value が空）
    return False


def card_numbers(session: PersonaSession, kind: str, rows: list[dict], key_field: str) -> dict[str, int]:
    """候補カードの番号（H2・第 25 周）。製品のカードに番号は無い — 投影がペルソナに指させるための印。

    番号はセッションの中で候補のキー（``cluster_key`` / ``edge_key``）に固定し、最初に見た順に振る。見送り・
    採用で一覧から消えても番号は詰めず、消えた番号を再利用しない（一覧の位置で毎回振ると、読み直すたびに
    同じ番号が別の候補を指して頭脳が取り違えた）。
    """
    table: dict[str, int] = session.scratch.setdefault(f"atlas_{kind}_numbers", {})
    for r in rows:
        k = str(r.get(key_field) or "")
        if k and k not in table:
            table[k] = max(table.values(), default=0) + 1
    return table


def renumber_cards(session: PersonaSession, kind: str, rows: list[dict], key_field: str) -> None:
    """［すべて表示］の直後に番号を表示順で振り直す（第 25 周 H19）。

    ``card_numbers`` は最初に見た順に固定するので、近さ順に割り込んだ未見の候補が末尾の番号を受け取り、並びが
    「1〜10 → 32 → 11 …」と飛んだ。振り直しは［すべて表示］を押したときの 1 回だけ（読み直すたびに振ると
    H2 の取り違えに戻る）。番号の表は hint の解決にも使うので、表示と解決は同じ番号を指す。前の番号は
    ``atlas_{kind}_numbers_previous`` に残し、カードの見出しに「（前の表示の番号 N）」を併記する
    （前に見ていない候補は None — 「新しく出た候補」の印）。
    """
    old: dict[str, int] = dict(session.scratch.get(f"atlas_{kind}_numbers") or {})
    table: dict[str, int] = {}
    for r in rows:
        k = str(r.get(key_field) or "")
        if k and k not in table:
            table[k] = len(table) + 1
    for k, n in sorted(old.items(), key=lambda kv: kv[1]):
        if k not in table:  # 表示に無い（見送り済み等）候補にも番号を残す（再利用しない）
            table[k] = len(table) + 1
    session.scratch[f"atlas_{kind}_numbers"] = table
    session.scratch[f"atlas_{kind}_numbers_previous"] = {k: old.get(k) for k in table if k in {
        str(r.get(key_field) or "") for r in rows}}


# 第 25 周 H27（第 4 周）: カードの番号は投影の印。区画の頭で 1 回だけそう言う（製品のカードに番号は無い）
CARD_NUMBER_NOTE = ("（ハーネスの番号: カード先頭の「N.」はハーネスが振った印で、画面のカードに番号は無い。"
                    "いまの表示の順に振り直している — 指すときはこの番号か名前で）")


def display_numbers(session: PersonaSession, kind: str, rows: list[dict], key_field: str) -> dict[str, int]:
    """候補カードの番号を、いまの表示の順に振り直す（第 25 周 H27・第 4 周）。

    H2 の「最初に見た順に固定」は、採用で帯が入れ替わる・見送りで畳みから繰り上がると「2 → 1 → 3」「… 32・33 …」
    のように表示の並びと番号が食い違い、頭脳が製品の並びの欠陥として記した。表示のたびに表示順で振り、hint の
    解決は同じ表（キー → 番号）で行う — 頭脳が最後に見た番号がそのまま指す先になる。前の表示の番号は
    ``atlas_{kind}_numbers_previous`` に残し、変わったカードだけ「（前の表示の番号 N）」を併記する。
    """
    old: dict[str, int] = dict(session.scratch.get(f"atlas_{kind}_numbers") or {})
    table: dict[str, int] = {}
    for r in rows:
        k = str(r.get(key_field) or "")
        if k and k not in table:
            table[k] = len(table) + 1
    session.scratch[f"atlas_{kind}_numbers"] = table
    session.scratch[f"atlas_{kind}_numbers_previous"] = {k: old.get(k) for k in table}
    return table


def previous_number_note(session: PersonaSession, kind: str, key: str, now: int) -> str:
    """前の表示と番号が変わったカードの見出しに添える文（前の表示に無ければ空）。"""
    old = (session.scratch.get(f"atlas_{kind}_numbers_previous") or {}).get(key)
    return f"（前の表示の番号 {old}）" if old and old != now else ""


def _hint_pick(rows: list[dict], hint: Any, label_keys: tuple[str, ...],
               numbers: Optional[dict[str, int]] = None, key_field: str = "") -> Optional[dict]:
    """カードの番号か、ラベルの一部で 1 行を選ぶ。

    ``numbers``（``card_numbers`` の表）があれば番号はその表で引く（H2）。無ければ一覧の位置（1 始まり）。
    """
    h = str(hint or "").strip()
    if not rows:
        return None
    if not h:
        return rows[0]
    m = re.fullmatch(r"(?:#|No\.?\s*)?(\d+)", h)
    if m:
        i = int(m.group(1))
        if numbers is not None and key_field:
            return next((r for r in rows if numbers.get(str(r.get(key_field) or "")) == i), None)
        return rows[i - 1] if 1 <= i <= len(rows) else None
    key = h.casefold()
    for r in rows:
        if any(key in str(r.get(k) or "").casefold() for k in label_keys):
            return r
    return None


# ---------------------------------------------------------------------------
# 教員: 骨格の状態（loadState）
# ---------------------------------------------------------------------------
def _skeleton_digest(body: Any) -> dict:
    if not isinstance(body, dict):
        return {}
    frozen = (body.get("frozen") or {}).get("skeleton") if isinstance(body.get("frozen"), dict) else None
    draft = body.get("draft") if isinstance(body.get("draft"), dict) else None
    return {
        "kind": "skeleton_state",
        "frozen_version": str((frozen or {}).get("version") or ""),
        "frozen_fingerprint": _sha(frozen) if frozen else "",
        "draft_exists": draft is not None,
        "draft_revision": (draft or {}).get("revision"),
        "draft_fingerprint": _sha((draft or {}).get("skeleton")) if draft else "",
        "draft_error_issues": len(((draft or {}).get("validation") or {}).get("errors") or []),
        # 審判（H9 ②）が教員向けの投影に骨格の内部 id が出ていないかを見る材料（id の集合だけ・数は持たない）
        "node_ids": sorted(_skeleton_node_ids(frozen) | _skeleton_node_ids((draft or {}).get("skeleton"))),
    }


def _skeleton_node_ids(skeleton: Any) -> set[str]:
    """骨格の領域・概念の id（regions[].id / regions[].concepts[].id / 平らな nodes 辞書のキー）。"""
    out: set[str] = set()
    if not isinstance(skeleton, dict):
        return out
    for r in skeleton.get("regions") or []:
        if isinstance(r, dict):
            out.add(str(r.get("id") or ""))
            out.update(str(c.get("id") or "") for c in r.get("concepts") or [] if isinstance(c, dict))
    nodes = skeleton.get("nodes")
    if isinstance(nodes, dict):
        out.update(str(k) for k in nodes)
    out.discard("")
    return out


def _decision_digest(c: dict) -> dict:
    """候補の判断の要約（H9 ③ — 判断済みの候補のカードに判断の帰属が描かれているかを審判が見る材料）。"""
    d = c.get("decision") if isinstance(c.get("decision"), dict) else {}
    decider = next((str(d.get(k)) for k in ("decided_by_name", "decided_by_label", "decider_label")
                    if d.get(k)), "")
    return {"decided": (d.get("status") or "candidate") in ("accepted", "dismissed", "merged"),
            "decided_by_in_api": bool(d.get("decided_by") or decider), "decider": decider}


def _document_refs(c: dict) -> dict:
    docs = [d for d in (c.get("documents") or []) if isinstance(d, dict)]
    return {"document_titles": [str(d.get("title") or "") for d in docs],
            "document_ids": [str(d.get("document_id") or d.get("id") or "") for d in docs]}


def _status_of(c: Any) -> str:
    return str(((c or {}).get("decision") or {}).get("status") or "candidate") if isinstance(c, dict) else ""


def unreflected(c: Any) -> bool:
    """採用済みで、まだ次版の下書きに入っていない（凍結を止める）候補・関係・外れた判断。"""
    return (isinstance(c, dict) and _status_of(c) == "accepted" and not c.get("in_draft")
            and not ((c.get("decision") or {}).get("applied_version")))


def pending_unreflected(session: PersonaSession, kind: str, *, where: str = "all") -> list[dict]:
    """``kind`` = gap / edge の採用済み・未反映。``where`` = candidates / orphaned / all（第 25 周 H25）。"""
    data = session.scratch.get("atlas_gaps" if kind == "gap" else "atlas_edges")
    if not isinstance(data, dict):
        return []
    rows: list = []
    if where in ("candidates", "all"):
        rows += list(data.get("candidates") or [])
    if where in ("orphaned", "all"):
        rows += list(data.get("orphaned_decisions") or [])
    return [r for r in rows if unreflected(r)]


def refresh_freeze_ready(session: PersonaSession) -> None:
    """状態の旗 ``freeze_ready``（scenario.STATE_FLAGS・第 25 周 H25）を候補の一覧から立て直す。

    下書きがあり、候補・関係の両方の一覧を読んでいて、どちらにも採用済み・未反映が無ければ真。凍結の影響を
    読んだときはサーバの readiness が勝つ（``_freeze_impact``）。一覧を読んでいなければ旗に触れない。
    """
    gaps, edges = session.scratch.get("atlas_gaps"), session.scratch.get("atlas_edges")
    flags = session.scratch.setdefault("until_flags", {})
    if not (isinstance(gaps, dict) or isinstance(edges, dict)):
        return
    draft = any(isinstance(d, dict) and d.get("draft_exists") for d in (gaps, edges))
    flags["freeze_ready"] = bool(draft and not pending_unreflected(session, "gap")
                                 and not pending_unreflected(session, "edge"))


def _load_skeleton(ctx) -> tuple[Optional[int], Any]:
    status, body = _call(ctx, "GET", _CART + "/skeleton")
    if status == 200 and isinstance(body, dict):
        s = ctx.session
        s.scratch["atlas_state"] = body
        draft = body.get("draft") if isinstance(body.get("draft"), dict) else None
        s.scratch["atlas_draft_revision"] = (draft or {}).get("revision")  # admin.js draftRevision
        _annotate(ctx, _skeleton_digest(body))
    return status, body


def _load_reports(ctx) -> None:
    status, body = _call(ctx, "GET", _CART + "/reports")
    if status == 200 and isinstance(body, dict):
        ctx.session.scratch["atlas_reports"] = body


def _gap_digest(body: Any) -> dict:
    cands = [c for c in (body.get("candidates") or []) if isinstance(c, dict)] if isinstance(body, dict) else []
    return {
        "kind": "gap_candidates",
        "skeleton_version": str((body or {}).get("skeleton_version") or "") if isinstance(body, dict) else "",
        "draft_exists": bool((body or {}).get("draft_exists")) if isinstance(body, dict) else False,
        "draft_revision": (body or {}).get("draft_revision") if isinstance(body, dict) else None,
        "candidates": [{
            "cluster_key": str(c.get("cluster_key") or ""),
            "status": str((c.get("decision") or {}).get("status") or "candidate"),
            "label": str(c.get("proposed_label") or ""),
            **_document_refs(c),
            **_decision_digest(c),
            "near_anchor": bool((c.get("near_anchor") or {}).get("node_label")),
            "near_anchor_version": str((c.get("near_anchor") or {}).get("skeleton_version") or ""),
        } for c in cands],
        # 第 2 波（IK-0851 / IK-0884）: 候補ゼロの理由の有無と、導出から外れた判断の名前（審判 B15・B16）
        "empty_notes": bool((body or {}).get("empty_notes")) if isinstance(body, dict) else False,
        "orphaned_labels": [str(i.get("proposed_label") or "") for i in ((body or {}).get("orphaned_decisions") or [])
                            if isinstance(i, dict)] if isinstance(body, dict) else [],
    }


def _load_gaps(ctx) -> tuple[Optional[int], Any]:
    s = ctx.session
    include = bool(s.scratch.get("atlas_gap_include_dismissed"))
    status, body = _call(ctx, "GET", _CART + "/gap-candidates",
                         params={"include_dismissed": "true"} if include else None)
    if status == 200 and isinstance(body, dict):
        s.scratch["atlas_gaps"] = body
        _annotate(ctx, _gap_digest(body))
    elif status == 404:
        s.scratch["atlas_gaps"] = None  # 骨格が無い分野はグループごと非表示（fail-closed）
    refresh_freeze_ready(s)
    return status, body


def _edge_digest(body: Any) -> dict:
    cands = [c for c in (body.get("candidates") or []) if isinstance(c, dict)] if isinstance(body, dict) else []
    return {
        "kind": "edge_candidates",
        "skeleton_version": str((body or {}).get("skeleton_version") or "") if isinstance(body, dict) else "",
        "draft_exists": bool((body or {}).get("draft_exists")) if isinstance(body, dict) else False,
        "draft_revision": (body or {}).get("draft_revision") if isinstance(body, dict) else None,
        "candidates": [{
            "edge_key": str(c.get("edge_key") or ""),
            "from": str(c.get("from_id") or ""), "to": str(c.get("to_id") or ""),
            "status": str((c.get("decision") or {}).get("status") or "candidate"),
            "origins": [str(o) for o in (c.get("origins") or [])],
            "label": _edge_pair_label(c),
            **_document_refs(c),
            **_decision_digest(c),
            "in_draft": bool(c.get("in_draft")),
        } for c in cands],
        "empty_notes": bool((body or {}).get("empty_notes")) if isinstance(body, dict) else False,
        # 第 6 周 H37: 索引の構築中に候補を出していない理由の文（一覧の上の index_note）も空の理由として数える（審判 B15）
        "index_note": bool((body or {}).get("index_note")) if isinstance(body, dict) else False,
        "folded": bool((body or {}).get("folded_note")) if isinstance(body, dict) else False,
        "orphaned_labels": [_edge_pair_label(i) for i in ((body or {}).get("orphaned_decisions") or [])
                            if isinstance(i, dict)] if isinstance(body, dict) else [],
    }


def _load_edges(ctx) -> tuple[Optional[int], Any]:
    s = ctx.session
    include = bool(s.scratch.get("atlas_edge_include_dismissed"))
    params = {}
    if include:
        params["include_dismissed"] = "true"
    if s.scratch.get("atlas_edge_show_all"):
        params["show_all"] = "true"  # admin.js loadEdgeCandidates（IK-0887）
    status, body = _call(ctx, "GET", _CART + "/edge-candidates", params=params or None)
    if status == 200 and isinstance(body, dict):
        s.scratch["atlas_edges"] = body
        _annotate(ctx, _edge_digest(body))
    elif status == 404:
        s.scratch["atlas_edges"] = None
    refresh_freeze_ready(s)
    return status, body


def _load_vectors(ctx) -> tuple[Optional[int], Any]:
    status, body = _call(ctx, "GET", _CART + "/vectors/status")
    if status == 404:
        body = {"available": False}
    if isinstance(body, dict) and status in (200, 404):
        ctx.session.scratch["atlas_vectors"] = body
    return status, body


def _load_aliases(ctx, include_dismissed: bool = False) -> tuple[Optional[int], Any]:
    status, body = _call(ctx, "GET", _CART + "/aliases",
                         params={"include_dismissed": "true" if include_dismissed else "false"})
    if status == 200 and isinstance(body, dict):
        ctx.session.scratch["atlas_aliases"] = body
    return status, body


def _load_state(ctx) -> None:
    """admin.js loadState: 骨格 → 修正報告 → 候補 → 関係の候補 → 索引と別名（ポーリングなし）。"""
    _load_skeleton(ctx)
    _load_reports(ctx)
    _load_gaps(ctx)
    _load_edges(ctx)
    _load_vectors(ctx)
    _load_aliases(ctx)


# ---------------------------------------------------------------------------
# 教員: 投影
# ---------------------------------------------------------------------------
def overview_lines(session: PersonaSession) -> list[str]:
    """admin.js renderState（公開中の版の情報）+ renderAtlasOverview（要約カード 3 つ。コース配置のカードは除く）。"""
    state = session.scratch.get("atlas_state") or {}
    frozen = (state.get("frozen") or {}).get("skeleton") if isinstance(state.get("frozen"), dict) else None
    draft = state.get("draft") if isinstance(state.get("draft"), dict) else None
    validation = (draft or {}).get("validation") or {}
    reports = [r for r in ((session.scratch.get("atlas_reports") or {}).get("reports") or []) if isinstance(r, dict)]
    pending = sum(1 for r in reports if r.get("status") == "pending")
    accepted_open = sum(1 for r in reports if r.get("status") == "accepted" and not r.get("incorporated_at")
                        and not r.get("applied_version"))
    name = session.scratch.get("atlas_domain_name") or session.scratch.get("atlas_domain") or ""
    lines = [f"分野の地図タブ（分野: {name}）"]
    errors = validation.get("errors") or []
    draft_text = ("編集中" + (f"・エラー {len(errors)}件" if errors else "・確認可能")) if draft else "なし"
    report_text = f"未確認 {pending}件" + (f"・次版で未対応 {accepted_open}件" if accepted_open else "")
    # 要約カード（第 3 波・IK-0942）: 「学習者に公開中 = 地図 版 X」
    lines.append(f"学習者に公開中: {(atlas_version_text(frozen.get('version')) or '—') if frozen else '未公開'}"
                 f" ／ 編集中の次版: {draft_text} ／ 修正報告: {report_text}")
    if frozen:
        # admin.js renderState: 作成方法・確認者はサーバが表示語に写した data.frozen.provenance を描く
        # （IK-0850。取込経路のトークン・確認者の UUID は描かない）。provenance の無い旧サーバの応答は「—」。
        provenance = (state.get("frozen") or {}).get("provenance") or {}
        reviewers = ", ".join(str(x) for x in (provenance.get("reviewed_by_labels") or []))
        # renderState（第 3 波・IK-0942）: 公開中の版は要約カードが言うので状態行は「作成方法: …」から始まる
        lines.append(f"作成方法: {provenance.get('generated_by_label') or '—'}"
                     f" ／ 確認者: {reviewers or '—'}（公開済みの版は直接変更せず、次版として編集します）")
        entries = [e for e in (frozen.get("changelog") or []) if isinstance(e, dict)]
        if entries:
            lines.append("これまでの変更:")
            for e in reversed(entries):
                credit = f"（修正報告の帰属: {', '.join(e.get('credits') or [])}）" if e.get("credits") else ""
                lines.append(f"- {atlas_version_text(e.get('version'))} — {e.get('note') or ''}{credit}")
    else:
        lines.append(NO_FROZEN_TEXT)
    if frozen:
        # 第 5 波（IK-0992）: #atlas-published-view（atlas.published-contents）の折りたたみ。既定で閉じている。
        # 第 6 波（IK-0992 の残り）: 下書きがある間も出る（見出しが「…下で編集している次版の下書きとは別です」）。
        # admin.atlas.published_view で開くと同じプレビュー部品（読み取り専用）で版を描く
        lines += published_view_lines(frozen, bool(session.scratch.get("atlas_published_open")),
                                      with_draft=bool(draft),
                                      version=(state.get("frozen") or {}).get("version"))
    if draft:
        lines.append("次版の下書き（プレビュー）:")
        for r in (draft.get("skeleton") or {}).get("regions") or []:
            if isinstance(r, dict):
                concepts = "、".join(str((c or {}).get("label") or (c or {}).get("id") or "")
                                    for c in (r.get("concepts") or []) if isinstance(c, dict))
                lines.append(f"- {r.get('label') or r.get('id') or ''}: {concepts or '（概念なし）'}")
        lines += draft_relations_lines(draft.get("skeleton") or {}, frozen)
        for e in errors[:8]:
            lines.append(f"検証エラー: {e}")
        for w in (validation.get("warnings") or [])[:8]:
            lines.append(f"検証警告: {w}")
    return lines


# admin.js ATLAS_PUBLISHED_VIEW_WITH_DRAFT_TEXT（第 6 波・IK-0992 の残り）
PUBLISHED_VIEW_WITH_DRAFT_TEXT = "公開中の版（{version}）を見る（読み取り専用 — 下で編集している次版の下書きとは別です）"


def published_view_summary(version: Any, *, with_draft: bool = False) -> str:
    """admin.js renderPublishedView の summary（IK-0992）。下書きがあるときは別の見出し（第 6 波）。"""
    v = atlas_version_text(version)
    if with_draft:
        return PUBLISHED_VIEW_WITH_DRAFT_TEXT.replace("{version}", v or "地図")
    return "公開中の版" + (f"（{v}）" if v else "") + "の項目と関係を見る（読み取り専用）"


def published_view_lines(frozen: Any, opened: bool, *, with_draft: bool = False,
                         version: Any = None) -> list[str]:
    """``#atlas-published-view``（公開中の版があるとき出る折りたたみ）。閉じていれば summary の 1 行だけ。
    開いていれば領域・概念（AtlasDraftPreview）+「関係（辺）」の節（opts.published = 次版で加わる印なし・
    空なら「この版には関係（辺）がありません。」）。版は data.frozen.version → skeleton.version の順（admin.js と同じ）。"""
    if not isinstance(frozen, dict):
        return []
    summary = published_view_summary(version or frozen.get("version"), with_draft=with_draft)
    if not opened:
        return [f"▸ {summary}（折りたたみ・閉じている）"]
    lines = [f"▾ {summary}:"]
    for r in frozen.get("regions") or []:
        if isinstance(r, dict):
            concepts = "、".join(str((c or {}).get("label") or (c or {}).get("id") or "")
                                for c in (r.get("concepts") or []) if isinstance(c, dict))
            lines.append(f"- {r.get('label') or r.get('id') or ''}: {concepts or '（概念なし）'}")
    lines += draft_relations_lines(frozen, None, published=True)
    return lines


def draft_relations_lines(skeleton: Any, frozen: Any, *, published: bool = False) -> list[str]:
    """admin.js _appendDraftRelationsSection（IK-0886）: 下書きの関係を両端の名前・種類・向きの文で並べ、
    現在の公開版に無い（種類が違う）関係に「— 次版で加わる関係」を添える。件数は出さない。"""
    if not isinstance(skeleton, dict):
        return []
    labels: dict[str, str] = {}
    concept_ids: set[str] = set()
    for r in skeleton.get("regions") or []:
        if isinstance(r, dict) and r.get("id"):
            labels[str(r["id"])] = str(r.get("label") or r["id"])
            for c in r.get("concepts") or []:
                if isinstance(c, dict) and c.get("id"):
                    labels[str(c["id"])] = str(c.get("label") or c["id"])
                    concept_ids.add(str(c["id"]))

    def pair(e: dict) -> str:
        a, b = str(e.get("from") or ""), str(e.get("to") or "")
        return f"{a}|{b}" if a < b else f"{b}|{a}"

    frozen_pairs = {pair(e): str(e.get("kind") or "") for e in ((frozen or {}).get("edges") or [])
                    if isinstance(e, dict)} if isinstance(frozen, dict) else {}
    edges = [e for e in (skeleton.get("edges") or []) if isinstance(e, dict)]
    lines = [f"{DRAFT_RELATIONS_TITLE}:"]
    if not edges:
        lines.append(PUBLISHED_RELATIONS_EMPTY if published else DRAFT_RELATIONS_EMPTY)
    note = relations_mix_note(edges, concept_ids)
    if note:
        lines.append(note)
    for e in edges:
        a = labels.get(str(e.get("from") or ""), str(e.get("from") or ""))
        b = labels.get(str(e.get("to") or ""), str(e.get("to") or ""))
        kind = str(e.get("kind") or "")
        kl = EDGE_KIND_LABELS.get(kind, "")
        text = f"「{a}」が「{b}」に依存する" if kind == "depends" else f"「{a}」と「{b}」" + (f"（{kl}）" if kl else "")
        k = pair(e)
        is_new = bool(frozen) and (k not in frozen_pairs or frozen_pairs[k] != kind)
        lines.append(f"- {text}" + (f" {DRAFT_RELATION_NEW}" if is_new else ""))
    return lines


#: 第 25 周 第 8 周 atlas H47: 製品の関係の一覧は領域どうし・概念どうしを小見出し無しで 1 列に並べ、概念どうしの関係は
#: 末尾に来る。頭脳が先頭の領域どうしの行で読みやめ「概念どうしの関係が落ちた」と疑った（偽所見）。投影は全行を写したまま、
#: 両方が混ざるときだけハーネスの注記を 1 行添える（画面の文ではない・件数は言わない）
RELATIONS_MIX_NOTE = ("（ハーネス: この一覧は領域どうしの関係と概念どうしの関係を同じ列に並べている — 概念どうしの関係は"
                      "一覧の後ろの方にある。画面の全行を写しているので、欠けを疑う前に末尾まで読む。これは画面の文ではない）")


def relations_mix_note(edges: list, concept_ids: set) -> str:
    """関係の一覧に領域どうしと概念どうし（端点が概念）の両方があれば注記（純関数）。"""
    def has_concept(e: dict) -> bool:
        return str(e.get("from") or "") in concept_ids or str(e.get("to") or "") in concept_ids
    rows = [e for e in edges if isinstance(e, dict)]
    with_c = [has_concept(e) for e in rows]
    return RELATIONS_MIX_NOTE if any(with_c) and not all(with_c) else ""


GAP_EXPLAIN_LINES = frozenset({GAP_ACCEPT_NOTE, GAP_AT_CAPACITY_TEXT, GAP_NOT_ACCEPTED_TEXT, GAP_NO_DRAFT_TEXT})
EDGE_EXPLAIN_LINES = frozenset({EDGE_ACCEPT_NOTE, EDGE_NOT_ACCEPTED_TEXT, EDGE_NOT_ACCEPTED_NO_DRAFT_TEXT,
                                EDGE_NO_DRAFT_TEXT})
# 第 25 周 H27（第 4 周）: 投影の畳み（製品はカードごとに同じ文を繰り返す）には「ハーネス」の印を付ける
SAME_EXPLAIN_NOTE = "  （ハーネス: 投影の省略 — 説明は上の同じ状態のカードと同じ）"
# 第 25 周 H28（第 4 周）: 観察の文字数上限で畳んだ行の印。頭脳が製品の表示件数の規則の変化として読まないよう、
# 表示の都合で畳んだこと・画面には全カードがあることを先に言う
FOLD_HEAD = "（ハーネス: 表示の都合で畳んだ — 画面には全カードがある。以下の{what}は画面の下に続く: "
# 観察の上限（state.OBSERVATION_CHARS）から、区画をつなぐ空行・runner の注記の分を引いた投影の予算（H1）
OBSERVATION_MARGIN = 300


def _size(lines: list[str]) -> int:
    return sum(len(x) + 1 for x in lines)


def _edge_pair_label(c: dict) -> str:
    return f"{c.get('from_label') or c.get('from_id') or ''} — {c.get('to_label') or c.get('to_id') or ''}"


def fold_cards(cards: list[tuple[str, list[str]]], explain: frozenset, budget: Optional[int],
               what: str, keep: Optional[list[bool]] = None) -> list[str]:
    """候補カードを並べる（H1・第 25 周）。

    - カードの説明文（採用・見送りの注記と押せない理由）が、上で全文を出した同じ状態のカードの説明と同じなら
      「（説明は上の同じ状態のカードと同じ）」の 1 行に畳む（製品はカードごとに同じ文を繰り返す — 読む中身は同じ）。
      第 9 波（atlas H49）: 基準は「1 枚目」ではなく、説明の組ごとに最初の 1 枚（1 枚目が採用済みになって説明が
      変わると、旧来は以降の全カードで同じ 2 行を繰り返した）。
    - ``budget``（字数）を越えるカードは、題名だけを「（以下の{what}は画面の下に続く: …）」に列挙して畳む。
      件数は書かない。無言で切らず、切ったことを投影の側で事実として言う（観察の上限で末尾が消え、
      後ろの区画が一度も頭脳に届かなかった）。
    - ``keep`` が真のカード（判断済み・下書きに入っている）は予算を越えても全文で残す（第 25 周 H18。製品の
      cap_for_review が判断済みを畳まないのと同じ規律 — 畳むと［採用］の帰属と反映済みの印が頭脳に届かない）。
    """
    out: list[str] = []
    shown_explains: set[tuple[str, ...]] = set()
    rest: list[str] = []
    for i, (title, lines) in enumerate(cards):
        kept = bool(keep[i]) if keep is not None and i < len(keep) else False
        explain_lines = tuple(x for x in lines if x.strip() in explain)
        folded = bool(explain_lines) and explain_lines in shown_explains
        if folded:
            lines = [x for x in lines if x.strip() not in explain] + [SAME_EXPLAIN_NOTE]
        if not kept and budget is not None and out and (rest or _size(out) + _size(lines) > budget):
            rest.append(title)
            continue
        out += lines
        if explain_lines and not folded:
            shown_explains.add(explain_lines)  # 全文を出したカードの説明だけを基準にする
    if rest:
        head = FOLD_HEAD.format(what=what)
        room = (budget - _size(out) - len(head) - 4) if budget is not None else 0
        shown: list[str] = []
        for t in rest:
            if len("、".join(shown + [t])) > max(room, 0):
                break
            shown.append(t)
        if not shown:
            # 第 25 周 H14: 字数が尽きても題名を最低 1 件は出す（「: 、…」の空の列挙にしない）
            shown = [rest[0]]
        tail = "、".join(shown) + ("、…" if len(shown) < len(rest) else "")
        out.append(head + tail + "）")
    return out


def review_origin_text(decision: Any, fallback: str) -> str:
    """admin.js _reviewOriginText: 判断のある候補は出所の位置に provenance_line
    （「AIによる… → 〇〇が 日付 に採用しました」）。無ければ従来の出所ラベル（旧サーバも同じ）。"""
    if isinstance(decision, dict) and decision.get("provenance_line"):
        return str(decision["provenance_line"])
    return fallback


def review_decision_lines(decision: Any) -> list[str]:
    """admin.js _reviewDecisionLineHtml: provenance_line に入らない判断の 1 行（見送りから戻した等）。"""
    if not isinstance(decision, dict) or decision.get("provenance_line") or not decision.get("decision_line"):
        return []
    return [f"  {decision['decision_line']}"]


def review_list_notes(data: Any) -> list[str]:
    """admin.js _reviewListNotesHtml: 一覧の上のサーバの事実文（照合の状態 near_anchor_check.note・
    見送り済みゼロ dismissed_note）。文言はサーバが持つ。"""
    if not isinstance(data, dict):
        return []
    out = []
    check = data.get("near_anchor_check")
    if isinstance(check, dict) and check.get("note"):
        out.append(str(check["note"]))
    if data.get("dismissed_note"):
        out.append(str(data["dismissed_note"]))
    return out


def _gap_layer_text(c: dict) -> str:
    """admin.js _gapLayerBadgeText。"""
    if c.get("layer") == "concept":
        return f"概念（親: {c.get('parent_region_label') or c.get('parent_region_id') or ''}）"
    return str(c.get("layer_label") or "領域")


def gap_card_lines(c: dict, idx: int, draft_exists: bool, label_edit: str = "") -> list[str]:
    """admin.js _gapCandidateCardHtml（番号はペルソナが gap_hint で指すための投影の印）。"""
    decision = c.get("decision") if isinstance(c.get("decision"), dict) else {}
    status = decision.get("status") or "candidate"
    accepted = status == "accepted"
    suppressed = status in ("dismissed", "merged")
    head = [f"{idx}. {label_edit or c.get('proposed_label') or ''}", _gap_layer_text(c),
            review_origin_text(decision, GAP_ORIGIN_LABEL)]
    if decision.get("status_label"):
        head.append(f"[{decision['status_label']}]")
    if c.get("version_mismatch"):
        ver = next((d.get("skeleton_version") for d in c.get("documents") or []
                    if isinstance(d, dict) and d.get("version_mismatch") and d.get("skeleton_version")), "")
        if ver:
            head.append(f"この候補は版 {ver} の地図に対するものです")
    lines = ["  ".join(head)] + review_decision_lines(decision)
    # admin.js _gapCandidateCardHtml（第 3 波・IK-0938）: 採用済み・未反映の候補はサーバの pending_note を判断の行の下に
    if c.get("pending_note"):
        lines.append(f"  {c['pending_note']}")
    lines += gap_document_lines(c)
    near = c.get("near_anchor") if isinstance(c.get("near_anchor"), dict) else None
    if near and near.get("node_label"):
        parts = [p for p in (near.get("nearness_label"), atlas_version_text(near.get("skeleton_version"))) if p]
        lines.append(f"  既存の『{near['node_label']}』の別表記の可能性があります" + (f"（{'・'.join(parts)}）" if parts else ""))
    if decision.get("review_note"):
        lines.append(f"  {decision_note_head(decision)}: {decision['review_note']}")
    if suppressed:
        lines.append("  ボタン: [見送りから戻す]")
    else:
        buttons = ["[採用]" + ("（押せない）" if accepted else ""), "[却下…]"]
        if near and near.get("node_id"):
            buttons.append("[別名として登録]")
        if accepted and not draft_exists:
            buttons.append("[現在の版から次版の下書きを作る]")
        else:
            off = not accepted or not draft_exists or c.get("parent_region_at_capacity")
            buttons.append("[次版の下書きに取り込む…]" + ("（押せない）" if off else ""))
        lines.append("  ボタン: " + " ".join(buttons))
        lines.append(f"  {GAP_ACCEPT_NOTE}")
        blocked = (GAP_AT_CAPACITY_TEXT if c.get("parent_region_at_capacity") else
                   GAP_NOT_ACCEPTED_TEXT if not accepted else GAP_NO_DRAFT_TEXT if not draft_exists else "")
        if blocked:
            lines.append(f"  {blocked}")
    return lines


def gap_document_lines(c: Any) -> list[str]:
    """admin.js _gapDocumentsHtml: 「この項目に触れている論文」の題名の列挙（開くと理由と原文の逐語）。
    通常の候補と、導出から外れた採用済みの判断（第 3 波・IK-0937）の両方が同じ形で描く。"""
    docs = [d for d in ((c or {}).get("documents") or []) if isinstance(d, dict)] if isinstance(c, dict) else []
    if not docs:
        return []
    lines = [f"  {GAP_DOCUMENTS_HEAD}:"]
    for d in docs:
        lines.append(f"  ▸ {d.get('title') or '無題の論文'}")
        if d.get("reason"):
            lines.append(f"      {d['reason']}")
        if d.get("evidence_quote"):
            lines.append(f"      「{d['evidence_quote']}」")
    return lines


def decided_card(c: Any) -> bool:
    """畳まないカード（第 25 周 H18）: 判断のある（未判断でない）候補か、下書きに入っている候補。"""
    if not isinstance(c, dict):
        return False
    decision = c.get("decision") if isinstance(c.get("decision"), dict) else {}
    return str(decision.get("status") or "candidate") != "candidate" or bool(c.get("in_draft"))


def gaps_lines(session: PersonaSession, budget: Optional[int] = None) -> list[str]:
    """admin.js renderGapCandidates（404 = 骨格の無い分野はグループごと出さない）。"""
    data = session.scratch.get("atlas_gaps")
    if not isinstance(data, dict):
        return []
    filt = "オン" if session.scratch.get("atlas_gap_include_dismissed") else "オフ"
    lines = [f"{GAP_GROUP_TITLE}（見送り済みも表示: {filt}）", GAP_GROUP_INTRO] + review_list_notes(data)
    cands = [c for c in (data.get("candidates") or []) if isinstance(c, dict)]
    orphan_items = [i for i in (data.get("orphaned_decisions") or []) if isinstance(i, dict)]
    numbers = display_numbers(session, "gap", cands + orphan_items, "cluster_key")
    if cands or orphan_items:
        lines.append(CARD_NUMBER_NOTE)
    orphaned = gap_orphaned_lines(session, data)
    if not cands:
        # admin.js _reviewEmptyHtml: 従来の一文 + 候補ゼロの理由（empty_notes・IK-0851 第 2 周）
        return lines + [GAP_EMPTY_TEXT] + review_empty_notes(data) + orphaned
    edits = session.scratch.get("atlas_gap_label_edits") or {}
    cards = []
    for c in cands:
        key = str(c.get("cluster_key") or "")
        label = edits.get(key, "") or str(c.get("proposed_label") or "")
        card = gap_card_lines(c, numbers.get(key, 0), bool(data.get("draft_exists")), edits.get(key, ""))
        if card:
            card[0] += previous_number_note(session, "gap", key, numbers.get(key, 0))
        cards.append((label, card))
    rest = None if budget is None else budget - _size(lines) - _size(orphaned)
    return lines + fold_cards(cards, GAP_EXPLAIN_LINES, rest, "論文の解析から見つかった候補",
                              keep=[decided_card(c) for c in cands]) + orphaned


def review_empty_notes(data: Any) -> list[str]:
    """admin.js _reviewEmptyHtml の 2 行目以降（サーバの ``empty_notes`` を素通し）。"""
    if not isinstance(data, dict):
        return []
    return [str(n) for n in (data.get("empty_notes") or []) if n]


def gap_orphaned_lines(session: PersonaSession, data: Any) -> list[str]:
    """admin.js _gapOrphanedHtml（IK-0884）: 導出から外れた「採用済み・未反映」の判断の別枠。

    番号はふつうの候補と同じ表（cluster_key に固定）— 候補から外れても同じ番号で指せる。
    ボタン: [候補へ戻す] + 下書きが無ければ[現在の版から次版の下書きを作る]、あれば[次版の下書きに取り込む…]
    （``in_draft`` なら押せない）。
    """
    if not isinstance(data, dict):
        return []
    items = [i for i in (data.get("orphaned_decisions") or []) if isinstance(i, dict)]
    if not items:
        return []
    numbers = card_numbers(session, "gap", items, "cluster_key")
    draft_exists = bool(data.get("draft_exists"))
    lines = [GAP_ORPHANED_TITLE]
    if data.get("orphaned_note"):
        lines.append(str(data["orphaned_note"]))
    for item in items:
        decision = item.get("decision") if isinstance(item.get("decision"), dict) else {}
        n = numbers.get(str(item.get("cluster_key") or ""), 0)
        lines.append("  ".join(x for x in (f"{n}. {item.get('proposed_label') or ''}",
                                            str(item.get("layer_label") or "")) if x))
        lines += review_decision_lines(decision)
        if decision.get("provenance_line"):
            lines.append(f"  {decision['provenance_line']}")
        if item.get("reason_fact"):
            lines.append(f"  {item['reason_fact']}")
        # 第 3 波（IK-0937）: 外れた判断にも通常の候補と同じ根拠（題名・理由・逐語）。閲覧できない論文を外したときは
        # 固定文 1 行（admin.js GAP_DOCUMENTS_HIDDEN_TEXT）。判断のメモはその下（旧サーバは documents が無く何も出ない）
        lines += gap_document_lines(item)
        if item.get("documents_hidden"):
            lines.append(f"  {GAP_DOCUMENTS_HIDDEN_TEXT}")
        if decision.get("review_note"):
            lines.append(f"  {decision_note_head(decision)}: {decision['review_note']}")
        if item.get("in_draft_note"):
            lines.append(f"  {item['in_draft_note']}")
        buttons = ["[候補へ戻す]"]
        if not draft_exists:
            buttons.append("[現在の版から次版の下書きを作る]")
        else:
            buttons.append("[次版の下書きに取り込む…]" + ("（押せない）" if item.get("in_draft") else ""))
        lines.append("  ボタン: " + " ".join(buttons))
    return lines


def edge_card_lines(c: dict, idx: int, draft_exists: bool) -> list[str]:
    """admin.js _edgeCandidateCardHtml。"""
    decision = c.get("decision") if isinstance(c.get("decision"), dict) else {}
    status = decision.get("status") or "candidate"
    accepted = status == "accepted"
    suppressed = status == "dismissed"
    pair = _edge_pair_label(c)
    chips = []
    for origin in c.get("origins") or []:
        if origin == "vector":
            # admin.js _edgeOriginChipsHtml（IK-0887）: 近さの段階ラベルは描かない（basis_line が何が近いかを言う）
            chips.append(EDGE_ORIGIN_VECTOR_LABEL)
        elif origin == "co_occurrence":
            chips.append(EDGE_ORIGIN_CO_OCCURRENCE_LABEL)
    head = [f"{idx}. {pair}"] + chips + [review_origin_text(decision, EDGE_ORIGIN_NOTE)]
    if decision.get("status_label"):
        head.append(f"[{decision['status_label']}]")
    if decision.get("applied_version"):
        head.append(f"版{decision['applied_version']}で反映済み")
    lines = ["  ".join(head)] + review_decision_lines(decision)
    # admin.js（IK-0886 / IK-0887）: 根拠の一文・向き・下書きの状態をチップの下に
    # 第 3 波（IK-0938）: 採用済み・未反映の pending_note を根拠の一文より前に（admin.js _edgeCandidateCardHtml の順）
    for key in ("pending_note", "basis_line", "direction_line", "in_draft_note"):
        if c.get(key):
            lines.append(f"  {c[key]}")
    in_draft = bool(c.get("in_draft"))
    docs = [d for d in (c.get("documents") or []) if isinstance(d, dict)]
    if docs:
        # admin.js _edgeDocumentsHtml: endpoints があれば両端それぞれの配置の理由と逐語（IK-0849）
        lines.append(f"  {EDGE_DOCUMENTS_HEAD}:")
        for d in docs:
            lines.append(f"  ▸ {d.get('title') or '無題の論文'}")
            for e in d.get("endpoints") or []:
                if not isinstance(e, dict):
                    continue
                lines.append(f"      {e.get('node_label') or ''}")
                if e.get("reason"):
                    lines.append(f"        {e['reason']}")
                if e.get("evidence_quote"):
                    lines.append(f"        「{e['evidence_quote']}」")
    if decision.get("review_note"):
        lines.append(f"  {decision_note_head(decision)}: {decision['review_note']}")
    if suppressed:
        lines.append("  ボタン: [見送りから戻す]")
    else:
        kind = EDGE_KIND_LABELS.get(str(decision.get("edge_kind") or ""), "関係の種類を選ぶ")
        off = not accepted or not draft_exists or in_draft
        # 第 10 波（H54）: 下書きに入った採用（dismiss_locked）は［見送り…］を描かず、ボタンの下に dismiss_locked_note
        dismiss = "" if c.get("dismiss_locked") else "[見送り…] "
        # admin.js: 採用済みの辺は[採用]と種類の選択を押せなくする（IK-0848）
        lines.append(f"  関係の種類: {kind}（隣接 / 依存 / 関連）{'（選べない）' if accepted else ''}"
                     f"  ボタン: [採用]{'（押せない）' if accepted else ''} {dismiss}"
                     f"[次版の下書きへ反映…]{'（押せない）' if off else ''}")
        if c.get("dismiss_locked_note"):
            lines.append(f"  {c['dismiss_locked_note']}")
        lines.append(f"  {EDGE_ACCEPT_NOTE}")
        blocked = ("" if in_draft
                   else (EDGE_NOT_ACCEPTED_TEXT if draft_exists else EDGE_NOT_ACCEPTED_NO_DRAFT_TEXT) if not accepted
                   else EDGE_NO_DRAFT_TEXT if not draft_exists else "")
        if blocked:
            lines.append(f"  {blocked}")
    return lines


#: 第 10 波（H54）: dismiss_locked のカードに［見送り…］が無いことのハーネスの注記（画面の文ではない）
EDGE_DISMISS_LOCKED_HARNESS = "（このカードには［見送り…］が出ていない — 次版の下書きに入った採用は見送れない。カードの事実文を読む）"


def edge_draft_create_lines(data: Any, retired: bool = False) -> list[str]:
    """admin.js ``_edgeDraftCreateHtml``（IK-1044・第 7 波）: 下書きが無いときだけ事実文 1 行 +［現在の版から次版の
    下書きを作る］（廃止済みの分野はボタン無効 + 理由の文）。下書きがあれば何も出さない。"""
    if not isinstance(data, dict) or data.get("draft_exists"):
        return []
    note = EDGE_DRAFT_CREATE_RETIRED_NOTE if retired else EDGE_DRAFT_CREATE_NOTE
    return [f"{note} [{EDGE_DRAFT_CREATE_BUTTON}]" + ("（押せない）" if retired else "")]


def edges_lines(session: PersonaSession, budget: Optional[int] = None) -> list[str]:
    """admin.js renderEdgeCandidates（budget を越えるカードは題名の列挙に畳む — H1）。"""
    data = session.scratch.get("atlas_edges")
    if not isinstance(data, dict):
        return []
    filt = "オン" if session.scratch.get("atlas_edge_include_dismissed") else "オフ"
    lines = [f"{EDGE_GROUP_TITLE}（見送り済みも表示: {filt}）", EDGE_GROUP_INTRO] + review_list_notes(data) \
        + edge_draft_create_lines(data, bool(session.scratch.get("atlas_domain_retired")))
    # 第 5 波（IK-0993）: 保存済みアンカーが無いときの index_note は一覧の上（候補ゼロのときも同じ位置）
    if data.get("index_note"):
        lines.append(str(data["index_note"]))
    # 第 5 波（IK-0994）: 判断の結果文 result_line は区画の状態行に出したまま一覧を読み直す
    if session.scratch.get("atlas_edge_result_line"):
        lines.append(str(session.scratch.pop("atlas_edge_result_line")))
    cands = [c for c in (data.get("candidates") or []) if isinstance(c, dict)]
    orphan_items = [i for i in (data.get("orphaned_decisions") or []) if isinstance(i, dict)]
    shown_all_now = bool(session.scratch.pop("atlas_edge_renumber_pending", False))
    had_numbers = bool(session.scratch.get("atlas_edge_numbers"))
    numbers = display_numbers(session, "edge", cands + orphan_items, "edge_key")
    if cands or orphan_items:
        lines.append(CARD_NUMBER_NOTE)
    orphaned = edge_orphaned_lines(session, data)
    if not cands:
        return lines + [EDGE_EMPTY_TEXT] + review_empty_notes(data) + orphaned
    previous = session.scratch.get("atlas_edge_numbers_previous") or {}
    cards = []
    for c in cands:
        key = str(c.get("edge_key") or "")
        card = edge_card_lines(c, numbers.get(key, 0), bool(data.get("draft_exists")))
        if card:
            note = previous_number_note(session, "edge", key, numbers.get(key, 0))
            if not note and shown_all_now and had_numbers and previous.get(key) is None:
                note = "（［すべて表示］で新しく出た候補）"
            card[0] += note
        cards.append((_edge_pair_label(c), card))
    # admin.js renderEdgeCandidates（IK-0887）: 近さだけの候補を畳んだ事実文 + [すべて表示]
    folded = [f"{data['folded_note']} [{EDGE_SHOW_ALL_BUTTON}]"] if data.get("folded_note") else []
    rest = None if budget is None else budget - _size(lines) - _size(orphaned) - _size(folded)
    return lines + fold_cards(cards, EDGE_EXPLAIN_LINES, rest, "関係の候補",
                              keep=[decided_card(c) for c in cands]) + folded + orphaned


def edge_orphaned_lines(session: PersonaSession, data: Any) -> list[str]:
    """admin.js _edgeOrphanedHtml（IK-0884 と同じ構造）: ボタンは[見送り…]と[次版の下書きへ反映…]。"""
    if not isinstance(data, dict):
        return []
    items = [i for i in (data.get("orphaned_decisions") or []) if isinstance(i, dict)]
    if not items:
        return []
    numbers = card_numbers(session, "edge", items, "edge_key")
    draft_exists = bool(data.get("draft_exists"))
    lines = [EDGE_ORPHANED_TITLE]
    if data.get("orphaned_note"):
        lines.append(str(data["orphaned_note"]))
    # 第 7 波（IK-1044）: 「採用済み・未反映」の区画にも、反映の前提となる下書きの入口
    lines += edge_draft_create_lines(data, bool(session.scratch.get("atlas_domain_retired")))
    for item in items:
        decision = item.get("decision") if isinstance(item.get("decision"), dict) else {}
        lines.append(f"{numbers.get(str(item.get('edge_key') or ''), 0)}. {_edge_pair_label(item)}")
        lines += review_decision_lines(decision)
        if decision.get("provenance_line"):
            lines.append(f"  {decision['provenance_line']}")
        # admin.js _edgeOrphanedHtml（第 3 波・IK-0937 辺版）: 採用の根拠（記録された出所）と判断のメモ
        if item.get("basis_line"):
            lines.append(f"  {item['basis_line']}")
        if decision.get("review_note"):
            lines.append(f"  {decision_note_head(decision)}: {decision['review_note']}")
        for key in ("direction_line", "in_draft_note", "dismiss_locked_note"):  # 第 10 波（H54）: 見送れない理由
            if item.get(key):
                lines.append(f"  {item[key]}")
        off = not draft_exists or bool(item.get("in_draft"))
        dismiss = "" if item.get("dismiss_locked") else "[見送り…] "
        lines.append(f"  ボタン: {dismiss}[次版の下書きへ反映…]{'（押せない）' if off else ''}")
    return lines


def vector_lines(session: PersonaSession) -> list[str]:
    """admin.js renderVectorStatus（第 2 波: サーバの facts と built_at_label。件数・ISO 時刻は描かない）。"""
    data = session.scratch.get("atlas_vectors")
    if not isinstance(data, dict):
        return []
    lines = [f"{VECTOR_GROUP_TITLE}:"]
    if data.get("available") is False:
        return lines + [VECTOR_NO_SKELETON_TEXT, "[索引を再構築]（押せない）"]
    # admin.js renderVectorStatus（IK-0889・第 2 波）: 用途 → 要否の一文はサーバの facts を素通しし、
    # 日時は表示語（built_at_label）。保存値（件数・ISO 時刻）は描かない。facts の無い旧サーバは版と stale だけ
    facts = [str(f) for f in (data.get("facts") or []) if f]
    if facts:
        lines += facts
    else:
        if data.get("skeleton_version"):
            lines.append(atlas_version_text(data["skeleton_version"]))
        # 第 5 波（IK-0993・H33）: 凍結が裏で索引を作っている最中（building）は再構築を促す文を出さない
        if data.get("stale") and not data.get("building"):
            lines.append(VECTOR_STALE_TEXT)
    if data.get("built_at_label"):
        lines.append(f"最後に作成: {data['built_at_label']}")
    return lines + ["[索引を再構築]"]


def aliases_lines(session: PersonaSession) -> list[str]:
    """admin.js renderAliases（node_id ごとにまとめる。見送り済みは淡色で残す）。"""
    data = session.scratch.get("atlas_aliases")
    if not isinstance(data, dict):
        return []
    lines = [f"{ALIAS_GROUP_TITLE}:"]
    rows = [r for r in (data.get("aliases") or []) if isinstance(r, dict)]
    if not rows:
        return lines + [ALIAS_EMPTY_TEXT, ALIAS_DISMISS_NOTE]
    order: list[str] = []
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        nid = str(r.get("node_id") or "")
        if nid not in grouped:
            grouped[nid] = []
            order.append(nid)
        grouped[nid].append(r)
    for nid in order:
        first = grouped[nid][0]
        # 画面は見出しに項目の id を括弧で添える（「地図の項目のid」で手動登録するため）
        lines.append(f"- {first.get('node_label')}（{nid}）" if first.get("node_label") else f"- {nid}")
        for r in grouped[nid]:
            tail = "見送り済み" if r.get("status") == "dismissed" else "[見送り]"
            lines.append(f"    {r.get('alias') or ''}  {tail}")
    return lines + [ALIAS_DISMISS_NOTE]


BUDGETED_SECTIONS = ("gaps_lines", "edges_lines")


def _tab_fact(ctx, *sections: Callable[..., list[str]], head: Optional[list[str]] = None,
              status: Optional[int] = None) -> None:
    """区画を画面の縦の順に並べて投影する。候補カードの区画（gaps / edges）は、ほかの区画を置いた残りの
    字数を予算として受け取り、越える分を題名の列挙に畳む（H1 — 観察の上限で無言に切れない）。"""
    from uxsim.runner.state import OBSERVATION_CHARS  # 観察の上限の正本

    parts: dict[int, list[str]] = {}
    for i, fn in enumerate(sections):
        if getattr(fn, "__name__", "") not in BUDGETED_SECTIONS:
            parts[i] = fn(ctx.session)
    remaining = OBSERVATION_CHARS - OBSERVATION_MARGIN - _size(list(head or [])) \
        - sum(_size(p) + 1 for p in parts.values())
    for i, fn in enumerate(sections):
        if i not in parts:
            parts[i] = fn(ctx.session, budget=max(remaining, 0))
            remaining -= _size(parts[i]) + 1
    lines = list(head or [])
    for i in range(len(sections)):
        part = parts[i]
        if part:
            if lines:
                lines.append("")
            lines += part
    _fact(ctx, "\n".join(lines), status)


# ---------------------------------------------------------------------------
# 教員: 行為
# ---------------------------------------------------------------------------
def _open(ctx) -> None:
    """分野の選択（loadCartridges の選択肢 → select.change → loadState）。"""
    s = ctx.session
    status, body = ctx.main()
    domains = [d for d in ((body or {}).get("domains") or []) if isinstance(d, dict)] if isinstance(body, dict) else []
    if status != 200:
        return _fact(ctx, f"分野の一覧を読み込めませんでした: {_detail(body) or f'HTTP {status}'}", status)
    hint = ctx.args.get("domain_hint") or ctx.args.get("cartridge_id") or s.scratch.get("atlas_domain")
    picked = _hint_pick(domains, hint, ("domain_key", "domain_name"))
    if picked is None:
        names = "、".join(str(d.get("domain_name") or d.get("domain_key") or "") for d in domains)
        return _fact(ctx, f"「{hint}」に合う分野が選択肢に無い。選択肢: {names or '（なし）'}", 200)
    if s.scratch.get("atlas_domain") != str(picked.get("domain_key") or ""):
        s.scratch["atlas_edge_show_all"] = False  # admin.js select.change: edgeShowAll = false
        s.scratch["atlas_published_open"] = False  # 分野を切り替えると折りたたみは閉じた状態から
    s.scratch["atlas_domain"] = str(picked.get("domain_key") or "")
    s.scratch["atlas_domain_name"] = str(picked.get("domain_name") or picked.get("domain_key") or "")
    s.scratch["atlas_domain_retired"] = str(picked.get("lifecycle") or "active") == "retired"  # domainLifecycles
    _load_state(ctx)
    # 製品の縦の並び: マップの区画（概要 → ベクトル索引 → 登録済みの別名）→ 修正報告の区画（ギャップ候補 → 関係の候補）
    _tab_fact(ctx, overview_lines, vector_lines, aliases_lines, reports_lines, gaps_lines, edges_lines, status=200)


REPORT_STATUS_LABELS = {"pending": "レビュー待ち", "accepted": "採用済み", "declined": "見送り", "merged": "重複統合"}
#: 報告者の名前が無いときの JS の既定（ACCOUNT_NAME_UNREGISTERED_ACTOR・第 5 波 IK-0848 / IK-1007）
REPORTER_UNREGISTERED = "名前を登録していない利用者"


def reports_lines(session: PersonaSession) -> list[str]:
    """admin.js renderReports（既定の絞り込み = レビュー待ち）。第 5 波（IK-0848 / IK-1007）: 報告者は
    ``reporter_label``（本人が登録した名前・未登録は「名前を登録していない利用者」）。ハーネスで名前を作らない。"""
    data = session.scratch.get("atlas_reports")
    if not isinstance(data, dict):
        return []
    status_filter = str(session.scratch.get("atlas_report_filter") or "pending")
    rows = [r for r in data.get("reports") or [] if isinstance(r, dict) and (not status_filter or r.get("status") == status_filter)]
    lines = [f"修正報告（表示: {REPORT_STATUS_LABELS.get(status_filter, status_filter or 'すべて')}）:"]
    if not rows:
        return lines + ["該当する報告はありません。"]
    for r in rows:
        target = f"ノード {r['node_id']}" if r.get("node_id") else f"領域 {r.get('region_id') or ''}"
        if r.get("node_label"):
            target += f"（{r['node_label']}）"
        label = REPORT_STATUS_LABELS.get(str(r.get("status")), str(r.get("status") or ""))
        if r.get("status") == "accepted":
            label = ("公開版に反映済み" if r.get("applied_version") else "次版で対応済み" if r.get("incorporated_at")
                     else "採用・次版で対応待ち")
        head = f"- {target} レベル{r.get('level') or ''} 骨格 {r.get('skeleton_version') or ''}"
        if r.get("version_mismatch"):
            head += f" 〔旧版（現行 {data.get('current_version') or '—'}）〕"
        head += f" ［{label}］"
        if (r.get("open_count_for_target") or 0) > 1:
            head += f" 同一対象の未クローズ報告: {r['open_count_for_target']}件"
        lines.append(head)
        lines.append(f"  {r.get('report_text') or ''}")
        created = str(r.get("created_at") or "")[:16].replace("T", " ")
        lines.append(f"  報告者: {r.get('reporter_label') or r.get('reporter_name') or REPORTER_UNREGISTERED} ／ {created}")
        for key, lead in (("resolution_note", "処理メモ: "), ("incorporation_note", "次版での対応: ")):
            if r.get(key):
                lines.append(f"  {lead}{r[key]}")
        if r.get("status") == "pending":
            lines.append("  ［採用（次版へ）］［見送り（理由つき）］［重複統合］")
    return lines


def _published_view(ctx) -> None:
    """``#atlas-published-view`` の summary を開く（第 5 波・IK-0992）。下書きがあるときは出ない。"""
    if not _need_domain(ctx):
        return
    s = ctx.session
    _load_skeleton(ctx)
    state = s.scratch.get("atlas_state") or {}
    frozen = (state.get("frozen") or {}).get("skeleton") if isinstance(state.get("frozen"), dict) else None
    if not frozen:
        return _fact(ctx, harness_note("（公開中の版の折りたたみは表示されていない — この分野には公開中の版が無い）"), 200)
    # 第 6 波: 下書きがある間も出る（見出しが変わるだけ）
    s.scratch["atlas_published_open"] = True
    _fact(ctx, "\n".join(published_view_lines(frozen, True, with_draft=isinstance(state.get("draft"), dict),
                                               version=(state.get("frozen") or {}).get("version"))), 200)


# 教材管理の行［⋯］→［位置づけ（分野マップ）…］（admin.js renderLandscapePlacements・第 5 波 IK-0995 で到達口として案内される）
LANDSCAPE_EMPTY_NOTICE = "配置候補はまだありません。[AIで再提案] で生成できます。"
LANDSCAPE_CORPUS_NOTICE = ("この一覧は解析パイプラインが生成した配置候補（AI推定）と、教員の確認結果です。"
                           "確認していない候補も学習者には「AIによる推定（未確認）」として表示され、却下すると表示されなくなります。")
LANDSCAPE_GAP_SIGNAL_NOTICE = "この分野の地図への候補として記録されています（分野の地図タブで確認できます）"
LANDSCAPE_STATUS_FALLBACK = {"inferred": "AI推定（未確認）", "confirmed": "教員確認済み", "rejected": "却下",
                             "review_required": "再検討中", "superseded": "再解析により置換"}
LANDSCAPE_NODE_STATUS_LABELS = {"migrated": "前の版から対応づけ", "unmapped": "現行版に対応する場所なし"}
LANDSCAPE_STATUS_ACTIONS = (("confirmed", "確認"), ("rejected", "却下"), ("review_required", "再検討"))


def landscape_modal_lines(data: Any) -> list[str]:
    """admin.js renderLandscapePlacements の写し（ドメイン別グループ → 行 → 置けなかった分野 → フッター）。
    原文の引用は既定で閉じた折りたたみ。置けなかった分野の行は ``gap_topics``（第 5 波・IK-0995）を名前で添える。"""
    data = data if isinstance(data, dict) else {}
    placements = [p for p in data.get("placements") or [] if isinstance(p, dict)]
    unplaced = [u for u in data.get("unplaced_domains") or [] if isinstance(u, dict)]
    lines: list[str] = []
    if not placements and not unplaced:
        lines.append(LANDSCAPE_EMPTY_NOTICE)
    groups: dict[str, dict] = {}
    for p in placements:
        key = str(p.get("domain_key") or "")
        g = groups.setdefault(key, {"domain_key": key, "domain_name": p.get("domain_name") or key, "rows": []})
        g["rows"].append(p)

    def frozen_version(g: dict) -> str:
        for d in data.get("domains") or []:
            if isinstance(d, dict) and d.get("domain_key") == g["domain_key"]:
                return str(d.get("frozen_version") or d.get("skeleton_version") or "")
        for r in g["rows"]:
            if r.get("skeleton_version"):
                return str(r["skeleton_version"])
        return ""

    versions = []
    for g in groups.values():
        ver = frozen_version(g)
        lines.append(f"{g['domain_name']}" + (f"（{atlas_version_text(ver)}）" if ver else ""))
        if ver:
            versions.append(f"{g['domain_name']} {atlas_version_text(ver)}")
        for p in g["rows"]:
            meta = " ・ ".join(str(x) for x in (p.get("perspective_label"), p.get("weight_label")) if x)
            status = str(p.get("status") or "")
            chip = str(p.get("status_label") or LANDSCAPE_STATUS_FALLBACK.get(status) or status or "不明")
            node_chip = LANDSCAPE_NODE_STATUS_LABELS.get(str(p.get("node_status") or ""))
            lines.append(f"- {p.get('node_label') or p.get('node_id') or ''}" + (f"（{meta}）" if meta else "")
                         + f"［{chip}］" + (f"〔{node_chip}〕" if node_chip else ""))
            if p.get("reason"):
                lines.append("  " + (f"〔{p['reason_label']}〕" if p.get("reason_label") else "") + str(p["reason"]))
                if p.get("reason_note"):
                    lines.append(f"  {p['reason_note']}")
            quotes = [e for e in p.get("evidence") or [] if isinstance(e, dict) and e.get("quote")]
            lines.append("  ▸ 原文の引用を見る（折りたたみ・閉じている）" if quotes else "  原文の引用はありません")
            if p.get("review_note"):
                lines.append(f"  メモ: {p['review_note']}")
            lines.append("  " + " ".join(f"[{lb}]" + ("（押せない — いまの状態）" if st == status else "")
                                         for st, lb in LANDSCAPE_STATUS_ACTIONS))
    if unplaced:
        lines.append("配置できなかった分野")
        for u in unplaced:
            lines.append(f"- この論文は「{u.get('domain_name') or u.get('domain_key') or ''}」の地図に配置できませんでした: "
                         f"{u.get('reason') or ''}")
            if u.get("gap_signals_recorded"):
                topics = [str(t) for t in u.get("gap_topics") or [] if t]
                lines.append("  " + (f"置けなかった主題: {'、'.join(topics)}。" if topics else "") + LANDSCAPE_GAP_SIGNAL_NOTICE)
    lines.append(LANDSCAPE_CORPUS_NOTICE)
    facts = []
    if data.get("last_run_at") or data.get("generated_at"):
        facts.append(f"最終生成: {data.get('last_run_at') or data.get('generated_at')}")
    if versions:
        facts.append("使用した地図: " + " ・ ".join(versions))
    if facts:
        lines.append(" ・ ".join(facts))
    return lines


def _landscape_view(ctx) -> None:
    """教材管理の行［⋯］→［位置づけ（分野マップ）…］のモーダルを読む（読むだけ。確認・却下・再提案はしない）。"""
    from uxsim.runner.share_exec import pick_material  # 循環を避ける（share_exec は atlas_exec を import しない）
    doc = str(ctx.args.get("document_id") or "").strip()
    title = ""
    if not doc:
        st, body = _call(ctx, "GET", "/api/admin/materials")
        rows = [m for m in body or [] if isinstance(m, dict)] if st == 200 and isinstance(body, list) else []
        mat = pick_material(rows, ctx.args.get("material_hint"), ctx.args.get("format_hint"))
        if mat is None or not mat.get("document_id"):
            names = "、".join(str(m.get("title") or m.get("filename") or "") for m in rows[:12])
            return _fact(ctx, f"「{ctx.args.get('material_hint') or ''}」に合う教材の行が無い。教材: {names or '（なし）'}", st)
        doc = str(mat["document_id"])
        title = str(mat.get("title") or mat.get("filename") or "")
    status, body = ctx.main(overrides={"ref": doc, "document_id": doc})
    if status != 200 or not isinstance(body, dict):
        return _fact(ctx, f"位置づけ（分野マップ）を読み込めませんでした: {_detail(body) or f'HTTP {status}'}", status)
    head = [f"位置づけ（分野マップ）— {title}" if title else "位置づけ（分野マップ）"]
    _fact(ctx, "\n".join(head + landscape_modal_lines(body) + ["[AIで再提案] [閉じる]"]), status)


def _gaps_list(ctx) -> None:
    if not _need_domain(ctx):
        return
    status, body = _load_gaps(ctx)
    if status == 404:
        return _fact(ctx, harness_note(f"（{GAP_GROUP_TITLE}の区画は表示されていない — この分野には凍結済みの骨格が無い）"), status)
    if status != 200:
        return _fact(ctx, f"候補の読み込みに失敗しました: HTTP {status}", status)
    _tab_fact(ctx, gaps_lines, status=status)


def _gaps_filter(ctx) -> None:
    if not _need_domain(ctx):
        return
    s = ctx.session
    on = ctx.args.get("show")
    s.scratch["atlas_gap_include_dismissed"] = (not s.scratch.get("atlas_gap_include_dismissed")) if on is None else bool(on)
    _gaps_list(ctx)


def _pick_gap(ctx, *, want: Optional[tuple[str, ...]] = None) -> Optional[dict]:
    data = ctx.session.scratch.get("atlas_gaps")
    if not isinstance(data, dict):
        _load_gaps(ctx)
        data = ctx.session.scratch.get("atlas_gaps")
    cands = [c for c in ((data or {}).get("candidates") or []) if isinstance(c, dict)]
    hint = ctx.args.get("gap_hint")
    if want and not str(hint or "").strip():
        cands_w = [c for c in cands if str((c.get("decision") or {}).get("status") or "candidate") in want]
        if want == ("accepted",):  # 取り込みは、まだ下書きに入っていない採用を先に（H25）
            cands_w = [c for c in cands_w if unreflected(c)] or cands_w
        return cands_w[0] if cands_w else None
    numbers = card_numbers(ctx.session, "gap", cands, "cluster_key")
    return _hint_pick(cands, hint, ("proposed_label", "normalized_label", "parent_region_label"),
                      numbers, "cluster_key")


def _gap_decide(ctx, action: str, note: str, cluster_key: str) -> tuple[Optional[int], Any]:
    status, body = _call(ctx, "POST", _CART + "/gap-candidates/decide",
                         json={"cluster_key": cluster_key, "action": action, "review_note": note or ""})
    return status, body


def prompt_line(prompt_text: str, written: str) -> str:
    """prompt ダイアログの投影 1 行（「（見出し — 説明）に「…」と書いた」/ 空のまま OK）。"""
    shown = prompt_text.replace("\n", " — ")
    return f"（{shown}）" + (f"に「{written}」と書いた" if written else "は空のまま OK した")


def _after_gap_decide(ctx, status, body, done: str = "", head: Optional[list[str]] = None) -> None:
    pre = list(head or [])
    if status is not None and 200 <= status < 300:
        _load_gaps(ctx)
        _tab_fact(ctx, gaps_lines, head=(pre + [done] if done else pre) or None, status=status)
    else:
        _tab_fact(ctx, gaps_lines, head=pre + [f"処理に失敗しました: {_detail(body) or f'HTTP {status}'}"],
                  status=status)


def _decide_gap(action: str, want: tuple[str, ...]) -> Callable[[Any], None]:
    def run(ctx) -> None:
        if not _need_domain(ctx):
            return
        picked = _pick_gap(ctx, want=want)
        if picked is None:
            ctx.missing("gap_candidate")
            return _tab_fact(ctx, gaps_lines, head=[harness_note("（指した候補が一覧に無い）")])
        status_now = str((picked.get("decision") or {}).get("status") or "candidate")
        if action == "restore" and status_now not in ("dismissed", "merged"):
            ctx.missing("gap_dismissed")
            return _tab_fact(ctx, gaps_lines, head=[harness_note("（この候補には[見送りから戻す]が出ていない）")])
        if action == "accept" and status_now == "accepted":
            ctx.missing("gap_not_accepted")
            return _tab_fact(ctx, gaps_lines, head=[harness_note("（[採用] は押せない — この候補は採用済み）")])
        note = ""
        head: list[str] = []
        if action == "accept":
            # admin.js handleGapAction（第 3 波・IK-0939）: prompt「採用のメモ（任意）…」。書いた文が review_note
            # （凍結する教員が止め理由と一緒に読む）。空でも送る（取消 = 送らない、はハーネスでは起きない）
            note = str(ctx.args.get("memo") or "").strip()
            head = [prompt_line(GAP_ACCEPT_MEMO_PROMPT, note)]
        if action == "dismiss":
            note = str(ctx.args.get("reason") or "")
            if not note.strip():
                # admin.js handleGapAction: prompt が空なら送らずに事実文
                ctx.missing("reason")
                return _tab_fact(ctx, gaps_lines, head=[f"（却下の理由（必須） — {GAP_DISMISS_NOTE}）",
                                                        GAP_DISMISS_REASON_REQUIRED])
        status, body = _gap_decide(ctx, action, note, str(picked.get("cluster_key") or ""))
        _after_gap_decide(ctx, status, body, head=head)
    return run


def _gap_alias_register(ctx) -> None:
    """admin.js gapRegisterAlias: 登録（source=gap_signal）→ 成功時だけ理由を自動で書いて却下 → 一覧の読み直し。"""
    if not _need_domain(ctx):
        return
    s = ctx.session
    auto = str(ctx.args.get("gap_hint") or "").strip().casefold() in ("", "auto", "注記", "注記のある候補")
    picked = None if auto else _pick_gap(ctx)
    if auto:
        data = s.scratch.get("atlas_gaps") or {}
        picked = next((c for c in data.get("candidates") or [] if isinstance(c, dict)
                       and (c.get("near_anchor") or {}).get("node_id")
                       and str((c.get("decision") or {}).get("status") or "candidate") not in ("dismissed", "merged")),
                      None)
    near = (picked or {}).get("near_anchor") if isinstance((picked or {}).get("near_anchor"), dict) else None
    if not picked or not near or not near.get("node_id"):
        ctx.missing("near_anchor")
        return _tab_fact(ctx, gaps_lines, head=[harness_note("（この候補には[別名として登録]が出ていない — 近い既存項目の注記が無い）")])
    key = str(picked.get("cluster_key") or "")
    alias = (s.scratch.get("atlas_gap_label_edits") or {}).get(key) or str(picked.get("proposed_label") or "")
    status, body = _call(ctx, "POST", _CART + "/aliases",
                         json={"node_id": near["node_id"], "alias": alias, "source": "gap_signal",
                               "evidence": {"cluster_key": key}})
    if status is None or status >= 400:
        _load_aliases(ctx)
        return _tab_fact(ctx, aliases_lines, gaps_lines,
                         head=[f"登録できませんでした: {_detail(body) or f'HTTP {status}'}"], status=status)
    _load_aliases(ctx)
    _load_vectors(ctx)
    note = GAP_ALIAS_NOTE_HEAD + str(near.get("node_label") or near["node_id"]) + GAP_ALIAS_NOTE_TAIL
    st2, b2 = _gap_decide(ctx, "dismiss", note, key)
    if st2 is not None and st2 < 400:
        _load_gaps(ctx)
        return _tab_fact(ctx, aliases_lines, gaps_lines, head=["別名として登録しました"], status=st2)
    _tab_fact(ctx, aliases_lines, gaps_lines, head=["別名として登録しました",
                                                    f"処理に失敗しました: {_detail(b2) or f'HTTP {st2}'}"], status=st2)


def _draft_from_frozen(ctx) -> None:
    """admin.js gapCreateDraftFromFrozen（confirm の文 → POST → loadState）。"""
    if not _need_domain(ctx):
        return
    s = ctx.session
    gaps = s.scratch.get("atlas_gaps") if isinstance(s.scratch.get("atlas_gaps"), dict) else {}
    # 第 25 周 H13: 製品のボタンは「採用済みのギャップ候補 ∧ 下書きなし」のときだけカードに出る（admin.js
    # renderGapCard の ``accepted && !draftExists``）。出ていないボタンの行為は行わない（HTTP を出さない）
    # 第 2 波: 候補の条件から外れた採用済みの判断（orphaned_decisions）のカードにも同じボタンが出る
    shown = not bool(gaps.get("draft_exists")) and not draft_known(s) and (any(
        isinstance(c, dict) and (c.get("decision") or {}).get("status") == "accepted"
        for c in gaps.get("candidates") or []) or bool(gaps.get("orphaned_decisions")))
    if not shown:
        return ctx.missing("draft_from_frozen_button")
    version = str(gaps.get("skeleton_version") or "")
    confirm = f"（確認）現行版 {version} を複製して下書きを作ります。下書きは学習者には表示されません → OK"
    status, body = ctx.main(overrides={"cartridge_id": _domain(ctx)})
    if status is None or status >= 400:
        return _fact(ctx, f"{confirm}\n下書きを作れませんでした: {_detail(body) or f'HTTP {status}'}", status)
    _load_skeleton(ctx)
    _load_gaps(ctx)
    _load_edges(ctx)
    _tab_fact(ctx, overview_lines, gaps_lines, edges_lines, head=[confirm, "次版の下書きを作りました"], status=status)


def _edge_draft_from_frozen(ctx) -> None:
    """関係の区画の［現在の版から次版の下書きを作る］（IK-1044・第 7 波）。admin.js は gapCreateDraftFromFrozen を
    setEdgesStatus で呼ぶ — 確認の文 → POST → 結果文は関係の区画の状態行（「次版の下書きを作りました」/
    「下書きを作れませんでした: {detail}」）→ loadState。"""
    if not _need_domain(ctx):
        return
    s = ctx.session
    edges = s.scratch.get("atlas_edges") if isinstance(s.scratch.get("atlas_edges"), dict) else {}
    if edges.get("draft_exists") or draft_known(s):
        return ctx.missing("edge_draft_from_frozen_button")
    if s.scratch.get("atlas_domain_retired"):
        ctx.missing("edge_draft_from_frozen_disabled")
        return _tab_fact(ctx, edges_lines, head=[f"（［{EDGE_DRAFT_CREATE_BUTTON}］は押せない: {EDGE_DRAFT_CREATE_RETIRED_NOTE}）"])
    version = str(edges.get("skeleton_version") or (s.scratch.get("atlas_gaps") or {}).get("skeleton_version") or "")
    confirm = f"（確認）現行版 {version} を複製して下書きを作ります。下書きは学習者には表示されません → OK"
    status, body = ctx.main(overrides={"cartridge_id": _domain(ctx)})
    if status is None or status >= 400:
        s.scratch["atlas_edge_result_line"] = f"下書きを作れませんでした: {_detail(body) or f'HTTP {status}'}"
        return _tab_fact(ctx, edges_lines, head=[confirm], status=status)
    s.scratch["atlas_edge_result_line"] = "次版の下書きを作りました"
    _load_skeleton(ctx)
    _load_gaps(ctx)
    _load_edges(ctx)
    _tab_fact(ctx, overview_lines, gaps_lines, edges_lines, head=[confirm], status=status)


def _issue_text(issue: Any) -> str:
    if isinstance(issue, str):
        return issue
    if isinstance(issue, dict):
        return str(issue.get("message") or "")
    return ""


def _gap_incorporate_preview(ctx) -> None:
    """admin.js gapIncorporate → openGapIncorporateConfirm（確認の画面まで）。"""
    if not _need_domain(ctx):
        return
    s = ctx.session
    picked = _pick_gap(ctx, want=("accepted",))
    if picked is None:
        # 第 25 周 H20: 採用済みの判断が候補の条件から外れて別枠（orphaned_decisions）にあるときは、製品の
        # 別枠カードの［次版の下書きに取り込む…］（同じ gapIncorporate）へ回す
        data = s.scratch.get("atlas_gaps") if isinstance(s.scratch.get("atlas_gaps"), dict) else {}
        if any(isinstance(i, dict) for i in data.get("orphaned_decisions") or []):
            if not str(ctx.args.get("gap_hint") or "").strip():
                first = next((i for i in data.get("orphaned_decisions") or []
                              if isinstance(i, dict) and not i.get("in_draft")), None)
                if first is not None:
                    numbers = card_numbers(s, "gap", [first], "cluster_key")
                    ctx.args["gap_hint"] = str(numbers.get(str(first.get("cluster_key") or ""), ""))
            if _pick_orphan(ctx, "gap") is not None:
                return _gap_orphan_incorporate(ctx)
        ctx.missing("gap_accepted")
        return _tab_fact(ctx, gaps_lines, head=[harness_note("（[次版の下書きに取り込む…] が押せる候補が無い）")])
    key = str(picked.get("cluster_key") or "")
    label = str(ctx.args.get("label") or "").strip()
    if label:
        s.scratch.setdefault("atlas_gap_label_edits", {})[key] = label
    label = label or (s.scratch.get("atlas_gap_label_edits") or {}).get(key, "")
    status, body = ctx.main(json={"cluster_key": key, "proposed_label": label},
                            overrides={"cartridge_id": _domain(ctx)})
    _gap_preview_fact(ctx, key, status, body)


def preview_decision_lines(preview: Any) -> list[str]:
    """admin.js openGapIncorporateConfirm / openEdgeIncorporateConfirm（第 3 波・IK-0939）: 誰の採用か
    （「採用: {decision_line}」）→ 根拠（basis_line）→ 採用のメモ（note_line）。旧サーバ（キーなし）は何も出ない。"""
    if not isinstance(preview, dict):
        return []
    out = [f"採用: {preview['decision_line']}"] if preview.get("decision_line") else []
    out += [str(preview[k]) for k in ("basis_line", "note_line") if preview.get(k)]
    return out


def _gap_preview_fact(ctx, key: str, status: Optional[int], body: Any) -> None:
    """admin.js openGapIncorporateConfirm（確認の画面まで）。"""
    s = ctx.session
    if status is None or status >= 400:
        return _tab_fact(ctx, gaps_lines, head=[f"取り込みを準備できませんでした: {_detail(body) or f'HTTP {status}'}"],
                         status=status)
    preview = body if isinstance(body, dict) else {}
    validation = preview.get("validation") or {}
    lines = []
    if preview.get("summary"):
        lines.append(str(preview["summary"]))
    # admin.js openGapIncorporateConfirm: id は出さない・親領域は名前（parent_region_label。IK-0850）
    if preview.get("proposed_label"):
        lines.append(f"名前: {preview['proposed_label']}")
    if preview.get("layer") == "concept" and preview.get("parent_region_label"):
        lines.append(f"親領域: {preview['parent_region_label']}")
    for issue in list(validation.get("errors") or []) + list(validation.get("warnings") or []):
        if _issue_text(issue):
            lines.append(f"検証: {_issue_text(issue)}")
    if not preview.get("patched_draft"):
        s.scratch.pop("atlas_pending_incorporation", None)
        return _fact(ctx, "この項目は下書きに追加できませんでした: " + (" / ".join(lines) or "内容を確認してください"), status)
    s.scratch["atlas_pending_incorporation"] = {"kind": "gap", "key": key, "preview": preview}
    lines += preview_decision_lines(preview)
    lines.append(GAP_SAVE_TO_DRAFT + str(preview.get("timing_line") or ""))
    _fact(ctx, "次版の下書きに取り込む（確認）\n" + "\n".join(lines) + "\n[キャンセル] [下書きに追加する]", status)


def _save_draft(ctx, skeleton: dict) -> tuple[Optional[int], Any, str]:
    """admin.js applyAssistProposal → saveDraft（revision は画面が最後に読んだ draftRevision）。"""
    s = ctx.session
    status, body = _call(ctx, "PUT", _CART + "/skeleton/draft",
                         json={"skeleton": {"atlas_skeleton": skeleton}, "revision": s.scratch.get("atlas_draft_revision")})
    if status == 200 and isinstance(body, dict) and isinstance(body.get("draft"), dict):
        s.scratch["atlas_draft_revision"] = body["draft"].get("revision")
        _annotate(ctx, {"kind": "draft_saved", "draft_revision": body["draft"].get("revision"),
                        "draft_fingerprint": _sha(body["draft"].get("skeleton"))})
        return status, body, "次版を保存しました"
    if status == 409:
        _load_skeleton(ctx)
        return status, body, ("保存に失敗しました: 他の教員が次版を更新しました。"
                              "最新の内容を読み込み直したため、編集をやり直してください")
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict) and detail.get("errors"):
        return status, body, f"保存に失敗しました: {detail.get('message') or 'バリデーションエラー'}"
    return status, body, f"保存に失敗しました: {_detail(body) or f'HTTP {status}'}"


def _incorporate_apply(kind: str) -> Callable[[Any], None]:
    def run(ctx) -> None:
        if not _need_domain(ctx):
            return
        s = ctx.session
        pending = s.scratch.get("atlas_pending_incorporation") or {}
        if pending.get("kind") != kind or not isinstance((pending.get("preview") or {}).get("patched_draft"), dict):
            return ctx.missing("incorporate_preview")
        preview = pending["preview"]
        status, body, save_text = _save_draft(ctx, preview["patched_draft"])
        lines_fn = gaps_lines if kind == "gap" else edges_lines
        if status != 200:
            (_load_gaps if kind == "gap" else _load_edges)(ctx)
            fail = "取り込みに失敗しました" if kind == "gap" else "反映に失敗しました"
            return _tab_fact(ctx, lines_fn, head=[save_text, f"{fail}: {save_text.split(': ', 1)[-1]}"], status=status)
        if kind == "gap":
            st2, b2 = _call(ctx, "POST", _CART + "/gap-candidates/mark-incorporated",
                            json={"cluster_key": pending["key"], "draft_node_id": preview.get("node_id") or ""})
            (s.scratch.get("atlas_gap_label_edits") or {}).pop(pending["key"], None)
        else:
            st2, b2 = _call(ctx, "POST", _CART + "/edge-candidates/mark-incorporated", json={"edge_key": pending["key"]})
        s.scratch.pop("atlas_pending_incorporation", None)
        (_load_gaps if kind == "gap" else _load_edges)(ctx)
        if st2 is not None and st2 < 400:
            # admin.js gapApplyIncorporation / edgeApplyIncorporation（第 3 波・IK-0938）: 状態文はサーバの
            # result_line（「『…』を次版の下書きに追加しました。」）。旧サーバは従来の一文
            done = (b2.get("result_line") if isinstance(b2, dict) else "") or "次版の下書きに追加しました"
            return _tab_fact(ctx, lines_fn, head=[save_text, str(done)], status=st2)
        fail = "取り込みに失敗しました" if kind == "gap" else "反映に失敗しました"
        _tab_fact(ctx, lines_fn, head=[save_text, f"{fail}: {_detail(b2) or f'HTTP {st2}'}"], status=st2)
    return run


def _edges_list(ctx) -> None:
    if not _need_domain(ctx):
        return
    status, body = _load_edges(ctx)
    if status == 404:
        return _fact(ctx, harness_note(f"（{EDGE_GROUP_TITLE}の区画は表示されていない — この分野には凍結済みの骨格が無い）"), status)
    if status != 200:
        return _fact(ctx, f"候補の読み込みに失敗しました: HTTP {status}", status)
    _tab_fact(ctx, edges_lines, status=status)


def _edges_filter(ctx) -> None:
    if not _need_domain(ctx):
        return
    s = ctx.session
    on = ctx.args.get("show")
    s.scratch["atlas_edge_include_dismissed"] = (not s.scratch.get("atlas_edge_include_dismissed")) if on is None else bool(on)
    _edges_list(ctx)


def _pick_edge(ctx, *, want: Optional[tuple[str, ...]] = None) -> Optional[dict]:
    data = ctx.session.scratch.get("atlas_edges")
    if not isinstance(data, dict):
        _load_edges(ctx)
        data = ctx.session.scratch.get("atlas_edges")
    cands = [c for c in ((data or {}).get("candidates") or []) if isinstance(c, dict)]
    hint = ctx.args.get("edge_hint")
    if want and not str(hint or "").strip():
        cw = [c for c in cands if str((c.get("decision") or {}).get("status") or "candidate") in want]
        if want == ("accepted",):  # 反映は、まだ下書きに入っていない採用を先に（H25）
            cw = [c for c in cw if unreflected(c)] or cw
        if want == ("candidate", "accepted"):  # 第 10 波（H54）: 見送りの手の候補から dismiss_locked のカードを外す
            cw = [c for c in cw if not c.get("dismiss_locked")]
        return cw[0] if cw else None
    numbers = card_numbers(ctx.session, "edge", cands, "edge_key")
    h = re.sub(r"\s*[—–]\s*|\s+-\s+", "—", str(hint or "").strip()).casefold()
    if "—" in h:  # カードの見出し「A — B」で指したとき
        hit = next((c for c in cands if re.sub(r"\s*—\s*", "—", _edge_pair_label(c)).casefold() == h), None)
        if hit is not None:
            return hit
    return _hint_pick(cands, hint, ("from_label", "to_label"), numbers, "edge_key")


def _decide_edge(action: str, want: tuple[str, ...]) -> Callable[[Any], None]:
    def run(ctx) -> None:
        if not _need_domain(ctx):
            return
        picked = _pick_edge(ctx, want=want)
        if picked is None:
            ctx.missing("edge_candidate")
            return _tab_fact(ctx, edges_lines, head=[harness_note("（指した関係の候補が一覧に無い）")])
        status_now = str((picked.get("decision") or {}).get("status") or "candidate")
        if action == "restore" and status_now != "dismissed":
            ctx.missing("edge_dismissed")
            return _tab_fact(ctx, edges_lines, head=[harness_note("（この関係には[見送りから戻す]が出ていない）")])
        if action != "restore" and status_now == "dismissed":
            ctx.missing("edge_not_dismissed")
            return _tab_fact(ctx, edges_lines, head=[harness_note("（見送り済みの関係には[見送りから戻す]しか出ていない）")])
        if action == "dismiss" and picked.get("dismiss_locked"):
            # 第 10 波（H54）: 下書きに入った採用のカードには［見送り…］が出ない（画面の文は dismiss_locked_note）
            ctx.missing("edge_dismiss_locked")
            return _tab_fact(ctx, edges_lines, head=[harness_note(EDGE_DISMISS_LOCKED_HARNESS)])
        payload = {"edge_key": str(picked.get("edge_key") or ""), "action": action, "review_note": ""}
        edge_head: list[str] = []
        if action == "accept":
            raw = str(ctx.args.get("kind") or "").strip().strip("[]「」")
            kind = KIND_WORDS.get(raw, "")
            if not kind:
                # admin.js handleEdgeAction: 種類が未選択なら送らない
                ctx.missing("kind")
                return _tab_fact(ctx, edges_lines, head=[EDGE_KIND_REQUIRED])
            payload["kind"] = kind
            # admin.js edgeDecide（第 4 波・IK-0962）: 採用のときは候補カードの origins から出所を送る（共起を優先）
            origins = [str(o) for o in picked.get("origins") or []]
            if "co_occurrence" in origins:
                payload["origin"] = "co_occurrence"
            elif "vector" in origins:
                payload["origin"] = "vector"
            # admin.js handleEdgeAction（第 3 波・IK-0939）: 種類の後に prompt「採用のメモ（任意）…」→ review_note
            memo = str(ctx.args.get("memo") or "").strip()
            payload["review_note"] = memo
            edge_head = [prompt_line(EDGE_ACCEPT_MEMO_PROMPT, memo)]
        if action == "dismiss":
            note = str(ctx.args.get("reason") or "")
            if not note.strip():
                ctx.missing("reason")
                return _tab_fact(ctx, edges_lines, head=[f"（見送りの理由（必須） — {EDGE_DISMISS_NOTE}）",
                                                         EDGE_DISMISS_REASON_REQUIRED])
            payload["review_note"] = note
        status, body = _call(ctx, "POST", _CART + "/edge-candidates/decide", json=payload)
        if status is not None and status < 400:
            if isinstance(body, dict) and body.get("result_line"):
                ctx.session.scratch["atlas_edge_result_line"] = str(body["result_line"])
            _load_edges(ctx)
            return _tab_fact(ctx, edges_lines, head=edge_head or None, status=status)
        _tab_fact(ctx, edges_lines, head=edge_head + [f"処理に失敗しました: {_detail(body) or f'HTTP {status}'}"],
                  status=status)
    return run


def _edge_incorporate_preview(ctx) -> None:
    """admin.js edgeIncorporate → openEdgeIncorporateConfirm（検証エラーがあれば確認を出さずに止める）。"""
    if not _need_domain(ctx):
        return
    s = ctx.session
    picked = _pick_edge(ctx, want=("accepted",))
    if picked is None:
        # 第 25 周 H20（辺も同じ）: 採用済みの関係が別枠にあれば別枠の［次版の下書きへ反映…］へ回す
        data = s.scratch.get("atlas_edges") if isinstance(s.scratch.get("atlas_edges"), dict) else {}
        if any(isinstance(i, dict) for i in data.get("orphaned_decisions") or []):
            if not str(ctx.args.get("edge_hint") or "").strip():
                first = next((i for i in data.get("orphaned_decisions") or []
                              if isinstance(i, dict) and not i.get("in_draft")), None)
                if first is not None:
                    numbers = card_numbers(s, "edge", [first], "edge_key")
                    ctx.args["edge_hint"] = str(numbers.get(str(first.get("edge_key") or ""), ""))
            if _pick_orphan(ctx, "edge") is not None:
                return _edge_orphan_incorporate(ctx)
        ctx.missing("edge_accepted")
        return _tab_fact(ctx, edges_lines, head=[harness_note("（[次版の下書きへ反映…] が押せる関係が無い）")])
    key = str(picked.get("edge_key") or "")
    status, body = ctx.main(json={"edge_key": key}, overrides={"cartridge_id": _domain(ctx)})
    _edge_preview_fact(ctx, key, picked, status, body)


def _edge_preview_fact(ctx, key: str, picked: Optional[dict], status: Optional[int], body: Any) -> None:
    """admin.js openEdgeIncorporateConfirm（candidates に無い関係はプレビューの from_id / to_id を描く）。"""
    s = ctx.session
    if status is None or status >= 400:
        return _tab_fact(ctx, edges_lines, head=[f"反映を準備できませんでした: {_detail(body) or f'HTTP {status}'}"],
                         status=status)
    preview = body if isinstance(body, dict) else {}
    validation = preview.get("validation") or {}
    if picked is not None:
        pair = _edge_pair_label(picked)
    else:
        pair = f"{preview.get('from_id') or ''} — {preview.get('to_id') or ''}"
    lines = [f"つなぐ項目: {pair}"]
    if preview.get("kind"):
        lines.append(f"関係の種類: {EDGE_KIND_LABELS.get(str(preview['kind']), preview['kind'])}")
    # 第 7 波（IK-1047）: 依存の向き（preview.direction_line・無ければ候補の direction_line）を種類の直後に
    direction = preview.get("direction_line") or (picked or {}).get("direction_line")
    if direction:
        lines.append(str(direction))
    lines += [f"検証: {_issue_text(w)}" for w in validation.get("warnings") or [] if _issue_text(w)]
    errors = [_issue_text(e) for e in validation.get("errors") or [] if _issue_text(e)]
    if errors or not preview.get("patched_draft"):
        s.scratch.pop("atlas_pending_incorporation", None)
        return _fact(ctx, "この関係は下書きに追加できませんでした: " + (" / ".join(errors) or " / ".join(lines)
                                                              or "内容を確認してください"), status)
    s.scratch["atlas_pending_incorporation"] = {"kind": "edge", "key": key, "preview": preview}
    lines += preview_decision_lines(preview)
    lines.append(GAP_SAVE_TO_DRAFT + str(preview.get("timing_line") or ""))
    _fact(ctx, "次版の下書きへ反映する（確認）\n" + "\n".join(lines) + "\n[キャンセル] [下書きに追加する]", status)


def _aliases_list(ctx) -> None:
    if not _need_domain(ctx):
        return
    status, body = _load_aliases(ctx, bool(ctx.args.get("include_dismissed")))
    if status == 404:
        return _fact(ctx, "", status)
    if status != 200:
        return _fact(ctx, f"別名を読み込めませんでした: HTTP {status}", status)
    _tab_fact(ctx, aliases_lines, status=status)


def _vectors_status(ctx) -> None:
    if not _need_domain(ctx):
        return
    status, body = _load_vectors(ctx)
    if status not in (200, 404):
        return _fact(ctx, f"索引の状態を読み込めませんでした: HTTP {status}\n[索引を再構築]", status)
    _tab_fact(ctx, vector_lines, status=status)


def vector_refresh_fact(summary: dict) -> str:
    """admin.js vectorRefreshFactText。"""
    if summary.get("status") == "skipped":
        reason = summary.get("skipped_reason")
        if summary.get("fact"):  # 第 5 波（IK-0993）: 構築中のスキップ等、サーバの事実文があればそれ
            return str(summary["fact"])
        if reason == "daily_call_limit_reached":
            return VECTOR_LIMIT_TEXT
        if reason == "no_frozen_skeleton":
            return VECTOR_NO_SKELETON_TEXT
        return "索引は作り直されませんでした" + (f"（{reason}）" if reason else "")
    # IK-0889: 結果の一文はサーバの fact（全件再利用なら「変更はありませんでした」）。件数は描かない
    ver = f"（{atlas_version_text(summary['skeleton_version'])}）" if summary.get("skeleton_version") else ""
    return str(summary.get("fact") or VECTOR_DONE_TEXT) + ver


def _vectors_refresh(ctx) -> None:
    if not _need_domain(ctx):
        return
    vectors = ctx.session.scratch.get("atlas_vectors")
    if isinstance(vectors, dict) and vectors.get("available") is False:
        ctx.missing("frozen_skeleton")
        return _tab_fact(ctx, vector_lines, head=[harness_note("（[索引を再構築] は押せない）")])
    status, body = ctx.main(overrides={"cartridge_id": _domain(ctx)})
    _runner_note(ctx.session, "runner: 索引の再構築は embedding を本物の OpenAI へ素通しする従量（周に 1 回）")
    head = (vector_refresh_fact(body) if (status is not None and status < 400 and isinstance(body, dict))
            else f"索引を作り直せませんでした: {_detail(body) or f'HTTP {status}'}")
    _load_vectors(ctx)
    _tab_fact(ctx, vector_lines, head=[head], status=status)


def _freeze_draft_labels(session: PersonaSession) -> dict[str, str]:
    """admin.js _freezeDraftLabels（下書きの node_id → ラベル）。"""
    labels: dict[str, str] = {}
    draft = ((session.scratch.get("atlas_state") or {}).get("draft") or {}).get("skeleton") or {}
    for r in draft.get("regions") or []:
        if isinstance(r, dict) and r.get("id"):
            labels[str(r["id"])] = str(r.get("label") or r["id"])
            for c in r.get("concepts") or []:
                if isinstance(c, dict) and c.get("id"):
                    labels[str(c["id"])] = str(c.get("label") or c["id"])
    return labels


def _justification_label(c: dict) -> str:
    """admin.js _freezeJustificationLabel。"""
    if c.get("via") == "alias":
        return "別名"
    if c.get("justification") == "manual_curation":
        return "登録概念"
    if c.get("justification") == "lexical_match":
        return "名前の一致"
    return ""


def impact_facts(impact: dict) -> list[str]:
    """admin.js ``#atlas-freeze`` の click: サーバの facts に、止める条件の無いときの readiness.facts を足す（IK-0885）。"""
    facts = [str(f) for f in impact.get("facts") or [] if f]
    readiness = impact.get("readiness") if isinstance(impact.get("readiness"), dict) else None
    if readiness and readiness.get("ready") is not False:
        facts += [str(f) for f in readiness.get("facts") or [] if f]
    return facts


def freeze_blocked(impact: dict) -> bool:
    """freeze-impact が「止める条件がある」と言った（readiness.ready が偽）。旧サーバ（readiness なし）は偽。"""
    readiness = impact.get("readiness") if isinstance(impact.get("readiness"), dict) else None
    return bool(readiness) and readiness.get("ready") is False


def freeze_blocked_lines(impact: dict) -> list[str]:
    """admin.js openFreezeImpactModal(blockedLines, "", null, true): 止める理由 → サーバの facts、
    ボタンは[キャンセル][止めている候補を見る][公開できません]（押せない）。"""
    readiness = impact.get("readiness") or {}
    lines = [str(f) for f in readiness.get("facts") or [] if f]
    # 第 3 波（IK-0939）: 止めている採用ごとに「・{label} ／ {decision_line} ／ {basis_line} ／ {note_line}」
    # （readiness.pending_items。旧サーバはキーなし = 何も足さない）。サーバの facts はその後
    for item in readiness.get("pending_items") or []:
        if isinstance(item, dict) and item.get("label"):
            parts = [str(item["label"])] + [str(item[k]) for k in ("decision_line", "basis_line", "note_line")
                                             if item.get(k)]
            lines.append("・" + " ／ ".join(parts))
    lines += [str(f) for f in impact.get("facts") or [] if f]
    return ["凍結の影響"] + lines + [f"[キャンセル] [{FREEZE_BLOCKED_GOTO}] [{FREEZE_BLOCKED_BUTTON}]（押せない）"]


def impact_lines(session: PersonaSession, impact: dict) -> list[str]:
    """``#atlas-freeze`` の click が組む「凍結の影響」（summaryLines + _freezeCorrespondenceHtml）。"""
    lines: list[str] = []
    removed = impact.get("removed_node_ids") or []
    affected = [c for c in impact.get("affected_courses") or [] if isinstance(c, dict)]
    if removed or affected:
        titles = [str(c.get("title") or "") for c in affected]
        shown = "、".join(titles[:5]) + (" …" if len(titles) > 5 else "")
        lines.append(f"この凍結で {len(removed)} 概念が削除され、{len(affected)} コースの対応が外れます"
                     + (f": {shown}" if shown else "") + "。")
    lines += impact_facts(impact)
    corr = impact.get("correspondence") if isinstance(impact.get("correspondence"), dict) else {}
    cands = [c for c in corr.get("candidates") or [] if isinstance(c, dict)]
    unmatched = [u for u in corr.get("unmatched_removed") or [] if isinstance(u, dict)]
    if cands or unmatched:
        labels = _freeze_draft_labels(session)
        lines.append(f"{FREEZE_CORRESPONDENCE_TITLE}:")
        lines += [str(f) for f in corr.get("facts") or [] if f]
        lines.append(FREEZE_CORRESPONDENCE_NOTE)
        for c in cands:
            reason = _justification_label(c)
            lines.append(f"□ {c.get('from_label') or c.get('from_id')} → {c.get('to_label') or c.get('to_id')}"
                         + (f"（根拠: {reason}）" if reason else ""))
        if unmatched:
            lines.append(FREEZE_CORRESPONDENCE_UNMATCHED_TITLE)
            options = "、".join(labels.get(str(a), str(a)) for a in impact.get("added_node_ids") or [])
            for u in unmatched:
                lines.append(f"- {u.get('label') or u.get('node_id')}  対応先: [対応づけない]"
                             + (f"（選べる項目: {options}）" if options else ""))
        declared = [f"{labels.get(str(p.get('from')), p.get('from'))} → {labels.get(str(p.get('to')), p.get('to'))}"
                    for p in corr.get("already_declared") or [] if isinstance(p, dict) and p.get("from") and p.get("to")]
        if declared:
            lines.append(f"{FREEZE_CORRESPONDENCE_ALREADY_NOTE} {'、'.join(declared)}")
    return lines


def _impact_modal_shown(impact: dict) -> bool:
    """admin.js: 影響のモーダルを出すのは、削除される概念・外れるコース・対応の候補（未対応を含む）があるときだけ。
    事実文（facts）だけなら、モーダルを出さずに公開前チェックへ差し込む。"""
    corr = impact.get("correspondence") if isinstance(impact.get("correspondence"), dict) else {}
    return bool(impact.get("removed_node_ids") or impact.get("affected_courses")
                or corr.get("candidates") or corr.get("unmatched_removed"))


def freeze_check_lines(version: str, draft: dict, reports: list[dict], impact: dict) -> list[str]:
    """admin.js ``#atlas-freeze`` の click の ``checkLines``（facts は問いの直前に差し込む）。"""
    pending = sum(1 for r in reports if r.get("status") == "pending")
    warnings = (draft.get("validation") or {}).get("warnings") or []
    return ([f"公開版: {version}", "検証エラー: 0件", f"検証警告: {len(warnings)}件", f"未確認の修正報告: {pending}件"]
            + impact_facts(impact) + [FREEZE_QUESTION])


def _freeze_gates(ctx, version: str) -> Optional[tuple[dict, list[dict]]]:
    """admin.js ``#atlas-freeze`` の click の前段（版数 → 下書き → 検証エラー → 未対応の修正報告）。
    止まったら事実文を置いて None。"""
    s = ctx.session
    if not version:
        ctx.missing("version")
        _fact(ctx, "版数を入力してください (例: 2026.1)")
        return None
    _load_skeleton(ctx)
    _load_reports(ctx)
    draft = (s.scratch.get("atlas_state") or {}).get("draft")
    if not isinstance(draft, dict):
        ctx.missing("draft")
        _fact(ctx, harness_note("（[公開（凍結）] は表示されていない — 次版の下書きが無い）"))
        return None
    errors = (draft.get("validation") or {}).get("errors") or []
    if errors:
        ctx.missing("validation_errors")
        _fact(ctx, f"検証エラーが {len(errors)}件あります。修正してから公開してください")
        return None
    reports = [r for r in ((s.scratch.get("atlas_reports") or {}).get("reports") or []) if isinstance(r, dict)]
    accepted_open = sum(1 for r in reports if r.get("status") == "accepted" and not r.get("incorporated_at")
                        and not r.get("applied_version"))
    if accepted_open:
        ctx.missing("accepted_reports_open")
        _fact(ctx, f"採用済みで次版に未対応の修正報告が {accepted_open}件あります。対応後に公開してください")
        return None
    return draft, reports


def freeze_check_title(version: Any) -> str:
    """凍結の確認モーダルの見出し（admin.js openFreezeChecklist・IK-0942 残り・第 5 波）。
    旧「分野の地図骨格を公開前チェック」。"""
    v = atlas_version_text(version)
    return (v + " を公開する前の確認") if v else "を公開する前の確認"


def _freeze_impact(ctx) -> None:
    """[公開（凍結）] → 影響のモーダル（影響があるとき）か公開前チェック（無いとき）を読んで[キャンセル]する。

    H5（第 25 周）: 影響ゼロのとき、製品はモーダルを出さずに公開前チェック（公開版 / 検証エラー / 検証警告 /
    未確認の修正報告 / 問い）へ進む。版数が空なら影響を読む前に「版数を入力してください」で止まる。
    """
    if not _need_domain(ctx):
        return
    s = ctx.session
    version = str(ctx.args.get("version") or "").strip()
    gates = _freeze_gates(ctx, version)
    if gates is None:
        return
    draft, reports = gates
    status, body = ctx.main(overrides={"cartridge_id": _domain(ctx)})
    impact = body if (status == 200 and isinstance(body, dict)) else {}
    if impact:
        s.scratch["atlas_freeze_impact"] = impact
        readiness = impact.get("readiness") if isinstance(impact.get("readiness"), dict) else None
        _annotate(ctx, {"kind": "freeze_impact", "frozen_version": str(impact.get("frozen_version") or ""),
                        "correspondence_candidates": len((impact.get("correspondence") or {}).get("candidates") or []),
                        # 第 2 波（IK-0885）: None = 旧サーバ（readiness なし）
                        "ready": None if readiness is None else readiness.get("ready") is not False})
    cancel = "（この操作ではキャンセルして凍結しない）"
    s.scratch["atlas_freeze_blocked"] = freeze_blocked(impact)
    s.scratch["atlas_freeze_blocker"] = first_freeze_blocker(impact)
    if isinstance(impact.get("readiness"), dict):
        s.scratch.setdefault("until_flags", {})["freeze_ready"] = not freeze_blocked(impact)
    if freeze_blocked(impact):
        return _fact(ctx, "\n".join(freeze_blocked_lines(impact)), status)
    if _impact_modal_shown(impact):
        lines = ["凍結の影響"] + impact_lines(s, impact) + ["凍結しますか？", "[キャンセル] [続ける]" + cancel]
    else:
        # 影響の取得に失敗したときも、製品は公開前チェックへ進む（プレビューが無くても凍結は止めない）
        lines = [freeze_check_title(version)] + freeze_check_lines(version, draft, reports, impact) \
            + ["[キャンセル] [公開する]" + cancel]
    _fact(ctx, "\n".join(lines), status)


def _pick_id_migrations(impact: dict, choice: Any) -> list[dict]:
    """admin.js _freezeCollectIdMigrations（同じ旧ノードは 1 対だけ）。既定はチェックしない。"""
    raw = str(choice or "none").strip()
    corr = impact.get("correspondence") if isinstance(impact.get("correspondence"), dict) else {}
    cands = [c for c in corr.get("candidates") or [] if isinstance(c, dict) and c.get("from_id") and c.get("to_id")]
    if raw.casefold() in ("", "none", "なし", "チェックしない"):
        return []
    if raw.casefold() in ("all", "全部", "すべて"):
        chosen = cands
    else:
        hints = [h.strip().casefold() for h in re.split(r"[,、]", raw) if h.strip()]
        chosen = [c for c in cands if any(h in str(c.get("from_label") or c.get("from_id") or "").casefold() for h in hints)]
    out, seen = [], set()
    for c in chosen:
        if c["from_id"] in seen:
            continue
        seen.add(c["from_id"])
        out.append({"from": str(c["from_id"]), "to": str(c["to_id"])})
    return out


def _freeze(ctx) -> None:
    """``#atlas-freeze`` の click → 影響確認 → 公開前チェック → POST freeze → loadState。"""
    if not _need_domain(ctx):
        return
    s = ctx.session
    version = str(ctx.args.get("version") or "").strip()
    gates = _freeze_gates(ctx, version)
    if gates is None:
        return
    draft, reports = gates
    st_i, impact = _call(ctx, "GET", _CART + "/freeze-impact")
    impact = impact if (st_i == 200 and isinstance(impact, dict)) else {}
    if impact:
        readiness = impact.get("readiness") if isinstance(impact.get("readiness"), dict) else None
        _annotate(ctx, {"kind": "freeze_impact", "frozen_version": str(impact.get("frozen_version") or ""),
                        "ready": None if readiness is None else readiness.get("ready") is not False})
    if freeze_blocked(impact):
        # admin.js（IK-0885）: 止める条件があれば[公開する]へ進ませない（押してから 409 で止めない）
        s.scratch["atlas_freeze_blocked"] = True
        s.scratch["atlas_freeze_blocker"] = first_freeze_blocker(impact)
        ctx.missing("freeze_ready")
        return _fact(ctx, "\n".join(freeze_blocked_lines(impact)), st_i)
    id_migrations = _pick_id_migrations(impact, ctx.args.get("correspondence"))
    check = freeze_check_lines(version, draft, reports, impact)
    body_json: dict = {"version": version, "note": str(ctx.args.get("note") or "").strip()}
    if id_migrations:
        body_json["id_migrations"] = id_migrations
    status, body = ctx.main(json=body_json, overrides={"cartridge_id": _domain(ctx)})
    head = (["凍結の影響（確認）:"] + impact_lines(s, impact)) if _impact_modal_shown(impact) else []
    if id_migrations:
        head.append("チェックした対応: " + "、".join(f"{m['from']} → {m['to']}" for m in id_migrations))
    head += [freeze_check_title(version) + ":"] + check + ["[公開する]"]
    if status is None or status >= 400:
        detail = body.get("detail") if isinstance(body, dict) else None
        if isinstance(detail, dict) and detail.get("pending_labels"):
            msg = (detail.get("message") or GAP_FREEZE_PENDING_TEXT) + ": " + "、".join(detail["pending_labels"])
        elif isinstance(detail, dict) and detail.get("pending_edges"):
            msg = (detail.get("message") or EDGE_FREEZE_PENDING_TEXT) + ": " + "、".join(detail["pending_edges"])
        else:
            msg = _detail(body) or f"HTTP {status}"
        return _fact(ctx, "\n".join(head + [f"公開に失敗しました: {msg}"]), status)
    _annotate(ctx, {"kind": "freeze", "version": version, "id_migrations": len(id_migrations)})
    _runner_note(s, "runner: 凍結の後、製品はベクトル索引の構築を裏で始める（embedding の従量・best-effort）")
    _load_state(ctx)
    # 第 25 周第 5 周 H33: 凍結の後、画面（loadState）は索引・候補・関係の候補も読み直して描く。admin.atlas.open と
    # 同じ区画の並びで投影する（凍結直後の「索引は前の版のまま」・関係の候補の空がペルソナに見えなかった）
    _tab_fact(ctx, overview_lines, vector_lines, aliases_lines, gaps_lines, edges_lines,
              head=head + [f"{atlas_version_text(version)} を学習者向けに公開しました。{GAP_FREEZE_REANALYSIS_TEXT}"],
              status=status)


def _freeze_blocked_goto(ctx) -> None:
    """admin.js ``#atlas-freeze-impact-goto``: モーダルを閉じて修正報告の区画（候補 → 関係の候補）へ。"""
    if not _need_domain(ctx):
        return
    s = ctx.session
    if not s.scratch.get("atlas_freeze_blocked"):
        return ctx.missing("freeze_blocked_goto_button")
    s.scratch["atlas_freeze_blocked"] = False
    _load_gaps(ctx)
    _load_edges(ctx)
    head = freeze_blocker_goto_line(s, s.scratch.pop("atlas_freeze_blocker", None))
    _tab_fact(ctx, gaps_lines, edges_lines, head=[head], status=200)


def first_freeze_blocker(impact: dict) -> Optional[dict]:
    """admin.js scrollToFreezeBlocker: readiness.pending_items のうち ``target_key`` を持つ先頭（第 6 波・IK-1019）。
    target_key は内部の番地なので画面には写さない（カードの照合にだけ使う）。"""
    readiness = impact.get("readiness") if isinstance(impact.get("readiness"), dict) else {}
    for item in readiness.get("pending_items") or []:
        if isinstance(item, dict) and item.get("target_key"):
            return {"kind": str(item.get("kind") or ""), "label": str(item.get("label") or ""),
                    "target_key": str(item["target_key"])}
    return None


def freeze_blocker_goto_line(session: PersonaSession, blocker: Optional[dict]) -> str:
    """［止めている候補を見る］の行き先（admin.js scrollToFreezeBlocker）: 先頭の止めている候補のカード
    （``data-edge-key`` / ``data-cluster-key`` が target_key と一致）→ そのグループ → 区画の先頭。"""
    lead = f"（[{FREEZE_BLOCKED_GOTO}] — 凍結の影響を閉じて"
    if not blocker:
        return lead + "候補の区画へ移った）"
    is_edge = blocker.get("kind") == "edge"
    kind = "edge" if is_edge else "gap"
    key_field = "edge_key" if is_edge else "cluster_key"
    group = EDGE_GROUP_TITLE if is_edge else GAP_GROUP_TITLE
    rows = (_list_rows(session, kind) or []) + (_list_rows(session, kind, "orphaned_decisions") or [])
    card = any(str(r.get(key_field) or "") == blocker.get("target_key") for r in rows)
    if card:
        return lead + f"「{group}」の「{blocker.get('label') or ''}」のカードへ移った（カードの枠をしばらく強調））"
    if _list_rows(session, kind) is not None:
        return lead + f"「{group}」のグループへ移った）"
    return lead + "候補の区画へ移った）"


def _edges_show_all(ctx) -> None:
    """admin.js ``[data-edge-show-all]``: 畳んだ近さだけの候補を全部出す（``show_all=true``・分野の切替で戻る）。"""
    if not _need_domain(ctx):
        return
    s = ctx.session
    data = s.scratch.get("atlas_edges")
    if not (isinstance(data, dict) and data.get("folded_note")) and not s.scratch.get("atlas_edge_show_all"):
        return ctx.missing("edge_show_all_button")
    if not s.scratch.get("atlas_edge_show_all"):
        s.scratch["atlas_edge_renumber_pending"] = True  # 第 25 周 H19: 表示順に 1 回だけ振り直す
    s.scratch["atlas_edge_show_all"] = True
    _edges_list(ctx)


def _pick_orphan(ctx, kind: str) -> Optional[dict]:
    """候補の条件から外れた判断（orphaned_decisions）から番号か名前で 1 件。"""
    data = ctx.session.scratch.get("atlas_gaps" if kind == "gap" else "atlas_edges")
    if not isinstance(data, dict):
        (_load_gaps if kind == "gap" else _load_edges)(ctx)
        data = ctx.session.scratch.get("atlas_gaps" if kind == "gap" else "atlas_edges")
    items = [i for i in ((data or {}).get("orphaned_decisions") or []) if isinstance(i, dict)]
    key_field = "cluster_key" if kind == "gap" else "edge_key"
    numbers = card_numbers(ctx.session, kind, items, key_field)
    hint = ctx.args.get("gap_hint" if kind == "gap" else "edge_hint")
    labels = ("proposed_label",) if kind == "gap" else ("from_label", "to_label")
    return _hint_pick(items, hint, labels, numbers, key_field)


def _route_to_normal(ctx, kind: str) -> bool:
    """外れた枠の［取り込む／反映…］を選んだが、指した判断が外れた枠に無いとき、通常の候補一覧の
    採用済み・未反映へ回す（第 25 周 H25 — H20 の逆向き）。

    凍結を止めている採用が、いまも導出されて通常の一覧の先頭帯にあるとき（製品はそこに［次版の下書きへ反映…］を
    出し、凍結の影響もその区画を名指す）、頭脳が「外れた枠」の手を選んでも同じ採用を指せるようにする。
    hint がその候補の番号・名前ならそれを、空か外れた枠向けの番号なら最初の採用済み・未反映を選ぶ。回したことは
    runner_note に残す。回す先が無ければ偽（従来どおり「指した判断が無い」）。
    """
    s = ctx.session
    pending = pending_unreflected(s, kind, where="candidates")
    if not pending:
        return False
    key_field, hint_key = ("cluster_key", "gap_hint") if kind == "gap" else ("edge_key", "edge_hint")
    numbers = s.scratch.get(f"atlas_{kind}_numbers") or {}
    hint = str(ctx.args.get(hint_key) or "").strip()
    labels = ("proposed_label",) if kind == "gap" else ("from_label", "to_label")
    hit = _hint_pick(pending, hint, labels, numbers, key_field) if hint else None
    target = hit or pending[0]
    ctx.args[hint_key] = str(numbers.get(str(target.get(key_field) or ""), "")) or (
        str(target.get("proposed_label") or "") if kind == "gap" else _edge_pair_label(target))
    title = GAP_GROUP_TITLE if kind == "gap" else EDGE_GROUP_TITLE
    name = str(target.get("proposed_label") or "") if kind == "gap" else _edge_pair_label(target)
    button = "［次版の下書きに取り込む…］" if kind == "gap" else "［次版の下書きへ反映…］"
    _runner_note(s, harness_note(f"「{name}」は外れた枠ではなく「{title}」の一覧に採用済み・未反映として出ている。"
                                 f"その一覧の{button}として実行した"))
    return True


def _gap_orphan_restore(ctx) -> None:
    """admin.js _gapOrphanedHtml [候補へ戻す] → gapDecide(restore)（判断を未判断へ・行は消さない）。"""
    if not _need_domain(ctx):
        return
    item = _pick_orphan(ctx, "gap")
    if item is None:
        ctx.missing("gap_orphaned")
        return _tab_fact(ctx, gaps_lines, head=[harness_note(f"（「{GAP_ORPHANED_TITLE}」に指した判断が無い）")])
    # admin.js handleGapAction（第 3 波・IK-0939）: 別枠の［候補へ戻す］は常に prompt「候補へ戻す理由…」。
    # 他の教員の採用を空の理由で戻すとサーバが 422（detail は事実文）— 画面と同じく「処理に失敗しました: …」
    reason = str(ctx.args.get("reason") or "").strip()
    status, body = _gap_decide(ctx, "restore", reason, str(item.get("cluster_key") or ""))
    _after_gap_decide(ctx, status, body, head=[prompt_line(GAP_RESTORE_REASON_PROMPT, reason)])


def _gap_orphan_incorporate(ctx) -> None:
    """admin.js _gapOrphanedHtml [次版の下書きに取り込む…] → gapIncorporate（ラベルの欄は無い = 空で送る）。"""
    if not _need_domain(ctx):
        return
    s = ctx.session
    item = _pick_orphan(ctx, "gap")
    data = s.scratch.get("atlas_gaps") or {}
    if item is None and _route_to_normal(ctx, "gap"):
        return _gap_incorporate_preview(ctx)
    if item is None:
        ctx.missing("gap_orphaned")
        return _tab_fact(ctx, gaps_lines, head=[harness_note(f"（「{GAP_ORPHANED_TITLE}」に指した判断が無い）")])
    if not data.get("draft_exists") or item.get("in_draft"):
        ctx.missing("gap_orphaned_incorporate_button")
        return _tab_fact(ctx, gaps_lines, head=[harness_note("（[次版の下書きに取り込む…] は押せない）")])
    key = str(item.get("cluster_key") or "")
    status, body = _call(ctx, "POST", _CART + "/gap-candidates/incorporate-preview",
                         json={"cluster_key": key, "proposed_label": ""})
    _gap_preview_fact(ctx, key, status, body)


def _edge_orphan_dismiss(ctx) -> None:
    """admin.js _edgeOrphanedHtml [見送り…]（理由は必須・空は送らない）。"""
    if not _need_domain(ctx):
        return
    item = _pick_orphan(ctx, "edge")
    if item is None:
        ctx.missing("edge_orphaned")
        return _tab_fact(ctx, edges_lines, head=[harness_note(f"（「{EDGE_ORPHANED_TITLE}」に指した関係が無い）")])
    if item.get("dismiss_locked"):
        ctx.missing("edge_dismiss_locked")  # 第 10 波（H54）
        return _tab_fact(ctx, edges_lines, head=[harness_note(EDGE_DISMISS_LOCKED_HARNESS)])
    note = str(ctx.args.get("reason") or "")
    if not note.strip():
        ctx.missing("reason")
        return _tab_fact(ctx, edges_lines, head=[f"（見送りの理由（必須） — {EDGE_DISMISS_NOTE}）",
                                                 EDGE_DISMISS_REASON_REQUIRED])
    status, body = _call(ctx, "POST", _CART + "/edge-candidates/decide",
                         json={"edge_key": str(item.get("edge_key") or ""), "action": "dismiss", "review_note": note})
    if status is not None and status < 400:
        if isinstance(body, dict) and body.get("result_line"):
            ctx.session.scratch["atlas_edge_result_line"] = str(body["result_line"])
        _load_edges(ctx)
        return _tab_fact(ctx, edges_lines, status=status)
    _tab_fact(ctx, edges_lines, head=[f"処理に失敗しました: {_detail(body) or f'HTTP {status}'}"], status=status)


def _edge_orphan_incorporate(ctx) -> None:
    """admin.js _edgeOrphanedHtml [次版の下書きへ反映…] → edgeIncorporate。確認の「つなぐ項目」は
    _edgeCandidateByKey が candidates しか引かないので、外れた関係ではプレビューの from_id / to_id を描く（製品のまま）。"""
    if not _need_domain(ctx):
        return
    s = ctx.session
    item = _pick_orphan(ctx, "edge")
    data = s.scratch.get("atlas_edges") or {}
    if item is None and _route_to_normal(ctx, "edge"):
        return _edge_incorporate_preview(ctx)
    if item is None:
        ctx.missing("edge_orphaned")
        return _tab_fact(ctx, edges_lines, head=[harness_note(f"（「{EDGE_ORPHANED_TITLE}」に指した関係が無い）")])
    if not data.get("draft_exists") or item.get("in_draft"):
        ctx.missing("edge_orphaned_incorporate_button")
        return _tab_fact(ctx, edges_lines, head=[harness_note("（[次版の下書きへ反映…] は押せない）")])
    key = str(item.get("edge_key") or "")
    status, body = _call(ctx, "POST", _CART + "/edge-candidates/incorporate-preview", json={"edge_key": key})
    _edge_preview_fact(ctx, key, None, status, body)


# ---------------------------------------------------------------------------
# 学生
# ---------------------------------------------------------------------------
def threads_lines(atlas: dict, drawn: Optional[set] = None, names: Optional[dict] = None) -> list[str]:
    """atlas-threads-layer.js threadsData / factText（available !== true ならトグルごと出さない）。

    第 25 周第 5 周 H32: ``drawn``（``levels["2"].nodes`` の id）を渡すと、両端が描かれる糸だけを点線として並べ、端が
    描かれない糸はサーバの ``threads.undrawn_note``（無ければ ``L2_UNDRAWN_NOTE``）の下に分ける。"""
    threads = atlas.get("threads") if isinstance(atlas.get("threads"), dict) else None
    if not threads or threads.get("available") is not True or not threads.get("items"):
        lines = ["推定の糸: （切り替えが出ていない）"]
        if threads and threads.get("note"):
            # 画面はレイヤーを出さない。理由文はサーバの事実文（第 14 周）— 画面には描かれないので投影の注記に留める
            lines.append(f"（サーバの事実文・画面には出ない: {threads['note']}）")
        return lines
    version = str(threads.get("skeleton_version") or "")
    fact = THREADS_PROVENANCE_TEXT + (f"・{atlas_version_text(version)}" if version else "")
    lines = [f"[☐ 推定の糸] をオンにした: 点線の糸（{fact}）"]
    hidden = []
    for it in threads.get("items") or []:
        if isinstance(it, dict):
            near = f"（{it['nearness_label']}）" if it.get("nearness_label") else ""
            row = f"- {it.get('from_label') or ''} ⋯ {it.get('to_label') or ''}{near}"
            if drawn is not None and not (str(it.get("from") or "") in drawn and str(it.get("to") or "") in drawn):
                hidden.append(row)
                continue
            lines.append(row)
    if drawn is not None and len(lines) == 1:
        lines.append("（両端が描かれている糸は無い — 画面に点線は出ない）")
    if hidden:
        # 画面は糸の事実行の下に .threads-undrawn（サーバの undrawn_note）。無ければハーネスの印
        lines.append(str(threads.get("undrawn_note") or L2_UNDRAWN_NOTE))
        lines += hidden
    elif drawn is not None and threads.get("undrawn_note"):
        lines.append(str(threads["undrawn_note"]))
    # 第 4 波（IK-0887）: 事実行の下に「糸ごとの根拠」の折りたたみ（items[].basis_line の素通し・atlas-threads-layer.js）
    bases = [str(it["basis_line"]) for it in threads.get("items") or [] if isinstance(it, dict) and it.get("basis_line")]
    if bases:
        lines.append("▸ 糸ごとの根拠（折りたたみ — 開いた中身）:")
        lines += [f"  {b}" for b in bases]
    return lines


def placement_reason_lines(p: dict, indent: str = "") -> list[str]:
    """第 4 波（IK-0967）: 配置の理由は「〔AIの要約〕理由」（``reason_label``）、その下に ``reason_note``（理由の固有の語が
    引用に無いときだけ）。app.js / landscape-layer.js / admin.js / admin-release-review.js が同じ順で描く。"""
    if not p.get("reason"):
        return []
    label = f"〔{p['reason_label']}〕" if p.get("reason_label") else ""
    out = [f"{indent}{label}{p['reason']}"]
    if p.get("reason_note"):
        out.append(f"{indent}{p['reason_note']}")
    return out


def placement_layer_lines(landscape: Any) -> list[str]:
    """landscape-layer.js（groupByNode / マーカーのポップオーバー / domainFactLines / corpusFactText）。"""
    if not isinstance(landscape, dict):
        return [f"{LANDSCAPE_TOGGLE_LABEL}: （切り替えが出ていない）"]
    docs = [d for d in landscape.get("documents") or [] if isinstance(d, dict)]
    has = any((d.get("placements") or []) for d in docs)
    lines: list[str] = []
    if not has:
        lines.append(f"{LANDSCAPE_TOGGLE_LABEL}: （切り替えが出ていない — 配置が 1 件も無い）")
    else:
        lines.append(f"[☐ {LANDSCAPE_TOGGLE_LABEL}] をオンにした（マーカーを開いた中身）:")
        groups: dict[str, list[tuple[str, dict]]] = {}
        order: list[str] = []
        for d in docs:
            for p in d.get("placements") or []:
                if not isinstance(p, dict) or p.get("node_status") == "unmapped":
                    continue
                nid = str(p.get("current_node_id") or p.get("node_id") or "")
                if not nid:
                    continue
                if nid not in groups:
                    groups[nid] = []
                    order.append(nid)
                groups[nid].append((str(d.get("title") or "無題の論文"), p))
        for nid in order:
            label = groups[nid][0][1].get("node_label") or "この位置の論文"
            lines.append(f"- {label}:")
            for title, p in groups[nid]:
                bits = "・".join(x for x in (p.get("perspective_label"), p.get("weight_label")) if x)
                prov = f"〔{p['provenance_label']}〕" if p.get("provenance_label") else ""
                lines.append(f"    {title}（{bits}）{prov}")
                lines += placement_reason_lines(p, "      ")
                for ev in p.get("evidence") or []:
                    if isinstance(ev, dict) and ev.get("quote"):
                        lines.append(f"      「{ev['quote']}」")
                        if ev.get("source_label"):  # 第 4 波（IK-0677）: 引用の出所の印
                            lines.append(f"      {ev['source_label']}")
    seen: list[str] = []
    for dom in landscape.get("domains") or []:
        for f in (dom or {}).get("facts") or [] if isinstance(dom, dict) else []:
            if f and str(f) not in seen:
                seen.append(str(f))
    lines += seen
    if has:
        corpus = landscape.get("corpus") if isinstance(landscape.get("corpus"), dict) else {}
        n = corpus.get("source_document_count") if isinstance(corpus.get("source_document_count"), int) else 0
        version = ""
        domains = [d for d in landscape.get("domains") or [] if isinstance(d, dict)]
        picked = next((d for d in domains if d.get("is_course_map")), domains[0] if domains else None)
        if picked and picked.get("frozen_version"):
            version = str(picked["frozen_version"])
        notes = ([atlas_version_text(version)] if version else []) + (
            ["AI推定を含む"] if any(p.get("status") != "confirmed" for d in docs for p in d.get("placements") or []
                                  if isinstance(p, dict)) else [])
        lines.append(f"登録済み論文 {n} 件の解析から生成" + (f"（{'・'.join(notes)}）" if notes else ""))
    return lines


PAPER_PLACEMENT_HEAD = "分野の中の位置づけ"
PAPER_PLACEMENT_INTRO = ("この論文が分野の地図のどのあたりとつながるかの一覧です。"
                         "ひとつの論文が複数の領域に載ることがあります。")
PAPER_PLACEMENT_UNPLACED = "この論文は、現在の分野の地図（版 {version}）のどの領域にも配置されていません。"


def _paper_placement_version(data: dict) -> str:
    """app.js paperPlacementSkeletonVersion（skeleton_version → is_course_map の domain → 先頭の domain）。"""
    if data.get("skeleton_version"):
        return str(data["skeleton_version"])
    domains = [d for d in data.get("domains") or [] if isinstance(d, dict)]
    picked = next((d for d in domains if d.get("is_course_map")), domains[0] if domains else None)
    return str((picked or {}).get("frozen_version") or "")


def paper_placement_section_lines(data: Any) -> list[str]:
    """app.js renderPaperPlacementSection / buildPaperPlacementHtml / buildPaperPlacementUnplacedHtml /
    paperPlacementCorpusFact（出典タブ「分野の中の位置づけ」— 第 25 周 H15）。

    配置済みの論文が 1 件も無ければ節ごと出さない（fail-closed）。分野の行・domains の facts・
    ``placed_document_count`` は画面に出ないので投影しない（旧投影は DTO の残りキーをダンプしていた）。
    """
    if not isinstance(data, dict):
        return []
    docs = [d for d in data.get("documents") or [] if isinstance(d, dict)]
    placed = [d for d in docs if isinstance(d.get("placements"), list) and d["placements"]]
    if not placed:
        return []
    lines = [f"{PAPER_PLACEMENT_HEAD}:", PAPER_PLACEMENT_INTRO]
    for doc in placed:
        lines.append(f"論文: {doc.get('title') or '無題の論文'}")
        for p in doc["placements"]:
            if not isinstance(p, dict):
                continue
            bits = "・".join(str(x) for x in (p.get("perspective_label"), p.get("weight_label")) if x)
            chip = str(p.get("node_label") or p.get("node_id") or "") + (f"（{bits}）" if bits else "")
            prov = f" 〔{p['provenance_label']}〕" if p.get("provenance_label") else ""
            lines.append(f"  - {chip}{prov}")
            evs = [ev for ev in p.get("evidence") or [] if isinstance(ev, dict) and ev.get("quote")]
            if p.get("reason") or evs:
                lines.append("    ［根拠を見る］を開いた:")
                lines += placement_reason_lines(p, "      ")
                for ev in evs:
                    lines.append(f"      「{ev['quote']}」")
                    if ev.get("source_label"):  # 第 4 波（IK-0677）: 引用の出所の印
                        lines.append(f"      {ev['source_label']}")
    version = _paper_placement_version(data)
    if version:
        for doc in data.get("unplaced_documents") or []:
            if isinstance(doc, dict):
                lines.append(f"論文: {doc.get('title') or '無題の論文'}")
                lines.append(f"  {PAPER_PLACEMENT_UNPLACED.format(version=version)}")
    corpus = data.get("corpus") if isinstance(data.get("corpus"), dict) else {}
    n = corpus.get("source_document_count") if isinstance(corpus.get("source_document_count"), int) else 0
    notes = ([atlas_version_text(version)] if version else []) + (
        ["AI推定を含む"] if any(isinstance(p, dict) and p.get("status") != "confirmed"
                              for d in docs for p in d.get("placements") or []) else [])
    lines.append(f"登録済み論文 {n} 件の解析から生成" + (f"（{'・'.join(notes)}）" if notes else ""))
    return lines


OVERLAY_LEGEND = ("原文に裏付け", "解釈が分かれる", "暗黙の前提・行間（AIが補完）", "論文未投入の地形")

OVERLAY_L1_REGIONS = 12  # atlas-overlay.js LIMITS.l1Regions
OVERLAY_L1_CONCEPTS = 6  # atlas-overlay.js LIMITS.l1ConceptsPerRegion

#: 端点が L2 に描かれない糸の印（ハーネスの注記 — サーバの undrawn_note が無いときだけ使う）
L2_UNDRAWN_NOTE = "（ハーネス: 以下の糸は端が L2 に描かれていないので画面に線が無い。これは画面の文ではない）"


def l2_drawn(atlas: Any) -> tuple[list[dict], set[str], dict[str, str]]:
    """L2 で描かれる概念・その id の集合・id → 名前（第 25 周第 5 周 H32）。

    面 C の是正（IK-0989）で L2 の代表選出はサーバ（``routes/atlas_view.py::select_l2_concept_ids``）へ移り、
    ``levels["2"].nodes`` / ``edges`` が描く範囲そのもの。投影は製品の描画上限（JS の LIMITS）をミラーせず DTO をそのまま読む。"""
    levels = atlas.get("levels") if isinstance(atlas, dict) and isinstance(atlas.get("levels"), dict) else {}
    l2 = levels.get("2") if isinstance(levels.get("2"), dict) else {}
    info = atlas.get("nodes") if isinstance(atlas, dict) and isinstance(atlas.get("nodes"), dict) else {}
    nodes = [n for n in l2.get("nodes") or [] if isinstance(n, dict)]
    ids = {str(n.get("id") or "") for n in nodes if n.get("id")}
    names: dict[str, str] = {}
    for n in l2.get("nodes") or []:
        if isinstance(n, dict) and n.get("id"):
            nid = str(n["id"])
            meta = info.get(nid) if isinstance(info.get(nid), dict) else {}
            names[nid] = str(n.get("label") or meta.get("label") or nid)
    for nid, meta in info.items():
        if isinstance(meta, dict) and meta.get("label"):
            names.setdefault(str(nid), str(meta["label"]))
    return nodes, ids, names


def l2_lines(atlas: Any) -> list[str]:
    """L2（コースレベル）の描画の文字版: 描かれる概念・実線の関係（両端が描かれる ``levels["2"].edges``）・
    サーバの ``omitted_note``（画面の層の事実文・IK-0989）。"""
    nodes, ids, names = l2_drawn(atlas)
    levels = atlas.get("levels") if isinstance(atlas, dict) and isinstance(atlas.get("levels"), dict) else {}
    l2 = levels.get("2") if isinstance(levels.get("2"), dict) else {}
    lines = []
    labels = [names.get(str(n.get("id") or ""), "") for n in nodes]
    if labels:
        lines.append("地図の概念（描かれている）: " + "、".join(x for x in labels if x))
    # 第 9 波（sync_keys_wave9 面 C・IK-1072）: directed_edges に含まれる組だけ矢じり（向き「A → B（依存）」）
    directed = {(str(d[0]), str(d[1])) for d in l2.get("directed_edges") or []
                if isinstance(d, (list, tuple)) and len(d) >= 2}
    edges = []
    for e in l2.get("edges") or []:
        if isinstance(e, (list, tuple)) and len(e) >= 2 and str(e[0]) in ids and str(e[1]) in ids:
            a, b = names.get(str(e[0]), str(e[0])), names.get(str(e[1]), str(e[1]))
            edges.append(f"関係（実線）: {a} → {b}（依存）" if (str(e[0]), str(e[1])) in directed
                         else f"関係（実線）: {a} — {b}")
    lines += edges or ["関係（実線）: （描かれている概念どうしの関係の線は無い）"]
    note = level_note_text(l2)
    if note:
        lines.append(note)
    return lines


def level_note_text(level_data: Any) -> str:
    """atlas-overlay.js ``#atlas-level-note``（第 9 波・IK-1072 / IK-1073）: ``now_note → omitted_note → relations_note``
    を空白で結んだ 1 行（文はサーバ）。無ければ空。"""
    if not isinstance(level_data, dict):
        return ""
    return " ".join(str(level_data[k]) for k in ("now_note", "omitted_note", "relations_note") if level_data.get(k))


def overlay_l1_lines(atlas: Any) -> list[str]:
    """atlas-overlay.js renderL1 / buildLegend / makeNodeGroup（aria-label = 名前（pill））/ selectNode の
    パネル（pill と pill_note）— 学習者の分野の地図を開いた最初の画面（第 25 周 H15: YAML ダンプだった）。

    領域ごとに代表概念（領域あたり 6 まで）を並べる。霧の領域は名前とハッチだけで概念ラベルを描かない（§1.2）。
    パネルの説明文（pill_note）は概念を選んだときに出る文なので、同じ文は 1 回だけ末尾に「選ぶと」で添える。
    """
    if not isinstance(atlas, dict):
        return ["（分野の地図が開かなかった）"]
    info = atlas.get("nodes") if isinstance(atlas.get("nodes"), dict) else {}
    l1 = ((atlas.get("levels") or {}).get("1") or {}) if isinstance(atlas.get("levels"), dict) else {}
    head = "・".join(str(x) for x in (atlas.get("domain_name"), f"{ATLAS_VERSION_PREFIX}{atlas['skeleton_version']}"
                                       if atlas.get("skeleton_version") else "", atlas.get("provenance")) if x)
    lines = [f"分野の地図（{head}）" if head else "分野の地図", "凡例: " + " / ".join(OVERLAY_LEGEND)]
    if l1.get("svgDesc"):
        lines.append(str(l1["svgDesc"]))
    per_region: dict[str, list[dict]] = {}
    for n in l1.get("nodes") or []:
        if isinstance(n, dict):
            bucket = per_region.setdefault(str(n.get("region") or ""), [])
            if len(bucket) < OVERLAY_L1_CONCEPTS:
                bucket.append(n)
    notes: dict[str, str] = {}

    def node_text(nid: str, label: str) -> str:
        meta = info.get(nid) if isinstance(info.get(nid), dict) else {}
        name = label or str(meta.get("label") or nid)
        if meta.get("status") == "now":
            return f"{name}（いまここ）"
        pill = str(meta.get("pill") or "")
        if pill and meta.get("pill_note"):
            notes.setdefault(pill, str(meta["pill_note"]))
        return f"{name}（{pill}）" if pill else name

    for r in [r for r in l1.get("regions") or [] if isinstance(r, dict)][:OVERLAY_L1_REGIONS]:
        rid = str(r.get("id") or "")
        if r.get("kind") == "fog":
            lines.append(f"- {r.get('label') or ''}（霧のハッチ — 論文未投入の地形）")
            node_text(rid, str(r.get("label") or ""))  # 霧の領域を選んだときのパネルの説明
            continue
        lines.append(f"- {r.get('label') or ''}:")
        for n in per_region.get(rid, []):
            lines.append(f"    ○ {node_text(str(n.get('id') or ''), str(n.get('label') or ''))}")
    if notes:
        lines.append("（概念を選ぶと右のパネルに出る説明）")
        lines += [f"  {pill}: {note}" for pill, note in notes.items()]
    # 第 9 波（sync_keys_wave9 面 C・IK-1073）: L1 の事実文の行（いまここの不在 now_note など）
    note = level_note_text(l1)
    if note:
        lines.append(note)
    return lines


def _atlas_view(ctx) -> None:
    """app.js の「分野の地図」→ atlas-data.js（/api/atlas）→ AtlasOverlay.open（L1）。"""
    s = ctx.session
    status, atlas = ctx.main(params={"course": s.course_id, "topic": s.topic_id, "level": ctx.args.get("level") or 1})
    if status == 404:
        return _fact(ctx, "（このコースの画面には分野の地図が表示されていない）", status)
    if status != 200 or not isinstance(atlas, dict):
        return _fact(ctx, "（分野の地図が開かなかった）", status)
    _annotate(ctx, _atlas_digest(atlas))
    _fact(ctx, "\n".join(overlay_l1_lines(atlas)), status)


PANEL_VERIFY_FALLBACK = "検証の記録がありません。"  # atlas-panel.js renderPanel
PANEL_ACTIONS = (("learn", "ここから学ぶ ↗", False), ("evid", "根拠を見る ↗", False),
                 ("mind", "気になる ↗", True), ("report", "修正を報告", True))  # atlas-panel.js ACTIONS


def _panel_pick(atlas: dict, hint: Any) -> Optional[str]:
    """地図で選ぶ要素の解決: L1 の領域 → 表示レベルの概念の順に、名前の完全一致 → 部分一致 → 番号（領域の並び）。"""
    info = atlas.get("nodes") if isinstance(atlas.get("nodes"), dict) else {}
    levels = atlas.get("levels") if isinstance(atlas.get("levels"), dict) else {}
    ids: list[str] = []
    for lv in sorted(levels, key=str):
        data = levels.get(lv) or {}
        for key in ("regions", "nodes"):
            for it in data.get(key) or [] if isinstance(data, dict) else []:
                nid = str((it or {}).get("id") or "") if isinstance(it, dict) else ""
                if nid and nid not in ids:
                    ids.append(nid)
    ids += [k for k in info if k not in ids]
    text = str(hint or "").strip().strip("「」『』")
    if not ids:
        return None
    if not text:
        return ids[0]
    def name(nid: str) -> str:
        meta = info.get(nid) if isinstance(info.get(nid), dict) else {}
        return str(meta.get("label") or "")
    for nid in ids:
        if name(nid) == text:
            return nid
    for nid in ids:
        if text and (text in name(nid) or name(nid) and name(nid) in text):
            return nid
    if text.isdigit() and 1 <= int(text) <= len(ids):
        return ids[int(text) - 1]
    return None


def panel_lines(atlas: dict, node_id: str) -> list[str]:
    """atlas-panel.js renderPanel（第 3 波・IK-0940 / IK-0941）: 選んだ要素の名前と状態語（pill）・pill の説明
    （atlas-overlay.js の #atlas-panel-pill-note）→ 検証行（``verify``。無ければ固定文）→ 承認行（``endorse``。
    無ければ「—」）→ アクション行（learn / evid はノードのフラグがあるときだけ）。配置だけで灯った領域の
    ``verify`` はサーバの事実文（「この領域には論文が置かれています（論文の位置）。…」）をそのまま描く。"""
    info = atlas.get("nodes") if isinstance(atlas.get("nodes"), dict) else {}
    meta = info.get(node_id) if isinstance(info.get(node_id), dict) else {"label": node_id}
    name = str(meta.get("label") or node_id)
    pill = str(meta.get("pill") or "")
    lines = [f"選んだ: {name}" + (f"（{pill}）" if pill else "")]
    if meta.get("pill_note"):
        lines.append(str(meta["pill_note"]))
    lines.append(str(meta.get("verify") or PANEL_VERIFY_FALLBACK))
    lines.append(str(meta.get("endorse") or "—"))
    if meta.get("relations"):
        # 第 9 波（sync_keys_wave9 面 C）: 確定済みの関係（atlas-panel.js p.atlas-panel-relations・承認の行の下）
        lines.append(str(meta["relations"]))
    if meta.get("papers"):
        # 第 10 波（sync_keys_wave10 面 C）: ここに置かれたこのコースの論文（atlas-panel.js p.atlas-panel-papers・関係の行の下）
        lines.append(str(meta["papers"]))
    buttons = [f"[{label}]" for key, label, always in PANEL_ACTIONS if always or meta.get(key)]
    lines.append("ボタン: " + " ".join(buttons))
    return lines


def _atlas_select(ctx) -> None:
    """atlas-overlay.js selectNode → "atlas:nodeselect" → atlas-panel.js renderPanel（地図の領域・概念を選ぶ）。
    ``node_hint`` は名前か番号（領域の並び）。地図は ``/api/atlas`` を読み直す（画面は開いたときの data を使う）。"""
    s = ctx.session
    status, atlas = ctx.main(params={"course": s.course_id, "topic": s.topic_id, "level": ctx.args.get("level") or 1})
    if status == 404:
        return _fact(ctx, "（このコースの画面には分野の地図が表示されていない）", status)
    if status != 200 or not isinstance(atlas, dict):
        return _fact(ctx, "（分野の地図が開かなかった）", status)
    _annotate(ctx, _atlas_digest(atlas))
    nid = _panel_pick(atlas, ctx.args.get("node_hint"))
    if nid is None:
        ctx.missing("atlas_node")
        return _fact(ctx, "\n".join(overlay_l1_lines(atlas) + [harness_note("（指した名前の領域・概念が地図に無い）")]),
                     status)
    # 第 4 波（IK-0907）: app.js の "atlas:nodeselect" の受け手は AtlasOverlay.data.nodes[node_id] の名前と状態を
    # 控え、次のチャットの screen_context.view に載せる（領域など nodes に無い id は控えない）
    nodes = atlas.get("nodes") if isinstance(atlas.get("nodes"), dict) else {}
    info = nodes.get(nid) if isinstance(nodes.get(nid), dict) else None
    if info and info.get("label"):
        ctx.session.scratch["atlas_last_node"] = {"label": str(info["label"]), "status": str(info.get("status") or ""),
                                                  "ledger_status": str(info.get("ledger_status") or ""),
                                                  # 第 6 波（IK-1031）: app.js は detail.node_id も控えて view.atlas_node_id で送る
                                                  "node_id": str(nid)}
        # 第 10 波（FD-note 4）: 選んだ直後の 1 往復だけ selection.atlas_node_id（app.js state.atlasNodeFreshForTurn）
        ctx.session.scratch["atlas_node_fresh"] = True
    ctx.session.scratch.pop("personal_map_view", None)  # 分野の地図（モーダル）を開くのはわたしの地図を閉じた後
    # 第 9 波（atlas H48）: L2 で選んだときは L2 のキャンバスのまま右のパネルが開く（製品の atlas-overlay.js）— L1 に戻さない
    canvas = l2_map_lines(atlas) if _select_level(ctx.args.get("level")) == 2 else overlay_l1_lines(atlas)
    _fact(ctx, "\n".join(canvas + ["", "右のパネル:"] + panel_lines(atlas, nid)), status)


def _atlas_digest(atlas: dict) -> dict:
    threads = atlas.get("threads") if isinstance(atlas.get("threads"), dict) else None
    region_ids = [str(r.get("id") or "") for r in atlas.get("regions") or [] if isinstance(r, dict)]
    return {
        "kind": "learner_atlas",
        "skeleton_version": str(atlas.get("skeleton_version") or ""),
        "cartridge": str(atlas.get("cartridge") or ""),
        "region_ids": region_ids,
        "node_ids": sorted(set(region_ids) | {str(k) for k in (atlas.get("nodes") or {})
                                              if isinstance(atlas.get("nodes"), dict)} - {""}),
        "threads_present": threads is not None,
        "threads_available": bool(threads and threads.get("available") is True),
        "threads_version": str((threads or {}).get("skeleton_version") or ""),
        "threads_note": str((threads or {}).get("note") or ""),
        "thread_pairs": [[str(it.get("from") or ""), str(it.get("to") or "")]
                         for it in (threads or {}).get("items") or [] if isinstance(it, dict)],
    }


def _landscape_digest(landscape: dict) -> dict:
    placements = [p for d in landscape.get("documents") or [] if isinstance(d, dict)
                  for p in d.get("placements") or [] if isinstance(p, dict)]
    return {
        "kind": "learner_landscape",
        "provenance_labels": sorted({str(p.get("provenance_label") or "") for p in placements}),
        "statuses": sorted({str(p.get("status") or "") for p in placements}),
        "unmapped": sum(1 for p in placements if p.get("node_status") == "unmapped") > 0,
        "domain_versions": sorted({str((d or {}).get("frozen_version") or "") for d in landscape.get("domains") or []
                                   if isinstance(d, dict)}),
        "domain_facts": [str(f) for d in landscape.get("domains") or [] if isinstance(d, dict)
                         for f in d.get("facts") or [] if f],
    }


def l2_map_lines(atlas: dict) -> list[str]:
    """L2 のキャンバスの文字版（見出し・パンくず・描かれる概念と実線・推定の糸）。``learning.atlas.layers`` と
    ``learning.atlas.select_node``（level 2 — 第 9 波・atlas H48）が共有する。"""
    # 第 4 波（IK-0942）: オーバーレイのフッターは「地図 版 X / provenance」（旧「骨格 X」）
    lines = [f"分野の地図（{atlas.get('domain_name') or ''}・{ATLAS_VERSION_PREFIX}{atlas.get('skeleton_version') or ''}・"
             f"{atlas.get('provenance') or ''}）"]
    crumbs = (atlas.get("crumbs") or {}).get("2") if isinstance(atlas.get("crumbs"), dict) else ""
    if crumbs:
        lines.append(str(crumbs))
    # 第 25 周第 5 周 H32: 製品の描く範囲（levels["2"] の nodes / edges・omitted_note・threads.undrawn_note）に揃える
    _nodes, drawn, names = l2_drawn(atlas)
    lines += l2_lines(atlas)
    lines.append("")
    lines += threads_lines(atlas, drawn=drawn, names=names)
    return lines


def _select_level(raw: Any) -> int:
    try:
        return 2 if int(str(raw or 1).strip()) == 2 else 1
    except ValueError:
        return 2 if "2" in str(raw) or "L2" in str(raw).upper() else 1


def _atlas_layers(ctx) -> None:
    s = ctx.session
    if not s.course_id:
        return ctx.missing("course_id")
    status, atlas = ctx.main(params={"course": s.course_id, "topic": s.topic_id, "level": ctx.args.get("level") or 2})
    if status == 404:
        return _fact(ctx, "（このコースの画面には分野の地図が表示されていない）", status)
    if status != 200 or not isinstance(atlas, dict):
        return _fact(ctx, "（分野の地図が開かなかった）", status)
    _annotate(ctx, _atlas_digest(atlas))
    lines = l2_map_lines(atlas)
    st2, land = ctx.call("GET", "/api/learning/courses/{course_id}/landscape")
    lines.append("")
    if st2 == 200 and isinstance(land, dict):
        _annotate(ctx, _landscape_digest(land))
        lines += placement_layer_lines(land)
    else:
        lines += placement_layer_lines(None)
    _fact(ctx, "\n".join(lines), status)


def _density_level(count: int) -> int:
    level = 0
    for i, t in enumerate(REGION_DENSITY_THRESHOLDS):
        if count >= t:
            level = i + 1
    return level


def _anchor_size(count: int) -> str:
    size = ANCHOR_SIZE_WORDS[0]
    for i, t in enumerate(ANCHOR_RADIUS_THRESHOLDS):
        if count >= t:
            size = ANCHOR_SIZE_WORDS[i]
    return size


def _sea_anchor_label(anchor_id: str, nodes: dict, regions: dict, skeleton: Any) -> str:
    """corpus-sea.js anchorLabel（第 3 波・IK-0941: 領域 → L1 の概念 → 骨格の nodes 辞書 → id の順。作らない）。
    領域の円の名前は領域名（骨格の nodes 辞書の霧の注記付きラベルを引かない）。"""
    region = regions.get(anchor_id) or {}
    if region.get("label"):
        return str(region["label"])
    node = nodes.get(anchor_id) or {}
    if node.get("label"):
        return str(node["label"])
    meta = (((skeleton or {}).get("nodes") or {}) if isinstance(skeleton, dict) else {}).get(anchor_id)
    if isinstance(meta, dict) and meta.get("label"):
        return str(meta["label"])
    return anchor_id


def papers_lines(documents: Any) -> list[str]:
    """corpus-sea.js renderPapers（題名・著者・年・「地図に置かれていない」注記 → サーバの事実文）。"""
    if not isinstance(documents, dict):
        return []
    docs = [d for d in documents.get("documents") or [] if isinstance(d, dict)]
    facts = [str(f) for f in documents.get("facts") or [] if isinstance(f, str) and f]
    if not docs:
        return [SEA_NO_PAPERS_TEXT] + facts
    lines = [SEA_PAPERS_HEAD]
    for d in docs[:30]:
        meta = [", ".join(str(a) for a in d.get("authors") or [] if a)] if d.get("authors") else []
        if d.get("year"):
            meta.append(str(d["year"]))
        meta = [m for m in meta if m]
        lines.append(f"- {d.get('title') or ''}" + (f"（{' · '.join(meta)}）" if meta else ""))
        if d.get("placed") is False:
            lines.append(f"    {SEA_NOT_PLACED_TEXT}")
        if d.get("can_discuss") is False:
            lines.append("    （選んでも [この論文と議論する] は出ない）")
    lines.append("（論文を選ぶと、その論文の位置づけと [この論文と議論する] が出る）")
    return lines + facts


def sea_density(placements: list[dict], nodes: dict, regions: dict) -> tuple[dict, dict, dict]:
    """corpus-sea.js densityIndex: アンカーごと・領域ごとの論文の集合と、L1 に描かれない概念の帰属領域。

    概念ノードは帰属領域へ、領域への配置はその領域へ、L1 に概念ノードが無ければ配置の ``region_id``
    （landscape 応答）へ寄せる。L1 の領域に無い帰属は数えない（IK-0847 の是正後の製品）。
    """
    anchors: dict[str, set] = {}
    by_region: dict[str, set] = {}
    home: dict[str, str] = {}
    for p in placements:
        aid, did = str(p.get("anchor_node_id") or ""), str(p.get("document_id") or "")
        if not aid or not did:
            continue
        anchors.setdefault(aid, set()).add(did)
        node = nodes.get(aid)
        rid = str(node.get("region") or "") if node else (aid if aid in regions else str(p.get("region_id") or ""))
        if not rid or rid not in regions:
            continue
        by_region.setdefault(rid, set()).add(did)
        if not node and aid not in regions and aid not in home:
            home[aid] = rid
    return anchors, by_region, home


def sea_map_lines(landscape: Any, skeleton: Any, documents: Any) -> list[str]:
    """corpus-sea.js renderMap（段階ビン・件数は出さない）/ renderEdges / renderPapers。

    霧は「この海で閲覧できる論文の配置が無い領域」（厚みの段階 0。骨格側の ``r.kind`` では決めない —
    IK-0847 の是正後の renderMap）。円は ``anchorPoint`` が位置を返すアンカーに描かれる: L1 に描かれる
    概念・領域、それに L1 に概念ノードが無くても配置の ``region_id`` の箱の中（帰属領域を添えて投影する）。
    帰属領域も無いアンカーは円にならないので列挙しない（H3）。
    """
    lines: list[str] = []
    lvl = (((skeleton or {}).get("levels") or {}).get("1") or {}) if isinstance(skeleton, dict) else {}
    nodes = {str(n.get("id")): n for n in lvl.get("nodes") or [] if isinstance(n, dict)}
    regions = {str(r.get("id")): r for r in lvl.get("regions") or [] if isinstance(r, dict)}
    placements = [p for p in ((landscape or {}).get("placements") or []) if isinstance(p, dict)] \
        if isinstance(landscape, dict) else []
    anchors, by_region, home = sea_density(placements, nodes, regions)
    if regions:
        lines.append(f"地図（凡例: {DENSITY_LEGEND_TITLE} — {' / '.join(DENSITY_LEGEND_LABELS)}）:")
        # 第 25 周 H29（第 4 周）: 製品（corpus-sea.js anchorPoint）は概念の円を L1 のノード位置＝領域の箱の中に
        # 描くので、どの円がどの領域かは位置で読める。投影も円を領域ごとに入れ子で列挙する（平らに並べると
        # 「どの円がどの領域の中か読めない」という投影由来の所見になった）
        circles: dict[str, list[str]] = {}
        outside: list[str] = []
        for aid, docs in anchors.items():
            if aid not in nodes and aid not in regions and aid not in home:
                continue  # anchorPoint が null — 画面は円を描かない
            label = _sea_anchor_label(aid, nodes, regions, skeleton)
            size = _anchor_size(len(docs))
            if aid in regions:
                circles.setdefault(aid, []).append(f"  ○ {label}（円の大きさ: {size}・領域そのもの）")
            elif aid in home:
                circles.setdefault(home[aid], []).append(
                    f"  ○ {label}（円の大きさ: {size}・領域「{regions[home[aid]].get('label') or home[aid]}」の箱の中）")
            else:
                rid = str((nodes.get(aid) or {}).get("region") or "")
                line = f"  ○ {label}（円の大きさ: {size}）"
                (circles.setdefault(rid, []) if rid in regions else outside).append(line)
        for rid, r in regions.items():
            level = _density_level(len(by_region.get(rid, ())))
            lines.append(f"- {r.get('label') or rid}: {'霧' if level == 0 else DENSITY_LEGEND_LABELS[level]}")
            lines += circles.get(rid, [])
        if outside:
            lines.append("- （どの領域の箱にも入っていない円）")
            lines += outside
        # 第 7 波（IK-0942 の残り）: 凡例の直下に地図の版（サーバの skeleton_version_label の素通し・空なら出さない）
        vlabel = landscape.get("skeleton_version_label") if isinstance(landscape, dict) else None
        if isinstance(vlabel, str) and vlabel:
            lines.append(vlabel)
    else:
        lines.append("（地図は描かれていない）")
    if isinstance(landscape, dict):
        for f in landscape.get("fringe") or []:
            if isinstance(f, dict):
                lines.append(f"縁: {f.get('region_label') or f.get('region_id') or ''} — {f.get('fact_line') or ''}")
                lines += [f"    {t}" for t in f.get("paper_titles") or []]
                lines.append("    [この先を知りたい]")
        outer = landscape.get("outer") if isinstance(landscape.get("outer"), dict) else None
        if outer:
            lines += [f"外: {outer.get('fact_line') or ''}", "    [この先を知りたい]"]
    lines += papers_lines(documents) or ["（論文の一覧は表示されていない）"]
    return lines


def _domain_label(entry: dict) -> str:
    """corpus-sea.js domainLabelOf（label → domain_name → domain_key）。"""
    return str(entry.get("label") or entry.get("domain_name") or entry.get("domain_key") or "")


def _domain_has_papers(entry: dict) -> bool:
    """corpus-sea.js domainHasPapers（真偽だけ。件数ではない）。"""
    for k in ("has_placements", "has_visible_papers"):
        if isinstance(entry.get(k), bool):
            return entry[k]
    return False


def domains_lines(body: Any) -> list[str]:
    """corpus-sea.js renderDomains / loadDomains（分野名のボタンと「論文が置かれている」点）。"""
    rows = [d for d in ((body or {}).get("domains") or []) if isinstance(d, dict)] if isinstance(body, dict) else []
    if not rows:
        return [SEA_NO_DOMAINS_TEXT]
    lines = ["論文の海（分野を選ぶ）:"]
    for d in rows:
        dot = f"  ●（{SEA_DOMAIN_DOT_TITLE}）" if _domain_has_papers(d) else ""
        lines.append(f"[{_domain_label(d)}]{dot}")
    return lines


def _sea_domain_name(session: PersonaSession, key: str, skeleton: Any) -> str:
    if isinstance(skeleton, dict) and skeleton.get("domain_name"):
        return str(skeleton["domain_name"])
    for d in session.scratch.get("corpus_domain_rows") or []:
        if isinstance(d, dict) and str(d.get("domain_key") or "") == key:
            return _domain_label(d)
    return key


def _sea_select(ctx, key: str, *, main: bool) -> tuple[Optional[int], list[str]]:
    """corpus-sea.js selectDomain: 地図の配置・骨格（/api/atlas）・論文の一覧を読む。"""
    s = ctx.session
    if main:
        status, land = ctx.main(params={"domain_key": key})
    else:
        status, land = ctx.call("GET", "/api/learning/corpus/landscape", params={"domain_key": key})
    st2, skel = ctx.call("GET", "/api/atlas", params={"cartridge": key})
    if st2 == 200 and isinstance(skel, dict):
        _annotate(ctx, _atlas_digest(skel))
    st3, docs = ctx.call("GET", "/api/learning/corpus/documents", params={"domain_key": key})
    for d in ((docs or {}).get("documents") or []) if isinstance(docs, dict) else []:
        if isinstance(d, dict):
            s.remember("corpus_documents", str(d.get("document_id") or d.get("id") or ""))
    land_ok = land if status == 200 else None
    skel_ok = skel if st2 == 200 else None
    _annotate(ctx, _sea_digest(land_ok, skel_ok))
    name = _sea_domain_name(s, key, skel_ok)
    return status, [f"論文の海（分野: {name}）"] + sea_map_lines(land_ok, skel_ok, docs if st3 == 200 else None)


def _sea_digest(landscape: Any, skeleton: Any) -> dict:
    """審判（H9 ④）が読む要約: 配置がある・L1 の領域がすべて霧か（数は持たない）。"""
    lvl = (((skeleton or {}).get("levels") or {}).get("1") or {}) if isinstance(skeleton, dict) else {}
    regions = [r for r in lvl.get("regions") or [] if isinstance(r, dict)]
    placements = [p for p in ((landscape or {}).get("placements") or []) if isinstance(p, dict)] \
        if isinstance(landscape, dict) else []
    return {"kind": "sea_map",
            "has_placements": any(p.get("anchor_node_id") and p.get("document_id") for p in placements),
            "regions_present": bool(regions),
            # corpus-sea.js renderMap: 霧 = 厚みの段階 0（配置から決める — IK-0847 の是正後）
            "all_regions_fog": bool(regions) and not sea_density(
                placements, {str(n.get("id")): n for n in lvl.get("nodes") or [] if isinstance(n, dict)},
                {str(r.get("id")): r for r in regions})[1]}


def _remember_domains(session: PersonaSession, body: Any) -> list[dict]:
    rows = [d for d in ((body or {}).get("domains") or []) if isinstance(d, dict)] if isinstance(body, dict) else []
    session.scratch["corpus_domain_rows"] = rows
    for d in rows:
        session.remember("domains", str(d.get("domain_key") or d.get("key") or ""))
    return rows


def _corpus_domains(ctx) -> None:
    """[🌊 論文の海] を開く（loadDomains → 分野が 1 つなら selectDomain まで — 製品の挙動）。"""
    s = ctx.session
    status, body = ctx.main()
    if status != 200 or not isinstance(body, dict):
        return  # 製品は何も出さない（取得できなかったことを「無い」と言い換えない）→ 汎用のエラー投影
    rows = _remember_domains(s, body)
    lines = domains_lines(body)
    if len(rows) == 1:
        key = str(rows[0].get("domain_key") or "")
        if key:
            st, more = _sea_select(ctx, key, main=False)
            lines += ["（分野が 1 つだけなので、画面はそのままその分野の地図を開いた）", ""] + more
    _fact(ctx, "\n".join(lines), status)


def _corpus_documents(ctx) -> None:
    """論文の海の論文リスト（renderPapers）。"""
    s = ctx.session
    key = str(ctx.args.get("domain_key") or s.latest("domains") or "")
    status, body = ctx.main(params={"domain_key": key} if key else None)
    for d in ((body or {}).get("documents") or []) if isinstance(body, dict) else []:
        if isinstance(d, dict):
            s.remember("corpus_documents", str(d.get("document_id") or d.get("id") or ""))
    if status != 200 or not isinstance(body, dict):
        return
    _fact(ctx, "\n".join(papers_lines(body)), status)


def _sea_map(ctx) -> None:
    s = ctx.session
    key = str(ctx.args.get("domain_key") or s.latest("domains") or "")
    if not key:
        st0, body0 = ctx.call("GET", "/api/learning/corpus/domains")
        _remember_domains(s, body0)
        key = s.latest("domains")
    if not key:
        return ctx.missing("domain_key")
    status, lines = _sea_select(ctx, key, main=True)
    _fact(ctx, "\n".join(lines), status)


# ---------------------------------------------------------------------------
# 公開面
# ---------------------------------------------------------------------------
HANDLERS: dict[str, Callable[[Any], None]] = {
    "admin.atlas.open": _open,
    "admin.atlas.gaps.list": _gaps_list,
    "admin.atlas.gaps.dismissed_filter": _gaps_filter,
    "admin.atlas.gaps.accept": _decide_gap("accept", ("candidate",)),
    "admin.atlas.gaps.dismiss": _decide_gap("dismiss", ("candidate", "accepted")),
    "admin.atlas.gaps.restore": _decide_gap("restore", ("dismissed", "merged")),
    "admin.atlas.gaps.alias_register": _gap_alias_register,
    "admin.atlas.gaps.draft_from_frozen": _draft_from_frozen,
    "admin.atlas.edges.draft_from_frozen": _edge_draft_from_frozen,  # 第 7 波（IK-1044）
    "admin.atlas.gaps.incorporate_preview": _gap_incorporate_preview,
    "admin.atlas.gaps.incorporate_apply": _incorporate_apply("gap"),
    "admin.atlas.edges.list": _edges_list,
    "admin.atlas.edges.dismissed_filter": _edges_filter,
    "admin.atlas.edges.accept": _decide_edge("accept", ("candidate",)),
    "admin.atlas.edges.dismiss": _decide_edge("dismiss", ("candidate", "accepted")),
    "admin.atlas.edges.restore": _decide_edge("restore", ("dismissed",)),
    "admin.atlas.edges.incorporate_preview": _edge_incorporate_preview,
    "admin.atlas.edges.incorporate_apply": _incorporate_apply("edge"),
    "admin.atlas.aliases.list": _aliases_list,
    "admin.atlas.vectors.status": _vectors_status,
    "admin.atlas.vectors.refresh": _vectors_refresh,
    "admin.atlas.freeze_impact": _freeze_impact,
    "admin.atlas.freeze": _freeze,
    "admin.atlas.published_view": _published_view,  # 第 25 周 第 5 波（IK-0992）
    "admin.material.landscape_view": _landscape_view,  # 第 25 周 第 5 波（IK-0995 の到達口）
    "admin.atlas.freeze_blocked_goto": _freeze_blocked_goto,  # 第 25 周 第 2 波（IK-0885）
    "admin.atlas.edges.show_all": _edges_show_all,  # IK-0887
    "admin.atlas.gaps.orphaned_restore": _gap_orphan_restore,  # IK-0884
    "admin.atlas.gaps.orphaned_incorporate": _gap_orphan_incorporate,
    "admin.atlas.edges.orphaned_dismiss": _edge_orphan_dismiss,
    "admin.atlas.edges.orphaned_incorporate": _edge_orphan_incorporate,
    "learning.atlas.view": _atlas_view,  # 第 25 周 H15
    "learning.atlas.select_node": _atlas_select,  # 第 3 波の同期（IK-0940: 配置だけで灯った領域の verify）
    "learning.atlas.layers": _atlas_layers,
    "learning.atlas.sea_map": _sea_map,
    "learning.corpus.domains": _corpus_domains,
    "learning.corpus.documents": _corpus_documents,
}
ACTION_IDS = frozenset(HANDLERS)


def _list_rows(session: PersonaSession, kind: str, field: str = "candidates") -> Optional[list[dict]]:
    """読んだ一覧の行（一覧をまだ読んでいなければ None — 画面の状態が分からないので外さない）。"""
    data = session.scratch.get("atlas_gaps" if kind == "gap" else "atlas_edges")
    if not isinstance(data, dict):
        return None
    return [r for r in data.get(field) or [] if isinstance(r, dict)]


def draft_known(session: PersonaSession) -> bool:
    """読んだ一覧（候補・関係の候補）か骨格の状態（loadState）のどれかが「下書きあり」と言うか。"""
    for key in ("atlas_gaps", "atlas_edges"):
        d = session.scratch.get(key)
        if isinstance(d, dict) and d.get("draft_exists"):
            return True
    state = session.scratch.get("atlas_state")
    return isinstance(state, dict) and isinstance(state.get("draft"), dict)


def unavailable(action_id: str, session: PersonaSession) -> Optional[str]:
    """画面に対象の無い行為（第 25 周 H26・第 4 周）。``actions_exec.unavailable_reason`` が選択肢から外す。

    製品の画面にそのボタンが出ていないと**読んだ一覧から分かる**ときだけ理由を返す（一覧をまだ読んでいなければ
    None）。理由は runner_note に入る短い事実（画面の文ではない）。
    """
    s = session
    kind = "gap" if ".gaps." in action_id else "edge" if ".edges." in action_id else ""
    if not kind and action_id != "admin.atlas.freeze_blocked_goto":
        return None
    if action_id == "admin.atlas.freeze_blocked_goto":
        return None if s.scratch.get("atlas_freeze_blocked") else "凍結の影響に［止めている候補を見る］が出ていない"
    if action_id.endswith(".draft_from_frozen") and draft_known(s):
        # 第 25 周第 5 周 H34（H26 の取りこぼし）: 下書きが既にあれば［現在の版から次版の下書きを作る］は画面に出ない。
        # 候補・関係の一覧・骨格の状態のどれか 1 つでも下書きありと言えば外す（gap の一覧を読む前でも）
        return "次版の下書きが既にある（［現在の版から次版の下書きを作る］は出ていない）"
    cands = _list_rows(s, kind)
    if cands is None:
        return None
    orphans = _list_rows(s, kind, "orphaned_decisions") or []
    data = s.scratch.get("atlas_gaps" if kind == "gap" else "atlas_edges") or {}
    statuses = [_status_of(c) for c in cands]
    what = "候補" if kind == "gap" else "関係の候補"
    leaf = action_id.rsplit(".", 1)[-1]
    if leaf == "accept" and "candidate" not in statuses:
        return f"［採用］を押せる{what}が一覧に無い"
    if leaf == "dismiss" and not any(x in ("candidate", "accepted") for x in statuses):
        return f"［却下／見送り］を押せる{what}が一覧に無い"
    if leaf == "restore" and not any(x in ("dismissed", "merged") for x in statuses):
        return f"［見送りから戻す］が出ている{what}が一覧に無い"
    if leaf in ("incorporate_preview", "orphaned_incorporate"):
        if not data.get("draft_exists"):
            return "次版の下書きが無い（取り込み・反映のボタンは押せない）"
        if not pending_unreflected(s, kind):
            return f"採用済みでまだ下書きに入っていない{what}が無い"
    if leaf in ("orphaned_restore", "orphaned_dismiss") and not orphans:
        return "候補の条件から外れた採用済みの判断の枠が出ていない"
    if leaf == "incorporate_apply":
        pending = s.scratch.get("atlas_pending_incorporation") or {}
        if pending.get("kind") != kind:
            return "取り込み・反映の確認の画面が開いていない"
    if leaf == "draft_from_frozen" and kind == "edge":
        # 第 7 波（IK-1044）: 関係の区画の先頭のボタンは下書きが無ければ候補 0 件でも出る（廃止済みは無効）
        if data.get("draft_exists"):
            return "［現在の版から次版の下書きを作る］が出ていない"
        if s.scratch.get("atlas_domain_retired"):
            return "廃止済みの分野では［現在の版から次版の下書きを作る］は押せない"
    elif leaf == "draft_from_frozen" and (data.get("draft_exists") or not (
            any(x == "accepted" for x in statuses) or orphans)):
        return "［現在の版から次版の下書きを作る］が出ていない"
    if leaf == "show_all" and not data.get("folded_note"):
        return "［すべて表示］が出ていない"
    if leaf == "alias_register" and not any(
            (c.get("near_anchor") or {}).get("node_id") and _status_of(c) not in ("dismissed", "merged")
            for c in cands):
        return "［別名として登録］が出ている候補が無い"
    return None


def execute(action_id: str, ctx) -> bool:
    """この面の行為なら実行して True（``actions_exec`` の dispatch から呼ぶ）。"""
    fn = HANDLERS.get(action_id)
    if fn is None:
        return False
    fn(ctx)
    return True


def ui_only(action_id: str, session: PersonaSession) -> bool:
    """この面には API の無い画面だけの行為は無い（全行為が HTTP を出す）。"""
    return False
