"""第 23 周（vision 5 機能）の行為の実行と画面の投影。

対象: 帰還の扉・持ち越し（§3.4）/ 撤回・訂正主権（§3.7・§5.2）/ 精読・理解サイクル（§3.1）/
わたしの記録と開示（§5.2・§5.4）/ リリース前の確認と確定文脈（§5.1）。

投影は**画面と同じ欄を同じ形で**出す（IK-0634 / IK-0628 の型: 投影の誤りが偽所見になる）。各関数の
docstring に写した JS の関数名を書く。画面が出していない数・内部 ID は投影でも出さない。
"""
from __future__ import annotations

import re
from typing import Any, Callable, Optional

from uxsim.runner.state import PersonaSession

DISCUSSION_TOPIC_ID = "_discussion"
DISCUSSION_WORDS = ("discussion", "discuss", "議論", "_discussion")

# ---------------------------------------------------------------------------
# 画面の文言の逐語ミラー（JS が正本。変えたら JS を読んで写し直す）
# ---------------------------------------------------------------------------
# app.js renderReturnDoor
RETURN_DOOR_ROWS = (("leave_note", "あなたの書き置き"), ("carryover", "持ち越しの問い"),
                    ("last_tension", "最後に確定した引っかかり"))
RETURN_DOOR_HIDDEN = "（コースの画面の最上部には何も出ていない — 帰還の扉は表示されない）"
# discuss.js leaveNoteSectionHtml / loadTodaysWordsOnce
TODAYS_WORDS_HEAD = "今日のあなたの言葉"
TODAYS_WORDS_NOTE = "あなたの言葉（今日の発話の逐語）です。押すと書き置きに引用されます。"
TODAYS_WORDS_EMPTY = "今日の発話はまだありません。"
TODAYS_WORDS_TRUNCATED = "最近の発話に絞って表示しています。"
# discuss.js cycleLeaveSectionHtml / saveCycleCarryover
LEAVE_HEAD = "次に持ち越すなら、どの問いにしますか？"
LEAVE_REVISIT_CHIP = "あとで戻る"
LEAVE_FREE = "自分の言葉で書く"
LEAVE_SAVED = "次回の開幕で待っています。"
# my-records.js
RECORDS_TITLE = "わたしの記録"
RECORDS_PRIVACY = "この記録はあなたにだけ表示されます。成績評価には使用されません。"
RECORDS_SEAL = "この画面でできるのは、一覧を見ることと持ち出すことです。記録の内容を読めなくする（封印する）操作はありません。"
RECORDS_EMPTY_SYSTEMS_PREFIX = "まだ記録の無い種類: "
RECORDS_EMPTY = "まだ記録がありません。学習の中で問いを残すと、ここに現れます。"
RECORDS_SYSTEM_EMPTY = "この系統の記録はまだありません。"
RECORDS_MAP_EXCLUDED = "地図には出していません"
RECORDS_TRUNCATED = "表示は最新分のみです。持ち出しには記録が新しい順に含まれます（ごく大量の記録がある場合は上限まで）。"
RECORDS_EXPORT_DONE = "ダウンロードを開始しました。"
RECORDS_EXPORT_FAIL = "持ち出しに失敗しました。"
# app.js TRACE_KIND_LABEL / STATUS_META（問いの軌跡）
# 表示名の正本はサーバの t.kind_label（第 23 周 第 3 波）。これは旧サーバ向けの縮退表（app.js と同じ）
TRACE_KIND_LABEL = {"question": "問い", "detour": "寄り道", "misconception": "訂正あり", "raw": "記録", "tension": "違和感"}
TRACE_STATUS_LABEL = {"open": "未解決", "revisited": "再訪推奨", "resolved": "解決済み", "articulated": "言語化済み",
                      "connected": "接続済み", "abstracted": "抽象化済み"}
TRAJECTORY_EMPTY = "まだ記録された問いはありません。学習で生まれた問い・寄り道・誤答がここに溜まります。"
# personal-map(-home).js showTransientNote
MAP_EXCLUDED_NOTE = "地図には反映しません（問いの軌跡には残ります）"
MAP_RESTORED_NOTE = "地図に戻しました"
# app.js deleteMessageFrom
DELETE_CONFIRM = "このメッセージ以降の会話を削除します。よろしいですか？"
DELETE_FAIL = "メッセージの削除に失敗しました。"
# admin-release-review.js
RR_INTRO = "この論文が分野マップ（基準地図）のどこに位置づくかの候補です。1つの論文が複数の領域・複数の観点に置かれることがあります。"
RR_EMPTY = "この論文の分野マップ上の位置づけはまだありません。このまま公開できます。"
RR_NEXT_MEANING = ("「次へ」を押すと、表示されている内容を確認したものとして記録します。"
                   "個別に直したいものは各行の[却下][再検討]で変更できます。")
RR_REOPEN = "確認後も、教材管理の「位置づけ（分野マップ）」から個別に再検討・却下へ戻せます。"
RR_ACCEPT_MATCHED = "表示されていた配置と確認した配置は一致しています"
RR_ACCEPT_MISMATCH = "表示と確認した配置に差がありました。下の「ステップ 2 に戻って確かめる」で最新の配置を表示し直せます"
RR_BACK_TO_LANDSCAPE = "ステップ 2 に戻って確かめる"
RR_EVIDENCE_SUMMARY = "根拠を見る"
RR_EVIDENCE_EMPTY = "この配置には論文からの引用が残っていません。"
# 正本は backend/core/label_vocab.py::LANDSCAPE_STATUS_LABELS（JS の STATUS_FALLBACK_LABELS はその逐語ミラー）
RR_STATUS_FALLBACK = {"inferred": "AI推定（未確認）", "confirmed": "教員確認済み", "rejected": "却下",
                      "review_required": "再検討中", "superseded": "再解析により置換"}
