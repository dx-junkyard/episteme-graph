"""D層・SL層（前提の地図・台帳・疑義・検証提案・反実仮想）と学生の検証状態の一行の実行と投影（第 25 周・面 B）。

行為 id と引数は ``uxsim/actions/doubt_actions.py``。契約の正本は ``backend/api/routes/doubt.py``。

- 投影は**画面と同じ欄を同じ形で**出す（IK-0634 / IK-0628 の型）。教員側の正本は
  ``frontend/public/js/doubt-atlas.js``（``renderAtlasSvg`` / ``loadAssumptions`` / ``renderOpenAssumptionsList`` /
  ``loadCfSessions`` / ``renderLedgerInto`` / ``falsificationSectionHtml`` / ``supportLinesFactHtml`` /
  ``evidenceLinesSectionHtml`` / ``renderProposalStatusCard`` / ``updateCfStatus``）、学習側は ``app.js``
  （``fetchLearnerLedgerFactLine`` / ``annotateOneSourceCardLedger`` / ``appendLearnerLedgerLine`` /
  ``loadLearnerOpenAssumptions``）。**画面が出しているものは隠さない**（記帳者の欄の値・出典ドキュメントの id・
  origin / status の生の語彙 — 隠すと審判から欠陥が見えなくなる）。画面が出していないもの（散布図の点の
  target_id・負荷の生値）は出さない。
- 画面がクライアント側で止める入力（確定の理由が空・理由が空の検証状態・スコープ 0 件の直接検証・
  提案の文やコーパス外の確認が空）は HTTP を出さずに画面の文言を返し、runner_note に残す（画面と同じ止め方）。
- 応答の投影は ``session.last_body = {"_ui_fact": …}`` に置く（``state.project_observation`` が最初に読む）。
  審判は ``HttpTrace.response_excerpt`` を読むので、生の応答はそちらに残る。
- 画面が覚えるもの（``PersonaSession.scratch``）: ``doubt_courses`` / ``doubt_course_id`` / ``doubt_atlas`` /
  ``doubt_assumptions`` / ``doubt_assumption_filter`` / ``doubt_open`` / ``doubt_open_reachable`` /
  ``doubt_cf_sessions`` / ``doubt_target``（開いている台帳の対象）/ ``doubt_ledger``（その台帳。未記帳は None）/
  ``doubt_proposal``（登録直後の検証提案）/ ``doubt_graph``（原稿スタジオのグラフ）/ ``doubt_cf``（反実仮想の状態）/
  ``doubt_obs_targets``（観測系の主張）。
- このモジュールは ``actions_exec`` を import しない（``actions_exec`` が ``HANDLERS`` を取り込む）。
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Callable, Optional

from uxsim.runner.state import PersonaSession, _clip

ADMIN_PREFIX = "admin.doubt."
LEARNER_PREFIX = "learning.ledger."
DOUBT = "/api/admin/doubt"

# ---------------------------------------------------------------------------
# 画面の文言の逐語ミラー（doubt-atlas.js が正本。変えたら JS を読んで写し直す）
# ---------------------------------------------------------------------------
STATUS_LABELS = {"directly_verified": "直接検証の記帳あり", "indirectly_supported": "間接的な支持あり",
                 "untested": "未検証", "refuted": "反証の記帳あり", "unknown": "検証情報なし"}
LOAD_LABELS = {"low": "低", "medium": "中", "high": "高", "highest": "最高位"}
COVERAGE_LABELS = {"none": "記帳なし", "single": "1件", "several": "少数", "broad": "広い"}
CHALLENGE_TYPE_LABELS = {"scope_extrapolation": "検証スコープ外への外挿という疑義",
                         "untested_in_domain": "この領域では未検証という疑義",
                         "definitional": "定義の曖昧さという疑義",
                         "hidden_lemma": "暗黙の補題に依存しているという疑義"}
CHALLENGE_STATUS_LABELS = {"open": "未対応", "answered": "対応済み", "withdrawn": "取り下げ済み",
                           "led_to_verification": "検証提案へ昇格済み"}
CHALLENGE_MODE_LABELS = {"direct": "主張そのものへ", "undercut": "主張と根拠のつながりへ"}
EVIDENCE_LINE_KIND_LABELS = {"observation": "観測", "derivation": "導出", "external_reference": "外部文献",
                             "consistency": "整合性"}
FALSIFICATION_KIND_LABELS = {"observation_value": "観測値そのもの",
                             "auxiliary_hypothesis": "較正・装置などの補助仮説",
                             "not_formulable": "反証条件を定式化できないという記帳"}
REACHABILITY_LABELS = {"reachable": "現在の観測で検証可能", "next_generation": "次世代の装置・観測なら可能",
                       "unreachable": "現状では困難", "unassessed": "未評価"}
ASPECT_LABELS = {"value": "観測値そのもの", "systematics": "較正・装置などの補助仮説"}
SUPPORT_LEVEL_BADGE_LABELS = {"none": "支持記録なし", "single": "単一の支持線", "several": "複数の支持線"}
PROPOSAL_STATUS_LABELS = {"proposed": "検証に着手可能", "in_progress": "検証に着手済み", "completed": "完了",
                          "withdrawn": "取り下げ済み"}
ASSUMPTION_FILTERS = {"候補": "candidate", "確定済み": "confirmed", "却下済み": "dismissed", "却下済み（保持）": "dismissed",
                      "すべて": "all", "candidate": "candidate", "confirmed": "confirmed", "dismissed": "dismissed",
                      "all": "all"}

TAB_HEAD = "前提の地図 ※「分野の地図」とは別機能です（負荷度×検証度の事実の投影）"
COURSE_SELECT_EMPTY = "コースを選択..."
AXIS_X, AXIS_Y = "検証スコープの被覆", "依存の広がり"
DETAIL_PLACEHOLDER = "点をタップすると台帳の詳細を表示します。"
ASSUMPTIONS_EMPTY = "該当する前提はありません。"
OPEN_HEAD = "未検証合意リスト"
OPEN_NOTE = "台帳の投影です（編集不可）。スコープの記帳で自動的に増減します。"
OPEN_EMPTY = "高負荷 × 低検証の対象は現在ありません。"
CF_SESSIONS_EMPTY = "保存済みセッションはありません。"
NO_SCOPE = "検証スコープの記帳なし"
NO_CONSENSUS = "明示的合意: 承認記録なし"
NAIVE_SIGNAL = "学習者の問い: 複数の学習者がここで問いを立てています"
NO_CHALLENGE = "疑義はありません。"
NO_FALSIFICATION = "覆る条件はまだ記帳されていません。"
NO_EVIDENCE_LINES = "根拠の線はまだ記帳されていません。"
VSTATUS_SCOPE_NOTE = "直接検証の記帳には検証スコープが1件以上必要です（空欄は異常ではなく発見です。まずスコープを記帳してください）。"
VSTATUS_REASON_REQUIRED = "理由の入力が必要です。"
VSTATUS_DIRECT_NEEDS_SCOPE = "直接検証の記帳には検証スコープの記帳が1件以上必要です。"
VSTATUS_AUDIT_NOTE = "この記帳はあなたの操作として帰属され、監査ログに記録されます。"
VSTATUS_422_FALLBACK = "この検証状態には記帳できませんでした。"
SCOPE_422_FALLBACK = "1 軸以上 + 根拠 + 理由が必要です"
CHALLENGE_422 = "理由（本人の言葉）が必要です"
WITHDRAW_403 = "この疑義の取り下げは本人のみ可能です。"
PROPOSAL_EMPTY = "検証提案の内容が必要です。"
PROPOSAL_EXTERNAL_EMPTY = "コーパス外の文献確認の記録が必要です。"
PROPOSAL_422_FALLBACK = "登録できませんでした。"
PROPOSAL_STATUS_FAIL = "更新に失敗しました。"
FALSIFICATION_422_FALLBACK = "statement・kind・reason と根拠（引用または evidence）が必要です"
FALSIFICATION_REFRESH_SCHEDULED = "候補生成を予約しました（しばらくしてから再読込してください）。"
FALSIFICATION_REFRESH_DONE = "実行しました。"
FALSIFICATION_REFRESH_FAIL = "実行に失敗しました。"
FALSIFICATION_REFRESH_NO_COURSE = "コースを選択すると実行できます。"
OBS_EMPTY = "観測系の主張はこのコーパスの中では見つかりません。"
OBS_LOAD_FAIL = "読み込みに失敗しました。"
CF_NOTE = "前提ノードをタップすると「仮に偽」にできます。元のグラフは変更されません。"
CF_OBS_NOTE = "この観測に依存する範囲を確認します。元のグラフは変更されません。"
CF_NONE = "（トグル中の前提・観測はありません）"
CF_SAVED = "保存しました（共有範囲: private）"
CF_SAVE_FAIL = "保存に失敗しました"
# app.js（学習画面の D3-6）
LEARNER_OPEN_SUMMARY = "このコースの教材で、検証の記録がない前提を見る"
LEARNER_OPEN_FAIL = "一覧を取得できませんでした。"
LEARNER_OPEN_EMPTY = "現在、表示できる項目はありません。"
LEARNER_NO_LINE = "（この根拠カードには検証状態の一行が添えられていない — 画面は台帳の記帳が無い対象には何も出さない）"

_NUM_HINT_RE = re.compile(r"^\s*[#＃]?\s*(\d+)\s*$")


def _runner_note(session: PersonaSession, note: str) -> None:
    session.scratch.setdefault("runner_notes", []).append(note)


# 第 25 周 H1: runner の状態名（ledger_target 等）を頭脳へ渡さない。画面の言葉で「何が画面に無いか」を言い、
# ハーネスの文であることを接頭辞で示す（状態名は HttpTrace.error の ``precondition:<name>`` に残る）。
MISSING_WHAT = {
    "course_id": "前提の地図タブでコースを選んでいない",
    "ledger_target": "台帳を開ける点・行（散布図の点・未検証合意リストの行・確定した前提）が画面に無い",
    "supporting_claim": "［この主張の台帳を開く］が出ている支える主張が台帳に無い",
    "assumption_candidate": "指した前提候補のカードが画面に無い",
    "scope_candidate": "指したスコープの候補が台帳に無い",
    "verification_status": "選んだ検証状態が記帳フォームの選択肢に無い",
    "open_challenge": "取り下げ・提案にできる疑義が台帳に無い",
    "proposal_card": "状態を変えられる検証提案のカードが画面に無い",
    "falsification_candidate": "指した覆る条件の候補が台帳に無い",
    "graph_node": "指したノードが原稿スタジオのグラフに無い",
    "document_id": "原稿スタジオでグラフを開く教材を選んでいない",
    "observation_target": "指した観測系の主張が一覧に無い",
    "answer_sources": "出典の付いた回答がまだ無い（出典タブに根拠カードが無い）",
    "component_id": "開いている部品（⚓ の部品チップ）が無い",
}
MISSING_PREFIX = "（ハーネス: "
MISSING_SUFFIX = " — 行為は行われていない。これは画面の文ではない）"


def missing_text(name: str) -> str:
    return MISSING_PREFIX + MISSING_WHAT.get(name, "この操作の対象がまだ画面に無い") + MISSING_SUFFIX


def _missing(ctx, name: str) -> None:
    """``ctx.missing`` で precondition の trace を残し、頭脳へは画面の言葉の事実文（ハーネスの印付き）を渡す。"""
    ctx.missing(name)
    ctx.session.last_body = {"_precondition": name, "_ui_fact": missing_text(name)}


def _key(text: Any) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text or ""))).casefold()


def hint_pick(items: list[dict], hint: Any, fields: tuple[str, ...]) -> Optional[dict]:
    """番号（1 始まり）か、fields のどれかの部分一致で 1 件選ぶ。hint が空なら先頭。"""
    items = [i for i in items if isinstance(i, dict)]
    if not items:
        return None
    raw = str(hint if hint is not None else "").strip()
    if not raw:
        return items[0]
    m = _NUM_HINT_RE.match(raw)
    if m:
        n = int(m.group(1))
        return items[n - 1] if 1 <= n <= len(items) else None
    k = _key(raw)
    exact = [i for i in items if any(_key(i.get(f)) == k for f in fields)]
    if exact:
        return exact[0]
    part = [i for i in items if any(k and (k in _key(i.get(f)) or (_key(i.get(f)) and _key(i.get(f)) in k))
                                    for f in fields)]
    return part[0] if part else None


def _word(value: Any, table: dict[str, str], extra: Optional[dict[str, str]] = None) -> str:
    """画面の文言かキーを語彙のキーへ写す（部分一致も許す）。写せなければ空。"""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw in table:
        return raw
    for k, v in table.items():
        if raw == v:
            return k
    for k, v in (extra or {}).items():
        if k in raw:
            return v
    for k, v in table.items():
        if raw in v or v in raw:
            return k
    return ""


def _status_ok(status: Optional[int]) -> bool:
    return status is not None and 200 <= status < 300


def _detail(body: Any) -> str:
    d = body.get("detail") if isinstance(body, dict) else None
    return d if isinstance(d, str) else ""


# ---------------------------------------------------------------------------
# 前提の地図タブの投影
# ---------------------------------------------------------------------------
def atlas_lines(data: Any, budget: Optional[int] = None) -> list[str]:
    """``renderAtlasSvg``: 軸の名前と点。点は <title> のラベルで見分ける（画面に番号は無いので投影の番号は指し示し用）。

    第 25 周 H18 / H19: 同じ文面（ラベル・段階・印が同じ）の点は 1 行に畳み「（ほか、同じ文面の点: 点5・点9）」と
    番号だけを添える（番号は変えない — 指し示しは同じ番号で効く）。``budget``（字数）を越える点は題名を列挙せず
    「（ハーネス: 観察の上限で…）」の 1 行で切る — 後ろの区画（前提候補・未検証合意リスト・反実仮想セッション）を
    観察の上限に押し出さない。
    """
    points = [p for p in ((data or {}).get("points") or [])] if isinstance(data, dict) else []
    lines = [f"散布図（横軸: {AXIS_X}［{' / '.join(COVERAGE_LABELS.values())}］・"
             f"縦軸: {AXIS_Y}［{' / '.join(LOAD_LABELS.values())}］）:"]
    population = str(data.get("population_note") or "") if isinstance(data, dict) else ""
    if not points:
        lines.append("（点は描かれていない）")
        # renderAtlasSvg（IK-0853）: 点が無いときは理由と別の入口をサーバの事実文で添える（旧サーバは無い）
        if isinstance(data, dict) and data.get("empty_note"):
            lines.append(str(data["empty_note"]))
            if data.get("empty_hint"):
                lines.append(str(data["empty_hint"]))
        if population:
            lines.append(population)  # 第 3 波（IK-0933）: data-doubt-population-note
        return lines
    order: list[str] = []
    numbers: dict[str, list[int]] = {}
    for i, p in enumerate(points, 1):
        if not isinstance(p, dict):
            continue
        marks = []
        marks.append("前提（紫の輪郭）" if p.get("target_type") == "assumption" else "主張・式・部品（灰の輪郭）")
        if p.get("unscoped"):
            marks.append("塗りなし・点線の輪郭")
        if p.get("has_naive_signal"):
            marks.append("外側に淡い輪")
        text = (f"{p.get('label') or p.get('target_id') or ''}（{AXIS_X}: "
                f"{COVERAGE_LABELS.get(str(p.get('scope_coverage')), str(p.get('scope_coverage') or ''))}・"
                f"{AXIS_Y}: {LOAD_LABELS.get(str(p.get('load_level')), str(p.get('load_level') or ''))}・"
                f"{'・'.join(marks)}）")
        if text not in numbers:
            order.append(text)
            numbers[text] = []
        numbers[text].append(i)
    used = sum(len(x) + 1 for x in lines)
    for text in order:
        nums = numbers[text]
        row = f"- 点{nums[0]}: {text}" + (f"（ほか、同じ文面の点: {'・'.join(f'点{n}' for n in nums[1:])}）"
                                           if len(nums) > 1 else "")
        if budget is not None and used + len(row) + 1 + len(population) > budget:
            lines.append(ATLAS_POINTS_CLIPPED)
            break
        lines.append(row)
        used += len(row) + 1
    if population:
        # renderAtlasSvg（第 3 波・IK-0933）: 導出の手順を母集合から外した事実文を散布図の下に 1 行（予算で切らない）
        lines.append(population)
    return lines


#: 散布図の点を観察の予算で切ったときの印（画面の文ではない）
ATLAS_POINTS_CLIPPED = ("（ハーネス: 観察の上限で散布図の残りの点を省略した — 画面の散布図にはまだ点がある。"
                        "これは画面の文ではない）")


def assumption_lines(data: Any, status_filter: str) -> list[str]:
    """``loadAssumptions``: カード（前提文・origin と status のバッジ・出所の逐語・出典ドキュメント・ボタン）。"""
    label = {v: k for k, v in ASSUMPTION_FILTERS.items() if not k.isascii() and k != "却下済み"}.get(status_filter, status_filter)
    lines = [f"前提候補のレビュー（表示: {label}）:"]
    items = [a for a in ((data or {}).get("items") or [])] if isinstance(data, dict) else []
    if not items:
        lines.append(ASSUMPTIONS_EMPTY)
        return lines
    for i, a in enumerate(items, 1):
        if not isinstance(a, dict):
            continue
        badges = [str(a.get("origin") or ""), str(a.get("status") or "")]
        if a.get("has_naive_signal"):
            badges.append("複数の学習者がここで問いを立てています")
        lines.append(f"{i}. {a.get('statement') or ''}［{' / '.join(b for b in badges if b)}］")
        if a.get("evidence_quote"):
            lines.append(f"   出所（逐語）: 「{a['evidence_quote']}」")
        cf = a.get("created_from") if isinstance(a.get("created_from"), dict) else {}
        if cf.get("document_ids"):
            lines.append(f"   出典ドキュメント: {', '.join(str(x) for x in cf['document_ids'])}")
        if a.get("status") == "candidate":
            lines.append("   ［確定］［文面を訂正して確定］［却下］")
    return lines


def open_lines(items: list[dict], *, only_reachable: bool, empty_note: str = "",
               population_note: str = "") -> list[str]:
    """``renderOpenAssumptionsList``: 行と段階のバッジ（数値は出さない）。空のときはフィルタ中でなければ
    サーバの ``empty_note``（範囲が空か・該当なしか — IK-0853）、無ければ従来の文。

    第 3 波（IK-0933）: ``population_note``（導出の手順を母集合から外した事実文）は行があれば一覧の先頭、
    空なら空の文の下に 1 行（``data-doubt-population-note``）。"""
    lines = [f"{OPEN_HEAD}（{OPEN_NOTE}）" + ("［到達可能な反証条件がある項目だけ表示］" if only_reachable else "") + ":"]
    rows = [it for it in items if isinstance(it, dict)]
    if only_reachable:
        rows = [it for it in rows if it.get("reachability_summary") == "reachable"]
    if not rows:
        lines.append(empty_note if (empty_note and not only_reachable) else OPEN_EMPTY)
        if population_note:
            lines.append(population_note)
        return lines
    if population_note:
        lines.append(population_note)
    for i, it in enumerate(rows, 1):
        badges = [f"依存の広がり: {LOAD_LABELS.get(str(it.get('load_level')), str(it.get('load_level') or ''))}",
                  "検証: " + STATUS_LABELS.get(str(it.get("verification_status")), str(it.get("verification_status") or ""))
                  + ("（スコープの記帳なし）" if it.get("scope_count_is_zero") else "")]
        if it.get("challenge_count_label"):
            badges.append(f"疑義: {it['challenge_count_label']}")
        if it.get("has_verification_proposal"):
            badges.append("検証提案あり")
        if it.get("has_naive_signal"):
            badges.append("学習者の問いあり")
        badges.append("覆る条件: あり" if it.get("has_falsification_condition") else "覆る条件: なし")
        rs = it.get("reachability_summary")
        badges.append(f"到達可能性: {REACHABILITY_LABELS.get(str(rs), str(rs))}" if rs else "到達可能性: 記録なし")
        sl = it.get("support_line_level")
        badges.append(f"支持: {SUPPORT_LEVEL_BADGE_LABELS.get(str(sl), str(sl))}" if sl else "支持: 未算出")
        lines.append(f"{i}. {it.get('statement') or ''}［{' / '.join(badges)}］")
    return lines


def cf_session_lines(data: Any) -> list[str]:
    """``loadCfSessions``: 保存済みセッションのカード。"""
    lines = ["反実仮想セッション:"]
    items = [s for s in ((data or {}).get("items") or [])] if isinstance(data, dict) else []
    if not items:
        lines.append(CF_SESSIONS_EMPTY)
        return lines
    for s in items:
        if not isinstance(s, dict):
            continue
        col = (s.get("collapsed_subgraph") or {}).get("node_ids") or [] if isinstance(s.get("collapsed_subgraph"), dict) else []
        ind = (s.get("indeterminate_subgraph") or {}).get("node_ids") or [] \
            if isinstance(s.get("indeterminate_subgraph"), dict) else []
        lines.append(f"- ［{s.get('shared_scope_label') or s.get('shared_scope') or ''}］［作成: {s.get('owner_name') or ''}］"
                     + (f" {s['notes']}" if s.get("notes") else ""))
        lines.append(f"  この前提に依存する範囲: {len(col)} ノード / 両者に依存（判定不能）: {len(ind)} ノード")
    return lines


def tab_view(session: PersonaSession) -> str:
    sc = session.scratch
    courses = sc.get("doubt_courses") or []
    cid = sc.get("doubt_course_id") or ""
    chosen = next((c for c in courses if str(c.get("id")) == cid), None)
    lines = [TAB_HEAD]
    opts = " / ".join(str(c.get("title") or c.get("id") or "") for c in courses) or "（選べるコースが無い）"
    lines.append(f"コース: {chosen.get('title') if chosen else COURSE_SELECT_EMPTY}（選択肢: {opts}）"
                 "［再読込］［前提マイニング実行］［コーパス監査実行］［負荷再計算］")
    if not cid:
        return "\n".join(lines)
    # 第 25 周 H18 / H19: 散布図以外の区画を先に組み、散布図は残りの字数を予算として受け取る
    # （散布図の点で観察の上限に達し、保存した反実仮想セッションの欄が頭脳に届かなかった）
    from uxsim.runner.state import OBSERVATION_CHARS  # 観察の上限の正本

    rest = [f"詳細: {DETAIL_PLACEHOLDER}"]
    rest += assumption_lines(sc.get("doubt_assumptions"), str(sc.get("doubt_assumption_filter") or "candidate"))
    rest += open_lines(list(sc.get("doubt_open") or []), only_reachable=bool(sc.get("doubt_open_reachable")),
                       empty_note=str(sc.get("doubt_open_empty_note") or ""),
                       population_note=str(sc.get("doubt_open_population_note") or ""))
    rest += cf_session_lines(sc.get("doubt_cf_sessions"))
    budget = OBSERVATION_CHARS - 200 - sum(len(x) + 1 for x in lines + rest)
    lines += atlas_lines(sc.get("doubt_atlas"), budget=max(budget, 400))
    return "\n".join(lines + rest)


# ---------------------------------------------------------------------------
# 台帳の詳細の投影（renderLedgerInto）
# ---------------------------------------------------------------------------
def falsification_lines(entry: Optional[dict]) -> list[str]:
    conds = (entry or {}).get("falsification_conditions") or []
    cands = (entry or {}).get("falsification_candidates") or []
    lines = ["覆る条件:"]
    if conds:
        for c in conds:
            if not isinstance(c, dict):
                continue
            lines.append(f"- {c.get('statement') or ''}（区分: {FALSIFICATION_KIND_LABELS.get(str(c.get('kind')), str(c.get('kind') or ''))}"
                         f" / 到達可能性: {REACHABILITY_LABELS.get(str(c.get('reachability')), REACHABILITY_LABELS['unassessed'])}）")
            if c.get("evidence_quote"):
                lines.append(f"  根拠: 「{c['evidence_quote']}」")
            lines.append("  出所: 教員の記帳" + (f" — {c['reason']}" if c.get("reason") else ""))
    else:
        lines.append(NO_FALSIFICATION)
    for i, c in enumerate([c for c in cands if isinstance(c, dict)], 1):
        lines.append(f"- 候補{i}: {c.get('statement') or ''}（区分: "
                     f"{FALSIFICATION_KIND_LABELS.get(str(c.get('kind')), str(c.get('kind') or ''))}）")
        if c.get("evidence_quote"):
            lines.append(f"  「{c['evidence_quote']}」")
        if c.get("reason"):
            lines.append(f"  {c['reason']}")
        lines.append("  到達可能性（確認時に選択）［確認して記帳］［見送る］")
    lines.append("［覆る条件を記帳する］［AIに候補を出してもらう］")
    return lines


def support_lines(entry: Optional[dict]) -> list[str]:
    sl = (entry or {}).get("support_lines")
    if not isinstance(sl, dict) or not sl.get("fact_line"):
        return []
    lines = [f"支持線: {sl['fact_line']}"]
    if sl.get("level") == "single" and sl.get("cut_members"):
        labels = "、".join(str(m.get("label") or m.get("node_id") or "") for m in sl["cut_members"] if isinstance(m, dict))
        lines.append(f"同時に崩れると支持が途切れる箇所: {labels}")
    if sl.get("observation_roots"):
        roots = "、".join(str(r.get("label") or r.get("claim_id") or "") for r in sl["observation_roots"] if isinstance(r, dict))
        lines.append(f"観測からの支持根: {roots}")
    return lines


def evidence_line_lines(entry: Optional[dict]) -> list[str]:
    lines = ["根拠の線:"]
    rows = [r for r in ((entry or {}).get("evidence_lines") or []) if isinstance(r, dict)]
    if not rows:
        lines.append(NO_EVIDENCE_LINES)
    for r in rows:
        kind = r.get("line_kind_label") or EVIDENCE_LINE_KIND_LABELS.get(str(r.get("line_kind")), str(r.get("line_kind") or ""))
        lines.append(f"- {kind}" + (f" — {r['reason']}" if r.get("reason") else ""))
        ids = [f"{name}: {', '.join(str(x) for x in r.get(key) or [])}"
               for name, key in (("根拠", "evidence_ids"), ("主張", "claim_ids"), ("式", "equation_ids")) if r.get(key)]
        if ids:
            lines.append(f"  {' / '.join(ids)}")
        lines.append("  出所: 教員の記帳")
    lines.append("［根拠の線を追加］")
    return lines


def challenge_entry_lines(target: dict, entry: dict) -> list[str]:
    """doubt-atlas.js ``challengeEntryHtml``（第 3 波・IK-0931）: 台帳行の無い対象の疑義の区画。
    前提・主張は「疑義はありません。」+［疑義を残す］、式・部品は ``challenge_note`` + 支える主張と［この主張の台帳を開く］。"""
    lines = ["疑義:"]
    if target.get("target_type") in ("assumption", "claim"):
        lines += [NO_CHALLENGE, "［疑義を残す］"]
    elif entry.get("challenge_note"):
        lines.append(str(entry["challenge_note"]))
        for c in entry.get("supporting_claims") or []:
            if isinstance(c, dict) and c.get("claim_id"):
                lines.append(f"- {c.get('label') or ''} ［この主張の台帳を開く］")
    return lines


def ledger_heading(entry: dict) -> str:
    """doubt-atlas.js renderLedgerInto（第 3 波・IK-0934）: 「対象: 〈論文の題名〉 › 〈対象の名前〉」
    （題名が無い旧サーバは対象の名前だけ）。"""
    if entry.get("document_title"):
        return f"対象: {entry['document_title']} › {entry['target_label']}"
    return f"対象: {entry['target_label']}"


def ledger_view(target: dict, entry: Optional[dict], note: str = "") -> str:
    title = str(target.get("label") or target.get("target_id") or "")
    lines = [f"台帳の詳細: {title}", "認識的地位"]
    # doubt-atlas.js renderLedgerInto（第 2 波 + 第 3 波）: 対象の見出し → 直前の記帳の結果 → 帰属の注記 → 記帳できない理由
    if isinstance(entry, dict) and entry.get("target_label"):
        lines.append(ledger_heading(entry))
    if note:
        lines.append(note)
    if isinstance(entry, dict) and entry.get("attribution_note"):
        lines.append(str(entry["attribution_note"]))
    read_only = isinstance(entry, dict) and entry.get("can_edit") is False
    if read_only and entry.get("can_edit_note"):
        lines.append(str(entry["can_edit_note"]))
    off = "（押せない）" if read_only else ""
    if not entry or entry.get("exists") is False:
        # 台帳行なし（第 3 波・IK-0931: サーバは 200 + exists:false で名前・疑義の入口・can_edit を返す。
        # 旧サーバの 404 は entry=None）。文言はサーバの not_recorded_note、無ければ従来の文
        lines += [str((entry or {}).get("not_recorded_note") or NO_SCOPE), f"［スコープを記帳］［検証状態を記帳する］{off}"]
        lines += falsification_lines(None)
        lines += evidence_line_lines(None)
        if entry:
            lines += challenge_entry_lines(target, entry)
        return "\n".join(lines)
    lines.append("検証状態: " + STATUS_LABELS.get(str(entry.get("verification_status")), str(entry.get("verification_status") or "")))
    scopes = [s for s in entry.get("verification_scopes") or [] if isinstance(s, dict)]
    if scopes:
        for s in scopes:
            axes = " / ".join(str(s.get(k)) for k in ("condition", "domain", "precision", "system") if s.get(k))
            lines.append(f"- スコープ: {axes}")
            if s.get("evidence_quote"):
                lines.append(f"  根拠: 「{s['evidence_quote']}」")
            if s.get("evidence_ids"):
                lines.append(f"  evidence: {', '.join(str(x) for x in s['evidence_ids'])}")
            # IK-0905: 記帳者は名前（recorded_by_label）。UUID を出さない
            lines.append(f"  記帳者: {s.get('recorded_by_label') or '-'}" + (f" — {s['reason']}" if s.get("reason") else ""))
    else:
        lines.append(NO_SCOPE)
    lines.append(f"明示的合意: {entry['consensus_explicit_label']}" if entry.get("consensus_explicit_label") else NO_CONSENSUS)
    lines.append(f"行動上の合意: {entry.get('consensus_behavioral_dependents') or 0} 箇所が依存")
    if entry.get("load_level"):
        lines.append(f"依存の広がり（段階）: {LOAD_LABELS.get(str(entry['load_level']), str(entry['load_level']))}")
    if isinstance(entry.get("naive_signal"), dict) and entry["naive_signal"].get("present"):
        lines.append(NAIVE_SIGNAL)
    for i, c in enumerate([c for c in entry.get("scope_candidates") or [] if isinstance(c, dict)], 1):
        axes = " / ".join(str(c.get(k)) for k in ("condition", "domain", "precision", "system") if c.get(k))
        lines.append(f"- スコープの候補{i}: {axes or '(軸未設定)'}［確定］［却下］{off}")
        if c.get("evidence_quote"):
            lines.append(f"  「{c['evidence_quote']}」")
        if c.get("reason"):
            lines.append(f"  {c['reason']}")
    lines.append(f"［スコープを記帳］［検証状態を記帳する］{off}")
    lines.append("疑義:")
    open_ch = [c for c in entry.get("challenges") or [] if isinstance(c, dict) and c.get("status") != "withdrawn"]
    if open_ch:
        for i, c in enumerate(open_ch, 1):
            mode = c.get("challenge_mode_label") or CHALLENGE_MODE_LABELS.get(str(c.get("challenge_mode")), CHALLENGE_MODE_LABELS["direct"])
            lines.append(f"- 疑義{i}: {CHALLENGE_TYPE_LABELS.get(str(c.get('challenge_type')), str(c.get('challenge_type') or ''))}"
                         f"（向き: {mode}）{c.get('challenger_name') or ''}" + (f" — {c['reason']}" if c.get("reason") else ""))
            lines.append(f"  状態: {CHALLENGE_STATUS_LABELS.get(str(c.get('status')), str(c.get('status') or ''))}"
                         + ("［取り下げ］［検証提案にする］" if c.get("status") in ("open", "answered") else ""))
    else:
        lines.append(NO_CHALLENGE)
    if target.get("target_type") in ("assumption", "claim"):
        lines.append("［疑義を残す］")
    elif entry.get("challenge_note"):
        # IK-0904: 疑義の入口が無い理由と、支える主張の台帳へ辿る道（文言はサーバ）
        lines.append(str(entry["challenge_note"]))
        for c in entry.get("supporting_claims") or []:
            if isinstance(c, dict) and c.get("claim_id"):
                lines.append(f"- {c.get('label') or ''} ［この主張の台帳を開く］")
    lines += falsification_lines(entry)
    lines += support_lines(entry)
    lines += evidence_line_lines(entry)
    return "\n".join(lines)


def open_challenges(entry: Optional[dict]) -> list[dict]:
    return [c for c in (entry or {}).get("challenges") or [] if isinstance(c, dict) and c.get("status") != "withdrawn"]


# ---------------------------------------------------------------------------
# 実行（教員）
# ---------------------------------------------------------------------------
def _course(ctx) -> str:
    return str(ctx.session.scratch.get("doubt_course_id") or "")


def _reload_all(ctx) -> None:
    """``reloadAll``: 散布図・前提候補（現在の表示）・未検証合意リスト・反実仮想セッション。"""
    s, cid = ctx.session, _course(ctx)
    if not cid:
        return
    sc = s.scratch
    _, atlas = ctx.call("GET", DOUBT + "/courses/{course_id}/assumption-atlas", overrides={"course_id": cid})
    sc["doubt_atlas"] = atlas if isinstance(atlas, dict) else {}
    filt = str(sc.get("doubt_assumption_filter") or "candidate")
    sc["doubt_assumption_filter"] = filt
    _, ass = ctx.call("GET", DOUBT + "/courses/{course_id}/assumptions", params={"status": filt}, overrides={"course_id": cid})
    sc["doubt_assumptions"] = ass if isinstance(ass, dict) else {}
    _, opened = ctx.call("GET", DOUBT + "/courses/{course_id}/open-assumptions", overrides={"course_id": cid})
    sc["doubt_open"] = list((opened or {}).get("items") or []) if isinstance(opened, dict) else []
    sc["doubt_open_empty_note"] = str((opened or {}).get("empty_note") or "") if isinstance(opened, dict) else ""
    sc["doubt_open_population_note"] = (str((opened or {}).get("population_note") or "")
                                        if isinstance(opened, dict) else "")
    _, cfs = ctx.call("GET", DOUBT + "/counterfactual/sessions", params={"course_id": cid})
    sc["doubt_cf_sessions"] = cfs if isinstance(cfs, dict) else {}


def _show(ctx, text: str) -> None:
    ctx.session.last_body = {"_ui_fact": _clip(text)}


def _ui_stop(ctx, text: str, note: str) -> None:
    """画面がクライアント側で止めた（HTTP なし）。"""
    ctx.session.last_status = None
    ctx.session.last_body = {"_ui_fact": _clip(text)}
    _runner_note(ctx.session, f"ui_guard: {note}")


def tab_open(ctx) -> None:
    s = ctx.session
    status, body = ctx.call("GET", "/api/admin/courses")
    courses = [c for c in (body if isinstance(body, list) else []) if isinstance(c, dict) and (c.get("id") or c.get("course_id"))]
    courses = [{"id": str(c.get("id") or c.get("course_id")), "title": str(c.get("title") or c.get("id") or ""),
                "role": str(c.get("role") or "")} for c in courses]
    s.scratch["doubt_courses"] = courses
    hint = ctx.args.get("course_hint")
    chosen = hint_pick(courses, hint, ("title", "id")) if courses else None
    if hint and chosen is None:
        _runner_note(s, f"runner_choice: course_hint={hint} に合うコースが一覧に無いので先頭を選んだ")
        chosen = courses[0] if courses else None
    s.scratch["doubt_course_id"] = chosen["id"] if chosen else ""
    for k in ("doubt_atlas", "doubt_assumptions", "doubt_open", "doubt_cf_sessions", "doubt_target", "doubt_ledger"):
        s.scratch.pop(k, None)
    s.scratch["doubt_assumption_filter"] = "candidate"
    if chosen:
        s.remember("doubt_courses", chosen["id"])
        _reload_all(ctx)
    _show(ctx, tab_view(s))


def _need_course(ctx) -> bool:
    if _course(ctx):
        return True
    _missing(ctx, "course_id")
    return False


def refresh(ctx) -> None:
    if not _need_course(ctx):
        return
    _reload_all(ctx)
    _show(ctx, tab_view(ctx.session))


def _course_post(path: str, done: Callable[[Any], str]) -> Callable[[Any], None]:
    def run(ctx) -> None:
        if not _need_course(ctx):
            return
        status, body = ctx.call("POST", DOUBT + path, json={}, overrides={"course_id": _course(ctx)})
        head = done(body, status)
        _reload_all(ctx)
        _show(ctx, (head + "\n" if head else "") + tab_view(ctx.session))
    return run


BATCH_FAIL = "実行できませんでした。"


def _audit_done(body: Any, status: Optional[int] = 200) -> str:
    """doubt-atlas.js ``runBatch``（IK-0853）: ボタンの下の状態行 ``#doubt-batch-status`` にサーバの ``note`` を
    そのまま出す。note が無ければ成功は空・失敗（接続失敗を含む）は「実行できませんでした。」。旧サーバは note を
    返さないので成功時は何も出ない（旧画面と同じ）。"""
    note = str(body.get("note") or "") if isinstance(body, dict) else ""
    text = note or ("" if _status_ok(status) else BATCH_FAIL)
    return f"（ボタンの下の状態行: {text}）" if text else ""


def assumption_filter(ctx) -> None:
    if not _need_course(ctx):
        return
    filt = ASSUMPTION_FILTERS.get(str(ctx.args.get("status") or "").strip(), "")
    if not filt:
        filt = _word(ctx.args.get("status"), {"candidate": "候補", "confirmed": "確定済み", "dismissed": "却下済み（保持）",
                                               "all": "すべて"}) or "candidate"
    ctx.session.scratch["doubt_assumption_filter"] = filt
    status, body = ctx.call("GET", DOUBT + "/courses/{course_id}/assumptions", params={"status": filt},
                            overrides={"course_id": _course(ctx)})
    ctx.session.scratch["doubt_assumptions"] = body if isinstance(body, dict) else {}
    _show(ctx, "\n".join(assumption_lines(body, filt)))


def _assumption_items(ctx) -> list[dict]:
    data = ctx.session.scratch.get("doubt_assumptions") or {}
    return [a for a in data.get("items") or [] if isinstance(a, dict)] if isinstance(data, dict) else []


def assumption_confirm(ctx) -> None:
    if not _need_course(ctx):
        return
    cands = [a for a in _assumption_items(ctx) if a.get("status") == "candidate"]
    picked = hint_pick(cands, ctx.args.get("pick"), ("statement",))
    if not picked:
        return _missing(ctx, "assumption_candidate")
    body: dict[str, Any] = {}
    statement = str(ctx.args.get("statement") or "").strip()
    if statement:
        body["statement"] = statement
    reason = str(ctx.args.get("reason") or "").strip()
    if not reason:
        # 画面（bindAssumptionButtons）は window.prompt の理由が空なら何もしない（既定文のプリフィルなし = F7）
        return _ui_stop(ctx, "（確定の理由（必須・本人の言葉で）の入力欄が空のまま閉じたので、何も起きなかった）",
                        "assumption_confirm: 理由が空なので画面は確定を送らない")
    body["reason"] = reason
    status, resp = ctx.call("POST", DOUBT + "/assumptions/{assumption_id}/confirm", json=body,
                            overrides={"assumption_id": str(picked.get("id"))})
    if _status_ok(status):
        ctx.session.remember("doubt_assumptions", str(picked.get("id")))
        ctx.session.scratch.setdefault("doubt_confirmed_assumptions", []).append(
            {"target_type": "assumption", "target_id": str(picked.get("id")), "label": statement or picked.get("statement")})
    _reload_all(ctx)
    _show(ctx, tab_view(ctx.session))


def assumption_dismiss(ctx) -> None:
    if not _need_course(ctx):
        return
    cands = [a for a in _assumption_items(ctx) if a.get("status") == "candidate"]
    picked = hint_pick(cands, ctx.args.get("pick"), ("statement",))
    if not picked:
        return _missing(ctx, "assumption_candidate")
    status, _ = ctx.call("POST", DOUBT + "/assumptions/{assumption_id}/dismiss", json={},
                         overrides={"assumption_id": str(picked.get("id"))})
    if _status_ok(status):
        ctx.session.scratch.setdefault("doubt_dismissed_assumptions", []).append(str(picked.get("id")))
    _reload_all(ctx)
    _show(ctx, tab_view(ctx.session))


def _targets_from(ctx, source: str) -> list[dict]:
    sc = ctx.session.scratch
    if source == "atlas":
        pts = (sc.get("doubt_atlas") or {}).get("points") or [] if isinstance(sc.get("doubt_atlas"), dict) else []
        return [{"target_type": p.get("target_type"), "target_id": p.get("target_id"), "label": p.get("label") or p.get("target_id"),
                 "document_id": str(p.get("document_id") or "")}  # 第 2 波: points[].document_id（開く論文）
                for p in pts if isinstance(p, dict) and p.get("target_id")]
    if source == "open":
        rows = list(sc.get("doubt_open") or [])
        if sc.get("doubt_open_reachable"):
            rows = [r for r in rows if isinstance(r, dict) and r.get("reachability_summary") == "reachable"]
        return [{"target_type": r.get("target_type"), "target_id": r.get("target_id"), "label": r.get("statement"),
                 "document_id": str(r.get("document_id") or "")}  # 第 2 波: items[].document_id
                for r in rows if isinstance(r, dict) and r.get("target_id")]
    if source == "assumptions":
        # 確定した前提は散布図に点として載る（負荷が計算されていれば）。散布図に無いときの近道ではなく、
        # 同じ前提を確定直後に開く（画面では確定後に散布図・未検証合意リストを読み直す）
        return list(sc.get("doubt_confirmed_assumptions") or [])
    return []


def ledger_open(ctx) -> None:
    if not _need_course(ctx):
        return
    src = str(ctx.args.get("from") or "").strip()
    sources = [src] if src in ("atlas", "open", "assumptions") else ["atlas", "open", "assumptions"]
    picked, used = None, ""
    for name in sources:
        picked = hint_pick(_targets_from(ctx, name), ctx.args.get("target_hint"), ("label", "target_id"))
        if picked:
            used = name
            break
    if not picked:
        return _missing(ctx, "ledger_target")
    if used == "assumptions":
        _runner_note(ctx.session, "runner_choice: 散布図・未検証合意リストに対象が無いので、確定した前提の台帳を開いた"
                                  "（画面では散布図の点として現れてから開く）")
    _open_ledger(ctx, dict(picked))


def _ledger_doc_params(target: dict) -> Optional[dict]:
    """doubt-atlas.js ledgerPath（第 2 波・IK-0899）: 開いている論文があれば ``?document_id=``。"""
    doc = str(target.get("document_id") or "")
    return {"document_id": doc} if doc else None


def _mark_ledger_digest(ctx, target: dict, entry: Any) -> None:
    """審判 H17（doubt.py）が読む要約: 開いた論文と台帳行の document_id（ID だけ・数なし）。"""
    traces = getattr(ctx.client, "traces", None) or []
    if traces and isinstance(entry, dict):
        traces[-1].digest = {**(traces[-1].digest or {}), "doubt_ledger": {
            "requested_document_id": str(target.get("document_id") or ""),
            "response_document_id": str(entry.get("document_id") or ""),
            "target_id": str(target.get("target_id") or "")}}


def _open_ledger(ctx, target: dict) -> None:
    s = ctx.session
    s.scratch["doubt_target"] = target
    s.scratch.pop("doubt_proposal", None)
    status, entry = ctx.call("GET", DOUBT + "/ledger/{target_type}/{target_id}", params=_ledger_doc_params(target),
                             overrides={"target_type": str(target["target_type"]), "target_id": str(target["target_id"])})
    _mark_ledger_digest(ctx, target, entry)
    if status == 404:
        s.scratch["doubt_ledger"] = None
    elif _status_ok(status) and isinstance(entry, dict):
        s.scratch["doubt_ledger"] = entry
        if entry.get("target_label") and not target.get("label"):
            target["label"] = entry["target_label"]
    else:
        # 画面（renderNodeLedgerSection）は 404 以外の失敗で区画ごと出さない（fail-closed）
        s.scratch.pop("doubt_target", None)
        _show(ctx, "（台帳の区画は表示されなかった）")
        return
    _show(ctx, ledger_view(target, s.scratch.get("doubt_ledger")))


def _refresh_ledger(ctx, head: str = "", note: str = "") -> None:
    """``refreshLedgerSection``（第 2 波: 記帳の応答の ``note`` を台帳の頭に 1 行で描く）。"""
    target = ctx.session.scratch.get("doubt_target")
    if not target:
        return
    status, entry = ctx.call("GET", DOUBT + "/ledger/{target_type}/{target_id}", params=_ledger_doc_params(target),
                             overrides={"target_type": str(target["target_type"]), "target_id": str(target["target_id"])})
    _mark_ledger_digest(ctx, target, entry)
    if status == 404:
        ctx.session.scratch["doubt_ledger"] = None
    elif _status_ok(status) and isinstance(entry, dict):
        ctx.session.scratch["doubt_ledger"] = entry
    _show(ctx, (head + "\n" if head else "") + ledger_view(target, ctx.session.scratch.get("doubt_ledger"), note=note))


def _need_target(ctx) -> Optional[dict]:
    t = ctx.session.scratch.get("doubt_target")
    if not t:
        _missing(ctx, "ledger_target")
        return None
    return t


def _ledger_post(ctx, method: str, suffix: str, body: dict, fallback: str, *, overrides: Optional[dict] = None,
                 fixed_422: bool = False) -> tuple[Optional[int], Any]:
    t = _need_target(ctx)
    if not t:
        return None, None
    entry = ctx.session.scratch.get("doubt_ledger")
    if isinstance(entry, dict) and entry.get("can_edit") is False:
        # doubt-atlas.js disableLedgerWrites（IK-0900・第 2 波）: 記帳のボタンは無効。押しても何も起きない
        _ui_stop(ctx, ledger_view(t, entry) + "\n（記帳のボタンは押せない）",
                 "ledger_write: can_edit=false なので画面の記帳のボタンは無効")
        return None, None
    ov = {"target_type": str(t["target_type"]), "target_id": str(t["target_id"]), **(overrides or {})}
    status, resp = ctx.call(method, DOUBT + "/ledger/{target_type}/{target_id}" + suffix, json=body,
                            params=_ledger_doc_params(t), overrides=ov)
    if status == 422:
        msg = fallback if fixed_422 else (_detail(resp) or fallback)
        _show(ctx, ledger_view(t, ctx.session.scratch.get("doubt_ledger")) + f"\n（フォームの下に表示: {msg}）")
        return status, resp
    if _status_ok(status):
        # スコープ・検証状態の記帳は応答の note（記帳しました / すでにこの状態です / 重複）を描く（IK-0905）
        note = str(resp.get("note") or "") if isinstance(resp, dict) and suffix in ("/scopes", "/verification-status") else ""
        _refresh_ledger(ctx, note=note)
    return status, resp


def scope_record(ctx) -> None:
    body = {f: str(ctx.args.get(f) or "").strip() for f in ("condition", "domain", "precision", "system", "evidence_quote", "reason")}
    body["evidence_ids"] = []
    status, _ = _ledger_post(ctx, "POST", "/scopes", body, SCOPE_422_FALLBACK)
    if _status_ok(status):
        t = ctx.session.scratch.get("doubt_target") or {}
        ctx.session.scratch.setdefault("doubt_written", []).append({**t, "what": "scope"})


def scope_candidate_decide(ctx) -> None:
    t = _need_target(ctx)
    if not t:
        return
    cands = [c for c in (ctx.session.scratch.get("doubt_ledger") or {}).get("scope_candidates") or [] if isinstance(c, dict)]
    picked = hint_pick(cands, ctx.args.get("pick"), ("condition", "domain", "precision", "system", "reason"))
    if not picked:
        return _missing(ctx, "scope_candidate")
    decision = "dismiss" if _word(ctx.args.get("decision"), {"confirm": "確定", "dismiss": "却下"},
                                  {"見送": "dismiss", "却下": "dismiss"}) == "dismiss" else "confirm"
    _ledger_post(ctx, "POST", f"/scope-candidates/{{candidate_id}}/{decision}", {}, "",
                 overrides={"candidate_id": str(picked.get("candidate_id"))})


def verification_status(ctx) -> None:
    t = _need_target(ctx)
    if not t:
        return
    key = _word(ctx.args.get("status"), STATUS_LABELS, {"直接": "directly_verified", "間接": "indirectly_supported",
                                                        "反証": "refuted", "未検証": "untested", "情報なし": "unknown"})
    if not key:
        return _missing(ctx, "verification_status")
    entry = ctx.session.scratch.get("doubt_ledger") or {}
    scopes = len([s for s in entry.get("verification_scopes") or [] if isinstance(s, dict)])
    base = ledger_view(t, ctx.session.scratch.get("doubt_ledger"))
    form = "検証状態の記帳フォーム: " + " / ".join(
        v + ("（選べない）" if k == "directly_verified" and scopes < 1 else "") for k, v in STATUS_LABELS.items())
    if scopes < 1:
        form += f"\n{VSTATUS_SCOPE_NOTE}"
    form += f"\n{VSTATUS_AUDIT_NOTE}"
    if key == "directly_verified" and scopes < 1:
        return _ui_stop(ctx, f"{base}\n{form}\n（「直接検証の記帳あり」は選べない: {VSTATUS_DIRECT_NEEDS_SCOPE}）",
                        "verification_status: スコープ 0 件では「直接検証の記帳あり」の選択肢が無効で、画面は送らない")
    reason = str(ctx.args.get("reason") or "").strip()
    if not reason:
        return _ui_stop(ctx, f"{base}\n{form}\n（フォームの下に表示: {VSTATUS_REASON_REQUIRED}）",
                        "verification_status: 理由が空なので画面は送らない")
    status, _ = _ledger_post(ctx, "PUT", "/verification-status", {"verification_status": key, "reason": reason},
                             VSTATUS_422_FALLBACK)
    if _status_ok(status):
        ctx.session.scratch.setdefault("doubt_written", []).append({**t, "what": "verification_status", "status": key})


def challenge_record(ctx) -> None:
    t = _need_target(ctx)
    if not t:
        return
    if t.get("target_type") not in ("assumption", "claim"):
        return _ui_stop(ctx, ledger_view(t, ctx.session.scratch.get("doubt_ledger")) + "\n（「疑義を残す」ボタンはこの対象には無い）",
                        "challenge_record: 前提・主張以外の台帳には疑義のボタンが無い")
    if not ctx.session.scratch.get("doubt_ledger"):
        # 第 3 波（IK-0931）: 台帳が 404（旧サーバ）で開いた区画には疑義の入口が無い（challengeEntryHtml は entry があるときだけ）
        return _ui_stop(ctx, ledger_view(t, None) + "\n（「疑義を残す」ボタンはこの区画には無い）",
                        "challenge_record: 台帳の応答が無い（404）区画には疑義の入口が出ない")
    ctype = _word(ctx.args.get("challenge_type"), CHALLENGE_TYPE_LABELS,
                  {"外挿": "scope_extrapolation", "未検証": "untested_in_domain", "定義": "definitional", "補題": "hidden_lemma"})
    ctype = ctype or "scope_extrapolation"
    mode = _word(ctx.args.get("challenge_mode"), CHALLENGE_MODE_LABELS, {"つながり": "undercut", "根拠": "undercut"}) or "direct"
    body = {"challenge_type": ctype, "challenge_mode": mode, "reason": str(ctx.args.get("reason") or "").strip()}
    status, resp = ctx.call("POST", DOUBT + "/targets/{target_type}/{target_id}/challenges", json=body,
                            overrides={"target_type": str(t["target_type"]), "target_id": str(t["target_id"])})
    if status == 422:
        # 画面（bindChallengeForm）は 422 の detail を読まず固定文を出す
        return _show(ctx, ledger_view(t, ctx.session.scratch.get("doubt_ledger")) + f"\n（フォームの下に表示: {CHALLENGE_422}）")
    if _status_ok(status) and isinstance(resp, dict):
        ctx.session.remember("doubt_challenges", resp.get("challenge_id"))
        _refresh_ledger(ctx)


def challenge_withdraw(ctx) -> None:
    t = _need_target(ctx)
    if not t:
        return
    picked = hint_pick([c for c in open_challenges(ctx.session.scratch.get("doubt_ledger"))
                        if c.get("status") in ("open", "answered")], ctx.args.get("pick"), ("reason", "challenge_type"))
    if not picked:
        return _missing(ctx, "open_challenge")
    status, _ = ctx.call("POST", DOUBT + "/challenges/{challenge_id}/withdraw", json={},
                         overrides={"challenge_id": str(picked.get("id"))})
    if status == 403:
        return _show(ctx, ledger_view(t, ctx.session.scratch.get("doubt_ledger")) + f"\n（疑義の下に表示: {WITHDRAW_403}）")
    if _status_ok(status):
        ctx.session.scratch.setdefault("doubt_withdrawn", []).append(str(picked.get("id")))
        _refresh_ledger(ctx)


def challenge_propose(ctx) -> None:
    t = _need_target(ctx)
    if not t:
        return
    picked = hint_pick([c for c in open_challenges(ctx.session.scratch.get("doubt_ledger"))
                        if c.get("status") in ("open", "answered")], ctx.args.get("pick"), ("reason", "challenge_type"))
    if not picked:
        return _missing(ctx, "open_challenge")
    base = ledger_view(t, ctx.session.scratch.get("doubt_ledger"))
    proposal = str(ctx.args.get("proposal") or "").strip()
    external = str(ctx.args.get("external_check") or "").strip()
    reach = _word(ctx.args.get("reachability"), REACHABILITY_LABELS, {"現在": "reachable", "次世代": "next_generation",
                                                                      "困難": "unreachable"}) or "unassessed"
    form = "検証提案のフォーム（検証提案 / コーパス外の確認（必須） / 到達可能性）"
    if not proposal:
        return _ui_stop(ctx, f"{base}\n{form}\n（フォームの下に表示: {PROPOSAL_EMPTY}）", "challenge_propose: 提案の文が空")
    if not external:
        return _ui_stop(ctx, f"{base}\n{form}\n（フォームの下に表示: {PROPOSAL_EXTERNAL_EMPTY}）",
                        "challenge_propose: コーパス外の確認が空なので画面は送らない（サーバも 422 にする SL-8）")
    status, resp = ctx.call("POST", DOUBT + "/challenges/{challenge_id}/proposals",
                            json={"proposal": proposal, "external_check": external, "reachability": reach},
                            overrides={"challenge_id": str(picked.get("id"))})
    if status == 422:
        return _show(ctx, f"{base}\n{form}\n（フォームの下に表示: {_detail(resp) or PROPOSAL_422_FALLBACK}）")
    if _status_ok(status) and isinstance(resp, dict) and resp.get("proposal_id"):
        ctx.session.scratch["doubt_proposal"] = {"id": str(resp["proposal_id"]), "status": "proposed"}
        ctx.session.remember("doubt_proposals", resp["proposal_id"])
        # 画面は台帳を読み直さず（proposal_id を保つため）、その疑義のボタンを無効にしてカードを差し込む
        _show(ctx, base + "\n" + proposal_card("proposed") + "\n（この疑義の［取り下げ］［検証提案にする］は押せなくなった）")


def proposal_card(status: str) -> str:
    """``renderProposalStatusCard``。"""
    line = f"検証提案（コーパス外の確認あり）: {PROPOSAL_STATUS_LABELS.get(status, status)}"
    buttons = []
    if status == "proposed":
        buttons.append("［検証に着手］")
    if status == "in_progress":
        buttons.append("［完了を記録］")
    if status in ("proposed", "in_progress"):
        buttons.append("［取り下げ］")
    return line + ("".join(buttons) if buttons else "")


def proposal_status(ctx) -> None:
    prop = ctx.session.scratch.get("doubt_proposal")
    if not prop:
        return _missing(ctx, "proposal_card")
    want = _word(ctx.args.get("status"), {"in_progress": "検証に着手", "completed": "完了を記録", "withdrawn": "取り下げ"},
                 {"着手": "in_progress", "完了": "completed", "取り下げ": "withdrawn"})
    allowed = {"proposed": ("in_progress", "withdrawn"), "in_progress": ("completed", "withdrawn")}.get(prop["status"], ())
    if want not in allowed:
        return _ui_stop(ctx, proposal_card(prop["status"]) + "\n（そのボタンはこのカードに無い）",
                        f"proposal_status: {want or ctx.args.get('status')} のボタンは状態 {prop['status']} のカードに無い")
    status, _ = ctx.call("PATCH", DOUBT + "/proposals/{proposal_id}", json={"status": want},
                         overrides={"proposal_id": prop["id"]})
    if _status_ok(status):
        prop["status"] = want
        _show(ctx, proposal_card(want))
    else:
        _show(ctx, proposal_card(prop["status"]) + f"\n（カードに表示: {PROPOSAL_STATUS_FAIL}）")


def falsification_record(ctx) -> None:
    kind = _word(ctx.args.get("kind"), {"observation_value": "観測値そのもの", "auxiliary_hypothesis": "較正・装置などの補助仮説",
                                         "not_formulable": "反証条件を定式化できない"},
                 {"補助": "auxiliary_hypothesis", "較正": "auxiliary_hypothesis", "定式化": "not_formulable"}) or "observation_value"
    reach = _word(ctx.args.get("reachability"), REACHABILITY_LABELS, {"現在": "reachable", "次世代": "next_generation",
                                                                      "困難": "unreachable"}) or "unassessed"
    body = {"statement": str(ctx.args.get("statement") or "").strip(), "kind": kind,
            "reason": str(ctx.args.get("reason") or "").strip(), "reachability": reach,
            "evidence_quote": str(ctx.args.get("evidence_quote") or "").strip()}
    _ledger_post(ctx, "POST", "/falsification-conditions", body, FALSIFICATION_422_FALLBACK)


def falsification_candidate_decide(ctx) -> None:
    t = _need_target(ctx)
    if not t:
        return
    cands = [c for c in (ctx.session.scratch.get("doubt_ledger") or {}).get("falsification_candidates") or [] if isinstance(c, dict)]
    picked = hint_pick(cands, ctx.args.get("pick"), ("statement",))
    if not picked:
        return _missing(ctx, "falsification_candidate")
    decision = "dismiss" if _word(ctx.args.get("decision"), {"confirm": "確認して記帳", "dismiss": "見送る"},
                                  {"見送": "dismiss", "却下": "dismiss"}) == "dismiss" else "confirm"
    body = {}
    if decision == "confirm":
        body["reachability"] = _word(ctx.args.get("reachability"), REACHABILITY_LABELS,
                                     {"現在": "reachable", "次世代": "next_generation", "困難": "unreachable"}) or "unassessed"
    _ledger_post(ctx, "POST", f"/falsification-candidates/{{candidate_id}}/{decision}", body, "",
                 overrides={"candidate_id": str(picked.get("candidate_id"))})


def falsification_refresh(ctx) -> None:
    t = ctx.session.scratch.get("doubt_target") or {}
    base = ledger_view(t, ctx.session.scratch.get("doubt_ledger")) if t else ""
    if not _course(ctx):
        return _ui_stop(ctx, (base + "\n" if base else "") + f"（ボタンの横に表示: {FALSIFICATION_REFRESH_NO_COURSE}）",
                        "falsification_refresh: 前提の地図タブでコースを選んでいない")
    status, resp = ctx.call("POST", DOUBT + "/courses/{course_id}/falsification-candidates/refresh", json={},
                            overrides={"course_id": _course(ctx)})
    if _status_ok(status):
        # IK-0853: 理由（範囲が空など）はサーバの事実文 note をそのまま出す
        if isinstance(resp, dict) and resp.get("note"):
            msg = str(resp["note"])
        else:
            msg = FALSIFICATION_REFRESH_SCHEDULED if isinstance(resp, dict) and resp.get("scheduled") else FALSIFICATION_REFRESH_DONE
    else:
        msg = FALSIFICATION_REFRESH_FAIL
    _show(ctx, (base + "\n" if base else "") + f"（ボタンの横に表示: {msg}）")


# --- 原稿スタジオのグラフ ---------------------------------------------------
def _studio_doc(ctx) -> str:
    """原稿スタジオでグラフを開いている教材。``material_hint`` があれば教材一覧の題名・ファイル名の部分一致で選ぶ。"""
    s = ctx.session
    hint = str(ctx.args.get("material_hint") or "").strip()
    if hint and not ctx.args.get("document_id"):
        cached = s.scratch.get("doubt_materials")
        if cached is None:
            status, body = ctx.call("GET", "/api/admin/materials")
            cached = [{"document_id": str(m.get("document_id") or ""), "title": str(m.get("title") or ""),
                       "filename": str(m.get("filename") or "")}
                      for m in (body if isinstance(body, list) else []) if isinstance(m, dict) and m.get("document_id")]
            s.scratch["doubt_materials"] = cached
        picked = hint_pick(cached, hint, ("title", "filename", "document_id"))
        if picked:
            ctx.args["document_id"] = picked["document_id"]
        else:
            _runner_note(s, f"runner_choice: material_hint={hint} に合う教材が一覧に無い — 直前に開いた教材のグラフを使う")
    return str(ctx.args.get("document_id") or (s.scratch.get("doubt_graph") or {}).get("document_id")
               or s.latest("graph_documents") or s.latest("documents") or "")


def _load_graph(ctx) -> Optional[dict]:
    doc = _studio_doc(ctx)
    g = ctx.session.scratch.get("doubt_graph")
    if g and g.get("document_id") == doc and g.get("nodes"):
        return g
    if not doc:
        _missing(ctx, "document_id")
        return None
    status, body = ctx.call("GET", "/api/admin/documents/{document_id}/component-graph", overrides={"document_id": doc})
    graph = body.get("graph") if isinstance(body, dict) and isinstance(body.get("graph"), dict) else body
    nodes = []
    for n in (graph or {}).get("nodes") or [] if isinstance(graph, dict) else []:
        if not isinstance(n, dict):
            continue
        nid = str(n.get("component_id") or n.get("id") or n.get("node_id") or "")  # lsGraphNodeId
        label = next((str(n.get(k)).strip() for k in ("label", "visual_label", "title", "name") if str(n.get(k) or "").strip()), nid)
        nodes.append({"id": nid, "vis_id": str(n.get("id") or nid), "label": label, "layer": str(n.get("graph_layer") or "main")})
    # 画面は既定で主グラフの層を描く
    nodes.sort(key=lambda n: 0 if n["layer"] == "main" else 1)
    g = {"document_id": doc, "nodes": nodes}
    ctx.session.scratch["doubt_graph"] = g
    if status == 200:
        ctx.session.remember("graph_documents", doc)  # ゼミ前ブリーフ・グラフレビューの既定の教材（画面で開いている教材）
    if not nodes:
        _show(ctx, "（原稿スタジオのグラフにノードが描かれていない）")
        return None
    return g


def studio_node_ledger(ctx) -> None:
    g = _load_graph(ctx)
    if not g:
        return
    node = hint_pick(g["nodes"], ctx.args.get("node_hint"), ("label", "id"))
    if not node:
        return _missing(ctx, "graph_node")
    # lsRenderGraphNodeDetail → renderNodeLedgerSection(detail, "component", nodeId)
    _open_ledger(ctx, {"target_type": "component", "target_id": node["id"], "label": node["label"], "from": "studio",
                       "document_id": str(g.get("document_id") or "")})  # lsGraphActiveDocumentId()


def supporting_claim_ledger(ctx) -> None:
    """doubt-atlas.js bindSupportingClaims（IK-0904・第 2 波）: 疑義を残せない対象の台帳の［この主張の台帳を開く］で、
    支える主張の台帳を同じ論文で開く（画面は台帳の下の枠に重ねて開く — runner はその主張の台帳へ移る）。"""
    t = _need_target(ctx)
    if not t:
        return
    entry = ctx.session.scratch.get("doubt_ledger") or {}
    claims = [c for c in entry.get("supporting_claims") or [] if isinstance(c, dict) and c.get("claim_id")]
    picked = hint_pick(claims, ctx.args.get("claim_hint"), ("label", "claim_id"))
    if not picked:
        return _missing(ctx, "supporting_claim")
    _open_ledger(ctx, {"target_type": "claim", "target_id": str(picked["claim_id"]), "label": picked.get("label") or "",
                       "document_id": str(t.get("document_id") or ""), "from": "supporting_claim"})


def _cf(ctx) -> dict:
    cf = ctx.session.scratch.setdefault("doubt_cf", {"toggled": [], "observations": []})
    cf["document_id"] = _studio_doc(ctx)
    cf["course_id"] = _course(ctx) or ctx.session.course_id or ""
    return cf


def cf_toggled_names(cf: dict, result: Optional[dict]) -> list[str]:
    """doubt-atlas.js ``cfToggledNames``（IK-0854）: トグル中の前提の名前（グラフのノード表示名の 1 行目 →
    直近の試算結果の ``toggled_labels`` の順・重複なし）。件数ではなく名前で列挙する。"""
    labels = cf.get("labels") if isinstance(cf.get("labels"), dict) else {}
    from_result = list((result or {}).get("toggled_labels") or []) if isinstance(result, dict) else []
    names: list[str] = []
    for i, vid in enumerate(cf.get("toggled") or []):
        label = str(labels.get(vid) or "").split("\n")[0]
        if not label and i < len(from_result):
            label = str(from_result[i] or "")
        if label and label not in names:
            names.append(label)
    return names


def cf_view(cf: dict, result: Optional[dict], status_text: Optional[str] = None) -> str:
    """``updateCfStatus`` + ``renderCfWarnings`` + ``applyCounterfactualStyles``（グラフ上の見え方を文にする）。

    状態行は「仮に偽にしている前提: 名前、名前」（IK-0854。名前が引けなければ空）。``status_text`` は保存後に
    状態行が置き換わった文（「保存しました（共有範囲: 非公開）」）。試算できなかった対象は ``warnings`` を
    状態行の下に事実文で出す（旧サーバは warnings の名前列挙が無いが、文はそのまま描く）。
    """
    lines = [f"反実仮想モード: ON（{CF_NOTE}）"]
    if not cf["toggled"] and not cf["observations"]:
        lines.append(CF_NONE)
    else:
        names = cf_toggled_names(cf, result)
        text = status_text if status_text is not None else ("仮に偽にしている前提: " + "、".join(names) if names else "")
        lines.append((f"{text}" if text else "（状態行は空）") + "［すべて解除］［セッションを保存］")
    for o in cf["observations"]:
        lines.append(f"- 観測: {o['label']}（{ASPECT_LABELS.get(o['aspect'], o['aspect'])}）［解除］")
    if isinstance(result, dict):
        labels = result.get("node_labels") if isinstance(result.get("node_labels"), dict) else {}
        col = [labels.get(i, i) for i in result.get("collapsed_node_ids") or []]
        ind = [labels.get(i, i) for i in result.get("indeterminate_node_ids") or []]
        lines.append("グラフ上で薄く点線になったノード（この前提に依存する範囲）: " + ("、".join(col) if col else "なし"))
        lines.append("やや薄く点線になったノード（両者に依存・判定不能）: " + ("、".join(ind) if ind else "なし"))
        # renderCfWarnings（IK-0854）: 試算できなかった対象を事実文で（「依存する範囲が無い」と見分ける）
        lines += [str(w) for w in result.get("warnings") or [] if w]
    return "\n".join(lines)


def _compute(ctx, cf: dict) -> None:
    if not cf["toggled"] and not cf["observations"]:
        cf["last"] = None
        return _show(ctx, cf_view(cf, None))
    status, result = ctx.call("POST", DOUBT + "/counterfactual/compute", json={
        "toggled_assumption_ids": list(cf["toggled"]),
        "toggled_observations": [{"claim_id": o["claim_id"], "aspect": o["aspect"]} for o in cf["observations"]],
        "document_id": cf["document_id"], "course_id": cf["course_id"]})
    cf["last"] = result if _status_ok(status) and isinstance(result, dict) else cf.get("last")
    _show(ctx, cf_view(cf, cf.get("last")))  # 失敗は表示を現状維持（非破壊）


def counterfactual_toggle(ctx) -> None:
    g = _load_graph(ctx)
    if not g:
        return
    node = hint_pick(g["nodes"], ctx.args.get("node_hint"), ("label", "id"))
    if not node:
        return _missing(ctx, "graph_node")
    cf = _cf(ctx)
    vid = node["vis_id"]  # toggleCounterfactualNode は vis のノード id（params.nodes[0]）
    cf.setdefault("labels", {})[vid] = node["label"]  # cfToggledNames が引く vis のノード表示名
    if vid in cf["toggled"]:
        cf["toggled"].remove(vid)
    else:
        cf["toggled"].append(vid)
    _compute(ctx, cf)


def counterfactual_observation(ctx) -> None:
    cf = _cf(ctx)
    if not cf["course_id"]:
        return _missing(ctx, "course_id")
    targets = ctx.session.scratch.get("doubt_obs_targets")
    if targets is None:
        status, body = ctx.call("GET", DOUBT + "/courses/{course_id}/observation-targets",
                                params={"document_id": cf["document_id"]} if cf.get("document_id") else None,
                                overrides={"course_id": cf["course_id"]})
        if not _status_ok(status):
            return _show(ctx, f"観測を仮に倒す（{CF_OBS_NOTE}）\n{OBS_LOAD_FAIL}")
        targets = [t for t in (body or {}).get("targets") or [] if isinstance(t, dict)] if isinstance(body, dict) else []
        ctx.session.scratch["doubt_obs_targets"] = targets
    if not targets:
        return _show(ctx, f"観測を仮に倒す（{CF_OBS_NOTE}）\n{OBS_EMPTY}")
    picked = hint_pick([{**t, "label": t.get("label") or t.get("claim_id")} for t in targets], ctx.args.get("claim_hint"),
                       ("label", "claim_id"))
    if not picked:
        return _missing(ctx, "observation_target")
    aspect = _word(ctx.args.get("aspect"), ASPECT_LABELS, {"較正": "systematics", "装置": "systematics"}) or "value"
    if not any(o["claim_id"] == picked["claim_id"] and o["aspect"] == aspect for o in cf["observations"]):
        cf["observations"].append({"claim_id": str(picked["claim_id"]), "aspect": aspect, "label": str(picked["label"])})
    _compute(ctx, cf)


def counterfactual_save(ctx) -> None:
    cf = ctx.session.scratch.get("doubt_cf")
    if not cf or (not cf.get("toggled") and not cf.get("observations")):
        return _ui_stop(ctx, f"反実仮想モード: {CF_NONE}（［セッションを保存］は出ていない）",
                        "counterfactual_save: 仮に倒している前提・観測が無いので保存のボタンが無い")
    status, saved = ctx.call("POST", DOUBT + "/counterfactual/sessions", json={
        "toggled_assumption_ids": list(cf["toggled"]),
        "toggled_observations": [{"claim_id": o["claim_id"], "aspect": o["aspect"]} for o in cf["observations"]],
        "course_id": cf.get("course_id") or "", "document_id": cf.get("document_id") or "",
        "notes": str(ctx.args.get("notes") or ""), "shared_scope": "private"})
    if _status_ok(status):
        # IK-0854: 共有範囲は内部値ではなくサーバの日本語（shared_scope_label）。無ければ「保存しました」だけ
        scope = str(saved.get("shared_scope_label") or "") if isinstance(saved, dict) else ""
        text = f"保存しました（共有範囲: {scope}）" if scope else "保存しました"
    else:
        text = CF_SAVE_FAIL
    _show(ctx, cf_view(cf, cf.get("last"), status_text=text))


def open_reachable_filter(ctx) -> None:
    if not _need_course(ctx):
        return
    on = ctx.args.get("on")
    flag = not (on is False or str(on).strip().lower() in ("false", "0", "off", "いいえ"))
    ctx.session.scratch["doubt_open_reachable"] = flag
    ctx.session.last_status = 200
    _show(ctx, "\n".join(open_lines(list(ctx.session.scratch.get("doubt_open") or []), only_reachable=flag,
                                    empty_note=str(ctx.session.scratch.get("doubt_open_empty_note") or ""),
                                    population_note=str(ctx.session.scratch.get("doubt_open_population_note") or ""))))
    _runner_note(ctx.session, "ui_only: 到達可能フィルタは読み込み済みの一覧をその場で絞る（HTTP なし）")


# ---------------------------------------------------------------------------
# 実行（学生）
# ---------------------------------------------------------------------------
def _learner_line(ctx, target_type: str, target_id: str) -> Optional[str]:
    """``fetchLearnerLedgerFactLine``（セッション内キャッシュも画面と同じ）。"""
    from uxsim.runner.state import learner_focus_params

    s = ctx.session
    cache = s.scratch.setdefault("ledger_line_cache", {})
    # app.js fetchLearnerLedgerFactLine（第 2 波）: ?topic_id=&document_id=<焦点論文> を付け、キャッシュの鍵にも入れる
    focus = learner_focus_params(s)
    key = f"{s.course_id}|{target_type}|{target_id}|" + "&".join(f"{k}={v}" for k, v in focus.items())
    if key in cache:
        return cache[key]
    text = None
    status, data = ctx.call("GET", "/api/learning/courses/{course_id}/ledger/{target_type}/{target_id}",
                            params=focus or None, overrides={"target_type": target_type, "target_id": target_id})
    if _status_ok(status) and isinstance(data, dict) and data.get("fact_line"):
        text = str(data["fact_line"])
        axes = [" / ".join(str(sc.get(k)) for k in ("condition", "domain", "precision", "system") if sc.get(k))
                for sc in data.get("scopes") or [] if isinstance(sc, dict)]
        axes = [a for a in axes if a]
        if len(axes) > 1:
            text += "（記帳スコープ: " + "；".join(axes) + "）"
        if data.get("target_note"):
            # IK-0855: 部品を理論操作グラフの段へ写して引いた記録なら、どの段の記録かを添える
            text += " " + str(data["target_note"])
        if data.get("target_label"):
            # app.js _ledgerTargetLabelCache（第 3 波・IK-0901）: 行の主語はサーバの表示名（同じ鍵）
            s.scratch.setdefault("ledger_target_label_cache", {})[key] = str(data["target_label"])
    cache[key] = text
    return text


def learner_ledger_target_label(session: PersonaSession, target_type: str, target_id: str) -> str:
    """app.js ``learnerLedgerTargetLabel``（第 3 波・IK-0901）: ``_learner_line`` が控えた表示名（無ければ空）。"""
    from uxsim.runner.state import learner_focus_params

    focus = learner_focus_params(session)
    key = f"{session.course_id}|{target_type}|{target_id}|" + "&".join(f"{k}={v}" for k, v in focus.items())
    return str((session.scratch.get("ledger_target_label_cache") or {}).get(key) or "")


_FORMULA_PLACEHOLDER = re.compile(r"^\[\[FORMULA_\d+\]\]$")


def sources_tab(ctx) -> None:
    """``annotateOneSourceCardLedger``: 出典カードごとに式（source-chunk の formulas）→ 主張（claim-refs）の順で最大 3 行。"""
    s = ctx.session
    sources = s.scratch.get("last_sources") or {}
    if not sources:
        return _missing(ctx, "answer_sources")
    out = ["出典タブ「参照した根拠」:"]
    seen_chunks: set[str] = set()
    for no, chunk in sources.items():
        if not chunk or chunk in seen_chunks:
            continue
        seen_chunks.add(chunk)
        combined: list[tuple[str, str, str, str]] = []  # (kind, id, prefix, label)
        st, data = ctx.call("GET", "/api/learning/courses/{course_id}/source-chunk/{chunk_id}", overrides={"chunk_id": chunk})
        ids: set[str] = set()
        for f in (data or {}).get("formulas") or [] if _status_ok(st) and isinstance(data, dict) else []:
            fid = str((f or {}).get("id") or "") if isinstance(f, dict) else ""
            if not fid or _FORMULA_PLACEHOLDER.match(fid) or fid.startswith("TOPIC_FORMULA_") or fid in ids:
                continue
            ids.add(fid)
            combined.append(("equation", fid, "数式 ", str(f.get("label") or "")))
        st2, cdata = ctx.call("GET", "/api/learning/courses/{course_id}/chunks/{chunk_id}/claim-refs", overrides={"chunk_id": chunk})
        for c in (cdata or {}).get("claims") or [] if _status_ok(st2) and isinstance(cdata, dict) else []:
            if isinstance(c, dict) and c.get("id"):
                combined.append(("claim", str(c["id"]), "主張 ", str(c.get("label") or "")))
        shown: list[str] = []
        for kind, tid, prefix, label in combined:
            if len(shown) >= 3:
                break
            text = _learner_line(ctx, kind, tid)
            if not text:
                continue
            # 第 3 波（IK-0901）: 主語はサーバの target_label を優先（式の TeX ラベル・空の主語で行を出さない）
            subject = learner_ledger_target_label(s, kind, tid) or label
            line = f"{prefix}{subject} — {text}" if subject else text
            if line not in shown:
                shown.append(line)
        out.append(f"- 出典{no} のカード:")
        out += [f"  {ln}" for ln in shown] if shown else [f"  {LEARNER_NO_LINE}"]
    s.scratch["ledger_lines_shown"] = out[1:]
    _show(ctx, "\n".join(out))


EXPLANATIONS_FAIL = "説明を取得できませんでした。"
EXPLANATIONS_EMPTY = "承認済みの説明はまだありません。"
EXPLANATIONS_CONNECT_FAIL = "サーバーに接続できません。"


def _material_component_mentions(session: PersonaSession) -> list[dict]:
    """教材区画の「◯◯の説明」ボタン（app.js: chunk.graph_mentions の element_type=component・区画ごと先頭 4 件）。"""
    out: list[dict] = []
    chunks = (session.material or {}).get("chunks") if isinstance(session.material, dict) else None
    for ch in chunks if isinstance(chunks, list) else []:
        if not isinstance(ch, dict):
            continue
        for m in (ch.get("graph_mentions") or [])[:4]:
            if isinstance(m, dict) and m.get("element_type") == "component" and m.get("element_id"):
                label = str(m.get("label") or m.get("surface_text") or m.get("element_id") or "この要素")
                if all(o["id"] != str(m["element_id"]) for o in out):
                    out.append({"id": str(m["element_id"]), "label": label})
    return out


def _center_component(session: PersonaSession) -> str:
    """直前に開いた部品の文脈の**中心**（下位ノードではない — 第 25 周 H2）。"""
    graph = session.scratch.get("last_component_graph")
    focus = graph.get("focus") if isinstance(graph, dict) and isinstance(graph.get("focus"), dict) else {}
    return str(focus.get("id") or session.scratch.get("component_center") or "")


def component_line(ctx) -> None:
    """``showComponentExplanations``: 教材の「◯◯の説明」ボタン（理論コンポーネント）で開く説明バージョンの
    ポップアップ。検証状態の一行は説明の下に添えられる（第 25 周 第 2 波: 承認済みの説明がゼロでも空の文の下に
    添える。取得失敗では一行の取得まで進まない）。⚓ の部品の文脈パネルには一行は無い。

    第 25 周 H2: 直前の文脈で見えた下位ノードの id（``latest("components")``）で台帳を引いていた。対象は
    ①引数 ②教材の「説明」ボタン（hint で選ぶ）③直前に開いた部品の文脈の中心、の順に決める。
    """
    s = ctx.session
    cid = str(ctx.args.get("component_id") or "").strip()
    label = ""
    mentions = _material_component_mentions(s)
    if not cid and mentions:
        picked = hint_pick(mentions, ctx.args.get("component_hint"), ("label", "id")) or mentions[0]
        cid, label = picked["id"], picked["label"]
    if not cid:
        cid = _center_component(s)
        if cid:
            _runner_note(s, "runner_choice: 教材に「◯◯の説明」ボタン（理論コンポーネント）が無いので、"
                            "直前に開いた部品の文脈の中心の部品で説明のポップアップを開いた")
    if not cid:
        return _missing(ctx, "component_id")
    head = f"「{label}の説明」のポップアップ（説明バージョン）:" if label else "部品の説明のポップアップ（説明バージョン）:"
    from uxsim.runner.state import learner_focus_params

    status, data = ctx.call("GET", "/api/learning/courses/{course_id}/components/{component_id}/explanations",
                            params=learner_focus_params(s) or None, overrides={"component_id": cid})
    if status is None:
        return _show(ctx, f"{head}\n{EXPLANATIONS_CONNECT_FAIL}")
    if not _status_ok(status):
        # IK-0861: 404（このコースの教材に無い部品）はサーバの事実文 detail を描く（"Course not found" は除く）
        detail = data.get("detail") if isinstance(data, dict) else None
        fail = detail if (status == 404 and isinstance(detail, str) and detail and detail != "Course not found") \
            else EXPLANATIONS_FAIL
        return _show(ctx, f"{head}\n{fail}")
    items = [e for e in (data or {}).get("explanations") or [] if isinstance(e, dict)] if isinstance(data, dict) else []
    if not items:
        empty = str(data.get("note") or "") if isinstance(data, dict) else ""
        # 第 2 波: 承認済みの説明がゼロでも、空の文の下に検証状態の一行を添える（app.js appendLearnerLedgerLine）
        text = _learner_line(ctx, "component", cid)
        tail = (f"説明の下の一行: {text}" if text else
                "（説明の下に検証状態の一行は出ていない — 画面は台帳の記帳が無い対象には何も出さない）")
        return _show(ctx, f"{head}\n{empty or EXPLANATIONS_EMPTY}\n{tail}")
    lines = [head]
    for e in items:
        who = "標準の説明" if e.get("kind") == "standard" else f"教員: {e.get('author_name') or '不明'}"
        lines.append(f"- {who}" + (f" ・ {e['endorsement_label']}" if e.get("endorsement_label") else ""))
        if e.get("title"):
            lines.append(f"  {e['title']}")
        if e.get("body"):
            lines.append(f"  {e['body']}")
    text = _learner_line(ctx, "component", cid)
    lines.append(f"説明の下の一行: {text}" if text else
                 "（説明の下に検証状態の一行は出ていない — 画面は台帳の記帳が無い対象には何も出さない）")
    _show(ctx, "\n".join(lines))


def learner_open_assumptions(ctx) -> None:
    status, data = ctx.main()
    head = f"「{LEARNER_OPEN_SUMMARY}」を開いた:"
    # IK-0853: 取得失敗と空を同じ文にしない（空の理由はサーバの empty_note。旧サーバは従来の文）
    if not _status_ok(status) or not isinstance(data, dict):
        return _show(ctx, f"{head}\n{LEARNER_OPEN_FAIL}")
    items = [i for i in data.get("items") or [] if isinstance(i, dict)]
    if not items:
        return _show(ctx, f"{head}\n{data.get('empty_note') or LEARNER_OPEN_EMPTY}")
    lines = [head]
    for it in items:
        meta = [NO_SCOPE if it.get("scope_count_is_zero") else "検証: 記帳あり"]
        if it.get("has_verification_proposal"):
            meta.append("検証提案あり")
        lines.append(f"- {it.get('statement') or it.get('target_id') or ''}（{' ・ '.join(meta)}）")
    if data.get("population_note"):
        # app.js（第 3 波・IK-0933）: 行の後に data-lx-population-note の 1 行（空のときは出さない）
        lines.append(str(data["population_note"]))
    _show(ctx, "\n".join(lines))


# ---------------------------------------------------------------------------
# ゼミ前ブリーフ（admin.js renderSeminarBrief の逐語ミラー — 第 25 周 H3: 汎用ダンプで available / target_type が出ていた）
# ---------------------------------------------------------------------------
SB_INTRO = "輪講の前に、この論文の「何が崩れたら危ういか」を確認するための読み取り専用ビューです。"
SB_UNAVAILABLE = "この教材ではゼミ前ブリーフを合成できません。"
SB_LOAD_FAIL = "ブリーフの読み込みに失敗しました"
SB_HANDOFF_EMPTY_NOTE = "このコースの学習者の詰まりの記録はまだありません。"
SB_STUMBLE_AXES = (("error_rate", "誤り率"), ("symbol_descent", "記号降下"),
                   ("verdict_self_check_divergence", "判定の乖離"), ("faq", "質問・誤解"))


def _sb_fragile_meta(item: dict) -> list[str]:
    facts = []
    if isinstance(item.get("load_level_label"), str) and item["load_level_label"]:
        facts.append("下流への影響: " + item["load_level_label"])
    if isinstance(item.get("verification_status"), str) and item["verification_status"] in STATUS_LABELS:
        facts.append("検証: " + STATUS_LABELS[item["verification_status"]])
    if isinstance(item.get("challenge_count_label"), str) and item["challenge_count_label"]:
        facts.append("疑義: " + item["challenge_count_label"])
    if isinstance(item.get("support_line_level"), str) and item["support_line_level"] in SUPPORT_LEVEL_BADGE_LABELS:
        facts.append("支持: " + SUPPORT_LEVEL_BADGE_LABELS[item["support_line_level"]])
    return facts


def _sb_stumble(stumble: Any) -> list[str]:
    if not isinstance(stumble, dict) or not stumble.get("has_data"):
        return []
    axes = stumble.get("axes") if isinstance(stumble.get("axes"), dict) else {}
    parts = []
    for key, name in SB_STUMBLE_AXES:
        value = axes.get(key)
        if key == "faq":
            value = value.get("questions") if isinstance(value, dict) else None
        if isinstance(value, str) and value:
            parts.append(f"{name}: {value}")
    return ["つまづき — " + " ・ ".join(parts)] if parts else []


def _sb_merged(item: dict) -> list[str]:
    return [item["merged_note"]] if isinstance(item.get("merged_note"), str) and item["merged_note"] else []


def _sb_row(statement: str, facts: list[str]) -> list[str]:
    return [f"- {statement}"] + [f"  {f}" for f in facts]


def seminar_brief_view(data: Any, title: str = "") -> str:
    """``renderSeminarBrief``。行の無い区画（脆い前提・一点吊り・晴れ間）は、サーバの ``empty_notes`` にその区画の
    文があれば見出し + 事実文で出し（IK-0853 の是正後の ``_sbSection(heading, rows, emptyNote)``）、無ければ
    見出しごと出ない（旧サーバ・旧画面）。第 4 区画だけは常に出る。"""
    lines = [f"ゼミ前ブリーフ: {title}", SB_INTRO]
    data = data if isinstance(data, dict) else {}
    if not data.get("available"):
        lines.append(str(data.get("reason") or SB_UNAVAILABLE))
        return "\n".join(lines)
    sections = (
        ("脆い前提", "fragile_assumptions", lambda it: _sb_fragile_meta(it) + _sb_stumble(it.get("stumble")) + _sb_merged(it)),
        ("一点吊りの支持線", "single_support_lines", lambda it: ([it["fact_line"]] if it.get("fact_line") else []) + _sb_merged(it)),
        ("晴れ間", "clear_skies", lambda it: ([it["fact_line"]] if it.get("fact_line") else []) + _sb_merged(it)),
    )
    empty_notes = data.get("empty_notes") if isinstance(data.get("empty_notes"), dict) else {}
    for heading, key, facts in sections:
        rows: list[str] = []
        for it in data.get(key) or []:
            if isinstance(it, dict) and it.get("statement"):
                rows += _sb_row(str(it["statement"]), [str(f) for f in facts(it)])
        note = empty_notes.get(key)
        if rows:
            lines.append(f"〔{heading}〕")
            lines += rows
        elif isinstance(note, str) and note:
            lines += [f"〔{heading}〕", note]
        if key == "fragile_assumptions" and data.get("population_note"):
            # renderSeminarBrief（第 3 波・IK-0933）: 脆い前提の区画の下に population_note を 1 行
            lines.append(str(data["population_note"]))
    handed = data.get("learner_handoff") if isinstance(data.get("learner_handoff"), dict) else {}
    lines.append("〔学習者からの問い〕")
    for it in handed.get("items") or []:
        if isinstance(it, dict) and it.get("statement"):
            lines += _sb_row(str(it["statement"]), [str(it["fact_line"])] if it.get("fact_line") else [])
    lines.append(str(handed.get("note") or SB_HANDOFF_EMPTY_NOTE))
    return "\n".join(lines)


def seminar_brief(ctx) -> None:
    s = ctx.session
    doc = str(ctx.args.get("document_ref") or "").strip() or _studio_doc(ctx)
    if not doc:
        return _missing(ctx, "document_id")
    materials = s.scratch.get("doubt_materials") or []
    title = next((m.get("title") or m.get("filename") or "" for m in materials if m.get("document_id") == doc), "")
    status, data = ctx.main(overrides={"document_ref": doc})
    if not _status_ok(status):
        return _show(ctx, f"ゼミ前ブリーフ: {title}\n{SB_INTRO}\n{SB_LOAD_FAIL}")
    _show(ctx, seminar_brief_view(data, title))


# ---------------------------------------------------------------------------
# わたしの地図「いまここの周り」（personal-map-home.js renderNearby — 第 25 周 H5: 応答のダンプで edges の
# theory_op_* が見えていた。画面は edges を線として描き、id を文字で出さない）
# ---------------------------------------------------------------------------
NEARBY_AXIS_NOTE = {"near": "上から下へ、前提 → いまここ → それに依存するもの の順に並んでいます。",
                    "root": "上から下へ、土台から順に前提が積み上がって「いまここ」に至ります。",
                    "range": "上から下へ、土台から順に前提が積み上がっています。"}
NEARBY_EMPTY_NOTICE = "この記録は、まだ論文の理論構成に結びついていません。"
NEARBY_LEGEND_LEDGER = ("凡例: 枠線の実線 = 検証の記帳がある / 点線 = このコーパスの中では検証記録がない")
NEARBY_LEGEND_RANGE = "凡例: 濃い = この話題が触れている場所 / 薄い = この論文のその他の理論構成"
NEARBY_LEGEND_KINDS = "凡例: 問い / 引っかかり / 再構成"
NEARBY_FAIL = "いまは表示できません。"


def _nearby_chip(n: dict, *, excerpt: bool) -> str:
    parts = [str(n.get("label") or "")]
    if excerpt and n.get("claim_excerpt"):
        parts.append(f"「{n['claim_excerpt']}」")
    ver = n.get("verification") if isinstance(n.get("verification"), dict) else None
    parts.append(f"［{ver.get('label')}］" if ver and ver.get("label") else "（枠線: 台帳の記帳なし）")
    if n.get("touched") is False:
        parts.append("（薄い）")
    if n.get("mine"):
        parts.append("（自分の記録の印あり）")
    return " ".join(p for p in parts if p)


def nearby_scope_lines(dto: dict) -> list[str]:
    """personal-map-home.js ``nearbyScopeLinesHtml``（第 3 波・IK-0932）: 札の下の一覧。表示しているノードのうち
    ``verification.scope_line`` があるものだけ「〈名前〉（〈札の語〉）— 〈scope_line〉」。旧サーバは何も出ない。"""
    nodes: list[dict] = []
    if str(dto.get("mode") or "near") == "range":
        for doc in dto.get("range_documents") or []:
            if isinstance(doc, dict):
                nodes += [n for n in doc.get("nodes") or [] if isinstance(n, dict)]
    else:
        nodes += [n for n in dto.get("root_path") or [] if isinstance(n, dict)] if dto.get("mode") == "root" else \
            [n for n in dto.get("upstream") or [] if isinstance(n, dict)]
        if isinstance(dto.get("center"), dict):
            nodes.append(dto["center"])
        if dto.get("mode") != "root":
            nodes += [n for n in dto.get("downstream") or [] if isinstance(n, dict)]
    out = []
    for n in nodes:
        ver = n.get("verification") if isinstance(n.get("verification"), dict) else {}
        if ver.get("scope_line"):
            out.append(f"・{n.get('label') or ''}（{ver.get('label') or ''}）— {ver['scope_line']}")
    return out


def nearby_view(dto: Any) -> str:
    dto = dto if isinstance(dto, dict) else {}
    lines = ["わたしの地図 — いまここの周り:"]
    if dto.get("available") is False:
        lines.append(str(dto.get("notice") or NEARBY_EMPTY_NOTICE))
        if dto.get("facts"):
            lines.append("この記録について:")
            lines += [f"- {f}" for f in dto["facts"] if isinstance(f, str)]
        return "\n".join(lines)
    mode = str(dto.get("mode") or "near")
    lines.append(NEARBY_AXIS_NOTE.get(mode, ""))
    if mode == "range":
        lines.append("コースのソース論文の理論構成" if dto.get("range_fallback") else "この話題が触れている範囲")
        for doc in dto.get("range_documents") or []:
            if not isinstance(doc, dict):
                continue
            if doc.get("title"):
                lines.append(f"〔{doc['title']}〕")
            lines += [f"- {_nearby_chip(n, excerpt=True)}" for n in doc.get("nodes") or [] if isinstance(n, dict)]
    else:
        rows: list[tuple[str, list]] = []
        if mode == "root":
            rows += [("土台" if i == 0 else "", [n]) for i, n in enumerate(dto.get("root_path") or [])]
        elif dto.get("upstream"):
            rows.append(("これが前提にしていること", list(dto["upstream"])))
        rows.append(("いまここ", [dto.get("center") or {}]))
        if dto.get("downstream"):
            rows.append(("これに依存していること", list(dto["downstream"])))
        for label, nodes in rows:
            chips = " / ".join(_nearby_chip(n, excerpt=False) for n in nodes if isinstance(n, dict))
            lines.append(f"{label + ': ' if label else '  '}{chips}")
    lines += nearby_scope_lines(dto)
    if dto.get("ledger_available"):
        lines.append(NEARBY_LEGEND_LEDGER)
    if mode == "range" and not dto.get("range_fallback"):
        lines.append(NEARBY_LEGEND_RANGE)
    lines.append(NEARBY_LEGEND_KINDS)
    facts = [f for f in dto.get("facts") or [] if isinstance(f, str)]
    if facts:
        lines.append("この場所について:")
        lines += [f"- {f}" for f in facts]
    return "\n".join(lines)


def nearby(ctx) -> None:
    """``learning.personal_network.nearby``（actions_exec ``_network_node`` と同じ手順 + 画面の投影）。"""
    s = ctx.session
    if not (ctx.args.get("node_id") or s.latest("nodes")):
        _, body = ctx.call("GET", "/api/me/personal-network")
        for n in (body or {}).get("nodes") or [] if isinstance(body, dict) else []:
            if isinstance(n, dict) and (n.get("id") or n.get("node_id")):
                s.remember("nodes", n.get("id") or n.get("node_id"))
    params = {"node_id": ctx.args.get("node_id") or s.latest("nodes")}
    if not params["node_id"]:
        return _show(ctx, "わたしの地図 — いまここの周り:\nまだ論文の理論構成に結びついた記録がありません。"
                          "教材のどこについての記録か決めると、ここに現れます。")
    for k in ("mode", "center_component_id"):
        if ctx.args.get(k):
            params[k] = ctx.args[k]
    status, dto = ctx.main(params=params)
    if status == 404:
        return _show(ctx, "わたしの地図 — いまここの周り:\n" + NEARBY_EMPTY_NOTICE)
    if not _status_ok(status):
        return _show(ctx, "わたしの地図 — いまここの周り:\n" + NEARBY_FAIL)
    _show(ctx, nearby_view(dto))


HANDLERS: dict[str, Callable[[Any], None]] = {
    "admin.doubt.tab_open": tab_open,
    "admin.doubt.refresh": refresh,
    "admin.doubt.audit_run": _course_post("/courses/{course_id}/corpus-audit/run", _audit_done),
    "admin.doubt.mining_run": _course_post("/courses/{course_id}/assumption-mining/run", _audit_done),
    "admin.doubt.load_recompute": _course_post("/courses/{course_id}/load/recompute", _audit_done),
    "admin.doubt.assumption_filter": assumption_filter,
    "admin.doubt.assumption_confirm": assumption_confirm,
    "admin.doubt.assumption_dismiss": assumption_dismiss,
    "admin.doubt.ledger_open": ledger_open,
    "admin.doubt.open_reachable_filter": open_reachable_filter,
    "admin.doubt.studio_node_ledger": studio_node_ledger,
    "admin.doubt.supporting_claim_ledger": supporting_claim_ledger,  # 第 2 波（IK-0904）
    "admin.doubt.scope_record": scope_record,
    "admin.doubt.scope_candidate_decide": scope_candidate_decide,
    "admin.doubt.verification_status": verification_status,
    "admin.doubt.challenge_record": challenge_record,
    "admin.doubt.challenge_withdraw": challenge_withdraw,
    "admin.doubt.challenge_propose": challenge_propose,
    "admin.doubt.proposal_status": proposal_status,
    "admin.doubt.falsification_record": falsification_record,
    "admin.doubt.falsification_candidate_decide": falsification_candidate_decide,
    "admin.doubt.falsification_refresh": falsification_refresh,
    "admin.doubt.counterfactual_toggle": counterfactual_toggle,
    "admin.doubt.counterfactual_observation": counterfactual_observation,
    "admin.doubt.counterfactual_save": counterfactual_save,
    "learning.ledger.sources_tab": sources_tab,
    "learning.ledger.component_line": component_line,
    "learning.ledger.open_assumptions": learner_open_assumptions,
    "admin.seminar_brief.view": seminar_brief,  # 第 25 周 H3
    "learning.personal_network.nearby": nearby,  # 第 25 周 H5
}


def execute(action_id: str, ctx: Any) -> bool:
    """面 B の行為なら実行して True（``actions_exec._Ctx`` を受ける）。"""
    handler = HANDLERS.get(action_id)
    if handler is None:
        return False
    handler(ctx)
    return True


def ui_only(action_id: str, session: PersonaSession) -> bool:
    """面 B に API の無い行為は無い（到達可能フィルタも行為としては一覧の GET を宣言している）。"""
    return False
