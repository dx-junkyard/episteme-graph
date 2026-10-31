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

from core.account_display import NAME_UNREGISTERED_ACTOR, display_label
from core.document_refs import is_document_uuid
from core.label_vocab import ATLAS_VERSION_PREFIX, atlas_version_label  # 版の呼び名の正本（IK-0942）

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
                "SELECT id::text, COALESCE(profile_name, ''), COALESCE(status, '') "
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
        if status == "deleted":
            out[user_id] = DECISION_ACTOR_DELETED
            continue
        # 人に見せる名前は profile_name だけ（IK-1007: display_name はログイン ID を兼ねる）。
        # 登録が無ければ名前なしの事実語に戻す（ログイン ID へフォールバックしない）。
        out[user_id] = display_label({"profile_name": row[1]}) or NAME_UNREGISTERED_ACTOR
    return out


#: 表示語として固定の主語（名前ではないので鉤括弧で囲まない）。
_DESCRIPTIVE_ACTORS = frozenset(
    {DECISION_ACTOR_UNRECORDED, DECISION_ACTOR_UNKNOWN, DECISION_ACTOR_DELETED,
     NAME_UNREGISTERED_ACTOR}
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

    判断者の表示名は ``session_factory()`` のセッションで ``users.profile_name`` から引く
    （本人でも同じ — トークンの利用者名はログイン ID なので表示に使わない。IK-1007）。
    """
    if decision is None:
        return None
    actor_id = str(decision.get("decided_by") or "").strip()
    labels: dict[str, str] = {}
    if actor_id and session_factory is not None:
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
#: 次版の下書きを前の凍結版から作ったときのトークン接頭辞（IK-0991・第 25 周 第 5 波）。
#: 作成方法は版ごと — 前の版の generated_by を写さない。``revision_of:{前の版}``。
GENERATED_BY_REVISION_PREFIX = "revision_of:"
GENERATED_BY_REVISION_TEMPLATE = (
    "前の版（{version}）からの改訂（候補・関係の取り込みと教員の編集）"
)
GENERATED_BY_REVISION_UNKNOWN = "前の版からの改訂（候補・関係の取り込みと教員の編集）"


def revision_generated_by(previous_version: Any) -> str:
    """次版の下書きの ``generated_by`` トークン（IK-0991）。"""
    return f"{GENERATED_BY_REVISION_PREFIX}{str(previous_version or '').strip()}"

REVIEWER_REFERENCE_IMPORT = "基準地図の取り込み時の確認"
REVIEWER_BUNDLED = "同梱データの確認"
REVIEWER_UNKNOWN = DECISION_ACTOR_UNKNOWN


def generated_by_label(token: Any) -> str:
    """``generated_by``（取込経路のトークン）→ 表示語。トークン自体は出さない。"""
    text = str(token or "").strip()
    if not text:
        return GENERATED_BY_UNRECORDED
    lowered = text.lower()
    if lowered.startswith(GENERATED_BY_REVISION_PREFIX):
        previous = atlas_version_label(text[len(GENERATED_BY_REVISION_PREFIX):])
        if previous:
            return GENERATED_BY_REVISION_TEMPLATE.format(version=previous)
        return GENERATED_BY_REVISION_UNKNOWN
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
    # IK-0995（第 25 周 第 5 波）: 記録の所在を言う（存在だけを言って読めない場所にしない）。
    "各論文の置けなかった主題は、教材管理で論文の行の［⋯］→［位置づけ（分野マップ）…］を開くと読めます。"
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
#: IK-0966: 区画の冒頭は定義の文にする（行の状態を見ない断言をしない）。凍結を止める
#: 一文は、下書きに入っていない行があるときだけ :func:`orphaned_note` が足す。
ORPHANED_NOTE = (
    "次の候補は採用済みですが、いまの論文からは候補として導出されなくなった判断です。"
    "採用は残っています。"
)
#: 下書きに入っていない行があるときだけ足す一文（IK-0966）。
ORPHANED_BLOCKS_FREEZE = (
    "次版の下書きに入っていない候補があるあいだは、取り込むか候補へ戻すまで公開（凍結）はできません。"
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
#: 採用の時点の出所は記録されていないが、いまの導出では候補として立つ組（IK-0962）。
#: 採用の時点の根拠だと偽らないよう「いまの導出では」と時点を明示する（推測で埋めない）。
PENDING_EDGE_BASIS_DERIVED_TEMPLATE = (
    "採用の時点の根拠は記録されていません。いまの導出では: {basis}"
)
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
    edge_derived_origins: Mapping[str, Iterable[str]] | None = None,
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
    derived = dict(edge_derived_origins or {})
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
        # IK-1019: ［止めている候補を見る］がこのカードへ着地するための番地（表示しない）。
        entry["target_key"] = str(item.get("cluster_key") or "")
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
        key = str(item.get("edge_key") or "")
        entry["target_key"] = key  # IK-1019（表示しない番地）
        basis = edge_basis_line(origins.get(key, ()))
        if not basis:
            # 記録の無い過去の採用（IK-0962）: いま導出される組なら時点を明示して添える。
            now_basis = edge_basis_line(derived.get(key, ()))
            if now_basis:
                basis = PENDING_EDGE_BASIS_DERIVED_TEMPLATE.format(basis=now_basis)
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
#: IK-1118（第 25 周 第 10 波）: 関係の反映の知らせは種別の句（``label_vocab.edge_relation_phrase``）で言う
#: — 依存は「「A」が「B」に依存する関係を…」で向きを落とさない。種別が引けなければ従来の文。
INCORPORATED_EDGE_RESULT_RELATION = "{phrase}関係を次版の下書きに追加しました。"


def incorporated_edge_result_line(kind: str, from_label: str, to_label: str) -> str:
    """反映の知らせ（IK-0938 / IK-1118）。向きの句は ``label_vocab.edge_relation_phrase`` が正本。"""
    from core.label_vocab import edge_relation_phrase

    phrase = edge_relation_phrase(kind, from_label, to_label)
    if phrase:
        return INCORPORATED_EDGE_RESULT_RELATION.format(phrase=phrase)
    return INCORPORATED_EDGE_RESULT.format(label=f"{from_label} — {to_label}")


#: H54（第 25 周 第 10 波）: 次版の下書きに入った（または公開中の版に入っている）関係の見送りは受けない。
#: 判断だけ見送りになり、下書き・公開版には関係が残る食い違いを作らないため。外す操作は下書きの編集。
EDGE_DISMISS_LOCKED_IN_DRAFT = (
    "この関係は次版の下書きに入っているため、見送れません。"
    "見送るときは、先に次版の下書きからこの関係を外して保存してください。"
)
EDGE_DISMISS_LOCKED_IN_FROZEN = (
    "この関係は公開中の地図（版 {version}）に入っているため、見送れません。"
    "外すときは、次版の下書きを作ってこの関係を外し、凍結してください。"
)
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
#: IK-1072（第 25 周 第 9 波）: 向きの句は ``label_vocab.edge_direction_phrase`` が正本で、ここは結びだけを持つ。
#: 反映前（採用直後・反映の確認）は「記載されます」、次版の下書きに入った後は「記載されています」。
EDGE_DIRECTION_DEPENDS = "「{from_label}」が「{to_label}」に依存する関係として記載されます。"
EDGE_DIRECTION_UNDIRECTED = "「{from_label}」と「{to_label}」の関係として記載されます（向きはありません）。"
EDGE_DIRECTION_SUFFIX_PENDING = "関係として記載されます。"
EDGE_DIRECTION_SUFFIX_IN_DRAFT = "関係として次版の下書きに記載されています。"
EDGE_DIRECTION_UNDIRECTED_IN_DRAFT = (
    "「{from_label}」と「{to_label}」の関係として次版の下書きに記載されています（向きはありません）。"
)


def edge_direction_line(
    kind: str, from_label: str, to_label: str, *, in_draft: bool = False
) -> str:
    """種別の向きの一文（語彙外の種別は空文字）。種別の表示語は ``EDGE_KIND_LABELS``。

    ``in_draft`` が真なら（次版の下書きに入った後）現在形で言う（IK-1072 の頭脳 NEW — 反映後も未来形の文が残っていた）。
    """
    from core.label_vocab import EDGE_KIND_LABELS, edge_direction_phrase

    kind = str(kind or "")
    if kind not in EDGE_KIND_LABELS:
        return ""
    phrase = edge_direction_phrase(kind, from_label, to_label)
    if phrase:
        suffix = EDGE_DIRECTION_SUFFIX_IN_DRAFT if in_draft else EDGE_DIRECTION_SUFFIX_PENDING
        return phrase + suffix
    template = EDGE_DIRECTION_UNDIRECTED_IN_DRAFT if in_draft else EDGE_DIRECTION_UNDIRECTED
    return template.format(from_label=from_label or "", to_label=to_label or "")


#: IK-1149（第 25 周 第 11 波）: 有向の関係の向きが採用のときに選ばれていない（migration 096 より前の採用）ことの事実文。
#: カード・反映の確認に出す。記載の順は 2 つの項目の内部の並びで、教員の判断ではない。
EDGE_DIRECTION_UNRECORDED_NOTE = (
    "この関係の向きは、採用のときに選ばれていません（2 つの項目の内部の並びで記載されています）。"
    "向きが違うときは、次版の下書きでこの関係の向きを入れ替えて保存してください。"
)

#: 導出から外れた辺の判断（IK-0884 と同じ構造）。
EDGE_ORPHANED_NOTE = (
    "次の関係は採用済みですが、いまの索引・論文からは候補として導出されなくなった判断です。"
    "採用は残っています。"
)
#: 下書きに入っていない関係があるときだけ足す一文（IK-0966 の辺版）。
EDGE_ORPHANED_BLOCKS_FREEZE = (
    "次版の下書きに入っていない関係があるあいだは、反映するか候補へ戻すまで公開（凍結）はできません。"
)


def orphaned_note(items: Iterable[Mapping[str, Any]], *, edge: bool = False) -> str:
    """外れた判断の区画の冒頭文（IK-0966）。定義 + 未取り込みの行があるときだけ止める一文。"""
    rows = [item for item in items or () if isinstance(item, Mapping)]
    if edge:
        base, blocks = EDGE_ORPHANED_NOTE, EDGE_ORPHANED_BLOCKS_FREEZE
    else:
        base, blocks = ORPHANED_NOTE, ORPHANED_BLOCKS_FREEZE
    if any(not item.get("in_draft") for item in rows):
        return base + blocks
    return base


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
#: 索引はあるが、説明の材料（別名・確認済み配置の引用）が変わった項目がある（IK-0965。
#: 再構築と同じ ``source_hash`` の比較で判定する。件数は書かず名前で）。
VECTOR_INDEX_MATERIAL_CHANGED = (
    "現在の公開版（" + ATLAS_VERSION_PREFIX + "{version}）の索引のうち、説明の材料"
    "（別名・確認済み配置の引用）が変わった項目があります: {labels}。［索引を再構築］で反映されます。"
)
VECTOR_REFRESH_UNCHANGED = "変更はありませんでした（すべての項目の索引をそのまま使いました）。"
VECTOR_REFRESH_UPDATED = "索引を作り直しました（変わった項目だけ新しく索引しました）。"
#: 新しく索引した項目を名前で言う結果文（IK-0965）。
VECTOR_REFRESH_UPDATED_NAMED = "索引を作り直しました。新しく索引した項目: {labels}。"


#: 凍結の直後、裏で新しい版の索引を作っている間の一文（IK-0993・第 25 周 第 5 波）。
#: 手作業（［索引を再構築］）を促さない。所要時間の数値は言わない。
VECTOR_INDEX_BUILDING = (
    "地図が新しい版（" + ATLAS_VERSION_PREFIX + "{version}）になったので、索引を作っています。"
    "作り終わると状態がここに出ます（作り直しの操作は要りません）。"
)
#: 構築中に［索引を再構築］を押したときの一文（二重に embedding を走らせない）。
VECTOR_INDEX_BUILDING_REFRESH = (
    "いま新しい版の索引を作っています。作り終わるまで再構築は始めません。"
    "少し時間をおいて状態を読み直してください。"
)
#: 関係の候補のうち、説明文の近さによる候補が索引の不在で出せないときの一文（IK-0993）。
EDGE_INDEX_BUILDING_NOTE = (
    "新しい版の索引を作っているため、説明文どうしの近さによる関係の候補はまだ出していません。"
    "作り終わると、ここに戻ります。"
)
EDGE_INDEX_MISSING_NOTE = (
    "現在の公開版の索引が無いため、説明文どうしの近さによる関係の候補は出していません。"
    "「ベクトル索引」の［索引を再構築］で作れます。"
)

#: 関係の候補の判断の結果文（IK-0994）。組の名前で言い、件数・近さの数値は言わない。
EDGE_DECIDE_RESULT_TEMPLATES = {
    "accept": "「{left}」と「{right}」を関係として採用しました。次版の下書きへの反映は別の操作です。",
    "dismiss": (
        "「{left}」と「{right}」の関係の候補を見送りました（理由を記録しました）。"
        "［見送り済みも表示］で確かめられます。"
    ),
    "restore": "「{left}」と「{right}」の見送りを取り消し、候補に戻しました。",
}


#: IK-1118（第 25 周 第 10 波）: 採用の結果文は採用した種別の句で言う（依存は向きを落とさない）。
EDGE_ACCEPT_RESULT_RELATION = "{phrase}関係として採用しました。次版の下書きへの反映は別の操作です。"


def edge_decide_result_line(action: str, left: str, right: str, kind: str = "") -> str:
    """関係の候補の判断の結果文（IK-0994）。語彙外の操作・名前なしは空文字。

    採用で種別が分かるときは ``label_vocab.edge_relation_phrase`` の句で言う（IK-1118。
    ``left`` → ``right`` は edge_key の記載順 = カードの向きの文と同じ）。
    """
    action_text = str(action or "").strip()
    template = EDGE_DECIDE_RESULT_TEMPLATES.get(action_text)
    left_text = str(left or "").strip()
    right_text = str(right or "").strip()
    if not template or not left_text or not right_text:
        return ""
    if action_text == "accept" and kind:
        from core.label_vocab import edge_relation_phrase

        phrase = edge_relation_phrase(kind, left_text, right_text)
        if phrase:
            return EDGE_ACCEPT_RESULT_RELATION.format(phrase=phrase)
    return template.format(left=left_text, right=right_text)


def display_datetime(value: Any) -> str:
    """ISO の日時 → 「YYYY-MM-DD HH:MM（UTC）」（読めなければ空文字）。"""
    text = str(value or "").strip()
    if len(text) < 16 or text[4] != "-" or text[10] not in ("T", " "):
        return ""
    return f"{text[:10]} {text[11:16]}（UTC）"


def vector_status_facts(
    *,
    version: str,
    total_nodes: int,
    embedded_nodes: int,
    stale: bool,
    changed_labels: Iterable[str] = (),
    building: bool = False,
) -> list[str]:
    """索引の状態の事実文（用途 → 要否）。

    ``changed_labels`` は材料が変わった項目の名前（IK-0965。再構築と同じ判定）。
    全項目が索引済みでも、これがあれば「作り直す必要はありません」とは言わない。
    """
    facts = [VECTOR_INDEX_PURPOSE]
    changed = [str(label) for label in changed_labels or () if str(label or "").strip()]
    if building and (stale or embedded_nodes < total_nodes):
        # IK-0993: 凍結が裏で始めた構築の途中。手作業を促さない。
        facts.append(VECTOR_INDEX_BUILDING.format(version=version))
        return facts
    if stale:
        facts.append(VECTOR_INDEX_STALE)
    elif embedded_nodes <= 0:
        facts.append(VECTOR_INDEX_NONE.format(version=version))
    elif embedded_nodes < total_nodes:
        facts.append(VECTOR_INDEX_PARTIAL.format(version=version))
        if changed:
            facts.append(
                VECTOR_INDEX_MATERIAL_CHANGED.format(version=version, labels="、".join(changed))
            )
    elif changed:
        facts.append(
            VECTOR_INDEX_MATERIAL_CHANGED.format(version=version, labels="、".join(changed))
        )
    else:
        facts.append(VECTOR_INDEX_CURRENT.format(version=version))
    return facts


def vector_refresh_fact(
    summary: Mapping[str, Any], labels: Mapping[str, str] | None = None
) -> str:
    """再構築の要約 → 一文（完了したときだけ。スキップの理由は呼び出し側の既存表示）。

    ``labels``（node_id → 名前）が渡され、新しく索引した項目が分かれば名前で言う（IK-0965）。
    """
    if str(summary.get("status") or "") != "completed":
        return ""
    embedded = summary.get("embedded")
    if isinstance(embedded, int) and embedded == 0:
        return VECTOR_REFRESH_UNCHANGED
    names = [
        str((labels or {}).get(str(node_id), "") or "").strip()
        for node_id in summary.get("embedded_node_ids") or ()
    ]
    names = [name for name in names if name]
    if names:
        return VECTOR_REFRESH_UPDATED_NAMED.format(labels="、".join(names))
    return VECTOR_REFRESH_UPDATED


__all__ = [
    "ACCEPTED_PENDING_NOTE",
    "INCORPORATED_EDGE_RESULT",
    "INCORPORATED_EDGE_RESULT_RELATION",
    "incorporated_edge_result_line",
    "EDGE_ACCEPT_RESULT_RELATION",
    "EDGE_DISMISS_LOCKED_IN_DRAFT",
    "EDGE_DISMISS_LOCKED_IN_FROZEN",
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
    "ORPHANED_BLOCKS_FREEZE",
    "EDGE_ORPHANED_BLOCKS_FREEZE",
    "orphaned_note",
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