RR_NODE_STATUS = {"migrated": "前の版から対応づけ", "unmapped": "現行版に対応する場所なし"}
RR_ROW_ACTIONS = (("rejected", "却下"), ("review_required", "再検討"))
RR_ROW_FAIL = "この配置の状態を変更できませんでした。"
RR_RECORDED_HEADING = "ステップ 2 で確認として記録したもの"
RR_RECORDED_CONFIRMED = "確認済みにした配置: "
RR_RECORDED_EVIDENCE = "根拠を開いて読んだ配置: "
RR_RECORDED_DECIDED = "却下・再検討にした配置: "
RR_PUBLISH_NOTE = "公開すると受講者が受講できるようになります。公開後も内容の修正はいつでもできます。"
RR_PUBLISHED = "公開しました。"
# admin.js（コース管理の開示範囲・コースビルダー）
CM_VISIBILITY_DONE = "更新しました"
CB_REGISTERED = "登録完了!"
RR_AUTO_OPENED = "コースを登録しました。受講者に何が届くかを確認して公開まで進めます。"
# discuss.js / app.js（理解サイクル・印）
CYCLE_MOTIVE_SAVED = "記録しました。"
CYCLE_REVISIT_SAVED = "記録しました。"
CYCLE_REVISIT_FACTS_HEAD = "前回からの変化"
LEAVE_NOTE_SAVED = "残しました。次にこのコースを開いたときに表示されます。"
CYCLE_SAVE_FAILED = "保存できませんでした。入力はそのまま残しています。"
QUICK_ANCHOR_SAVED = "印を残しました"
# app.js renderAnchorDigestCard / renderTensionDigestCard
ANCHOR_DIGEST_EMPTY = "確認できる問いの候補はまだありません。学習が進むと表示されることがあります。"
TENSION_DIGEST_EMPTY = "引っかかりの候補はまだありません。学習が進むと表示されることがあります。"
RR_NO_PENDING = "（未確認の配置が無いので、「この配置で次へ」は何も記録せずにステップ 3: 公開 へ進んだ）"
# 却下済みの行（画面はボタンを描かない）を指したときの事実文
RR_ROW_REJECTED = "この行は却下済みでボタンがありません。"
RR_ROW_STATUS_WORDS = {"却下": "rejected", "rejected": "rejected", "再検討": "review_required",
                       "review_required": "review_required"}
# 精読モード（API runner では押せない画面だけの切り替え）
PRECISION_UI_ONLY = ("〔精読モードのトグルは「論文と議論」モードの帯にだけある画面だけの切り替え（状態はブラウザに保存）で、"
                     "この試験の進め方では押せない。予想の枠は「予想を立てる問い」で代わりに開く〕")
PRECISION_RUNNER_NOTE = ("unsupported_ui_only: 精読モードのトグル（#cycle-precision-toggle）は議論モードの帯（.discuss-bar）"
                         "にだけあり、API runner では押せない — 予想の枠は learning.chat.cycle で近似した")
UI_ONLY_NOTES = {"learning.cycle.precision_toggle": (PRECISION_UI_ONLY, PRECISION_RUNNER_NOTE)}


def _runner_note(session: PersonaSession, note: str) -> None:
    session.scratch.setdefault("runner_notes", []).append(note)


def ui_only(action_id: str, session: PersonaSession) -> bool:
    """画面だけの切り替え（API の無い行為）を、画面の事実文と runner_note に置き換える。扱ったら True。"""
    pair = UI_ONLY_NOTES.get(action_id)
    if not pair:
        return False
    session.last_status, session.last_body = None, {"_ui_fact": pair[0]}
    _runner_note(session, pair[1])
    return True


# ---------------------------------------------------------------------------
# 選び方の補助
# ---------------------------------------------------------------------------
def _hint_pick(rows: list[dict], hint: Any, label_keys: tuple[str, ...]) -> Optional[dict]:
    """一覧の番号（1 始まり）か、ラベルの一部で 1 行を選ぶ。無ければ None。"""
    h = str(hint or "").strip()
    if not h or not rows:
        return None
    m = re.fullmatch(r"(?:#|No\.?\s*)?(\d+)", h)
    if m:
        i = int(m.group(1))
        return rows[i - 1] if 1 <= i <= len(rows) else None
    key = h.casefold()
    for r in rows:
        if any(key in str(r.get(k) or "").casefold() for k in label_keys):
            return r
    return None


def resolve_chat_topic(session: PersonaSession, topic_arg: Any) -> str:
    t = str(topic_arg or "").strip()
    if t.lower() in DISCUSSION_WORDS:
        return DISCUSSION_TOPIC_ID
    if t:
        return t
    return str(session.scratch.get("last_chat_topic") or session.topic_id or "")


def pick_user_message(history: list[dict], nth_from_last: Any = None, message_id: str = "") -> tuple[int, str]:
    """自分の発言（id あり）を後ろから数えて選ぶ。戻り値 (履歴の位置, message_id)。無ければ (-1, "")。"""
    if message_id:
        idx = next((i for i, m in enumerate(history) if m.get("id") == message_id), -1)
        return idx, message_id if idx >= 0 else ""
    users = [(i, m.get("id")) for i, m in enumerate(history) if m.get("role") == "user" and m.get("id")]
    try:
        n = int(nth_from_last or 1)
    except (TypeError, ValueError):
        n = 1
    if not users or n < 1 or n > len(users):
        return -1, ""
    i, mid = users[-n]
    return i, str(mid)


def _visible_history(history: list[dict], n: int = 4) -> list[str]:
    out = []
    for m in history[-n:]:
        who = "あなた" if m.get("role") == "user" else "AI"
        out.append(f"- {who}: {str(m.get('content') or '')[:120]}")
    return out


# ---------------------------------------------------------------------------
# 学習側の行為
# ---------------------------------------------------------------------------
def delete_message(ctx) -> None:
    """app.js deleteMessageFrom: 確認の後に DELETE、成功したらその発言以降を画面の会話から消す。"""
    s = ctx.session
    tid = resolve_chat_topic(s, ctx.args.get("topic"))
    history = s.history_for(tid)
    idx, mid = pick_user_message(history, ctx.args.get("nth_from_last"), str(ctx.args.get("message_id") or ""))
    if not mid:
        return ctx.missing("message_id")
    status, _ = ctx.main(overrides={"topic_id": tid, "message_id": mid})
    if status == 200:
        del history[idx:]
        s.remember("deleted_messages", mid)
        lines = [f"確認: {DELETE_CONFIRM} → OK", "いま画面に残っている会話:"]
        lines += _visible_history(history) or ["（会話は空になった）"]
        fact = ctx.session.last_body.get("fact") if isinstance(ctx.session.last_body, dict) else None
        if isinstance(fact, str) and fact:
            lines.append(f"〔{fact}〕")  # app.js appendSystemNote（サーバの事実文をそのまま）
        s.last_body = {"_ui_fact": "\n".join(lines)}
    elif status is not None:
        s.last_body = {"_ui_fact": f"確認: {DELETE_CONFIRM} → OK\n（警告）{DELETE_FAIL}"}


