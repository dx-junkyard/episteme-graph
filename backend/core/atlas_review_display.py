"""分野の地図のレビュー画面に出す「誰が・いつ・どの経路で」の表示語（IK-0848 / IK-0850 / IK-0851）。

分野の地図タブ（カテゴリギャップ候補・関係（辺）の候補・骨格の版の出所）は、教員向け
表示投影（display_projection の DP7）の名指しの面ではない。ここに置くのは、その面に
内部値（判断者の UUID・取込経路のトークン）を出さずに、判断の帰属と来歴を**表示語に
写す**ための正本:

- :func:`project_decisions` — 候補の ``decision`` から ``decided_by``（UUID）を外し、
  表示名 ``decided_by_label`` / 日付 ``decided_on`` / 判断の 1 行 ``decision_line`` を足す
  （原則1 改訂: 判断の主体と時点が画面から再構成できること）。
- :func:`skeleton_provenance` — 骨格の ``generated_by`` / ``reviewed_by`` を表示語に写す。
- 空の結果の種類を言い分ける事実文（該当なし／調べていない／調べられなかった）。

数値・件数は出さない（LS5 / RE4 / CR3）。FastAPI を import しない（開発ルール2）。
SQL は ``users`` の表示名を引く 1 本だけで、呼び出し側がセッションの寿命を持つ。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import text as sa_text

from core.document_refs import is_document_uuid
from core.label_vocab import ATLAS_VERSION_PREFIX  # 版の呼び名の正本（IK-0942）

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 判断の帰属（IK-0848）
# ---------------------------------------------------------------------------

#: 判断者の記録が無い（パイプライン由来の行・旧データ）ときの表示語。
DECISION_ACTOR_UNRECORDED = "判断した人の記録なし"

#: 判断者の記録はあるが表示名が引けない（削除済みアカウント等）ときの表示語。
DECISION_ACTOR_UNKNOWN = "表示名を確認できない利用者"

#: 削除済み（墓標化）アカウントの表示語（墓標名 ``deleted-xxxxxxxx`` を出さない）。
DECISION_ACTOR_DELETED = "削除されたアカウント"

#: 判断の状態 → 判断の 1 行の動詞（gap・辺で共通の状態語彙）。
_DECISION_VERBS: Mapping[str, str] = {
    "accepted": "採用しました",
    "dismissed": "見送りました",
    "merged": "統合しました",
    # 見送りから戻した行（status=candidate かつ判断者の記録あり）。
    "candidate": "見送りから戻しました",
}


def _date_only(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return ""


def fetch_actor_labels(session: Any, user_ids: Iterable[Any]) -> dict[str, str]:
    """``user_id -> 表示名`` を返す（1 SQL・引けない id は結果に現れない）。

    墓標化されたアカウント（``status='deleted'``）は :data:`DECISION_ACTOR_DELETED` に写す。
    DB 例外は空（判断の行は :data:`DECISION_ACTOR_UNKNOWN` へ縮退 — 一覧は壊さない）。
    """
    ids = sorted({str(u).strip() for u in user_ids or () if is_document_uuid(u)})
    if not ids:
        return {}
    try:
        rows = session.execute(
            sa_text(
                "SELECT id::text, COALESCE(display_name, ''), COALESCE(status, '') "
                "FROM users WHERE id::text = ANY(:ids)"
            ),
            {"ids": ids},
        ).fetchall()
    except Exception:  # noqa: BLE001 — 表示名が引けなくても候補一覧は返す
        logger.warning("atlas review: actor labels unavailable (non-fatal)", exc_info=True)
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            logger.debug("atlas review: rollback failed (non-fatal)", exc_info=True)
        return {}
    out: dict[str, str] = {}
    for row in rows or ():
        user_id = str(row[0] or "").strip()
        if not user_id:
            continue
        status = str(row[2] if len(row) > 2 else "").strip()
        name = str(row[1] or "").strip()
        if status == "deleted":
            out[user_id] = DECISION_ACTOR_DELETED
        elif name:
            out[user_id] = name
    return out


#: 表示語として固定の主語（名前ではないので鉤括弧で囲まない）。
_DESCRIPTIVE_ACTORS = frozenset(
    {DECISION_ACTOR_UNRECORDED, DECISION_ACTOR_UNKNOWN, DECISION_ACTOR_DELETED}
)


def _actor_subject(actor_label: str) -> str:
    """判断の主語。利用者の表示名は鉤括弧で区切る（IK-0848 第 2 周: 表示名に姓名が無い
    アカウント — 英字の利用者名 — が助詞に直接続いて読みにくい）。表示語はそのまま。"""
    label = str(actor_label or "").strip()
    if not label or label in _DESCRIPTIVE_ACTORS:
        return label
    return f"「{label}」"


def decision_line(decision: Mapping[str, Any], actor_label: str) -> str:
    """判断の 1 行（「「〇〇」が 2026-10-22 に採用しました」）。判断が無ければ空文字。"""
    status = str(decision.get("status") or "").strip()
    verb = _DECISION_VERBS.get(status)
    decided_on = _date_only(decision.get("decided_at"))
    if not verb or (status == "candidate" and not decision.get("_has_actor")):
        return ""
    when = f"{decided_on} に" if decided_on else ""
    if actor_label == DECISION_ACTOR_UNRECORDED:
        return f"{when}{verb}（{DECISION_ACTOR_UNRECORDED}）"
    subject = _actor_subject(actor_label)
    return f"{subject}が {when}{verb}" if when else f"{subject}が{verb}"


#: 候補の出所（AI 由来であった事実）の接頭辞。判断のある候補は「出所 → 判断の 1 行」を
#: 1 行で示し、「AIによる…（未確認）」を判断の後に残さない（IK-0848）。
ORIGIN_PREFIX_GAP = "AIによる検出"
ORIGIN_PREFIX_EDGE = "AIによる推定"

#: 判断が確定している（AI の未確認の段階を過ぎた）状態。見送りから戻した候補は未確認に戻る。
_DECIDED_STATUSES = frozenset({"accepted", "dismissed", "merged"})


def project_decision(
    decision: Mapping[str, Any] | None,
    labels: Mapping[str, str],
    *,
    origin_prefix: str = "",
) -> dict | None:
    """``decision`` の表示用の写し（``decided_by`` の UUID を外し、表示語を足す）。

    ``origin_prefix`` があり判断が確定しているときは ``provenance_line``
    （「AIによる検出 → 〇〇が 2026-10-22 に採用しました」）を足す。画面はこの行を
    「AIによる…（未確認）」の代わりに出す。
    """
    if decision is None:
        return None
    out = dict(decision)
    actor_id = str(out.pop("decided_by", "") or "").strip()
    if not actor_id:
        actor_label = DECISION_ACTOR_UNRECORDED
    else:
        actor_label = labels.get(actor_id) or DECISION_ACTOR_UNKNOWN
    out["decided_by_label"] = actor_label
    out["decided_on"] = _date_only(out.get("decided_at"))
    line = decision_line({**out, "_has_actor": bool(actor_id)}, actor_label)
    if line:
        out["decision_line"] = line
        if origin_prefix and str(out.get("status") or "") in _DECIDED_STATUSES:
            out["provenance_line"] = f"{origin_prefix} → {line}"
    return out


def project_decision_for_response(
    decision: Mapping[str, Any] | None,
    current_user: Mapping[str, Any],
    *,
    session_factory: Any = None,
    origin_prefix: str = "",
) -> dict | None:
    """判断 API の応答を表示用に写す（判断者の UUID を応答に出さない）。

    判断者が操作した本人ならその表示名を使い、別の教員（取り込みの記録で先の採用者が
    残る場合）なら ``session_factory()`` のセッションで ``users`` から引く。
    """
    if decision is None:
        return None
    actor_id = str(decision.get("decided_by") or "").strip()
    own_id = str(current_user.get("id") or "").strip()
    name = str(current_user.get("display_name") or "").strip()
    labels: dict[str, str] = {}
    if actor_id and actor_id == own_id and name:
        labels = {actor_id: name}
    elif actor_id and session_factory is not None:
        try:
            session = session_factory()
        except Exception:  # noqa: BLE001 — 表示名が引けなくても応答は返す
            session = None
        if session is not None:
            try:
                labels = fetch_actor_labels(session, [actor_id])
            finally:
                session.close()
    return project_decision(decision, labels, origin_prefix=origin_prefix)


def project_decisions(
    session: Any, candidates: Iterable[Mapping[str, Any]], *, origin_prefix: str = ""
) -> list[dict]:
    """候補一覧の ``decision`` を一括で表示用に写す（判断者の表示名は 1 SQL）。"""
    items = [dict(c) for c in candidates or ()]
    actor_ids = [
        (c.get("decision") or {}).get("decided_by")
        for c in items
        if isinstance(c.get("decision"), Mapping)
    ]
    labels = fetch_actor_labels(session, actor_ids)
    for item in items:
        if isinstance(item.get("decision"), Mapping):
            item["decision"] = project_decision(
                item["decision"], labels, origin_prefix=origin_prefix
            )
    return items


# ---------------------------------------------------------------------------
# 骨格の版の出所（IK-0850）
# ---------------------------------------------------------------------------

GENERATED_BY_REFERENCE_MAP = "同梱の基準地図から取り込み"
GENERATED_BY_MODEL = "AIによる生成（教員の確認を経て公開）"
GENERATED_BY_UNRECORDED = "記録なし"
GENERATED_BY_OTHER = "作成方法の記録あり（表示名なし）"

REVIEWER_REFERENCE_IMPORT = "基準地図の取り込み時の確認"
REVIEWER_BUNDLED = "同梱データの確認"
REVIEWER_UNKNOWN = DECISION_ACTOR_UNKNOWN


def generated_by_label(token: Any) -> str:
    """``generated_by``（取込経路のトークン）→ 表示語。トークン自体は出さない。"""
    text = str(token or "").strip()
    if not text:
        return GENERATED_BY_UNRECORDED
    lowered = text.lower()
    if lowered.startswith("reference_map:"):
        return GENERATED_BY_REFERENCE_MAP
    if lowered.startswith("model:"):
        return GENERATED_BY_MODEL
    return GENERATED_BY_OTHER


def _reviewer_label(token: str, labels: Mapping[str, str]) -> str:
    if is_document_uuid(token):
        return labels.get(token) or REVIEWER_UNKNOWN
    lowered = token.lower()
    if lowered == "faculty:reference_import":
        return REVIEWER_REFERENCE_IMPORT
    if lowered.startswith("faculty:"):
        return REVIEWER_BUNDLED
    return REVIEWER_UNKNOWN


def skeleton_provenance(session: Any, skeleton: Any) -> dict | None:
    """骨格の来歴の表示語 ``{generated_by_label, reviewed_by_labels}``（重複は 1 つに）。"""
    if skeleton is None:
        return None
    reviewers = [str(r).strip() for r in getattr(skeleton, "reviewed_by", ()) or () if str(r).strip()]
    labels = fetch_actor_labels(session, reviewers) if session is not None else {}
    reviewed: list[str] = []
    for token in reviewers:
        label = _reviewer_label(token, labels)
        if label not in reviewed:
            reviewed.append(label)
    return {
        "generated_by_label": generated_by_label(getattr(skeleton, "generated_by", "")),
        "reviewed_by_labels": reviewed,
    }


# ---------------------------------------------------------------------------
# 空の結果の種類（IK-0851）
# ---------------------------------------------------------------------------

#: 近い既存項目との照合の状態（``near_anchor_check.state``）。
NEAR_ANCHOR_CHECKED = "checked"
NEAR_ANCHOR_NO_INDEX = "no_index"
NEAR_ANCHOR_UNAVAILABLE = "unavailable"

NEAR_ANCHOR_NOTES: Mapping[str, str] = {
    NEAR_ANCHOR_CHECKED: (
        "現在の版のベクトル索引と照合しました。別表記の注記が無い候補は、"
        "近い既存項目が見つからなかったものです。"
    ),
    NEAR_ANCHOR_NO_INDEX: (
        "この分野の現在の版にはベクトル索引がないため、近い既存項目との照合はしていません。"
        "「ベクトル索引」区画の［索引を再構築］で作ると、別表記の注記と［別名として登録］が出ます。"
    ),
    NEAR_ANCHOR_UNAVAILABLE: "近い既存項目との照合は、いまは行えませんでした。",
}

#: 見送り済みの表示をオンにしたが、見送り済みの候補が無いときの事実文（gap・辺で共通）。
DISMISSED_NONE_NOTE = "見送り済みの候補はありません。"


def near_anchor_check(state: str) -> dict | None:
    """``near_anchor_check`` の DTO（状態が分からなければ ``None`` = キーを付けない）。"""
    note = NEAR_ANCHOR_NOTES.get(str(state or ""))
    if not note:
        return None
    return {"state": state, "note": note}


def dismissed_none_note(
    candidates: Iterable[Mapping[str, Any]], *, include_dismissed: bool, suppressed: Iterable[str]
) -> str | None:
    """見送り済みの表示で見送り済みがゼロのときだけ事実文を返す。"""
    if not include_dismissed:
        return None
    wanted = set(suppressed)
    for candidate in candidates or ():
        decision = candidate.get("decision") if isinstance(candidate, Mapping) else None
        if isinstance(decision, Mapping) and str(decision.get("status") or "") in wanted:
            return None
    return DISMISSED_NONE_NOTE


# ---------------------------------------------------------------------------
# 候補ゼロの理由（IK-0851 第 2 周）と導出から外れた判断（IK-0884）
# ---------------------------------------------------------------------------

#: 候補ゼロの理由の事実文（``empty_notes``）。件数は書かない。閾値（2 本）は制度の
#: 定数で、データの数ではない（``schema.MIN_DOCUMENTS_FOR_CANDIDATE`` と一致させる）。
EMPTY_NO_SIGNALS = (
    "この分野では、論文の解析が「地図に置けなかった主題」をまだ記録していません。"
    "候補は、論文を取り込んで解析すると、置けなかった主題から作られます。"
)
EMPTY_BELOW_THRESHOLD = (
    "置けなかった主題の記録はありますが、2 本以上の論文で同じ主題が繰り返されていないため、"
    "候補にしていません（1 本の論文だけの主題は分野のカテゴリとして扱いません）。"
)
EMPTY_FOLDED_DUPLICATES = (
    "同じ論文を別の形式（PDF 版と TeX 版など）で取り込んだものは 1 本と数えます。"
    "そのため、別の取り込みどうしでしか繰り返されていない主題は候補になりません。"
)
EMPTY_RESOLVED = "論文が挙げた主題のうち、現在の公開版に同じ名前の項目があるものは候補にしていません。"
EMPTY_ALL_DECIDED = (
    "残っている候補はすべて見送り済みか統合済みです。「見送り済みも表示」で確かめられます。"
)


def empty_reason_notes(report: Mapping[str, Any] | None) -> list[str]:
    """候補ゼロのときに添える理由の事実文（該当するものだけ・順序は決定論）。"""
    if not isinstance(report, Mapping) or not report:
        return []
    if not report.get("signals_present"):
        return [EMPTY_NO_SIGNALS]
    notes: list[str] = []
    if report.get("below_threshold"):
        notes.append(EMPTY_BELOW_THRESHOLD)
    if report.get("folded_duplicates"):
        notes.append(EMPTY_FOLDED_DUPLICATES)
    if report.get("resolved_present"):
        notes.append(EMPTY_RESOLVED)
    if report.get("suppressed_present"):
        notes.append(EMPTY_ALL_DECIDED)
    return notes


#: 導出から外れた判断の枠の見出しの説明（``orphaned_note``）。
ORPHANED_NOTE = (
    "次の候補は採用済みですが、いまの論文からは候補として導出されなくなりました。"
    "採用は残っているので、次版の下書きへ取り込むか、候補へ戻すまで公開（凍結）はできません。"
)

#: 導出から外れた理由（``orphaned_decisions[].reason_fact``）。
ORPHANED_REASON_FACTS: Mapping[str, str] = {
    "below_threshold": (
        "いまの論文では、2 本以上の論文で繰り返される条件を満たしていません"
        "（同じ論文の重複取り込みは 1 本と数えます）。"
    ),
    "resolved": "現在の公開版に同じ名前の項目があります。",
    "no_signals": "この候補の根拠だった論文の記録が、再解析または削除でなくなりました。",
}

#: 取り込み済み（下書きに入っている）判断の一文。
ORPHANED_IN_DRAFT = "次版の下書きに入っています（公開すると地図の項目になります）。"


def project_orphaned_decisions(
    session: Any, items: Iterable[Mapping[str, Any]], *, origin_prefix: str = ORIGIN_PREFIX_GAP
) -> list[dict]:
    """導出から外れた判断行の表示用の写し（判断者は表示名・理由は事実文）。"""
    rows = [dict(item) for item in items or ()]
    projected = project_decisions(session, rows, origin_prefix=origin_prefix)
    out: list[dict] = []
    for item in projected:
        reason = str(item.get("reason") or "")
        item["reason_fact"] = ORPHANED_REASON_FACTS.get(reason, "")
        if item.get("in_draft"):
            item["in_draft_note"] = ORPHANED_IN_DRAFT
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# 判断済みを先頭の帯に（IK-0938）
# ---------------------------------------------------------------------------

#: 並びの帯（小さいほど先頭）。採用済み・未反映は凍結を止めるので最初に置く。
REVIEW_BAND_ACCEPTED_PENDING = 0
REVIEW_BAND_IN_DRAFT = 1
REVIEW_BAND_OTHER = 2

#: 採用済み・未反映の候補に添える一文（``pending_note``）。凍結を止めることを言う。
ACCEPTED_PENDING_NOTE = (
    "採用済みで、まだ次版の下書きに入っていません。下書きへ反映するか候補へ戻すまで、"
    "公開（凍結）はできません。"
)


def review_band(item: Mapping[str, Any], in_draft: bool) -> int:
    """候補 1 件の並びの帯（採用済み・未反映 → 下書きに入っている → それ以外）。"""
    decision = item.get("decision") if isinstance(item.get("decision"), Mapping) else None
    status = str((decision or {}).get("status") or "")
    if status == "accepted" and not in_draft:
        return REVIEW_BAND_ACCEPTED_PENDING
    if in_draft:
        return REVIEW_BAND_IN_DRAFT
    return REVIEW_BAND_OTHER


def order_decided_first(
    items: Iterable[Mapping[str, Any]], in_draft: Any
) -> list[dict]:
    """判断の状態で帯に分けて並べ直す（帯の中は入力の順 = 安定ソート。IK-0938）。

    ``in_draft`` は候補 → 下書きに入っているかの callable。近さの値・件数は使わない
    （帯の中の順位は導出側の順のまま — RE4）。採用済み・未反映には ``pending_note`` を足す。
    """
    rows = [dict(item) for item in items or ()]
    banded = []
    for index, row in enumerate(rows):
        try:
            flag = bool(in_draft(row))
        except Exception:  # noqa: BLE001 — 判定できない候補は「下書きに無い」と扱う
            flag = False
        band = review_band(row, flag)
        if band == REVIEW_BAND_ACCEPTED_PENDING:
            row["pending_note"] = ACCEPTED_PENDING_NOTE
        banded.append((band, index, row))
    banded.sort(key=lambda entry: (entry[0], entry[1]))
    return [row for _band, _index, row in banded]


# ---------------------------------------------------------------------------
# 凍結を止めている採用の帰属と根拠（IK-0939）
# ---------------------------------------------------------------------------

PENDING_NOTE_TEMPLATE = "採用のメモ: {note}"
PENDING_NOTE_NONE = "採用のメモはありません。"
PENDING_PAPERS_TEMPLATE = "根拠の論文: {titles}"
PENDING_PAPERS_HIDDEN = "閲覧できない論文も根拠に含まれています。"
PENDING_PAPERS_NONE = "根拠だった論文の記録はもうありません（再解析または削除）。"
PENDING_EDGE_BASIS_NONE = "採用の根拠（論文の配置か説明文の近さか）の記録はありません。"
PENDING_KIND_GAP = "gap"
PENDING_KIND_EDGE = "edge"


def _paper_titles_line(documents: Iterable[Mapping[str, Any]]) -> str:
    titles = [str(d.get("title") or "").strip() for d in documents or ()]
    titles = [f"『{t}』" for t in titles if t]
    return PENDING_PAPERS_TEMPLATE.format(titles="、".join(titles)) if titles else ""


def pending_freeze_items(
    session: Any,
    *,
    gap_items: Iterable[Mapping[str, Any]] = (),
    edge_items: Iterable[Mapping[str, Any]] = (),
    edge_label: Any = None,
    edge_origins: Mapping[str, Iterable[str]] | None = None,
    visible_document_ids: Iterable[str] | None = None,
) -> list[dict]:
    """凍結を止めている採用 1 件ずつの「ラベル + 帰属の一行 + 根拠の一文 + メモ」（IK-0939）。

    凍結のゲート（何が止めるか）は変えない。ここは止め理由を読む教員が、他の教員の採用を
    材料つきで引き受けるか戻すかを決められるようにする写しだけを作る。判断者の UUID は
    表示名へ写し（IK-0848）、根拠の論文は閲覧できるものだけを題名で列挙する（件数なし）。
    """
    from core.atlas_gaps.store import gate_documents

    gaps = [dict(i) for i in gap_items or ()]
    edges = [dict(i) for i in edge_items or ()]
    actor_ids = [
        (i.get("decision") or {}).get("decided_by")
        for i in gaps + edges
        if isinstance(i.get("decision"), Mapping)
    ]
    try:
        labels = fetch_actor_labels(session, actor_ids)
    except Exception:  # noqa: BLE001 — 表示名が引けなくても止め理由は出す
        labels = {}
    origins = dict(edge_origins or {})
    out: list[dict] = []

    def _base(kind: str, label: str, item: Mapping[str, Any]) -> dict:
        decision = item.get("decision") if isinstance(item.get("decision"), Mapping) else None
        projected = project_decision(decision, labels) if decision else None
        note = str(item.get("review_note") or (decision or {}).get("review_note") or "").strip()
        return {
            "kind": kind,
            "label": label,
            "decision_line": (projected or {}).get("decision_line", ""),
            "note_line": PENDING_NOTE_TEMPLATE.format(note=note) if note else PENDING_NOTE_NONE,
        }

    for item in gaps:
        label = str(item.get("proposed_label") or "").strip()
        if not label:
            continue
        entry = _base(PENDING_KIND_GAP, label, item)
        docs, hidden = gate_documents(item.get("documents") or (), visible_document_ids)
        lines = []
        titles = _paper_titles_line(docs)
        if titles:
            lines.append(titles)
        if hidden:
            lines.append(PENDING_PAPERS_HIDDEN)
        if not lines:
            lines.append(PENDING_PAPERS_NONE)
        entry["basis_line"] = " ".join(lines)
        out.append(entry)
    for item in edges:
        label = str(edge_label(item) if callable(edge_label) else "").strip()
        if not label:
            continue
        entry = _base(PENDING_KIND_EDGE, label, item)
        basis = edge_basis_line(origins.get(str(item.get("edge_key") or ""), ()))
        entry["basis_line"] = basis or PENDING_EDGE_BASIS_NONE
        out.append(entry)
    return out


#: 反映の確認の時点の一文（IK-0939: 「学習者には表示されません」が凍結後を言わなかった）。
INCORPORATE_TIMING_EDGE = (
    "下書きの間は学習者に表示されません。公開（凍結）すると、学習者の分野の地図に関係として出ます。"
)
INCORPORATE_TIMING_GAP = (
    "下書きの間は学習者に表示されません。公開（凍結）すると、学習者の分野の地図に項目として出ます。"
)


#: 反映した直後の結果文（IK-0938。何を足したかを名指す）。
INCORPORATED_EDGE_RESULT = "「{label}」を次版の下書きに追加しました。"
INCORPORATED_GAP_RESULT = "「{label}」を次版の下書きに追加しました。"


# ---------------------------------------------------------------------------
# 関係（辺）の候補の根拠と畳み（IK-0887 / IK-0886）
# ---------------------------------------------------------------------------

#: 候補の根拠の 1 行（``basis_line``）。近さの段階ラベルは最上位帯しか通さないので
#: 全件同じになる（無情報）— 代わりに「何が近いと言っているか」を書く。
EDGE_BASIS_VECTOR = (
    "両方の概念の説明文（ラベル・別名・確認済み配置の引用）どうしが近いという推定です。"
    "論文の配置の根拠はありません。"
)
EDGE_BASIS_CO_OCCURRENCE = "2 本以上の論文が、両方の概念に配置されています（下の論文を開くと理由と原文が出ます）。"
EDGE_BASIS_BOTH = (
    "2 本以上の論文が両方の概念に配置されており、説明文どうしも近いと推定されています。"
)


def edge_basis_line(origins: Iterable[str]) -> str:
    kinds = set(origins or ())
    if "co_occurrence" in kinds and "vector" in kinds:
        return EDGE_BASIS_BOTH
    if "co_occurrence" in kinds:
        return EDGE_BASIS_CO_OCCURRENCE
    if "vector" in kinds:
        return EDGE_BASIS_VECTOR
    return ""


#: 教員の一覧で畳んだ候補があるときの事実文（件数は出さない）。
EDGE_FOLDED_NOTE = (
    "ほかにも、説明文どうしが近いと推定された組があります（論文の根拠はありません）。"
    "論文の根拠がある組と、近いと推定された順の上位だけを表示しています。"
)

#: 下書きに既にある辺の候補の一文（``in_draft_note``）。
EDGE_IN_DRAFT = "次版の下書きに入っています（公開すると地図の関係になります）。"

#: 向きの文（``direction_line``）。依存だけが向きを持つ（記載順 from → to）。
EDGE_DIRECTION_DEPENDS = "「{from_label}」が「{to_label}」に依存する関係として記載されます。"
EDGE_DIRECTION_UNDIRECTED = "「{from_label}」と「{to_label}」の関係として記載されます（向きはありません）。"


def edge_direction_line(kind: str, from_label: str, to_label: str) -> str:
    """種別の向きの一文（語彙外の種別は空文字）。種別の表示語は ``EDGE_KIND_LABELS``。"""
    from core.label_vocab import EDGE_KIND_LABELS

    kind = str(kind or "")
    if kind not in EDGE_KIND_LABELS:
        return ""
    template = EDGE_DIRECTION_DEPENDS if kind == "depends" else EDGE_DIRECTION_UNDIRECTED
    return template.format(from_label=from_label or "", to_label=to_label or "")


#: 導出から外れた辺の判断（IK-0884 と同じ構造）。
EDGE_ORPHANED_NOTE = (
    "次の関係は採用済みですが、いまの索引・論文からは候補として導出されなくなりました。"
    "次版の下書きへ反映するか、候補へ戻すまで公開（凍結）はできません。"
)


# ---------------------------------------------------------------------------
# ベクトル索引の区画（IK-0889）
# ---------------------------------------------------------------------------

VECTOR_INDEX_PURPOSE = (
    "この索引は、論文由来の候補に「近い既存項目（別表記）」の注記を付けることと、"
    "関係（辺）の候補を推定することに使います。"
)
VECTOR_INDEX_CURRENT = (
    "現在の公開版（" + ATLAS_VERSION_PREFIX + "{version}）の全項目を索引しています。作り直す必要はありません。"
)
VECTOR_INDEX_PARTIAL = (
    "現在の公開版（" + ATLAS_VERSION_PREFIX + "{version}）に、索引の無い項目があります。［索引を再構築］で埋められます。"
)
VECTOR_INDEX_NONE = (
    "現在の公開版（" + ATLAS_VERSION_PREFIX + "{version}）の索引はまだありません。［索引を再構築］で作れます。"
)
VECTOR_INDEX_STALE = (
    "地図が新しい版になり、索引は前の版のままです。［索引を再構築］で作り直してください。"
)
VECTOR_REFRESH_UNCHANGED = "変更はありませんでした（すべての項目の索引をそのまま使いました）。"
VECTOR_REFRESH_UPDATED = "索引を作り直しました（変わった項目だけ新しく索引しました）。"


def display_datetime(value: Any) -> str:
    """ISO の日時 → 「YYYY-MM-DD HH:MM（UTC）」（読めなければ空文字）。"""
    text = str(value or "").strip()
    if len(text) < 16 or text[4] != "-" or text[10] not in ("T", " "):
        return ""
    return f"{text[:10]} {text[11:16]}（UTC）"


def vector_status_facts(
    *, version: str, total_nodes: int, embedded_nodes: int, stale: bool
) -> list[str]:
    """索引の状態の事実文（用途 → 要否）。"""
    facts = [VECTOR_INDEX_PURPOSE]
    if stale:
        facts.append(VECTOR_INDEX_STALE)
    elif embedded_nodes <= 0:
        facts.append(VECTOR_INDEX_NONE.format(version=version))
    elif embedded_nodes < total_nodes:
        facts.append(VECTOR_INDEX_PARTIAL.format(version=version))
    else:
        facts.append(VECTOR_INDEX_CURRENT.format(version=version))
    return facts


def vector_refresh_fact(summary: Mapping[str, Any]) -> str:
    """再構築の要約 → 一文（完了したときだけ。スキップの理由は呼び出し側の既存表示）。"""
    if str(summary.get("status") or "") != "completed":
        return ""
    embedded = summary.get("embedded")
    if isinstance(embedded, int) and embedded == 0:
        return VECTOR_REFRESH_UNCHANGED
    return VECTOR_REFRESH_UPDATED


__all__ = [
    "ACCEPTED_PENDING_NOTE",
    "INCORPORATED_EDGE_RESULT",
    "INCORPORATED_GAP_RESULT",
    "INCORPORATE_TIMING_EDGE",
    "INCORPORATE_TIMING_GAP",
    "order_decided_first",
    "pending_freeze_items",
    "review_band",
    "EDGE_FOLDED_NOTE",
    "EDGE_IN_DRAFT",
    "EDGE_ORPHANED_NOTE",
    "EMPTY_ALL_DECIDED",
    "EMPTY_BELOW_THRESHOLD",
    "EMPTY_FOLDED_DUPLICATES",
    "EMPTY_NO_SIGNALS",
    "EMPTY_RESOLVED",
    "ORPHANED_IN_DRAFT",
    "ORPHANED_NOTE",
    "ORPHANED_REASON_FACTS",
    "VECTOR_INDEX_PURPOSE",
    "display_datetime",
    "edge_basis_line",
    "edge_direction_line",
    "empty_reason_notes",
    "project_orphaned_decisions",
    "vector_refresh_fact",
    "vector_status_facts",
    "DECISION_ACTOR_DELETED",
    "DECISION_ACTOR_UNKNOWN",
    "DECISION_ACTOR_UNRECORDED",
    "DISMISSED_NONE_NOTE",
    "NEAR_ANCHOR_CHECKED",
    "NEAR_ANCHOR_NOTES",
    "NEAR_ANCHOR_NO_INDEX",
    "NEAR_ANCHOR_UNAVAILABLE",
    "ORIGIN_PREFIX_EDGE",
    "ORIGIN_PREFIX_GAP",
    "decision_line",
    "dismissed_none_note",
    "fetch_actor_labels",
    "generated_by_label",
    "near_anchor_check",
    "project_decision",
    "project_decision_for_response",
    "project_decisions",
    "skeleton_provenance",
]