def traces_list(ctx) -> None:
    """app.js loadInterestTraces: 表示中トピックを添えて取る。"""
    s = ctx.session
    status, body = ctx.main(params={"topic_id": s.topic_id} if s.topic_id else None)
    if status == 200 and isinstance(body, dict):
        traces = [t for t in body.get("traces") or [] if isinstance(t, dict)]
        s.scratch["trajectory"] = traces
        for t in traces:
            if t.get("map_excluded"):
                s.remember("map_excluded", t.get("id"))


def _map_nodes(session: PersonaSession) -> list[dict]:
    return [n for n in session.scratch.get("pm_nodes") or [] if n.get("node_kind") in ("tension", "question")]


def map_exclude(ctx) -> None:
    """personal-map-home.js requestMapExclude（対象は問い・引っかかりのノードだけ）。"""
    s = ctx.session
    tid = str(ctx.args.get("trace_id") or "").strip()
    if not tid:
        nodes = _map_nodes(s)
        if not nodes:
            # 画面はボタンの前に地図を読み込んでいる。それを写してノードを得る
            _, body = ctx.call("GET", "/api/me/personal-network")
            remember_network_nodes(s, body)
            nodes = _map_nodes(s)
        hint = ctx.args.get("node_hint")
        picked = _hint_pick(nodes, hint, ("label",)) if hint else (nodes[0] if nodes else None)
        if picked is None:
            return ctx.missing("map_node")
        tid = str(picked.get("id") or "")
    status, _ = ctx.main(overrides={"trace_id": tid})
    if status == 200:
        s.remember("map_excluded", tid)
        s.last_body = {"_ui_fact": MAP_EXCLUDED_NOTE}


def map_restore(ctx) -> None:
    """personal-map.js requestMapRestore（問いの軌跡の「地図に戻す」チップ）。"""
    s = ctx.session
    tid = str(ctx.args.get("trace_id") or "").strip() or s.latest("map_excluded")
    if not tid:
        return ctx.missing("map_excluded_trace")
    status, _ = ctx.main(overrides={"trace_id": tid})
    if status == 200:
        items = s.known.get("map_excluded") or []
        if tid in items:
            items.remove(tid)
        s.remember("map_restored", tid)
        s.last_body = {"_ui_fact": MAP_RESTORED_NOTE}


def remember_network_nodes(session: PersonaSession, body: Any) -> None:
    nodes = body.get("nodes") if isinstance(body, dict) else None
    if not isinstance(nodes, list):
        return
    session.scratch["pm_nodes"] = [{"id": str(n.get("id") or ""), "node_kind": str(n.get("node_kind") or ""),
                                    "label": str(n.get("label") or "")} for n in nodes if isinstance(n, dict) and n.get("id")]


def records_export(ctx) -> None:
    """my-records.js doExport: ダウンロードが始まった事実だけを出す（ファイルの中身は人が開いて読む）。"""
    s = ctx.session
    status, body = ctx.main()
    if status == 200 and isinstance(body, dict):
        kinds = sorted({str(r.get("kind") or "") for r in body.get("records") or [] if isinstance(r, dict)} - {""})
        statuses = sorted({str(r.get("status") or "") for r in body.get("records") or [] if isinstance(r, dict)} - {""})
        lines = [RECORDS_EXPORT_DONE, "（手元のファイルを開くと）"]
        if body.get("note"):
            lines.append(f"ファイルの冒頭の注記: {body['note']}")
        lines.append("ファイルに入っている記録の種類: " + ("、".join(kinds) if kinds else "なし"))
        if statuses:
            lines.append("記録の状態の値: " + "、".join(statuses))
        if body.get("truncated"):
            lines.append("ファイルの注記: 上限まで（truncated）")
        s.last_body = {"_ui_fact": "\n".join(lines)}
    elif status is not None:
        s.last_body = {"_ui_fact": RECORDS_EXPORT_FAIL}


def disclosure_view(ctx) -> None:
    kind = str(ctx.args.get("data_kind") or "learning_chat")
    ctx.main(overrides={"data_kind": kind})


def landing_candidates(ctx) -> None:
    s = ctx.session
    status, body = ctx.main()
    if status == 200 and isinstance(body, dict):
        s.scratch["landing_candidates"] = [c for c in body.get("candidates") or [] if isinstance(c, dict)]


def return_door(ctx) -> None:
    s = ctx.session
    status, body = ctx.main()
    if status == 200 and isinstance(body, dict) and isinstance(body.get("carryover"), dict):
        s.remember("carryover_traces", body["carryover"].get("trace_id"))


# ---------------------------------------------------------------------------
# 教員側の行為
# ---------------------------------------------------------------------------
def courses_list(ctx) -> None:
    s = ctx.session
    status, body = ctx.main()
    if status != 200 or not isinstance(body, list):
        return
    rows = [c for c in body if isinstance(c, dict) and c.get("id")]
    s.scratch["admin_courses"] = rows
    owned = [c for c in rows if c.get("role") == "owner"]
    for c in reversed(owned):  # 一覧は更新の新しい順。最新を最後に積む
        s.remember("owned_courses", c["id"])
    pick = None
    if str(ctx.args.get("prefer") or "") == "unpublished":
        pick = next((c for c in owned if not c.get("is_published")), None)
    pick = pick or (owned[0] if owned else None)
    if pick:
        s.course_id = str(pick["id"])
        _runner_note(s, f"runner_default: 「確認して公開」の対象を自分のコース「{pick.get('title', '')}」にした")


def _placements(landscape: Any) -> list[dict]:
    out: list[dict] = []
    docs = landscape.get("documents") if isinstance(landscape, dict) else None
    for doc in docs if isinstance(docs, list) else []:
        for p in (doc.get("placements") if isinstance(doc, dict) else None) or []:
            if isinstance(p, dict):
                out.append(p)
    return out


def remember_landscape(session: PersonaSession, body: Any) -> None:
    if isinstance(body, dict) and isinstance(body.get("documents"), list):
        session.scratch["release_landscape"] = body
        for p in _placements(body):
            session.remember("placements", p.get("id"))


def row_status(ctx) -> None:
    """admin-release-review.js updateRowStatus: PATCH → 一覧を読み直す（却下済みの行にはボタンが無い）。"""
    s = ctx.session
    raw = str(ctx.args.get("status") or "").strip().strip("[]［］「」")
    status_value = RR_ROW_STATUS_WORDS.get(raw, raw)
    # 番号は画面（投影 project_release_step2）と同じく却下済みの行も残した並びで数える（第 23 周 te-03:
    # 却下済みを除いて数え直したため、却下の後に番号がずれて別の行へ PATCH した）
    rows = _placements(s.scratch.get("release_landscape"))
    hint = ctx.args.get("placement_hint")
    if hint:
        picked = _hint_pick(rows, hint, ("node_label", "domain_name", "perspective_label"))
    else:
        picked = next((p for p in rows if p.get("status") != "rejected"), None)
    if picked is None:
        return ctx.missing("placement_row")
    if picked.get("status") == "rejected":
        # 画面は却下済みの行にボタンを出さない。記録（trace）には precondition を残し、頭脳へは画面の事実文を渡す
        # （第 23 周: 識別子 placement_row_rejected がそのまま頭脳への文に出ていた）
        ctx.missing("placement_row_rejected")
        s.last_body = {"_precondition": "placement_row_rejected", "_ui_fact": RR_ROW_REJECTED}
        return
    status, body = ctx.main(json={"status": status_value}, overrides={"placement_id": str(picked.get("id") or "")})
    if status == 200:
        s.scratch.setdefault("rows_decided", [])
        if str(picked.get("id")) not in s.scratch["rows_decided"]:
            s.scratch["rows_decided"].append(str(picked.get("id")))
        st2, reloaded = ctx.call("GET", "/api/admin/landscape/courses/{course_id}/placements")
        if st2 == 200:
            remember_landscape(s, reloaded)
            s.last_status, s.last_body = 200, reloaded
    elif status is not None:
        detail = body.get("detail") if isinstance(body, dict) else ""
        s.last_body = {"_ui_fact": f"（警告）{detail or RR_ROW_FAIL}"}


def read_evidence(ctx) -> None:
    """``details.release-review-evidence`` を開く（HTTP なし）。開いた行は「次へ」の来歴に載る（是正 F7）。"""
    s = ctx.session
    rows = _placements(s.scratch.get("release_landscape"))
    if not rows:
        return ctx.missing("release_landscape")
    hint = str(ctx.args.get("placement_hint") or "").strip()
    chosen = rows if hint.lower() in ("all", "全部", "すべて") else (
        [p] if (p := _hint_pick(rows, hint, ("node_label", "domain_name"))) is not None else ([] if hint else rows[:1]))
    if not chosen:
        return ctx.missing("placement_row")
    opened = s.scratch.setdefault("evidence_expanded", [])
    lines = []
    for p in chosen:
        pid = str(p.get("id") or "")
        if pid and pid not in opened:
            opened.append(pid)
        lines.append(f"▼ {RR_EVIDENCE_SUMMARY}（{p.get('node_label') or ''}）")
        quotes = [str((e or {}).get("quote") or "") for e in p.get("evidence") or [] if isinstance(e, dict)]
        quotes = [q for q in quotes if q]
        lines += [f'  "{q}"' for q in quotes] or [f"  {RR_EVIDENCE_EMPTY}"]
    s.last_status, s.last_body = 200, {"_ui_fact": "\n".join(lines)}
    _runner_note(s, "runner_ui_only: 根拠の折りたたみを開いた（読み込み済みの配置を表示しただけで HTTP は出していない）")


def release_step3_text(session: PersonaSession, *, published: bool) -> str:
    """admin-release-review.js renderPublishStep（recordedSummaryHtml + 箇条書き + 公開しました。）。"""
    lines: list[str] = ["リリース前の確認 — ステップ 3: 公開"]
    rec = session.scratch.get("accept_record")
    decided = len(session.scratch.get("rows_decided") or [])
    if rec or decided:
        lines.append(RR_RECORDED_HEADING)
        if rec:
            lines.append(f"- {RR_RECORDED_CONFIRMED}{rec.get('confirmed', 0)}件")
            lines.append(f"- {RR_RECORDED_EVIDENCE}{rec.get('evidence_opened', 0)}件")
        lines.append(f"- {RR_RECORDED_DECIDED}{decided}件")
    lines.append("- 学習マップの割り当ては変更していません。")
    land = session.scratch.get("release_landscape")
    if isinstance(land, dict):
        count = int(land.get("placement_count") or 0) if str(land.get("placement_count") or "0").isdigit() else 0
        pending = int(land.get("pending_count") or 0) if str(land.get("pending_count") or "0").isdigit() else 0
        if count:
            lines.append(f"- 分野マップ上の位置づけ {count}件を受講者に表示します"
                         + (f"（うち未確認 {pending}件は「AIによる推定（未確認）」と表示されます）" if pending else "") + "。")
        else:
            lines.append("- 分野マップ上の位置づけはまだありません。")
    lines.append(f"- {RR_PUBLISH_NOTE}")
    if published:
        lines += [RR_PUBLISHED, "ボタン: 閉じる"]
    else:
        lines.append("ボタン: 公開する / 公開しないで閉じる")
    return "\n".join(lines)


def course_visibility_view(session: PersonaSession, visibility: str) -> str:
    """公開の結果の画面。リリース前の確認の中ならステップ 3、そうでなければコース管理の「更新しました」。"""
    if isinstance(session.scratch.get("release_landscape"), dict) and visibility == "public":
        return release_step3_text(session, published=True)
    return f"（コース管理の開示範囲）{CM_VISIBILITY_DONE}"


def accept_body(session: PersonaSession) -> Optional[dict]:
    """admin-release-review.js acceptPlacements の body。未確認が無ければ None（POST しない）。"""
    landscape = session.scratch.get("release_landscape")
    if isinstance(landscape, dict):
        try:
            pending = int(landscape.get("pending_count") or 0)
        except (TypeError, ValueError):
            pending = 0
        if not pending:
            return None
        presented = [str(p.get("id")) for p in _placements(landscape) if p.get("status") == "inferred" and p.get("id")]
    else:
        presented = session.all("placements")
    return {"presented_placement_ids": presented,
            "evidence_expanded_placement_ids": list(session.scratch.get("evidence_expanded") or [])}


# ---------------------------------------------------------------------------
# 投影
# ---------------------------------------------------------------------------
def _project_return_door(body: dict) -> str:
    if body.get("empty"):
        return RETURN_DOOR_HIDDEN
    lines = []
    for key, label in RETURN_DOOR_ROWS:
        item = body.get(key)
        text = str(item.get("text") or "").strip() if isinstance(item, dict) else ""
        if text:
            lines.append(f"{label}: {text}")
    if not lines:
        return RETURN_DOOR_HIDDEN
    return "\n".join(["帰還の扉（コースの画面の最上部）:"] + lines + ["ボタン: ×（閉じる）"])


def _project_todays_words(body: dict) -> str:
    words = [str(w.get("text") or "") for w in body.get("words") or [] if isinstance(w, dict) and w.get("text")]
    lines = [f"▼ {TODAYS_WORDS_HEAD}（折りたたみを開いた）", TODAYS_WORDS_NOTE]
    lines += [f"- {w}" for w in words] or [TODAYS_WORDS_EMPTY]
    if words and body.get("truncated"):
        lines.append(TODAYS_WORDS_TRUNCATED)
    return "\n".join(lines)


def _project_landing(body: dict) -> str:
    lines = [LEAVE_HEAD]
    for i, c in enumerate([c for c in body.get("candidates") or [] if isinstance(c, dict)], 1):
        chip = f"〔{LEAVE_REVISIT_CHIP}〕" if c.get("revisit") else ""
        lines.append(f"- 選択肢{i}: {c.get('label', '')}{chip}")
    lines.append(f"ボタン: {LEAVE_FREE}")
    return "\n".join(lines)


def project_records(body: dict) -> str:
    """my-records.js recordsHtml / itemHtml（件数は描かない）。"""
    lines = [RECORDS_TITLE, RECORDS_PRIVACY, "ボタン: 持ち出す", RECORDS_SEAL]
    systems = [x for x in body.get("systems") or [] if isinstance(x, dict)]
    if not systems:
        lines.append(RECORDS_EMPTY)
    empty_labels = []
    for sys_ in systems:
        items = [x for x in sys_.get("items") or [] if isinstance(x, dict)]
        if not items:
            # my-records.js recordsHtml: 中身の無い系統は見出しを出さず末尾の 1 行に畳む（P-0008）
            if sys_.get("label"):
                empty_labels.append(str(sys_["label"]))
            continue
        lines.append(f"■ {sys_.get('label', '')}")
        if sys_.get("publicity_note"):
            lines.append(f"  {sys_['publicity_note']}")
        for it in items[:12]:
            chip = f"［{it.get('status_label')}］" if it.get("status_label") else ""
            lines.append(f"  - {chip}{it.get('text', '')}")
            meta = [str(x) for x in (it.get("created_at", "")[:10], it.get("course_label"), it.get("context_label")) if x]
            if meta:
                lines.append("    " + " · ".join(meta))
            if (it.get("flags") or {}).get("map_excluded"):
                lines.append(f"    {RECORDS_MAP_EXCLUDED}")
            if it.get("publicity"):
                lines.append(f"    {it['publicity']}")
        if len(items) > 12:
            lines.append("  - …（さらに続く）")
    if empty_labels and len(empty_labels) < len(systems):
        lines.append(RECORDS_EMPTY_SYSTEMS_PREFIX + "・".join(empty_labels))
    elif systems and len(empty_labels) == len(systems):
        lines.append(RECORDS_EMPTY)
    if body.get("truncated"):
        lines.append(RECORDS_TRUNCATED)
    if body.get("provenance_note"):
        lines += ["来歴", str(body["provenance_note"])]
    return "\n".join(lines)


def _project_trajectory(body: dict) -> str:
    """app.js renderProgressTab の「問いの軌跡」（+ personal-map.js の「地図に戻す」チップ）。"""
    traces = [t for t in body.get("traces") or [] if isinstance(t, dict)]
    lines = ["問いの軌跡:"]
    if not traces:
        return "\n".join(lines + [TRAJECTORY_EMPTY])
    for t in traces[:20]:
        kind = str(t.get("kind_label") or "") or TRACE_KIND_LABEL.get(str(t.get("kind") or ""), "記録")
        status = TRACE_STATUS_LABEL.get(str(t.get("status") or ""), str(t.get("status") or ""))
        lines.append(f"- 〔{kind}〕〔{status}〕{t.get('text', '')}")
        if t.get("context_label"):
            lines.append(f"  {t['context_label']}")
        sa = t.get("structure_anchor") if isinstance(t.get("structure_anchor"), dict) else None
        if sa and (sa.get("anchor_label") or sa.get("doubt_type_label")):
            extra = f" · {sa.get('doubt_type_label')}" if sa.get("doubt_type") != "unclassified" and sa.get("doubt_type_label") else ""
            lines.append(f"  📍 {sa.get('anchor_label') or sa.get('anchor_type_label') or ''}{extra}")
        if t.get("map_excluded"):
            lines.append("  ボタン: 地図に戻す")
        if t.get("status") != "resolved":
            lines.append("  ボタン: この問いに戻る / 解決済みにする / なぜ気になった？")
    return "\n".join(lines)


def _project_disclosure(body: dict) -> str:
    dk = body.get("data_kind") if isinstance(body.get("data_kind"), dict) else {}
    note = str(dk.get("note") or "").strip()
    if not note:
        return "（入力欄の下に記録の扱いの一行は出ていない）"
    return f"入力欄の下の一行: {note}"


def _rr_status_label(p: dict) -> str:
    return str(p.get("status_label") or RR_STATUS_FALLBACK.get(str(p.get("status") or ""), p.get("status") or ""))


def project_release_step2(body: dict) -> str:
    """admin-release-review.js renderLandscapeBody（ステップ 2「論文の位置づけ」）。根拠は既定で閉じている。"""
    lines = ["リリース前の確認 — ステップ 2: 論文の位置づけ", RR_INTRO]
    n = 0
    any_row = False
    for doc in [d for d in body.get("documents") or [] if isinstance(d, dict)]:
        pls = [p for p in doc.get("placements") or [] if isinstance(p, dict)]
        unplaced = [u for u in doc.get("unplaced_domains") or [] if isinstance(u, dict)]
        if not pls and not unplaced:
            continue
        lines.append(f"論文: {doc.get('title', '')}")
        for p in pls:
            n += 1
            any_row = True
            meta = (p.get("domain_name") or p.get("domain_key") or "") + (
                f" ・ {p['perspective_label']}" if p.get("perspective_label") else "")
            chip = f" 〔{RR_NODE_STATUS[p['node_status']]}〕" if p.get("node_status") in RR_NODE_STATUS else ""
            lines.append(f"  {n}. {p.get('node_label') or ''}（{meta}）［{_rr_status_label(p)}］{chip}")
            if p.get("reason"):
                lines.append(f"     {str(p['reason'])[:200]}")
            lines.append(f"     ▶ {RR_EVIDENCE_SUMMARY}（折りたたみ・閉じている）")
            if p.get("status") != "rejected":
                lines.append("     ボタン: " + " / ".join(f"[{lb}]" for _, lb in RR_ROW_ACTIONS))
        for u in unplaced:
            lines.append(f"  この論文は「{u.get('domain_name') or u.get('domain_key') or ''}」の地図に配置できませんでした: "
                         f"{u.get('reason') or ''}")
    if not any_row and len(lines) == 2:
        lines.append(RR_EMPTY)
    parts = [RR_REOPEN]
    pending = int(body.get("pending_count") or 0) if str(body.get("pending_count") or "0").isdigit() else 0
    if pending:
        parts.append(f"未確認 {pending}件")
    hidden = int(body.get("hidden_document_count") or 0) if str(body.get("hidden_document_count") or "0").isdigit() else 0
    if hidden:
        parts.append(f"権限のない論文 {hidden}件は対象外")
    versions = [f"{d.get('domain_name') or d.get('domain_key')} 骨格版 {d.get('frozen_version')}"
                for d in body.get("domains") or [] if isinstance(d, dict) and d.get("frozen_version")]
    if versions:
        parts.append("使用した骨格: " + " ・ ".join(versions))
    lines.append(RR_NEXT_MEANING)  # admin-release-review.js setNextMeaning: 主ボタンの直上に 1 行
    lines.append("ボタン: この配置で次へ / あとで")
    lines.append("脚注: " + " ・ ".join(parts))
    return "\n".join(lines)


def _project_accept(body: dict) -> str:
    lines = ["（「次へ」を記録して、ステップ 3: 公開 へ進んだ）"]
    ctx = body.get("decision_context") if isinstance(body.get("decision_context"), dict) else None
    if ctx is not None:
        if ctx.get("presented_matches_applied"):
            lines.append(RR_ACCEPT_MATCHED)
        else:
            note = body.get("decision_note")
            lines.append(note if isinstance(note, str) and note else RR_ACCEPT_MISMATCH)
            lines.append(f"ボタン: {RR_BACK_TO_LANDSCAPE}")
    if isinstance(body.get("skipped_note"), str) and body["skipped_note"]:
        lines.append(body["skipped_note"])
    return "\n".join(lines)


def _project_admin_courses(body: list) -> str:
    role_badge = {"owner": "所有", "editor": "editor", "viewer": "viewer"}
    lines = ["コース管理:"]
    for c in body[:20]:
        if not isinstance(c, dict):
            continue
        lines.append(f"- {c.get('title', '')} 〔{role_badge.get(str(c.get('role') or ''), 'editor')}〕 開示範囲: "
                     f"{c.get('visibility', '')}")
        if c.get("role") == "owner":
            lines.append("  ボタン: 確認して公開 / 共有設定 / 議論テーマ / AIモデル / 共有ダッシュボード")
    return "\n".join(lines) if len(lines) > 1 else "コースが 1 つも表示されていない。"


PROJECTORS: dict[str, Callable[[Any], str]] = {
    "learning.cycle.return_door": lambda b: _project_return_door(b) if isinstance(b, dict) else "",
    "learning.cycle.todays_words": lambda b: _project_todays_words(b) if isinstance(b, dict) else "",
    "learning.cycle.landing_candidates": lambda b: _project_landing(b) if isinstance(b, dict) else "",
    "learning.records.mine": lambda b: project_records(b) if isinstance(b, dict) and "systems" in b else "",
    "learning.traces.list": lambda b: _project_trajectory(b) if isinstance(b, dict) else "",
    "learning.disclosure.view": lambda b: _project_disclosure(b) if isinstance(b, dict) else "",
    "admin.release_review.placements": lambda b: project_release_step2(b) if isinstance(b, dict) and "documents" in b else "",
    "admin.release_review.row_status": lambda b: project_release_step2(b) if isinstance(b, dict) and "documents" in b else "",
    "admin.release_review.accept": lambda b: _project_accept(b) if isinstance(b, dict) else "",
    "admin.courses.list": lambda b: _project_admin_courses(b) if isinstance(b, list) else "",
}


def project(action_id: str, status: Optional[int], body: Any) -> str:
    """この周の行為の画面の投影（200 のときだけ。空文字なら state.py の既定の投影に任せる）。"""
    fn = PROJECTORS.get(action_id)
    if fn is None or status is None or status >= 400:
        return ""
    try:
        return fn(body)
    except Exception:  # noqa: BLE001 — 投影の失敗で記録を落とさない（既定の投影に戻る）
        return ""


# ---------------------------------------------------------------------------
# 第 23 周の偽所見の是正（uxsim/runs/round23/harness_fixes.md）— raw JSON を出していた投影を画面の文に
# ---------------------------------------------------------------------------
def intention_view(role: str, status: Optional[int], body: Any) -> str:
    """discuss.js の保存後の文（役割ごと）。失敗は SAVE_FAILED_FACT。"""
    if status is None or status >= 400 or not isinstance(body, dict):
        return CYCLE_SAVE_FAILED
    if role == "carryover_question":
        return f"{LEAVE_HEAD}\n{LEAVE_SAVED}"
    if role == "leave_note":
        return LEAVE_NOTE_SAVED
    if role == "revisit_answer":
        facts = [str(f) for f in body.get("facts") or [] if str(f or "").strip()]
        lines = [CYCLE_REVISIT_SAVED]
        if facts:
            lines.append(CYCLE_REVISIT_FACTS_HEAD)
            lines += [f"- {f}" for f in facts]
        return "\n".join(lines)
    return CYCLE_MOTIVE_SAVED


def anchor_card(body: Any, deferred: int = 0) -> str:
    """app.js renderAnchorDigestCard: 1 枚だけ描く（「あとで」で次の候補）。番号は「あとで」を押した回数 + 1。"""
    items = [x for x in (body.get("items") if isinstance(body, dict) else None) or [] if isinstance(x, dict)]
    if not items:
        return ANCHOR_DIGEST_EMPTY
    lines = []
    for i, it in enumerate(items[deferred:], deferred + 1):
        head = "いま表示されている候補" if i == deferred + 1 else f"「あとで」を {i - deferred - 1} 回押すと出る候補"
        ctxl = f" · {it['context_label']}" if it.get("context_label") else ""
        lines.append(f"候補{i}（{head}）: 疑問の在り処{ctxl}")
        lines.append(f"  この疑問は「{it.get('anchor_label') or it.get('anchor_type_label') or ''}」の"
                     f"{it.get('doubt_type_label') or ''}についてでしたか？")
        lines.append(f"  『{it.get('question_text') or ''}』")
        lines.append("  ボタン: そう、これ / 違う / あとで")
    lines.append("（pick に候補の番号を渡すと、その候補に「そう、これ」「違う」を押す — 前の候補は「あとで」にする）")
    return "\n".join(lines)


def tension_card(body: Any, deferred: int = 0) -> str:
    """app.js renderTensionDigestCard（同じく 1 枚ずつ）。"""
    items = [x for x in (body.get("items") if isinstance(body, dict) else None) or [] if isinstance(x, dict)]
    if not items:
        return TENSION_DIGEST_EMPTY
    lines = []
    for i, it in enumerate(items[deferred:], deferred + 1):
        head = "いま表示されている候補" if i == deferred + 1 else f"「あとで」を {i - deferred - 1} 回押すと出る候補"
        ctxl = f" · {it['context_label']}" if it.get("context_label") else ""
        lines.append(f"候補{i}（{head}）: 引っかかりの気配{ctxl}")
        lines.append("  今日の会話で、こんな引っかかりがあったかもしれません")
        para = f"——{it['paraphrase']}" if it.get("paraphrase") else ""
        lines.append(f"  『{it.get('evidence_quote') or ''}』{para}")
        lines.append("  ボタン: そう、これ / 違う / あとで")
    lines.append("（pick に候補の番号を渡すと、その候補に「そう、これ」「違う」を押す — 前の候補は「あとで」にする）")
    return "\n".join(lines)


def digest_pick(session: PersonaSession, scratch_key: str, pick: Any) -> str:
    """digest の候補（画面は先頭から 1 枚ずつ）を番号で選ぶ。無ければ先頭（いま表示されている 1 枚）。"""
    items = [x for x in session.scratch.get(scratch_key) or [] if isinstance(x, dict)]
    if not items:
        return ""
    try:
        i = int(str(pick).strip().lstrip("候補")) if pick not in (None, "") else 1
    except ValueError:
        i = 1
    i = i if 1 <= i <= len(items) else 1
    return str(items[i - 1].get("trace_id") or "")


def course_tree(body: Any) -> str:
    """app.js のサイドバー（章 → トピック。状態は status_label を title に出す）と概念マップ。"""
    master = body.get("master_course") if isinstance(body.get("master_course"), dict) else body
    topics = [t for t in (master.get("topics") or []) if isinstance(t, dict)]
    chapters = [c.get("title", "") if isinstance(c, dict) else str(c) for c in (master.get("chapters") or [])]
    lines: list[str] = []
    door = body.get("_return_door")
    if isinstance(door, dict):
        # app.js renderReturnDoor: コースの画面の最上部（非空のときだけ。空は何も出さない = RD3）
        shown = _project_return_door(door)
        if shown != RETURN_DOOR_HIDDEN:
            lines.append(shown)
    lines.append(f"コース: {master.get('title', '')}")
    current = None
    for t in topics:
        ci = t.get("chapter_index")
        if ci != current:
            current = ci
            if isinstance(ci, int) and 0 <= ci < len(chapters):
                lines.append(f"{chapters[ci]}:")
        label = f"（{t['status_label']}）" if t.get("status_label") else ""
        mark = "（確認問題あり）" if t.get("check_questions") else ""
        lines.append(f"- {t.get('id', '')} {t.get('title', '')}{label}{mark}")
    concepts = [c.get("name") if isinstance(c, dict) else str(c) for c in master.get("concepts") or []]
    concepts = [c for c in concepts if c]
    if concepts:
        lines.append("概念マップ: " + "、".join(concepts[:20]) + ("、…" if len(concepts) > 20 else ""))
    return "\n".join(lines)


def chat_history_view(body: Any) -> str:
    msgs = [m for m in (body.get("history") if isinstance(body, dict) else None) or [] if isinstance(m, dict)]
    if not msgs:
        return "（これまでの会話はまだない）"
    lines = ["これまでの会話:"]
    for m in msgs[-12:]:
        who = "あなた" if m.get("role") == "user" else "AI"
        lines.append(f"- {who}: {str(m.get('content') or '')[:400]}")
    return "\n".join(lines)


def _fragile_lines(points: list[dict]) -> list[str]:
    out = []
    for f in points:
        if f.get("label"):
            out.append(f"  - {f['label']}")
        if f.get("fact_line"):
            out.append(f"    {f['fact_line']}")
    return out


def discuss_opening_view(body: Any) -> str:
    """discuss.js buildOpeningHtml（畳まれた区画は「▶（畳まれている）」で示し、中身は開いたときに見える文として添える）。"""
    if not isinstance(body, dict):
        return ""
    docs = [d for d in body.get("documents") or [] if isinstance(d, dict)]
    focus = str(body.get("course_focus") or "")
    if not docs and not focus:
        return "（「論文と議論する」の開幕画面には何も出ていない）"
    fragile = [f for f in body.get("fragile_points") or [] if isinstance(f, dict)]
    lines: list[str] = ["「論文と議論する」の開幕画面:"]
    intention = body.get("intention") if isinstance(body.get("intention"), dict) else None
    if intention:
        carry = intention.get("carryover") if isinstance(intention.get("carryover"), dict) else None
        if carry:
            lines += ["前回、この問いを残しました", f"  {carry.get('text') or ''}", "いまならどう考えますか？（任意）",
                      "ボタン: 書いて進む / そのまま進む"]
        elif intention.get("has_motive") is False:
            lines += ["この論文を、なぜ今開きましたか？（任意・書かなくても進めます）", "ボタン: 記録する / 予想してから開く"]
    if focus:
        lines += ["このコースで議論したいこと（担当教員が書いたものです）", f"  {focus}"]
    lines.append("トピック順に縛られず、論文全体について話せます。回答の根拠（教材由来か、AIの一般知識か）は各回答に表示されます。"
                 "まず互いの読みを突き合わせてから、論文の主張を一緒に検討します。")
    lines.append("最初の一手: ボタン なぜこの設計? / 前提は何? / 他と矛盾しない?（自由に入力してもかまいません）")
    for doc in docs:
        if len(docs) > 1:
            lines.append(f"■ {doc.get('title', '')}")
        thesis = doc.get("thesis") if isinstance(doc.get("thesis"), dict) else {}
        if thesis.get("central_question") or thesis.get("paper_goal"):
            lines.append("この論文が答えようとした問い")
            if thesis.get("central_question"):
                lines += [f"  {thesis['central_question']}", "  ボタン: この問いから聞く"]
            if thesis.get("paper_goal"):
                lines.append(f"  この論文が目指したこと: {thesis['paper_goal']}")
        chapters = [b for b in doc.get("chapter_skeleton") or [] if isinstance(b, dict)
                    and (str(b.get("label") or "").strip() or str(b.get("summary") or "").strip())]
        if chapters:
            lines.append("▶ 論文の骨格（章の流れ）（畳まれている・開くと）:")
            for i, b in enumerate(chapters[:12], 1):
                lines.append(f"  {i}. {b.get('label') or ''} {str(b.get('summary') or '')[:120]}".rstrip())
        lines.append("この論文の主張")
        if thesis:
            if thesis.get("central_thesis_text"):
                lines.append(f"  {thesis['central_thesis_text']}")
            for c in thesis.get("central_claims") or []:
                if isinstance(c, dict) and c.get("label"):
                    lines.append(f"  {c['label']}")
            eqs = [e.get("label") for e in thesis.get("central_equations") or [] if isinstance(e, dict) and e.get("label")]
            if eqs:
                lines.append("  中心となる式: " + " / ".join(eqs))
        else:
            lines.append("  この論文の中心命題はまだ整理されていません。")
        seeds = [x for x in doc.get("discussion_seeds") or [] if isinstance(x, dict) and x.get("body")]
        if seeds:
            lines.append("議論のきっかけ（立場を決めて答えると、そこから議論が進みます）")
            for x in seeds:
                lines.append(f"  - {x['body']}")
            sign = next((x.get("authored_by_label") for x in seeds if x.get("authored_by_label")), "")
            if sign:
                lines.append(f"  {sign}")
        lines.append("▶ この論文の組み立てをくわしく見る（畳まれている・開くと）:")
        nodes = [n for n in doc.get("backbone") or [] if isinstance(n, dict)]
        if nodes:
            flow = " › ".join(f"{i}. {n.get('stage_label') or n.get('label') or ''}"
                              + ("" if n.get("backed") is True else "（点線）") for i, n in enumerate(nodes, 1))
            lines.append(f"  理論のバックボーン: {flow}")
            if any(n.get("backed") is not True for n in nodes):
                lines.append("  点線は、根拠がまだ確認されていない段階です。")
        else:
            lines.append("  理論のバックボーン: この論文のバックボーンはまだ整理されていません。")
        alts = [a for a in thesis.get("alternatives") or [] if isinstance(a, dict) and a.get("text")] if thesis else []
        if alts:
            lines.append("  別の見方（AI の提示）— 論文の主張そのものではありません。")
            lines += [f"    - {a['text']}" + (f"（{a['attribution_label']}）" if a.get("attribution_label") else "")
                      for a in alts]
        unconfirmed = [f for f in fragile if f.get("kind") == "backbone_node"
                       and (not doc.get("document_id") or f.get("document_id") == doc.get("document_id"))]
        if unconfirmed:
            lines.append("  まだ確認できていないところ（論文の弱点ではありません。この教材を解析したときに、裏付けを取れなかった箇所です。）")
            lines += _fragile_lines(unconfirmed)
    paper = [f for f in fragile if f.get("kind") == "assumption"]
    if paper:
        lines.append("▶ この論文が確かめていないこと（畳まれている・開くと）:")
        lines += _fragile_lines(paper)
    if body.get("truncated"):
        lines.append("確かめていないこと・確認できていないところは、主要なものに絞って表示しています。")
    return "\n".join(lines)


PROJECTORS.update({
    "learning.course.open": lambda b: course_tree(b) if isinstance(b, dict) else "",
    "learning.discuss.opening": discuss_opening_view,
    "learning.chat.history": chat_history_view,
    "learning.anchors.digest": lambda b: anchor_card(b),
    "learning.tension.digest": lambda b: tension_card(b),
    "learning.cycle.anchor": lambda b: QUICK_ANCHOR_SAVED if isinstance(b, dict) else "",
    "admin.course_builder.session_create": lambda b: (
        f"コースビルダー: 新しいセッション「{b.get('title') or b.get('display_name') or ''}」を作った（セッションの一覧で選ばれている）"
        if isinstance(b, dict) else ""),
    "admin.course_builder.register": lambda b: (
        f"ボタン表示: {CB_REGISTERED}\nリリース前の確認が開いた: {RR_AUTO_OPENED}\n"
        "（ステップ 1: 学習マップの割り当て → 2: 論文の位置づけ → 3: 公開）" if isinstance(b, dict) else ""),
})
