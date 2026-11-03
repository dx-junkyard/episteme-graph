"""行為の実行（行為レジストリ ``REGISTRY`` の id → 製品 API 呼び出し）。

- path の穴（``{course_id}`` 等）は引数 → ``PersonaSession`` の既知 ID の順で埋める。埋まらなければ
  HTTP を出さず ``precondition:`` の trace を残す（製品の欠陥ではなく、画面にまだ無い状態）。
- 未登録・unsupported の行為は ``unsupported:<id>`` の trace だけを残す（PE9）。
- 最後の呼び出しの結果を ``session.last_status`` / ``session.last_body`` に置く（投影の材料）。
"""
from __future__ import annotations

import re
from typing import Any, Callable, Optional

from uxsim.actions import registry
from uxsim.runner.client import EpistemeClient
from uxsim.runner.state import PersonaSession, is_pseudo_chunk_id, material_anchors, material_sentences
from uxsim.schema import HttpTrace
from uxsim.runner import vision5_exec as _vision5

DISCUSSION_TOPIC_ID = "_discussion"  # 製品 routes/learning.py の予約疑似トピック
HISTORY_WINDOW = 20

_HOLE_RE = re.compile(r"\{([a-z_]+)\}")


class _Missing(Exception):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


def fill_path(template: str, session: PersonaSession, args: dict, overrides: Optional[dict] = None) -> str:
    """path テンプレートの穴を埋める。埋まらない穴があれば ``_Missing``。"""
    values = dict(overrides or {})

    def repl(m: re.Match) -> str:
        name = m.group(1)
        v = values.get(name) or session.resolve(name, args)
        if not v:
            raise _Missing(name)
        return str(v)

    return _HOLE_RE.sub(repl, template)


def path_holes(template: str) -> list[str]:
    return _HOLE_RE.findall(template)


class _Ctx:
    """1 行為ぶんの実行文脈。"""

    def __init__(self, action: registry.Action, args: dict, session: PersonaSession, client: EpistemeClient):
        self.action, self.args, self.session, self.client = action, dict(args or {}), session, client

    def call(self, method: str, template: str, *, json: Any = None, params: Optional[dict] = None,
             overrides: Optional[dict] = None) -> tuple[Optional[int], Any]:
        try:
            path = fill_path(template, self.session, self.args, overrides)
        except _Missing as miss:
            self.client.traces.append(HttpTrace(method=method, path=template, status=None,
                                                error=f"precondition:{miss.name}"))
            self.session.last_status, self.session.last_body = None, {"_precondition": miss.name}
            note_not_sent(self.session, method, template, miss.name)
            return None, None
        status, body, _ = self.client.call(method, path, json=json, params=params)
        self.session.last_status, self.session.last_body = status, body
        return status, body

    def main(self, *, json: Any = None, params: Optional[dict] = None, overrides: Optional[dict] = None):
        method, template = self.action.api  # type: ignore[misc]
        return self.call(method, template, json=json, params=params, overrides=overrides)

    def missing(self, name: str) -> None:
        """必須の引数が画面にまだ無い（HTTP を出さずに precondition を記録する）。"""
        method, template = self.action.api  # type: ignore[misc]
        self.client.traces.append(HttpTrace(method=method, path=template, status=None, error=f"precondition:{name}"))
        self.session.last_status, self.session.last_body = None, {"_precondition": name}
        note_not_sent(self.session, method, template, name)

    def body_args(self) -> dict:
        """path の穴に使った引数を除いた残り（既定の JSON / query）。"""
        holes = set(path_holes(self.action.api[1])) if self.action.api else set()
        return {k: v for k, v in self.args.items() if k not in holes}


#: 第 25 周 第 8 周 atlas H46: runner が前提（下書きなし・対象なし）で送らなかった手は、HTTP 行が path の雛形
#: （``{cartridge_id}`` 等）のまま status なしで残る。審判が「製品が 404 を返した」と読み違えないよう、送らなかったことと
#: 理由を runner_notes に 1 行で残す（HTTP 行自体は審判の precondition 判定が使うので消さない）
NOT_SENT_NOTE = ("（ハーネス: 送らなかった — {method} {path}（前提「{name}」が画面に無い）。この手の HTTP 行は雛形の path の"
                 "ままで status が無く、製品は応答していない。これは画面の文ではない）")


def note_not_sent(session: PersonaSession, method: str, template: str, name: str) -> None:
    session.scratch.setdefault("runner_notes", []).append(
        NOT_SENT_NOTE.format(method=method, path=template, name=name))


def unavailable_reason(action_id: str, session: PersonaSession) -> Optional[str]:
    """画面の状態で、この行為の対象が画面に無いなら理由（無ければ None）。第 25 周 H26（第 4 周）。

    面ごとの ``unavailable(action_id, session)`` に委ねる（定義していない面は常に None = 外さない）。
    画面の状態をまだ読んでいない（一覧を開いていない）ときは外さない — 外すのは「画面に無いと分かっている」ときだけ。
    """
    for m in (_atlas_exec, _share_exec, _doubt_exec, _studio_exec, _vision5):
        fn = getattr(m, "unavailable", None)
        reason = fn(action_id, session) if fn is not None else None
        if reason:
            return str(reason)
    return None


def execute(action_id: str, args: dict, session: PersonaSession, client: EpistemeClient) -> list[HttpTrace]:
    """行為を 1 つ実行し、そのステップの ``HttpTrace`` 列を返す。"""
    client.take_traces()
    action = registry.get(action_id)
    if action is None or action.unsupported or action.api is None:
        session.last_status, session.last_body = None, None
        if action is not None:
            # 画面だけの切り替え（例: 精読モードのトグル）は、画面の事実文と runner_note に置き換える（第 23 周）
            if not _vision5.ui_only(action_id, session):  # r25: 面ごとの画面だけの切り替え
                for _m in (_atlas_exec, _doubt_exec, _studio_exec):
                    if _m.ui_only(action_id, session):
                        break
            session.last_action = action_id
        return [HttpTrace(method="-", path=f"unsupported:{action_id}", error="unsupported")]
    ctx = _Ctx(action, args, session, client)
    handler = _HANDLERS.get(action_id, _default)
    if action_id not in OPEN_CONTEXT_KEEPERS and not action_id.startswith("learning.chat."):
        # 画面では別の操作のクリック（mousedown）が部品の文脈のポップオーバーを閉じる（_srcOutsideClose）
        session.scratch.pop("open_context_element", None)
    try:
        handler(ctx)
    finally:
        session.last_action = action_id
    return client.take_traces()


# ----------------------------------------------------------------------------
# 既定と共通処理
# ----------------------------------------------------------------------------

def _default(ctx: _Ctx) -> None:
    method = ctx.action.api[0]  # type: ignore[index]
    extra = ctx.body_args()
    if method == "GET":
        ctx.main(params=extra)
    else:
        ctx.main(json=extra)


def _items(body: Any, *keys: str) -> list:
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for k in keys:
            v = body.get(k)
            if isinstance(v, list):
                return v
    return []


def _first(d: dict, *keys: str) -> str:
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return str(d[k])
    return ""


# ----------------------------------------------------------------------------
# 学習側
# ----------------------------------------------------------------------------

def remember_course_rows(session: PersonaSession, body: Any) -> None:
    """コース一覧の表示順（マイコース → 受講可能・各 20 件まで = state.py の投影と同じ）を覚える（第 25 周 H23）。"""
    rows = [c for c in _items(body, "courses")[:20] if isinstance(c, dict) and c.get("id")]
    ordered = [c for c in rows if not c.get("is_enrollable")] + [c for c in rows if c.get("is_enrollable")]
    session.scratch["course_list_rows"] = [{"id": str(c["id"]), "title": str(c.get("title") or ""),
                                            # 第 3 波（IK-0945）: 受付が止まったマイコースの事実文（コースを開いたとき出す）
                                            "enrollment_notice": str(c.get("enrollment_notice") or ""),
                                            "enrollable": bool(c.get("is_enrollable"))}
                                           for c in ordered]


def resolve_course_hint(session: PersonaSession, hint: Any) -> Any:
    """``course_id`` の引数を一覧の表示から引く（第 25 周 H23）。投影はコースの id を出さないので、頭脳は
    表示順の番号（「2」「2.」）か題名で指す。既知の id・8 文字以上の id の頭はそのまま／一覧の id に引き直す。
    引けなければ渡された値のまま（製品は 404 を返す — それは観測）。"""
    h = str(hint or "").strip()
    rows = session.scratch.get("course_list_rows") or []
    if not h or not rows:
        return hint
    if any(r["id"] == h for r in rows):
        return h
    m = re.fullmatch(r"(?:No\.?\s*|#)?(\d{1,3})\.?", h)
    if m:
        i = int(m.group(1))
        return rows[i - 1]["id"] if 1 <= i <= len(rows) else hint
    if len(h) >= 8:
        hit = [r for r in rows if r["id"].startswith(h)]
        if len(hit) == 1:
            return hit[0]["id"]
    key = h.casefold()
    exact = [r for r in rows if r["title"].strip().casefold() == key]
    part = exact or [r for r in rows if key in r["title"].casefold()]
    return part[0]["id"] if part else hint


def _course_list(ctx: _Ctx) -> None:
    status, body = ctx.main()
    remember_course_rows(ctx.session, body)
    for c in _items(body, "courses"):
        if isinstance(c, dict) and c.get("id"):
            ctx.session.remember("enrollable" if c.get("is_enrollable") else "courses", c["id"])


PIN_STRICT_NOTE = ("runner_fixed: 経路が pin_course を宣言しているので、campaign が固定した course_id={pinned} で入った"
                   "（ペルソナの選んだ {chosen} は使わない）。一覧に同名のコースが並んでいても、検証したいのは固定したコース"
                   "（同名コースの判別不能そのものは IK-0407。砂場準備: uxsim/sandbox/pin_course.py）")


def pinned_course_override(session: PersonaSession, cid: Any) -> Any:
    """経路が ``pin_course: true``（runner/api.py が ``scratch["pin_course_strict"]`` に立てる）なら campaign の
    course_id に差し替え、差し替えたことを runner_note に残す（第 25 周 H6: 同名の別コースを受講して進んだ）。"""
    pinned = str(session.scratch.get("pinned_course_id") or "")
    if not session.scratch.get("pin_course_strict") or not pinned or str(cid or "") == pinned:
        return cid
    runner_note(session, PIN_STRICT_NOTE.format(pinned=pinned, chosen=cid or "（指定なし）"))
    # 第 25 周 studio H23（第 4 周）: 差し替えを頭脳の観察にも出す（runner_note は頭脳に見えない）。出さないと、頭脳は
    # 自分の選んだ行（例: マイコースの同名コース）の知らせとして「受講を開始しました」を読んだ
    session.scratch.setdefault("observation_notes", []).append(pinned_swap_observation(session, pinned))
    return pinned


def pinned_swap_observation(session: PersonaSession, pinned: str) -> str:
    """固定コースへ差し替えたことを、一覧のどの行かで言う（id は出さない）。"""
    from uxsim.runner.state import COURSE_GROUP_ENROLLABLE, COURSE_GROUP_MINE

    rows = session.scratch.get("course_list_rows") or []
    hit = next(((i, r) for i, r in enumerate(rows, 1) if r.get("id") == pinned), None)
    if hit is None:
        return "ハーネス: 選んだコースではなく、この場面で固定したコースへ入った（一覧の行は特定できない）。これは画面の文ではない"
    i, r = hit
    group = COURSE_GROUP_ENROLLABLE if r.get("enrollable") else COURSE_GROUP_MINE
    return (f"ハーネス: 選んだコースではなく、この場面で固定したコースへ入った — 一覧の「{group}」の {i}. "
            f"「{r.get('title') or ''}」の行。以降の画面はこのコース。これは画面の文ではない")


def _course_enroll(ctx: _Ctx) -> None:
    cid = (resolve_course_hint(ctx.session, ctx.args.get("course_id")) or ctx.session.latest("enrollable")
           or ctx.session.latest("courses"))
    cid = pinned_course_override(ctx.session, cid)
    status, _ = ctx.main(overrides={"course_id": cid})
    if status and status < 300 and cid:
        ctx.session.course_id = cid
        ctx.session.remember("courses", cid)
        note_pinned_course_mismatch(ctx.session, cid, "learning.course.enroll")


def runner_note(session: PersonaSession, note: str) -> None:
    """runner が自分で補ったこと（ペルソナの判断ではない）を次に書くステップへ残す。"""
    session.scratch.setdefault("runner_notes", []).append(note)


PINNED_MISMATCH_MARK = "pinned_course_mismatch"


def note_pinned_course_mismatch(session: PersonaSession, course_id: str, action_id: str) -> bool:
    """campaign が ``course_id`` を固定しているのに、ペルソナが別のコースを受講登録・表示したら記録する。

    ペルソナの選択は差し替えない（それ自体が画面の観測 — 同名コースが並べば人も取り違える）。
    代わりに runner_note（``pinned_course_mismatch:`` で始まる）と ``scratch["pinned_course_mismatch"]``
    に残し、api.py が meta に、審判（dialogue f）が発見に上げる。これが無いと、固定したコースを
    一度も読んでいない週が「是正が効いていない」という偽の検証結果として読まれる（第 12 周 = IK-0506）。
    同じ (固定, 実際) の組は 1 セッション 1 回だけ記録する。
    """
    pinned = str(session.scratch.get("pinned_course_id") or "")
    cid = str(course_id or "")
    if not pinned or not cid or cid == pinned:
        return False
    seen = session.scratch.setdefault("pinned_course_mismatch", [])
    if any(m.get("actual") == cid for m in seen):
        return False
    seen.append({"pinned": pinned, "actual": cid, "action_id": action_id})
    runner_note(session, f"{PINNED_MISMATCH_MARK}: campaign が固定した course_id={pinned} ではなく {cid} を"
                         f"ペルソナが選んだ（{action_id}）。選択は差し替えていない — このセッションの内容依存の検証は"
                         "固定したコースを見ていない")
    return True


def default_topic_id(topics: dict[str, dict]) -> str:
    """最初の ``in_progress`` のトピック（無ければ先頭）。学習画面がコースを開いたときに選ぶトピック。"""
    for tid, t in topics.items():
        if isinstance(t, dict) and t.get("status") == "in_progress":
            return tid
    return next(iter(topics), "")


def _load_course(ctx: _Ctx, course_id: str, *, default_topic: bool = True) -> tuple[Optional[int], Any]:
    status, body = ctx.call("GET", "/api/learning/courses/{course_id}", overrides={"course_id": course_id})
    if status == 200 and isinstance(body, dict):
        master = body.get("master_course") if isinstance(body.get("master_course"), dict) else body
        s = ctx.session
        switched = s.course_id != course_id
        s.course_id = course_id
        note_pinned_course_mismatch(s, course_id, "learning.course.open")
        s.topics = {str(t.get("id")): t for t in master.get("topics") or [] if isinstance(t, dict)}
        s.topic_ids = list(s.topics)
        s.scratch["chapter_titles"] = [str(c.get("title") or "") if isinstance(c, dict) else str(c)
                                       for c in master.get("chapters") or []]
        for src in master.get("sources") or []:
            if isinstance(src, dict) and src.get("material_id"):
                s.remember("materials", src["material_id"])
        # topic_id が無い（または別のコースのもの）なら、画面と同じく最初の in_progress を既定にする
        # （第 7 周: トピックを開く前のチャットが precondition:topic_id で止まった）
        if default_topic and s.topics and (switched or not s.topic_id or s.topic_id not in s.topics):
            tid = default_topic_id(s.topics)
            if tid and tid != s.topic_id:
                s.topic_id = tid
                runner_note(s, f"runner_default: topic_id={tid}（コースを開いたときの最初の in_progress トピック）")
    return status, body


def prefetch_courses(session: PersonaSession, client: EpistemeClient) -> tuple[list[HttpTrace], list[str]]:
    """学生のセッション開始時に ``GET /api/learning/courses`` を 1 回呼び、コース ID を覚える（runner の手）。

    既定のコースは ①受講中・所有（一覧の ``is_enrollable`` が偽 — 製品は own / enrolled を
    ``is_enrollable=False`` で返す）の先頭 ②無ければ受講可能（``is_enrollable`` 真）の先頭。①は
    ``session.course_id`` に置き、②は候補として ``scratch["default_course_id"]`` に置くだけで受講登録はしない。
    前のセッションから引き継いだ course_id があれば変えない。
    """
    traces = execute("learning.course.list", {}, session, client)
    notes = ["runner_driven: セッション開始時にコース一覧を取得した（ペルソナの判断ではない）"]
    rows = [c for c in _items(session.last_body, "courses") if isinstance(c, dict) and c.get("id")]
    own = [str(c["id"]) for c in rows if not c.get("is_enrollable")]
    avail = [str(c["id"]) for c in rows if c.get("is_enrollable")]
    pinned = str(session.scratch.get("pinned_course_id") or "")
    if pinned and pinned in own:
        session.course_id = pinned
        notes.append(f"runner_default: campaign の course_id={pinned}（受講中）を使う")
    elif pinned and pinned in avail:
        session.scratch["default_course_id"] = pinned
        notes.append(f"runner_default: campaign の course_id={pinned} を受講可能な候補にした（受講登録はしていない）")
    elif session.course_id:
        notes.append(f"runner_default: 前のセッションの course_id={session.course_id} を引き継いだ")
    elif own:
        session.course_id = own[0]
        notes.append(f"runner_default: course_id={own[0]}（受講中・所有のコースの先頭）")
    elif avail:
        session.scratch["default_course_id"] = avail[0]
        notes.append(f"runner_default: 受講可能なコース {avail[0]} を既定の候補にした（受講登録はしていない）")
    return traces, notes


ENROLL_BEFORE_OPEN_NOTE = ("runner: 選んだコースはコース一覧の「新しく受講可能なコース」側にあった — 製品の画面と"
                           "同じく受講の確認で[受講する]を押してから開いた（app.js openEnrollConfirm → enrollCourse）")


def _enroll_before_open(ctx: _Ctx, cid: str) -> bool:
    """未受講のコースを開くとき、製品と同じく受講登録してから開く（H6・第 25 周）。

    app.js はコース選択で「新しく受講可能なコース」側を選ぶと、受講の確認（[受講する]）→
    ``POST /learning/courses/{id}/enroll`` → 一覧の読み直し → 切り替え、の順に進む。runner が GET だけを出すと
    未受講のコースは 404 になり、場面が 1 操作も進まずに終わった。受講に失敗したら開かない（False）。
    """
    s = ctx.session
    if cid not in s.all("enrollable") or cid in s.all("courses") or cid == s.course_id:
        return True
    status, body = ctx.call("POST", "/api/learning/courses/{course_id}/enroll", overrides={"course_id": cid})
    if not status or status >= 300:
        return False
    s.remember("courses", cid)
    runner_note(s, ENROLL_BEFORE_OPEN_NOTE)
    if isinstance(body, dict) and body.get("enrolled") and body.get("notice"):
        # 画面は切り替えの後に受講が成立した事実文（サーバ正本）を一度だけ出す（IK-0405）
        s.scratch.setdefault("observation_notes", []).append(str(body["notice"]))
    note_pinned_course_mismatch(s, cid, "learning.course.enroll")
    return True


def _course_open(ctx: _Ctx) -> None:
    s = ctx.session
    cid = (resolve_course_hint(s, ctx.args.get("course_id")) or s.course_id or s.latest("courses")
           or s.scratch.get("default_course_id") or s.latest("enrollable"))
    cid = pinned_course_override(s, cid)
    if not cid:
        ctx.call("GET", "/api/learning/courses/{course_id}")  # precondition を記録させる
        return
    if not _enroll_before_open(ctx, str(cid)):
        return
    status, body = _load_course(ctx, cid)
    if status != 200 or not isinstance(body, dict):
        return
    # 第 23 周: 画面（app.js loadAndRenderCourse → loadReturnDoor）はコースを開くたびに帰還の扉を 1 回読み、
    # 非空ならコースの画面の最上部に出す。投影がコースの目次だけを見せ、扉が無いように読めていた。
    s = ctx.session
    st2, door = ctx.call("GET", "/api/learning/courses/{course_id}/cycle/return-door", overrides={"course_id": cid})
    s.last_status, s.last_body = status, body
    if st2 == 200 and isinstance(door, dict) and not door.get("empty"):
        if isinstance(door.get("carryover"), dict):
            s.remember("carryover_traces", door["carryover"].get("trace_id"))
        s.last_body = {**body, "_return_door": door}
    # app.js loadAndRenderCourse → showCourseEnrollmentNotice（第 3 波・IK-0945）: 一覧の行の enrollment_notice を
    # コース選択の近く（#course-enroll-notice）に出す。一覧を読んでいなければ何も出さない（画面も一覧の行から引く）
    row = next((r for r in s.scratch.get("course_list_rows") or [] if r.get("id") == str(cid)), None)
    if row and row.get("enrollment_notice"):
        s.last_body = {**s.last_body, "_enrollment_notice": row["enrollment_notice"]}


def _next_topic(session: PersonaSession) -> str:
    opened = set(session.all("opened_topics"))
    for tid in session.topic_ids:
        if tid not in opened:
            return tid
    return session.topic_ids[0] if session.topic_ids else ""


def _topic_by_hint(session: PersonaSession, terms: Any) -> str:
    """経路の材料 ``topic_hint_terms``（語の列）に題名か章題が当たる最初のトピック。無ければ空。

    分野の材料（質問の種）がコースの特定の章を前提にしているとき、既定の「最初の in_progress」では
    材料とトピックが噛み合わない（第 8 周: 宇宙論の質問を Cep B のトピックで投げていた）。
    """
    if not terms or not session.topics:
        return ""
    words = [str(t).strip() for t in (terms if isinstance(terms, list) else str(terms).split("、")) if str(t).strip()]
    if not words:
        return ""
    chapters = session.scratch.get("chapter_titles") or []
    for tid, t in session.topics.items():
        if not isinstance(t, dict):
            continue
        title = str(t.get("title") or "")
        ci = t.get("chapter_index")
        chapter = str(chapters[ci]) if isinstance(ci, int) and 0 <= ci < len(chapters) else ""
        if any(w in title or w in chapter for w in words):
            runner_note(session, f"runner_default: topic_id={tid}（材料 topic_hint_terms に合う最初のトピック）")
            return tid
    return ""


def _topic_open(ctx: _Ctx) -> None:
    s = ctx.session
    if s.course_id and not s.topic_ids:
        _load_course(ctx, s.course_id, default_topic=False)  # このあと開くトピックが決まるので既定は置かない
    tid_arg = str(ctx.args.get("topic_id") or "").strip()
    if tid_arg and s.topic_ids and tid_arg not in s.topic_ids:
        # 第 25 周 第 10 波（share H60）: 場面の既定の topic_id（t1）がこのコースに無いときは送らず、題名の語で選び直す
        runner_note(s, f"runner_default: 材料の topic_id={tid_arg} はこのコースのトピックに無い — 題名の語で選び直した")
        tid_arg = ""
    tid = tid_arg or _topic_by_hint(s, ctx.args.get("topic_hint_terms")) or _next_topic(s)
    status, body = ctx.main(overrides={"topic_id": tid} if tid else None)
    if status == 200 and tid:
        s.topic_id = tid
        s.remember("opened_topics", tid)
        s.material = body if isinstance(body, dict) else {}
        s.scratch.pop("personal_map_seen", None)  # app.js: トピックを読み込むと state.personalMapSeen = false
        s.scratch.pop("atlas_node_fresh", None)  # 第 10 波（FD-note 4）: トピックを移ったら地図の「今の選択」を落とす
        s.scratch.pop("personal_map_view", None)  # 第 10 波（IK-1113）: トピックを開くのはわたしの地図を閉じた後
        for ch in _items(body, "chunks"):
            if not isinstance(ch, dict):
                continue
            # トピック教材の区画は擬似 id（製品 learning.py の ``topic:{topic_id}``）を持つ。実在のチャンクでは
            # ないので覚えない（第 17 周: claim-refs に渡して 404 の偽所見になった）
            if not is_pseudo_chunk_id(ch.get("id")):
                s.remember("chunks", ch.get("id"))
            for f in ch.get("formulas") or []:
                if isinstance(f, dict):
                    s.remember("equations", _first(f, "equation_id", "id"))
                    s.remember("symbols_seen", _first(f, "latex", "plain_text"))
            for ev in ch.get("evidence_items") or []:
                if isinstance(ev, dict) and ev.get("kind") in ("claim", "equation", "component") and ev.get("id"):
                    s.remember("element_types", ev["kind"])
                    s.remember("elements", ev["id"])
                    if ev["kind"] == "component":
                        s.remember("components", ev["id"])


def _chat(ctx: _Ctx, *, topic_id: Optional[str] = None, template: Optional[str] = None,
          overrides: Optional[dict] = None, **extra: Any) -> None:
    s = ctx.session
    tid = topic_id or s.topic_id
    history = s.history_for(tid)
    message = str(ctx.args.get("message") or "").strip()
    msg_id = s.next_message_id()
    body: dict[str, Any] = {"message": message, "history": [dict(role=m["role"], content=m["content"])
                                                           for m in history[-HISTORY_WINDOW:]],
                            "message_id": msg_id}
    for k in ("selection_text", "screen_mode"):
        if ctx.args.get(k):
            body[k] = ctx.args[k]
    body.update({k: v for k, v in extra.items() if v not in (None, "")})
    screen = simulation_screen_context(s, ctx.args.get("simulation_run_id"), tid,
                                       mode=str(body.get("screen_mode") or "chat"))
    opened = open_context_selection(s, tid, voice=str(body.get("screen_mode") or "") == "voice")
    if screen is None and (opened or s.scratch.get("personal_map_seen") or s.scratch.get("atlas_last_node")
                           or s.scratch.get("personal_map_view")):
        screen = learning_screen_context(s, tid, mode=str(body.get("screen_mode") or "chat"))
    if screen is not None and opened:
        screen["selection"].update(opened)
    if screen is not None:
        screen["selection"].update(screen_selection_refs(s))
    else:
        s.scratch.pop("atlas_node_fresh", None)  # 送った往復で落とす（app.js は getScreenContext の中で落とす）
    if screen is not None:
        body["screen_context"] = screen
    ov = dict(overrides or {})
    ov.setdefault("topic_id", tid)
    s.scratch["last_chat_topic"] = tid  # 書き直し・削除の既定の会話（画面で直前に話していた会話）
    if template:
        status, resp = ctx.call("POST", template, json=body, overrides=ov)
    else:
        status, resp = ctx.main(json=body, overrides=ov)
    if status == 200 and isinstance(resp, dict):
        history.append({"role": "user", "content": message, "id": msg_id})
        history.append({"role": "assistant", "content": str(resp.get("answer", ""))})
        s.remember("user_messages", msg_id)
        s.last_answer = str(resp.get("answer", ""))
        s.scratch["last_sources"] = {}
        for src in resp.get("sources") or []:
            if isinstance(src, dict) and not is_pseudo_chunk_id(src.get("chunk_id")):
                s.remember("chunks", src.get("chunk_id"))
                s.remember("source_chunks", src.get("chunk_id"))  # 画面の出典カード（claim-refs の宛先）
                if src.get("chunk_id"):
                    s.scratch["last_sources"][str(src.get("index") or len(s.scratch["last_sources"]) + 1)] = str(src["chunk_id"])
        confirm = resp.get("anchor_confirm")
        if isinstance(confirm, dict):
            s.remember("anchor_traces", confirm.get("trace_id"))


# 音声中心のペルソナ（行動型の ``uses_voice: mostly`` 等 — state.persona_uses_voice）は、学習画面の
LATEST_RUN_WORDS = ("latest", "最新", "いまの実行", "current")


def simulation_screen_context(session: Any, run_ref: Any, topic_id: Optional[str], *, mode: str = "chat") -> Optional[dict]:
    """「🧪 動かして確かめる」で実行を開いたまま送るときの ``screen_context``（app.js ``getScreenContext`` と同じ形）。

    画面は開いている自分の実行の id だけを ``selection.simulation_run_id`` に載せる（SA層 4-e・事実文にするのは
    サーバ）。台本の ``with: simulation_run_id: latest`` は画面の記録の先頭（最新）の実行を指す。指定が無い・
    記録が無いときは None（キーごと送らない — 開いていない画面と同じ）。
    """
    ref = str(run_ref or "").strip()
    if not ref:
        return None
    if ref.lower() in LATEST_RUN_WORDS:
        rows = session.scratch.get("sim_runs_dto") if isinstance(session.scratch.get("sim_runs_dto"), list) else []
        ref = str((rows[0] or {}).get("id") or "") if rows and isinstance(rows[0], dict) else ""
        if not ref:
            return None
    selection: dict[str, Any] = {"course_id": str(session.course_id or ""), "topic_id": str(topic_id or ""),
                                 "kind": "topic", "simulation_run_id": ref}
    doc = (session.material or {}).get("document_id") if isinstance(session.material, dict) else None
    if doc:
        selection["document_id"] = str(doc)
    view: dict[str, Any] = {"mode": mode if mode in ("chat", "voice", "lecture") else "chat", "precision_reading": False}
    if session.scratch.get("personal_map_seen"):
        view["personal_map_seen"] = True  # IK-0907: 真偽値のみ
    view.update(atlas_last_node_view(session))
    return {"screen": "learning", "selection": selection, "view": view, "visible_entities": []}


# 第 25 周 第 5 波（IK-0986）: app.js getScreenContext は明示の要素が無い送信でも、いま開いて表示中の部品の文脈
# （state.openContextElement）を ``selection.element_type/element_id`` に申告する。ポップオーバーは外側の mousedown で
# 閉じる。第 25 周 第 7 周 share H46（指揮者の判断）: 文字の送信でも、ポップオーバーを開いたまま尋ねた 1 往復は申告する
# （開いた部品について問う場面の材料）。送った後は控えを捨てるので 2 往復目には載らない。音声ループは開いたまま続く。
OPEN_CONTEXT_KEEPERS = frozenset({"learning.component.context", "learning.component.context_hop"})


def opened_component_label(body: Any) -> str:
    """部品の文脈の応答から、app.js が控える部品の名前（``instance.component.label || frozen_label``）。
    送るときに 40 字で切る（``open_context_selection``）。"""
    if not isinstance(body, dict):
        return ""
    inst = body.get("instance") if isinstance(body.get("instance"), dict) else {}
    comp = inst.get("component") if isinstance(inst.get("component"), dict) else {}
    return str(comp.get("label") or body.get("frozen_label") or "")


def remember_open_context(session: Any, component_id: str, label: str = "") -> None:
    """部品の文脈を開いた（200）ときに、画面と同じく開いている部品の参照（と見出しの名前）だけを控える。"""
    session.scratch["open_context_element"] = {"element_type": "component", "element_id": str(component_id),
                                               "topic_id": str(getattr(session, "topic_id", "") or ""),
                                               "element_label": str(label or "")}


TEXT_OPEN_CONTEXT_NOTE = ("runner_screen: ⚓ の部品の文脈を開いたまま文字で尋ねた — 画面と同じく、開いている部品を"
                          "screen_context.selection（kind=element・element_type・element_id・element_label）で申告した")


def open_context_selection(session: Any, topic_id: Optional[str], *, voice: bool) -> dict:
    """開いている部品の文脈を ``selection`` に足す分（無ければ空）。文字の送信ではポップオーバーが閉じるので控えを捨てる。"""
    opened = session.scratch.get("open_context_element")
    if not isinstance(opened, dict) or not opened.get("element_id"):
        return {}
    if str(opened.get("topic_id") or "") != str(topic_id or ""):
        if not voice:
            session.scratch.pop("open_context_element", None)
        return {}
    out = {"kind": "element", "element_id": str(opened["element_id"]), "element_type": str(opened["element_type"])}
    if opened.get("element_label"):
        out["element_label"] = str(opened["element_label"])[:40]
    if not voice:
        # 第 25 周 第 7 周 share H46: 文字の送信でも、送る瞬間にポップオーバーが表示中なら選択を申告する（getScreenContext は
        # 送信の前に組まれる）。送った後は入力欄の操作でポップオーバーが閉じるので控えを捨てる（次の送信には載らない）
        session.scratch.pop("open_context_element", None)
        runner_note(session, TEXT_OPEN_CONTEXT_NOTE)
    return out


def atlas_last_node_view(session: Any) -> dict:
    """app.js getScreenContext（第 4 波・IK-0907）: 分野の地図で最後に開いた場所の名前（40 字）と状態の enum だけ。
    ``learning.atlas.select_node`` が ``atlas:nodeselect`` と同じく ``atlas.nodes[node_id]`` から控える。"""
    node = session.scratch.get("atlas_last_node") if isinstance(session.scratch.get("atlas_last_node"), dict) else None
    if not node or not node.get("label"):
        return {}
    out = {"atlas_node_label": str(node["label"])[:40]}
    if node.get("node_id"):
        # 第 6 波（IK-1031・sync_keys_wave6.md 面 B）: 場所の id（app.js と同じく 160 字で切る）。サーバはこれで詳細パネルの
        # 検証・承認の行と、この場所に置かれたコースの論文の事実文を足す（無いと名前と札の 2 行だけ）
        out["atlas_node_id"] = str(node["node_id"])[:160]
    if node.get("status"):
        out["atlas_node_status"] = str(node["status"])
    if node.get("ledger_status"):
        out["atlas_node_ledger_status"] = str(node["ledger_status"])
    return out


PERSONAL_MAP_VIEW_NOTE = ("runner_screen: わたしの地図の「いまここの周り」を開いたまま尋ねた — 画面と同じく、見ている場所の参照"
                          "（selection.personal_map_node_id・点ビューなら personal_map_center_component_id）を申告した")
ATLAS_NODE_FRESH_NOTE = ("runner_screen: 分野の地図で場所を選んだ直後の 1 往復 — 画面と同じく、選んだ場所を "
                         "selection.atlas_node_id で申告した（次の往復からは view の「最後に開いた場所」だけ）")


def remember_personal_map_view(session: Any, node_id: Any, center_component_id: Any = None) -> None:
    """わたしの地図の「いまここの周り」を開いた（nearby 200）ときの中心（personal-map-home.js ``state.nearbyCenterNodeId`` /
    ``nearbyCenterComponentId``）。パネルを開いている間は次の往復でも載る（閉じる手 = トピックを開く・別のタブ・地図を開く）。"""
    if not node_id:
        return
    view = {"personal_map_node_id": str(node_id)[:160]}
    if center_component_id:
        view["personal_map_center_component_id"] = str(center_component_id)[:160]
    session.scratch["personal_map_view"] = view


def screen_selection_refs(session: Any) -> dict:
    """第 25 周 第 10 波: app.js ``getScreenContext`` の selection に足す参照（表示しない id だけ）。

    - IK-1113: わたしの地図の「いまここの周り」を開いている間 → ``personal_map_node_id``（+ ``personal_map_center_component_id``）
    - FD-note 4: 分野の地図で場所を選んだ直後の 1 往復 → ``atlas_node_id``（送ったら落とす。``view.atlas_node_*`` は残る）
    """
    out: dict[str, Any] = {}
    pm = session.scratch.get("personal_map_view")
    if isinstance(pm, dict) and pm.get("personal_map_node_id"):
        out.update(pm)
        runner_note(session, PERSONAL_MAP_VIEW_NOTE)
    node = session.scratch.get("atlas_last_node") if isinstance(session.scratch.get("atlas_last_node"), dict) else None
    if session.scratch.pop("atlas_node_fresh", None) and node and node.get("node_id"):
        out["atlas_node_id"] = str(node["node_id"])[:160]
        runner_note(session, ATLAS_NODE_FRESH_NOTE)
    return out


def learning_screen_context(session: Any, topic_id: Optional[str], *, mode: str = "chat") -> dict:
    """app.js getScreenContext の topic 選択の形（第 25 周 第 2 波）。runner は既定ではチャットに画面文脈を
    送らない（近似）が、わたしの地図を開いた後（``view.personal_map_seen``）と分野の地図で場所を選んだ後
    （第 4 波・``view.atlas_node_*``）だけはこの形を送る。"""
    selection: dict[str, Any] = {"course_id": str(session.course_id or ""), "topic_id": str(topic_id or ""),
                                 "kind": "topic"}
    doc = (session.material or {}).get("document_id") if isinstance(session.material, dict) else None
    if doc:
        selection["document_id"] = str(doc)
    view: dict[str, Any] = {"mode": mode if mode in ("chat", "voice", "lecture") else "chat", "precision_reading": False}
    if session.scratch.get("personal_map_seen"):
        view["personal_map_seen"] = True
    view.update(atlas_last_node_view(session))
    return {"screen": "learning", "selection": selection, "view": view, "visible_entities": []}


# ハンズフリー音声ループ（app.js handleVoiceSegment）と同じく ``intent_mode="casual"`` で送り、
# ``resolveScreenMode()`` と同じく ``screen_mode="voice"`` を添える（第 17 周: 音声のペルソナに文字の
# on_path を送り、書き言葉の回答を欠陥と数えていた）。
VOICE_ROUTING_NOTE = "runner_voice: 音声中心のペルソナなので、学習画面の音声ループと同じく intent_mode=casual + screen_mode=voice で送った"
# 音声のまま議論に入ると app.js は VOICE_DISCUSS_UNAVAILABLE_NOTE を出して音声を止める（音声版 discuss は設計 §6.4 の
# 非スコープ）。議論の回答は文字で読む書き言葉なので、LaTeX・[出典N] を音声の欠陥として数えない
VOICE_DISCUSS_NOTE = "音声モードは議論では使えない（設計 §6.4）"


def _voice_kwargs(ctx: _Ctx) -> dict[str, Any]:
    if not ctx.session.uses_voice:
        return {}
    runner_note(ctx.session, VOICE_ROUTING_NOTE)
    return {"intent_mode": "casual", "screen_mode": "voice"}


def _mark_voice_discuss(ctx: _Ctx) -> None:
    """議論系の行為。音声のペルソナでも画面は文字で送る（screen_mode=voice は付かない）ことを観察と記録に残す。"""
    if not ctx.session.uses_voice:
        return
    if str(ctx.args.get("screen_mode") or "") == "voice":
        ctx.args.pop("screen_mode", None)
    runner_note(ctx.session, f"runner_voice_discuss: {VOICE_DISCUSS_NOTE}。回答は文字の議論として読む"
                             "（書き言葉・LaTeX・[出典N] を音声向けでないことの欠陥として数えない）")
    ctx.session.scratch.setdefault("observation_notes", []).append(VOICE_DISCUSS_NOTE)


def _chat_ask(ctx: _Ctx) -> None:
    _chat(ctx, **{"intent_mode": "on_path", **_voice_kwargs(ctx)})


def _chat_casual(ctx: _Ctx) -> None:
    _chat(ctx, **{"intent_mode": "casual", **_voice_kwargs(ctx)})


def _chat_backstage(ctx: _Ctx) -> None:
    _chat(ctx, backstage=True)


def _chat_usage_help(ctx: _Ctx) -> None:
    # 音声ループ中でも使い方の問いは support_action=usage_help のまま（app.js のインスペクト分岐）。画面の様相だけ添える
    voice = {"screen_mode": "voice"} if ctx.session.uses_voice else {}
    _chat(ctx, support_action="usage_help", ui_anchor=ctx.args.get("ui_anchor"), **voice)


def _discuss_ask(ctx: _Ctx) -> None:
    _mark_voice_discuss(ctx)
    _chat(ctx, topic_id=DISCUSSION_TOPIC_ID, intent_mode="discuss",
          discuss_scope=ctx.args.get("discuss_scope") or "course_sources")


def _chat_rewrite(ctx: _Ctx) -> None:
    """app.js の ✏️: その発言以降を捨てて同じ位置から送り直す。議論中の発言は議論の会話のまま送る（第 23 周）。"""
    s = ctx.session
    tid = _vision5.resolve_chat_topic(s, ctx.args.get("topic"))
    history = s.history_for(tid)
    idx, target = _vision5.pick_user_message(history, ctx.args.get("nth_from_last"),
                                             str(ctx.args.get("replace_message_id") or ""))
    if target and idx >= 0:
        del history[idx:]  # UI と同じく当該メッセージ以降を捨てる
        runner_note(s, f"rewrite_target: {target}")  # 審判 E8 が派生痕跡の supersede を確かめる宛先
    if tid == DISCUSSION_TOPIC_ID:
        _mark_voice_discuss(ctx)
        _chat(ctx, topic_id=tid, replace_message_id=target or None, intent_mode="discuss",
              discuss_scope=ctx.args.get("discuss_scope") or "course_sources")
    else:
        _chat(ctx, topic_id=tid, replace_message_id=target or None)


def _chat_history(ctx: _Ctx) -> None:
    """これまでの会話を読む。画面はトピック（議論）を開くとサーバの履歴を描くので、手元の会話が空なら
    その履歴を手元に写す（翌日のセッションで書き直し・削除・続きの送信ができるように — 第 23 周）。"""
    s = ctx.session
    given = ctx.args.get("topic_id") or ctx.args.get("topic")
    tid = _vision5.resolve_chat_topic(s, given) if given else s.topic_id
    status, body = ctx.main(overrides={"topic_id": tid} if tid else None)
    if status == 200 and isinstance(body, dict):
        local = s.history_for(tid)
        if not local:
            for m in body.get("history") or []:
                if isinstance(m, dict) and m.get("role") in ("user", "assistant"):
                    local.append({"role": m["role"], "content": str(m.get("content") or ""),
                                  **({"id": str(m["id"])} if m.get("id") else {})})


def _corpus_discuss(ctx: _Ctx) -> None:
    doc = ctx.args.get("document_id") or ctx.session.latest("corpus_documents") or ctx.session.latest("documents")
    _mark_voice_discuss(ctx)
    _chat(ctx, topic_id=f"_doc:{doc}", overrides={"document_id": doc}, intent_mode="discuss")


def _check_take(ctx: _Ctx) -> None:
    s = ctx.session
    if not s.topic_id:
        return ctx.missing("topic_id")
    topic = s.topics.get(s.topic_id) or {}
    qs = [q for q in topic.get("check_questions") or [] if q]
    wanted = str(ctx.args.get("question") or "").strip()
    chosen = None
    if wanted:
        # ペルソナが指定した問いに対応する check_question を選ぶ（第 9 周: 2 問目を指定したのに 1 問目の要件で並置された）
        for q in qs:
            qt = q.get("question", "") if isinstance(q, dict) else str(q)
            if qt and (qt.strip() == wanted or wanted in qt or qt in wanted):
                chosen = q
                break
    if chosen is None:
        chosen = qs[0] if qs else None
    # 画面（app.js）は表示中の問いの本文と、その問いの check_question を対で送る。ペルソナの言い換えを
    # question に入れると別の問い（先頭の問い）の要件と組み合わさる（第 15 波: 前回の問いが添えられていた）
    question = (chosen.get("question", "") if isinstance(chosen, dict) else str(chosen or "")) or wanted
    ctx.main(json={"answer": str(ctx.args.get("answer", "")), "question": str(question),
                   "check_question": chosen if isinstance(chosen, dict) else None})


_SELF_CHECK_LABELS = {"合っていた": "agreed", "違っていた": "disagreed", "観点がおかしい": "verdict_wrong",
                      "agreed": "agreed", "disagreed": "disagreed", "verdict_wrong": "verdict_wrong"}


def _check_self_check(ctx: _Ctx) -> None:
    """自己確認。画面のボタン名（合っていた / 違っていた / 観点がおかしい）でも受け、API の語彙へ写す
    （第 9 周: ペルソナが画面の語で送り 422 になった）。"""
    body = ctx.body_args()
    raw = str(body.get("self_check") or "").strip()
    body["self_check"] = _SELF_CHECK_LABELS.get(raw, _SELF_CHECK_LABELS.get(raw.strip("「」"), raw))
    ctx.main(json=body)


def _source_chunk_open(ctx: _Ctx) -> None:
    """出典の本文を開く。画面の番号（「出典3」「3」）で指定されたら直前の回答の sources[].index から chunk を引く
    （第 10 周: 番号指定が最新のチャンクに化けて別の出典が開いていた）。"""
    s = ctx.session
    raw = str(ctx.args.get("source_no") or ctx.args.get("chunk_id") or "").strip()
    if ctx.args.get("source_no") not in (None, "") and not s.scratch.get("last_sources"):
        # 直前の回答に出典が無い（画面に出典の番号が無い）— 製品を叩かない
        return ctx.missing("source_no")
    m = re.fullmatch(r"(?:出典\s*)?(\d+)", raw)
    cid = raw
    if m:
        cid = (s.scratch.get("last_sources") or {}).get(m.group(1), "")
        if not cid:
            return ctx.missing("chunk_id")
    status, _ = ctx.main(overrides={"chunk_id": cid} if cid else None)
    if status == 200 and cid and not is_pseudo_chunk_id(cid):
        s.remember("source_chunks", cid)


def _discuss_opening(ctx: _Ctx) -> None:
    status, body = ctx.main()
    for d in _items(body, "documents"):
        if isinstance(d, dict):
            ctx.session.remember("documents", _first(d, "document_id", "id"))


def _cycle_intention(ctx: _Ctx) -> None:
    """discuss.js postCycleIntention。carryover_question の pick は「次に持ち越すなら」の選択肢
    （saveCycleCarryover は選んだ問いの文と出所を送る）、revisit_answer は扉・開幕で見た持ち越しの問いに答える。"""
    body = ctx.body_args()
    s = ctx.session
    pick = body.pop("pick", None)
    role = str(body.get("role") or "")
    own_text = str(body.get("text") or "").strip()
    if role == "carryover_question" and pick not in (None, ""):
        if own_text:
            # 画面は「選択肢を選ぶ」か「自分の言葉で書く」の二択。両方渡されたら本人の言葉を優先する
            # （第 23 周 st-02: pick を優先して本人の言葉が保存されなかった）
            runner_note(s, "runner_choice: pick と text の両方が渡されたので「自分の言葉で書く」（text）で送った")
        else:
            cand = _vision5._hint_pick(list(s.scratch.get("landing_candidates") or []), pick, ("label",))
            if cand is None:
                return ctx.missing("landing_candidate")
            body["text"], body["source_trace_id"] = str(cand.get("label") or ""), str(cand.get("trace_id") or "")
    # 候補ゼロでも「自分の言葉で書く」は押せる（第 23 周 st-05）— text があれば候補を前提にしない
    if role == "revisit_answer" and not body.get("source_trace_id") and s.latest("carryover_traces"):
        body["source_trace_id"] = s.latest("carryover_traces")
    body = {k: v for k, v in body.items() if v not in (None, "")}
    status, body = ctx.main(json=body)
    if isinstance(body, dict):
        ctx.session.remember("intention_traces", _first(body, "trace_id", "id"))
    if status is not None:
        ctx.session.last_body = {"_ui_fact": _vision5.intention_view(role, status, body)}


_QUICK_LABELS = {"気になる": "curious", "まだ分からない": "not_yet", "あとで戻る": "return_later",
                 "何かとつながりそう": "connects"}


def _cycle_anchor(ctx: _Ctx) -> None:
    body = ctx.body_args()
    body.setdefault("topic_id", ctx.session.topic_id)
    raw = str(body.get("quick_label") or "").strip().strip("「」")
    # 画面のボタン名で送られたら API の語彙へ写す（第 9 周: 語彙が画面に無く 422 になった）。
    # 言い換え（「あとで確かめる」等）は先頭の語で最も近いボタンに寄せる
    mapped = _QUICK_LABELS.get(raw)
    if mapped is None:
        for key, val in (("あとで", "return_later"), ("戻", "return_later"), ("気にな", "curious"),
                         ("分から", "not_yet"), ("わから", "not_yet"), ("つなが", "connects"), ("関係", "connects")):
            if key in raw:
                mapped = val
                break
    body["quick_label"] = mapped or raw
    ctx.main(json=body)


_DIGEST_PATHS = {"traces": "/api/learning/courses/{course_id}/tension/digest",
                 "anchor_traces": "/api/learning/courses/{course_id}/anchors/digest"}


def _digest(kind: str) -> Callable[[_Ctx], None]:
    def run(ctx: _Ctx) -> None:
        status, body = ctx.main()
        items = [it for it in _items(body, "items", "candidates", "digest") if isinstance(it, dict)]
        if status == 200:
            ctx.session.scratch[f"digest_{kind}"] = items  # 画面は先頭から 1 枚ずつ（「あとで」で次へ）
        for it in items:
            ctx.session.remember(kind, _first(it, "trace_id", "id"))
    return run


def _trace_action(kind: str, json_keys: tuple[str, ...] = ()) -> Callable[[_Ctx], None]:
    """候補カードの「そう、これ」「違う」。画面は digest の先頭の 1 枚（いま表示されている候補）に押す。
    pick（候補の番号）を渡すと、前の候補を「あとで」にしてその候補に押す。押した後、画面は digest を読み直す
    （第 23 周 st-06: 候補の宛先が画面に無く、最後に覚えた id へ送っていた）。"""
    def run(ctx: _Ctx) -> None:
        s = ctx.session
        tid = ctx.args.get("trace_id") or _vision5.digest_pick(s, f"digest_{kind}", ctx.args.get("pick")) \
            or s.latest(kind)
        body = {k: ctx.args[k] for k in json_keys if ctx.args.get(k) not in (None, "")}
        status, resp = ctx.main(json=body, overrides={"trace_id": tid} if tid else None)
        if status == 200 and kind in _DIGEST_PATHS:
            notice = resp.get("notice") if isinstance(resp, dict) else ""
            st2, dig = ctx.call("GET", _DIGEST_PATHS[kind])
            if st2 == 200:
                items = [it for it in _items(dig, "items") if isinstance(it, dict)]
                s.scratch[f"digest_{kind}"] = items
                card = (_vision5.anchor_card if kind == "anchor_traces" else _vision5.tension_card)(dig)
                s.last_status, s.last_body = 200, {"_ui_fact": (f"{notice}\n" if notice else "") + card}
            elif notice:
                s.last_status, s.last_body = 200, {"_ui_fact": str(notice)}
    return run


def _recon_next(ctx: _Ctx) -> None:
    status, body = ctx.main()
    if isinstance(body, dict):
        item = body.get("item") if isinstance(body.get("item"), dict) else body
        ctx.session.remember("recon_items", _first(item, "item_id", "id"))
        ctx.session.scratch["recon_space"] = [o for o in item.get("response_space") or [] if isinstance(o, dict)]


def _recon_response(ctx: _Ctx) -> dict:
    resp = ctx.args.get("response")
    if not isinstance(resp, dict):
        resp = {"text": str(resp or "")}
    # 選択式（画面はラジオの value = option_id を送る）。ペルソナはラベルか番号で選ぶ
    space = ctx.session.scratch.get("recon_space") or []
    pick = str(resp.get("option") or resp.get("option_id") or ctx.args.get("option") or "").strip()
    if space and "text" in resp and not pick:
        pick = str(resp.get("text") or "").strip()
    if space and pick:
        hit = next((o for i, o in enumerate(space, 1)
                    if pick in (str(i), str(o.get("id")), str(o.get("label")))), None)
        if hit is None:
            hit = next((o for o in space if str(o.get("label")) and str(o.get("label")) in pick), None)
        resp = {"option_id": str(hit["id"])} if hit else resp
    return resp


def _recon_submit(ctx: _Ctx) -> None:
    resp = _recon_response(ctx)
    status, body = ctx.main(json={"course_id": ctx.session.course_id, "response": resp,
                                  "revision_of": ctx.args.get("revision_of")})
    if isinstance(body, dict):
        ctx.session.remember("recons", _first(body, "recon_id", "reconstruction_id", "id"))


def _recon_revise(ctx: _Ctx) -> None:
    """reconstruction.js の「もう一度挑戦」: 直前の答え（current.reconId）を revision_of に付けて /revise へ。"""
    prev = ctx.args.get("revision_of") or ctx.session.latest("recons")
    if not prev:
        return ctx.missing("revision_of")
    resp = _recon_response(ctx)
    status, body = ctx.main(json={"course_id": ctx.session.course_id, "response": resp, "revision_of": prev})
    if isinstance(body, dict):
        ctx.session.remember("recons", _first(body, "recon_id", "reconstruction_id", "id"))


def _recon_self_check(ctx: _Ctx) -> None:
    ctx.main(json={"result": str(ctx.args.get("result") or "agreed")})


def _symbol_lookup(ctx: _Ctx) -> None:
    # 第 25 周 share H3: 画面（app.js openSymbolLookup）と同じ形で送り（記号は symbolTextFromToken の形・式の id は
    # タップした数式カードの data-equation-id・表示中トピック）、吹き出しの写しを投影する。正本は share_exec
    from uxsim.runner import share_exec  # 遅延 import（循環回避）

    share_exec.symbol_lookup(ctx)


DESCENT_ELEMENT_TYPES = ("equation", "component", "claim")  # 製品 routes/descent.py の SUPPORTED_ELEMENT_TYPES


def _descent_ladder(ctx: _Ctx) -> None:
    """画面は ⚓ をタップした要素の id で降下路を開く。⚓ の番号（「⚓3」/ 3）や element_ref は教材の ⚓ 一覧から
    要素に引き直す（第 18 周: 番号をそのまま element_id に送り not_found になっていた — ハーネスの写し漏れ）。"""
    s = ctx.session
    ref = str(ctx.args.get("element_ref") or "").strip()
    eid_arg = str(ctx.args.get("element_id") or "").strip()
    if not ref and re.fullmatch(r"(?:⚓\s*)?\d+", eid_arg):
        ref = eid_arg
    if ref:
        m = re.fullmatch(r"(?:⚓\s*)?(\d+)", ref)
        hit = next((a for a in material_anchors(s.material) if (m and a["no"] == int(m.group(1))) or a["id"] == ref),
                   None)
        if hit is None:
            return ctx.missing("element_ref")  # 一覧に無い番号・id は製品を叩かない
        etype = {"theory_component": "component", "theory_claim": "claim"}.get(hit["kind"], hit["kind"])
        if etype not in DESCENT_ELEMENT_TYPES:
            return ctx.missing("descent_anchor")  # 図・出典の ⚓ には降下路が無い
        ctx.args["element_type"], ctx.args["element_id"] = etype, hit["id"]
    if not (ctx.args.get("element_id") or s.latest("elements")):
        return ctx.missing("element_id")
    ctx.main(params={"element_type": ctx.args.get("element_type") or s.latest("element_types") or "claim",
                     "element_id": ctx.args.get("element_id") or s.latest("elements")})


def _pick_anchor(ctx: _Ctx) -> Optional[dict]:
    """``element_ref``（⚓ の番号 / 「⚓3」/ 要素 id）を教材の ⚓ 一覧から引く。"""
    ref = str(ctx.args.get("element_ref") or "").strip()
    if not ref:
        return None
    anchors = material_anchors(ctx.session.material)
    m = re.fullmatch(r"(?:⚓\s*)?(\d+)", ref)
    for a in anchors:
        if (m and a["no"] == int(m.group(1))) or a["id"] == ref:
            return a
    return {}


def _element_context(ctx: _Ctx) -> None:
    s = ctx.session
    picked = _pick_anchor(ctx)
    if picked == {}:
        return ctx.missing("element_ref")
    if picked:
        ctx.args.setdefault("element_type", picked["kind"])
        ctx.args.setdefault("element_id", picked["id"])
    etype = ctx.args.get("element_type") or s.latest("element_types") or "claim"
    eid = ctx.args.get("element_id") or s.latest("elements")
    # 画面（app.js）と同じ写し: theory_component → component（部品の文脈へ）/ theory_claim → claim（API の型）。
    # 第 15 周: theory_claim のまま呼んで 404 になっていた（製品欠陥ではない）
    etype = {"theory_component": "component", "theory_claim": "claim"}.get(etype, etype)
    if etype == "component":
        # 実フロント（focusQuerySuffix）と同じく表示中トピックを添える（第 17 周: 焦点論文の衝突解決は
        # topic が無いと fail-closed で 404 になる — 製品欠陥ではなくハーネスの写し漏れ）
        ctx.call("GET", "/api/learning/courses/{course_id}/components/{component_id}/context",
                 overrides={"component_id": eid},
                 params={"topic_id": s.topic_id} if getattr(s, "topic_id", None) else None)
    else:
        # 実フロントは表示中トピックを添える（式 ID の論文またぎの衝突をトピックの論文で解く）
        ctx.main(overrides={"element_type": etype, "element_id": eid},
                 params={"topic_id": s.topic_id} if getattr(s, "topic_id", None) else None)


def _network(ctx: _Ctx) -> None:
    status, body = ctx.main()
    # app.js（IK-0907・第 2 波）: わたしの地図を開いたら state.personalMapSeen = true（トピックを開き直すと戻る）
    ctx.session.scratch["personal_map_seen"] = True
    ctx.session.scratch.pop("personal_map_view", None)  # 第 10 波: 「いまの地図」タブ（いまここの周り以外）
    _vision5.remember_network_nodes(ctx.session, body)
    for n in _items(body, "nodes"):
        if isinstance(n, dict):
            ctx.session.remember("nodes", _first(n, "id", "node_id"))


def _network_node(extra: tuple[str, ...]) -> Callable[[_Ctx], None]:
    def run(ctx: _Ctx) -> None:
        if not (ctx.args.get("node_id") or ctx.session.latest("nodes")):
            # 画面はタブの前に「わたしの地図」を読み込む（personal-map-home.js）。それを写してノードを得る
            _, body = ctx.call("GET", "/api/me/personal-network")
            for n in _items(body, "nodes"):
                if isinstance(n, dict):
                    ctx.session.remember("nodes", _first(n, "id", "node_id"))
        params = {"node_id": ctx.args.get("node_id") or ctx.session.latest("nodes")}
        if not params["node_id"]:
            return ctx.missing("node_id")
        for k in extra:
            if ctx.args.get(k):
                params[k] = ctx.args[k]
        status, _ = ctx.main(params=params)
        if "center_component_id" in extra:  # nearby（いまここの周り）
            if status == 200:
                remember_personal_map_view(ctx.session, params["node_id"], params.get("center_component_id"))
        else:  # journey（問いからの旅）= 別のタブ
            ctx.session.scratch.pop("personal_map_view", None)
    return run


def _atlas_view(ctx: _Ctx) -> None:
    s = ctx.session
    ctx.main(params={"course": s.course_id, "topic": s.topic_id, "level": ctx.args.get("level") or 1})


def _corpus_domains(ctx: _Ctx) -> None:
    status, body = ctx.main()
    for d in _items(body, "domains"):
        if isinstance(d, dict):
            ctx.session.remember("domains", _first(d, "domain_key", "key"))


def _corpus_documents(ctx: _Ctx) -> None:
    status, body = ctx.main(params={"domain_key": ctx.args.get("domain_key") or ctx.session.latest("domains")})
    for d in _items(body, "documents"):
        if isinstance(d, dict):
            ctx.session.remember("corpus_documents", _first(d, "document_id", "id"))


def _help_inspect(event_path: str) -> Callable[[_Ctx], None]:
    def run(ctx: _Ctx) -> None:
        status, body = ctx.main()
        anchor = str(ctx.args.get("anchor_id") or "")
        anchors = body.get("anchors") if isinstance(body, dict) else None
        if anchor and status == 200 and isinstance(anchors, dict) and not anchors.get(anchor):
            payload: dict[str, Any] = {"anchor_id": anchor, "kind": "no_hit"}
            if event_path.startswith("/api/learning"):
                payload.update({"course_id": ctx.session.course_id or None, "topic_id": ctx.session.topic_id or None})
            ctx.call("POST", event_path, json=payload)
            ctx.session.remember("help_no_hit", anchor)
            ctx.session.last_status, ctx.session.last_body = 200, {"help": "この部品の説明は見つからなかった",
                                                                   "anchor_id": anchor}
            return
        if status == 200 and isinstance(anchors, dict):
            # 第 25 周 H5: 全アンカーをダンプして各 body を切り詰めていた。画面はホバーした部品 1 つの
            # ツールチップ（📖 使い方・見出し・本文の全文）だけを出す（app.js showInspectTooltip）
            ctx.session.last_status, ctx.session.last_body = 200, {"_ui_fact": help_inspect_view(anchors, anchor)}
    return run


INSPECT_BANNER = "使い方を確認中 — 知りたい部分にカーソルを合わせてください（Esc で終了）"


def _plain_manual_text(text: Any) -> str:
    """app.js ``_plainManualText``: 強調記号だけを落とす（語は変えない・要約しない）。"""
    t = str(text or "")
    t = re.sub(r"\*\*([\s\S]+?)\*\*", r"\1", t)
    t = re.sub(r"__([\s\S]+?)__", r"\1", t)
    t = re.sub(r"`([^`]+)`", r"\1", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def help_inspect_view(anchors: dict, anchor: str) -> str:
    """インスペクト・モードの画面: バナー + ホバーした部品 1 つのツールチップ（anchor が無ければバナーと
    カーソルを合わせられる部品の名前だけ）。番地（anchor_id）は末尾の「操作に使う番号」欄に分ける（H9）。"""
    lines = [INSPECT_BANNER]
    entry = anchors.get(anchor) if anchor else None
    if isinstance(entry, dict):
        lines += ["ツールチップ:", "📖 使い方", _plain_manual_text(entry.get("title")), _plain_manual_text(entry.get("body"))]
        return "\n".join(x for x in lines if x)
    names = [(k, _plain_manual_text(v.get("title"))) for k, v in anchors.items() if isinstance(v, dict)]
    if names:
        lines.append("カーソルを合わせると説明が出る部品（見出し）: " + " / ".join(dict.fromkeys(t for _, t in names if t)))
        lines.append("（操作に使う番号 — 画面には出ない。anchor_id に渡すとその部品のツールチップを見る）: "
                     + ", ".join(k for k, _ in names))
    return "\n".join(lines)


def _voice_speak(ctx: _Ctx) -> None:
    ctx.main(json={"text": str(ctx.args.get("text") or ctx.session.last_answer or "")[:1000]})


# ----------------------------------------------------------------------------
# 管理側
# ----------------------------------------------------------------------------

def _materials_list(ctx: _Ctx) -> None:
    status, body = ctx.main()
    for m in _items(body, "materials", "items"):
        if isinstance(m, dict):
            ctx.session.remember("materials", m.get("material_id"))
            ctx.session.remember("documents", m.get("document_id"))



def _materials_get(ctx: _Ctx) -> None:
    """教材 1 件の詳細。ペルソナが「選んだ」教材として記憶し、コースビルダーの selected_material_ids と
    登録時の sources に使う（画面の選択と同じ役割）。"""
    status, body = ctx.main()
    s = ctx.session
    mid = ctx.args.get("material_id") or (body.get("material_id") if isinstance(body, dict) else None)
    if mid:
        s.remember("materials_selected", mid)
        titles = getattr(s, "material_titles", None)
        if titles is None:
            titles = {}
            setattr(s, "material_titles", titles)
        if isinstance(body, dict):
            titles[str(mid)] = str(body.get("title") or body.get("filename") or "")


def _upload_url(ctx: _Ctx) -> None:
    status, body = ctx.main(json=ctx.body_args())
    if isinstance(body, dict):
        ctx.session.remember("tasks", body.get("task_id"))
        ctx.session.remember("materials", body.get("material_id"))


def _graph_open(ctx: _Ctx) -> None:
    doc = ctx.args.get("document_id") or ctx.session.latest("documents")
    status, body = ctx.main(overrides={"document_id": doc} if doc else None)
    if status == 200 and doc:
        ctx.session.remember("graph_documents", doc)
    graph = body.get("graph") if isinstance(body, dict) and isinstance(body.get("graph"), dict) else body
    targets = ctx.session.maps.setdefault("node_targets", {})
    nodes = [n for n in _items(graph, "nodes") if isinstance(n, dict)]
    if nodes:
        # 画面の選択と同じ材料（id / ラベル / 層 / 実体要素）をグラフの並び順で覚える。ノード対話はここから引く
        ctx.session.scratch["graph_nodes"] = []
        ctx.session.scratch.pop("graph_selected_node", None)
    for n in nodes:
        ctx.session.remember("components", _first(n, "db_id", "component_db_id", "component_id", "id"))
        nid = _first(n, "id", "node_id")
        tgt = deliberation_target_id(n)
        if nid:
            targets[nid] = tgt
            ctx.session.scratch["graph_nodes"].append({
                "id": nid, "target": tgt, "layer": str(n.get("graph_layer") or "main"),
                "labels": [str(n.get(k) or "").strip() for k in ("label", "visual_label", "title", "name")
                           if str(n.get(k) or "").strip()]})


_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


def deliberation_target_id(node: dict) -> str:
    """admin-graph-review.js ``deliberationTargetId`` の写し: DB UUID → 代表要素 → linked_component_ids の先頭。
    どれも無ければ空（画面はリクエストせず事実文を出す）。第 14 周: main ノード id をそのまま送り 404 になった。"""
    nid = _first(node, "id", "node_id")
    if nid and _UUID_RE.match(nid):
        return nid
    rep = str(node.get("representative_component_id") or "").strip()
    if rep:
        return rep
    for c in node.get("linked_component_ids") or []:
        if str(c or "").strip():
            return str(c).strip()
    return ""


def _graph_chat(ctx: _Ctx) -> None:
    s = ctx.session
    doc = ctx.args.get("document_id") or s.latest("graph_documents") or s.latest("documents")
    sid = s.maps.setdefault("graph_sessions", {}).get(doc or "")
    if not sid:
        status, body = ctx.call("POST", "/api/admin/deliberation/documents/{document_id}/graph-sessions",
                                overrides={"document_id": doc} if doc else None)
        sess = body.get("session") if isinstance(body, dict) else None
        sid = str(sess.get("id")) if isinstance(sess, dict) and sess.get("id") else ""
        if not sid:
            return
        s.maps["graph_sessions"][doc] = sid
    ctx.main(json={"content": str(ctx.args.get("content") or ctx.args.get("message") or ""),
                   "screen_context": graph_screen_context(doc, layer=str(ctx.args.get("layer") or "main"))},
             overrides={"document_id": doc, "session_id": sid})


def graph_screen_context(doc: str, *, node_id: str = "", component_id: Optional[str] = None,
                         layer: str = "main", mode: str = "graph") -> dict:
    """admin-graph-review.js ``getScreenContext`` と同じ形（ノード未選択なら node_id 空・component_id null）。"""
    return {"screen": "graph_review",
            "selection": {"document_id": str(doc or ""), "node_id": node_id or "",
                          "component_id": component_id or None, "graph_layer": "main" if node_id else ""},
            "view": {"mode": mode, "layer": layer}, "visible_entities": []}


def _cb_session_create(ctx: _Ctx) -> None:
    status, body = ctx.main(json={"title": str(ctx.args.get("title") or "")})
    if isinstance(body, dict):
        ctx.session.remember("cb_sessions", body.get("session_id"))


def _cb_chat(ctx: _Ctx) -> None:
    s = ctx.session
    sid = s.latest("cb_sessions")
    history = s.history_for(f"cb:{sid}")
    message = str(ctx.args.get("message") or "")
    # ペルソナが引数で指定 > 詳細を開いて選んだ教材 > 一覧の全件（画面の「選択」に相当。第 1 周は常に全件で
    # 「選択が勝手に広がる」観測になった）
    # 画面の既定は「一覧の全件を選択」に相当させる。詳細を開いた 1 本だけに絞る推定はしない
    # （第 5 周: 詳細を 1 本開いた教員の course builder が 1 教材だけの文脈になり「4 本と書いたのに 1 本」と混乱した）。
    selected = ctx.args.get("selected_material_ids") or getattr(s, "cb_selected", None) or s.all("materials")
    # 画面の「選択中の教材」に相当。一度決めた選択は次の turn と登録まで同じ集合を使う
    # （第 4 周: turn ごとに集合が変わり、handle が別の候補表で解決されて誤った単位が付いた）。
    setattr(s, "cb_selected", list(selected))
    status, body = ctx.main(json={"message": message, "history": history[-HISTORY_WINDOW:],
                                  "session_id": sid or None, "selected_material_ids": list(selected)})
    if status == 200 and isinstance(body, dict):
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": str(body.get("answer", ""))})
        if isinstance(body.get("course_draft"), dict):
            s.course_draft = body["course_draft"]


_COURSE_CREATE_KEYS = ("title", "chapters", "topics", "concepts", "sources", "description", "visibility", "group_id")



def _unit_handles(topic: Any, draft: Any = None) -> list:
    """admin.js の cbDraftUnitHandles と同じ（IK-0371 以降）。

    要素は "U3" 等の文字列 handle か ``{"handle", "stable_key"}``。参照キーは ①要素自身の
    stable_key ②草案に同梱された ``draft["unit_candidate_keys"]``（handle → 参照キー）から取り、
    どちらも無い handle は文字列のまま渡す。重複は参照キー（無ければ handle）で落とす。
    """
    if not isinstance(topic, dict) or not isinstance(topic.get("units"), list):
        return []
    table = draft.get("unit_candidate_keys") if isinstance(draft, dict) else None
    table = table if isinstance(table, dict) else {}
    out: list = []
    seen: list[str] = []
    for item in topic["units"]:
        handle, key = "", ""
        if isinstance(item, str):
            handle = item
        elif isinstance(item, dict):
            handle = item.get("handle") or item.get("unit") or ""
            key = item.get("stable_key") if isinstance(item.get("stable_key"), str) else ""
        handle, key = str(handle or "").strip(), str(key or "").strip()
        if not key and handle:
            mapped = table.get(handle.upper())
            if isinstance(mapped, str) and mapped:
                key = mapped
        if not handle and not key:
            continue
        ident = f"k:{key}" if key else f"h:{handle.upper()}"
        if ident in seen:
            continue
        seen.append(ident)
        out.append({"handle": handle, "stable_key": key} if key else handle)
    return out


def _draft_to_course_create(draft: dict, s: Any) -> dict:
    """course_draft（章の中にトピックが入る形）を CourseCreateRequest に変換する。

    正本は admin.js::approveCourse。**画面がしている変換をそのまま写す**（PE9）— 第 1 周で草案を
    素通ししたため章 4・トピック 0 のコースが登録された。sources は画面と同じく選択中の教材から組む。
    """
    chapters_in = draft.get("chapters") or []
    payload: dict = {
        "title": draft.get("title") or "新規コース",
        "chapters": [{"title": (ch.get("title") if isinstance(ch, dict) else ch) or "", "status": "locked", "progress_pct": 0}
                     for ch in chapters_in],
        "topics": [], "concepts": [], "sources": [],
    }
    idx = 0
    for ci, ch in enumerate(chapters_in):
        for t in ((ch.get("topics") if isinstance(ch, dict) else None) or []):
            title = t if isinstance(t, str) else (t.get("title") or "")
            prereqs = []
            if isinstance(t, dict) and isinstance(t.get("prerequisites"), list):
                for p in t["prerequisites"]:
                    name = p if isinstance(p, str) else (p.get("name") if isinstance(p, dict) else "")
                    if name:
                        prereqs.append({"name": name, "status": "not_started"})
            payload["topics"].append({"id": f"t{idx}", "title": title, "chapter_index": ci,
                                      "status": "in_progress" if idx == 0 else "locked",
                                      "prerequisites": prereqs, "misconceptions": [], "units": _unit_handles(t, draft)})
            idx += 1
    for c in draft.get("concepts") or []:
        name = c if isinstance(c, str) else (c.get("name") if isinstance(c, dict) else "")
        if name:
            payload["concepts"].append({"name": name, "status": "future",
                                        "children": (c.get("children") if isinstance(c, dict) else None) or [], "expanded": False})
    selected = list(getattr(s, "cb_selected", None) or []) or list(s.all("materials") or [])
    titles = getattr(s, "material_titles", {}) or {}
    if selected:
        payload["sources"] = [{"title": titles.get(m, ""), "subtitle": "", "license": "", "used_section": "", "material_id": m} for m in selected]
    else:
        for src in draft.get("sources") or []:
            if isinstance(src, str):
                payload["sources"].append({"title": src, "subtitle": "", "license": "", "used_section": "", "material_id": ""})
            elif isinstance(src, dict):
                payload["sources"].append({"title": src.get("title") or "", "subtitle": src.get("subtitle") or "", "license": src.get("license") or "",
                                           "used_section": src.get("used_section") or "", "material_id": src.get("material_id") or ""})
    return payload


def _cb_register(ctx: _Ctx) -> None:
    s = ctx.session
    draft = dict(s.course_draft or {})
    if not draft:
        s.last_status, s.last_body = None, {"_precondition": "course_draft"}
        ctx.client.traces.append(HttpTrace(method="POST", path="/api/learning/courses", error="precondition:course_draft"))
        return
    payload = _draft_to_course_create(draft, s)
    if ctx.args.get("title"):
        payload["title"] = ctx.args["title"]
    payload["is_template"] = True
    status, body = ctx.main(json=payload)
    cid = _first(body, "id", "course_id") if isinstance(body, dict) else ""
    if status and status < 300 and cid:
        s.course_id = cid
        s.remember("courses", cid)
        sid = s.latest("cb_sessions")
        if sid:
            ctx.call("PUT", "/api/admin/course-builder/sessions/{session_id}",
                     json={"status": "published", "published_course_id": cid}, overrides={"session_id": sid})
            s.last_status, s.last_body = status, body  # 投影は登録結果を見せる


def _course_visibility(ctx: _Ctx) -> None:
    vis = str(ctx.args.get("visibility") or "public")
    status, resp = ctx.main(json={"visibility": vis, "group_id": ctx.args.get("group_id")})
    if status == 200 and ctx.action.id == "admin.course.visibility":
        # 画面の文（リリース前の確認のステップ 3「公開しました。」/ コース管理「更新しました」）+ 第 3 波（IK-0947）の
        # readiness_note（本文の無いトピックが残るときだけ）
        note = str(resp.get("readiness_note") or "") if isinstance(resp, dict) else ""
        ctx.session.last_body = {"_ui_fact": _vision5.course_visibility_view(ctx.session, vis, readiness_note=note)}


def _release_placements(ctx: _Ctx) -> None:
    status, body = ctx.main()
    for doc in _items(body, "documents"):
        for p in _items(doc, "placements"):
            if isinstance(p, dict):
                ctx.session.remember("placements", _first(p, "id", "placement_id"))
    if status == 200:
        _vision5.remember_landscape(ctx.session, body)


def _release_accept(ctx: _Ctx) -> None:
    """admin-release-review.js acceptPlacements と同じ: 未確認が無ければ記録せずに次へ、あれば画面で未確認として
    描いていた配置の id と、根拠の折りたたみを開いた行の id を来歴として送る（第 23 周）。"""
    body = _vision5.accept_body(ctx.session)
    if body is None:
        # 画面は未確認が無ければ何も記録せずにステップ 3 を描く（acceptPlacements → advance）
        ctx.session.last_status, ctx.session.last_body = 200, {
            "_ui_fact": _vision5.RR_NO_PENDING + "\n" + _vision5.release_step3_text(ctx.session, published=False)}
        runner_note(ctx.session, "runner_ui_mirror: 未確認の配置が無いので「次へ」は POST しなかった（画面と同じ）")
        return
    status, resp = ctx.main(json=body)
    if status == 200:
        dc = resp.get("decision_context") if isinstance(resp, dict) else None
        if isinstance(dc, dict):
            reported = dc.get("client_reported") if isinstance(dc.get("client_reported"), dict) else {}
            opened = reported.get("evidence_expanded_placement_ids")
            ctx.session.scratch["accept_record"] = {
                "confirmed": int((dc.get("applied") or {}).get("count") or 0),
                "evidence_opened": len(opened) if isinstance(opened, list) else len(body.get("evidence_expanded_placement_ids") or [])}
        # 画面は記録の後に配置を取り直してからステップ 3 を描く（fetchLandscape）。投影は「次へ」の応答のまま
        st2, fresh = ctx.call("GET", "/api/admin/landscape/courses/{course_id}/placements")
        if st2 == 200:
            _vision5.remember_landscape(ctx.session, fresh)
        ctx.session.last_status, ctx.session.last_body = status, resp


def _atlas_propose(ctx: _Ctx) -> None:
    status, body = ctx.main()
    if isinstance(body, dict):
        ctx.session.scratch["atlas_proposal"] = body


def _atlas_save(ctx: _Ctx) -> None:
    proposal = ctx.session.scratch.get("atlas_proposal") or {}
    proposals = proposal.get("proposals") or []
    chosen = ctx.args.get("cartridge_id")
    if chosen is None:
        chosen = proposal.get("recommended") or ""
    picked = next((p for p in proposals if p.get("domain_key") == chosen), None)
    bindings = [{"topic_id": b.get("topic_id"), "atlas_node_id": b.get("atlas_node_id")}
                for b in (picked or {}).get("bindings") or [] if isinstance(b, dict)]
    ctx.main(json={"cartridge_id": chosen, "topic_bindings": bindings})


def _create_student(ctx: _Ctx) -> None:
    username = str(ctx.args.get("username") or f"uxsim_student_{ctx.session.persona_id}")
    body = {"username": username, "email": str(ctx.args.get("email") or f"{username}@uxsim.invalid"),
            "password": str(ctx.args.get("password") or ctx.session.password or "uxsim-persona-pass")}
    status, resp = ctx.main(json=body)
    if status and status < 300:
        ctx.session.maps.setdefault("created_students", {})[username] = body["password"]


def _group_create(ctx: _Ctx) -> None:
    status, body = ctx.main(json={"name": str(ctx.args.get("name") or "uxsim"),
                                  "description": str(ctx.args.get("description") or "")})
    if isinstance(body, dict):
        ctx.session.remember("groups", _first(body, "id", "group_id"))


def _group_add(ctx: _Ctx) -> None:
    ctx.main(json={"username": ctx.args.get("username"), "email": ctx.args.get("email")})


def _copilot(ctx: _Ctx) -> None:
    s = ctx.session
    history = s.history_for("copilot")
    message = str(ctx.args.get("message") or "")
    status, body = ctx.main(json={"message": message, "history": history[-8:],
                                  "screen_context": {"tab": str(ctx.args.get("tab") or "")}})
    if status == 200 and isinstance(body, dict):
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": str(body.get("answer", ""))})


def _discuss_review(ctx: _Ctx) -> None:
    doc = ctx.args.get("document_id") or ctx.session.latest("documents")
    ctx.main(params={"role": "discussion_seed"}, overrides={"document_id": doc} if doc else None)


# ----------------------------------------------------------------------------
# §18.2 論理構造を辿る行為（2026-09-30）
# ----------------------------------------------------------------------------

_COMPONENT_CONTEXT = "/api/learning/courses/{course_id}/components/{component_id}/context"


def _remember_component_graph(s: PersonaSession, body: Any) -> None:
    """文脈応答の graph（focus / upper / lower）から、画面で移動できる部品を覚える。"""
    graph = body.get("graph") if isinstance(body, dict) else None
    s.scratch["last_component_graph"] = graph if isinstance(graph, dict) else None
    if not isinstance(graph, dict):
        return
    for lane in ("upper", "lower"):
        for it in graph.get(lane) or []:
            if isinstance(it, dict) and it.get("element_type") == "theory_component" and it.get("navigable"):
                s.remember("components", it.get("id"))


def _component_context(ctx: _Ctx) -> None:
    s = ctx.session
    cid = str(ctx.args.get("component_id") or "").strip()
    chip_label = ""  # ⚓ の表示名（display_label）— 第 7 波（IK-0571）: 文脈パネルが見出しを英語の内部名で上書きしない
    # 第 15 周: ペルソナは ⚓ の番号（"1" / "⚓1"）や element_ref で部品を指すことがある。
    # 番号・一覧の id を教材の ⚓ 一覧から部品 id に引き直す（画面のチップと同じ対象）。製品を素の番号で叩かない。
    ref = cid or str(ctx.args.get("element_ref") or "").strip()
    if ref:
        anchors = [a for a in material_anchors(s.material) if a.get("kind") in ("component", "theory_component")]
        m = re.fullmatch(r"(?:⚓\s*)?(\d+)", ref)
        hit = next((a for a in material_anchors(s.material) if (m and a["no"] == int(m.group(1))) or a["id"] == ref), None)
        if hit is not None:
            if hit.get("kind") not in ("component", "theory_component"):
                # 第 25 周 studio H25（第 4 周）: 画面では主張・式の ⚓ は要素の文脈で開く。部品の文脈の行為で指されても
                # 行わずに止めると、頭脳は「⚓ が開けない」と製品の欠陥として記した — 画面と同じ要素の文脈へ回す
                return _anchor_as_element(ctx, hit)
            cid = hit["id"]
            chip_label = str(hit.get("display_label") or "")
        elif m:
            return ctx.missing("component_anchor")  # 一覧に無い番号
        elif not anchors and not s.latest("components"):
            note_no_component_anchor(s)
            return ctx.missing("component_id")
    cid = cid or s.latest("components")
    if not cid:
        note_no_component_anchor(s)
        return ctx.missing("component_id")
    status, body = ctx.main(overrides={"component_id": cid},
                            params={"topic_id": s.topic_id} if getattr(s, "topic_id", None) else None)
    if status == 200 and chip_label and isinstance(body, dict) and body.get("available") is not False:
        body["_chip_display_label"] = chip_label  # 投影だけが読む（画面の data-display-label と同じ役）
    if status == 200:
        s.scratch["component_center"] = str(cid)
        # 第 25 周 第 6 周 doubt H40: 説明のポップアップの見出し（app.js は部品名）に使う名前を控える
        s.scratch["component_center_label"] = opened_component_label(body)
        _remember_component_graph(s, body)
        remember_open_context(s, str(cid), s.scratch["component_center_label"])


#: 第 25 周 第 8 周 doubt H48: ⚓（部品）を開く手で、開いているトピックの教材に部品の ⚓ が 1 つも無かった。経路は止めずに
#: ``inrun_unmet``（→ meta の ``blocked_precondition``）と runner_note に残す。残さないと「⚓ が画面に無い」が製品所見に読まれ、
#: 前提の不成立（⚓ の無いトピックを開いた）が meta から辿れない
NO_COMPONENT_ANCHOR_TEXT = "開いているトピックの教材に ⚓（部品）がある"


def note_no_component_anchor(session: PersonaSession) -> None:
    anchors = [a for a in material_anchors(session.material) if a.get("kind") in ("component", "theory_component")]
    if anchors:
        return
    tid = str(getattr(session, "topic_id", "") or "")
    reason = f"トピック {tid or '（未選択）'} の教材に ⚓（部品）が 0 個 — ⚓ のあるトピックを場面の材料（topic_id）で指す"
    found = session.scratch.setdefault("inrun_unmet", [])
    if not any(x.get("check") == "component_anchor" and x.get("reason") == reason for x in found):
        found.append({"text": NO_COMPONENT_ANCHOR_TEXT, "reason": reason, "check": "component_anchor"})
        runner_note(session, f"（ハーネス: 場面の前提「{NO_COMPONENT_ANCHOR_TEXT}」が成り立っていない — {reason}。"
                             "これは画面の文ではない）")


ANCHOR_AS_ELEMENT_NOTE = ("（ハーネス: ⚓{no} は{kind}の ⚓ — 画面ではこの ⚓ は要素の文脈で開くので、部品の文脈ではなく"
                          "要素の文脈として開いた。これは画面の文ではない）")
_ANCHOR_KIND_WORDS = {"claim": "主張", "theory_claim": "主張", "equation": "式", "figure": "図", "source": "出典"}


def _anchor_as_element(ctx: _Ctx, hit: dict) -> None:
    """主張・式の ⚓ を要素の文脈（``learning.element.context`` と同じ GET）で開き、観察に回したことを添える。"""
    from uxsim.runner.state import project_observation  # 遅延 import（循環回避）

    s = ctx.session
    etype = {"theory_component": "component", "theory_claim": "claim"}.get(str(hit.get("kind")), str(hit.get("kind")))
    status, body = ctx.call("GET", "/api/learning/courses/{course_id}/elements/{element_type}/{element_id}/context",
                            overrides={"element_type": etype, "element_id": str(hit.get("id") or "")},
                            params={"topic_id": s.topic_id} if getattr(s, "topic_id", None) else None)
    if status is None:
        return
    note = ANCHOR_AS_ELEMENT_NOTE.format(no=hit.get("no", ""), kind=_ANCHOR_KIND_WORDS.get(str(hit.get("kind")), "要素"))
    runner_note(s, f"runner_choice: ⚓{hit.get('no', '')}（{hit.get('kind')}）は要素の文脈で開いた")
    s.last_status, s.last_body = status, {"_ui_fact": note + "\n" + project_observation("learning.element.context",
                                                                                         status, body)}


def _hop_target(graph: Any, wanted: str = "") -> str:
    """直前の文脈図で移動できる隣の部品（画面の「旅」と同じく navigable な theory_component だけ）。"""
    from uxsim.runner.state import component_hop_targets  # 投影の「番号 N」と同じ並び（第 25 周 share H2）

    cands = component_hop_targets(graph)
    if wanted:
        m = re.fullmatch(r"(?:番号\s*)?(\d+)", wanted.strip())
        if m:
            i = int(m.group(1))
            return cands[i - 1] if 1 <= i <= len(cands) else ""
        return wanted if wanted in cands else ""
    return cands[0] if cands else ""


def _component_context_hop(ctx: _Ctx) -> None:
    s = ctx.session
    graph = s.scratch.get("last_component_graph")
    target = _hop_target(graph, str(ctx.args.get("component_id") or ""))
    if not target:
        return ctx.missing("adjacent_component")
    status, body = ctx.main(overrides={"component_id": target},
                            params={"topic_id": s.topic_id} if getattr(s, "topic_id", None) else None)
    if status == 200:
        s.scratch["component_center"] = target
        _remember_component_graph(s, body)
        # 第 25 周 第 8 周 share H51: 移った先の部品の名前も控える（app.js は文脈パネルを開くたびに
        # ``instance.component.label || frozen_label`` を ``state.openContextElement.element_label`` に控える — IK-1064）
        label = opened_component_label(body)
        s.scratch["component_center_label"] = label
        remember_open_context(s, target, label)


def _claim_refs(ctx: _Ctx) -> None:
    """画面（app.js の出典カード ``data-lx-ledger-chunk``）は実在の出典チャンクにだけ claim-refs を引く。
    宛先は回答の出典か開いた出典本文のチャンクだけ（トピック教材の擬似 id ``topic:tNN`` は使わない — 第 17 周）。"""
    arg = str(ctx.args.get("chunk_id") or "").strip()
    cid = "" if is_pseudo_chunk_id(arg) else arg
    cid = cid or ctx.session.latest("source_chunks")
    if not cid:
        return ctx.missing("chunk_id")
    status, body = ctx.main(overrides={"chunk_id": cid})
    for c in _items(body, "claims"):
        if isinstance(c, dict):
            ctx.session.remember("elements", _first(c, "id", "claim_id"))
            if _first(c, "id", "claim_id"):
                ctx.session.remember("element_types", "claim")


def _chat_ask_selection(ctx: _Ctx) -> None:
    """画面では教材に実在する文しか選べない。``selection_ref``（「区画番号:文番号」）で投影の文を選ぶか、
    ``selection_text`` が教材本文に含まれることを確かめる（第 14 周: 本文に無い文を選んだことになっていた）。"""
    sents = material_sentences(ctx.session.material)
    norm = lambda x: re.sub(r"[\s*$]+", "", x)  # 画面で選ぶ文は描画後（強調・数式の区切りは見えない）
    text = str(ctx.args.get("selection_text") or "").strip()
    ref = str(ctx.args.get("selection_ref") or "").strip()
    ref_seg: Optional[int] = None
    ref_text = ""
    if ref:
        m = re.fullmatch(r"(\d+)\s*[:：-]\s*(\d+)", ref)
        if m and int(m.group(1)) < len(sents) and 1 <= int(m.group(2)) <= len(sents[int(m.group(1))]):
            ref_seg, ref_text = int(m.group(1)), sents[int(m.group(1))][int(m.group(2)) - 1]
        elif not text:
            return ctx.missing("selection_ref")
    # 第 23 周: 画面の「ここについて質問」は選んだ文そのものを送る。頭脳が文（selection_text）を書いたらそれを優先し、
    # selection_ref は区画の特定にだけ使う（ref の文で text を上書きしていた — st-02 run 27821 seq7）。
    if not text:
        text = ref_text
    elif ref_text and norm(ref_text) != norm(text):
        runner_note(ctx.session, "runner_choice: selection_text と selection_ref の文が食い違ったので、"
                                 "選んだ文（selection_text）を送った")
    if not text:
        return ctx.missing("selection_text")
    ctx.args["selection_text"] = text
    hits = [i for i, ss in enumerate(sents) if norm(text) in norm("".join(ss))]
    if sents and not hits:
        return ctx.missing("selection_text_not_in_material")
    if ref_seg is not None and (not hits or ref_seg in hits):
        ctx.args["selection_segment_id"] = ref_seg
    elif hits and not isinstance(ctx.args.get("selection_segment_id"), int):
        ctx.args["selection_segment_id"] = hits[0]
    seg = ctx.args.get("selection_segment_id")
    _chat(ctx, **{"intent_mode": "on_path", **_voice_kwargs(ctx)},
          selection_segment_id=seg if isinstance(seg, int) else None)


_CYCLE_MODES = ("elicit", "diff")


def _chat_cycle(ctx: _Ctx) -> None:
    """discuss.js と同じく議論中の会話に cycle_mode を添えて送る（不正値はそのまま送り 422 を見せる）。"""
    mode = str(ctx.args.get("cycle_mode") or "").strip()
    if not mode:
        return ctx.missing("cycle_mode")
    _mark_voice_discuss(ctx)
    _chat(ctx, topic_id=DISCUSSION_TOPIC_ID, intent_mode="discuss", discuss_scope="course_sources",
          cycle_mode=mode)


def _atlas_threads(ctx: _Ctx) -> None:
    s = ctx.session
    status, body = ctx.main(params={"course": s.course_id, "topic": s.topic_id, "level": ctx.args.get("level") or 2})
    if status == 200 and isinstance(body, dict):
        s.scratch["atlas_threads_present"] = isinstance(body.get("threads"), dict)


def _atlas_neighbors(ctx: _Ctx) -> None:
    if not (ctx.args.get("node_id") or ctx.session.latest("nodes")):
        _, body = ctx.call("GET", "/api/me/personal-network")
        for n in _items(body, "nodes"):
            if isinstance(n, dict):
                ctx.session.remember("nodes", _first(n, "id", "node_id"))
    nid = ctx.args.get("node_id") or ctx.session.latest("nodes")
    if not nid:
        return ctx.missing("node_id")
    ctx.main(params={"node_id": nid})


def _doc_of(ctx: _Ctx) -> str:
    s = ctx.session
    return str(ctx.args.get("document_id") or s.latest("graph_documents") or s.latest("documents") or "")


def _doc_view(ctx: _Ctx) -> None:
    doc = _doc_of(ctx)
    ctx.main(overrides={"document_id": doc} if doc else None)


def _seminar_brief(ctx: _Ctx) -> None:
    doc = str(ctx.args.get("document_ref") or "") or _doc_of(ctx)
    ctx.main(overrides={"document_ref": doc} if doc else None)


def _deliberation_overview(ctx: _Ctx) -> None:
    s = ctx.session
    etype = ctx.args.get("element_type") or "theory_component"
    eid = ctx.args.get("element_id") or (s.latest("components") if etype == "theory_component" else s.latest("elements"))
    if not eid:
        return ctx.missing("element_id")
    doc = _doc_of(ctx)
    ctx.main(params={"document_id": doc} if doc else None, overrides={"element_type": etype, "element_id": eid})


NODE_UNRESOLVABLE_FACT = ("このノードは論理要素として解決できないため、ノード対話は開始できません。"
                          "グラフ全体との対話をご利用ください。")  # admin-graph-review.js の事実文の逐語ミラー


_ID_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:\-]+$")


def _label_key(text: Any) -> str:
    import unicodedata
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text or ""))).casefold()


def _node_by_ref(nodes: list[dict], ref: str) -> Optional[dict]:
    """ペルソナが渡したノードの指定（id か画面のラベル）をグラフのノードに引く。ラベルは完全一致 → 一意な部分一致。"""
    ref = str(ref or "").strip()
    if not ref:
        return None
    for n in nodes:
        if n["id"] == ref:
            return n
    key = _label_key(ref)
    exact = [n for n in nodes if any(_label_key(lb) == key for lb in n.get("labels") or [])]
    if exact:
        return exact[0]
    part = [n for n in nodes if any(key and (key in _label_key(lb) or _label_key(lb) in key)
                                    for lb in n.get("labels") or [] if _label_key(lb))]
    return part[0] if len(part) == 1 else None


def _node_chat(ctx: _Ctx) -> None:
    """admin-graph-review.js の openNodeChat と同じ: ノード → 実体要素（deliberationTargetId）→ W層セッション → messages。

    - ノードの指定（node_id / component_id に渡された id かラベル）はグラフのノードに引き、画面と同じ規則
      （DB UUID → representative_component_id → linked_component_ids の先頭）で実体要素にする。
    - 指定が無ければ直前に選んだノード、それも無ければ主グラフの先頭で実体要素に解決できるノード（第 18 周:
      直近に覚えた id（ノードの component_id）をそのまま送り「この要素は見つかりませんでした」で止まった）。
    - ラベルを名指ししたのに引けないときは、覚えている古い id を送らない。
    - 解決できないノード、およびセッション作成の 404 / 422 は、画面と同じく事実文に置き換える。
    screen_context は画面の getScreenContext と同じ形。
    """
    s = ctx.session
    doc = _doc_of(ctx)
    targets = s.maps.get("node_targets") or {}
    nodes: list[dict] = list(s.scratch.get("graph_nodes") or [])
    node_arg = str(ctx.args.get("node_id") or "").strip()
    comp = str(ctx.args.get("component_id") or "").strip()
    layer = "main"
    picked: Optional[dict] = None
    named = node_arg or (comp if comp and not _UUID_RE.match(comp) and comp not in set(targets.values()) else "")
    if named and nodes:
        picked = _node_by_ref(nodes, named)
        if picked is None and named not in targets and not _UUID_RE.match(named):
            if named == comp and _ID_TOKEN_RE.match(comp):
                named = ""  # 部品の id（comp_* 等）を component_id に直接渡した — そのまま実体要素として開く
            else:
                # 名指ししたノード（ラベル）が画面のグラフに無い。覚えている古い id で代用しない
                return ctx.missing("graph_node")
    if picked is None and named in targets:
        picked = next((n for n in nodes if n["id"] == named), {"id": named, "target": targets[named], "layer": "main"})
    if picked is None and not named and not comp and nodes:
        sel = s.scratch.get("graph_selected_node")
        picked = next((n for n in nodes if n["id"] == sel), None) or next(
            (n for n in nodes if n.get("layer") == "main" and n.get("target")), None)
    if picked is not None:
        node_arg, comp, layer = picked["id"], picked.get("target") or "", picked.get("layer") or "main"
        s.scratch["graph_selected_node"] = picked["id"]
        if not comp:
            # 画面は解決できないノードではリクエストせず事実文を出す
            s.last_status, s.last_body = None, {"_ui_fact": NODE_UNRESOLVABLE_FACT}
            return
    elif not comp and node_arg:
        comp = node_arg if _UUID_RE.match(node_arg) else ""
    if not comp and not node_arg and not nodes:
        comp = str(s.latest("components") or "")  # グラフを開いていない（覚えた部品の id で開く）
    if not comp:
        return ctx.missing("component_id")
    key = f"{doc}|{comp}"
    sid = s.maps.setdefault("node_sessions", {}).get(key)
    if not sid:
        status, body = ctx.call("POST", "/api/admin/deliberation/sessions",
                                json={"scope": "document", "element_type": "theory_component",
                                      "element_id": comp, "document_id": doc or None, "title": ""})
        if status in (404, 422):
            # 画面は 404 / 422 を「論理要素として解決できない」縮退として事実文に置き換える（内部文言を素通ししない）
            s.last_status, s.last_body = None, {"_ui_fact": NODE_UNRESOLVABLE_FACT}
            return
        sess = body.get("session") if isinstance(body, dict) else None
        sid = str(sess.get("id")) if isinstance(sess, dict) and sess.get("id") else ""
        if not sid:
            return
        s.maps["node_sessions"][key] = sid
    node_id = node_arg or comp
    screen = graph_screen_context(doc, node_id=node_id, component_id=comp, layer=layer)
    ctx.main(json={"content": str(ctx.args.get("content") or ctx.args.get("message") or ""),
                   "screen_context": screen}, overrides={"session_id": sid})


def _approve_claim(ctx: _Ctx) -> None:
    cid = ctx.args.get("claim_id") or ctx.session.latest("claims")
    if not cid:
        return ctx.missing("claim_id")
    ctx.main(json={"review_status": str(ctx.args.get("review_status") or "teacher_approved")},
             overrides={"claim_id": cid})


def _graph_open_structure(ctx: _Ctx) -> None:
    """グラフレビューを開く + 根拠の主張（reference_index.claims の DB 行があるもの）を覚える。"""
    _graph_open(ctx)
    body = ctx.session.last_body
    ref = body.get("reference_index") if isinstance(body, dict) else None
    if not isinstance(ref, dict) and isinstance(body, dict) and isinstance(body.get("graph"), dict):
        ref = body["graph"].get("reference_index")
    claims = ref.get("claims") if isinstance(ref, dict) else None
    for entry in (claims or {}).values() if isinstance(claims, dict) else []:
        if isinstance(entry, dict):
            ctx.session.remember("claims", entry.get("claim_id") or entry.get("parent_claim_id"))


def _profile_name(ctx: _Ctx) -> None:
    """両画面の「👤 名前」（第 25 周 第 5 波・IK-0848 / IK-1007）: GET /api/me/profile で読み、``profile_name`` が
    渡されたときだけ PATCH する。画面の文はサーバの ``label`` / ``note`` / ``message`` をそのまま描く。"""
    st, body = ctx.call("GET", "/api/me/profile")
    current = body if isinstance(body, dict) else {}
    lines = ["👤 名前"]
    if st is None or st >= 400:
        lines.append(f"（名前を読み込めませんでした: HTTP {st}）")
    else:
        lines.append(f"共有先に出る名前: {current.get('label') or ''}")
        if current.get("note"):
            lines.append(str(current["note"]))
    if "profile_name" not in ctx.args or ctx.args.get("profile_name") is None:
        ctx.session.last_status, ctx.session.last_body = st, {"_ui_fact": "\n".join(lines)}
        return
    want = str(ctx.args.get("profile_name") or "")
    st2, body2 = ctx.main(json={"profile_name": want})
    if st2 is not None and st2 < 400 and isinstance(body2, dict):
        lines.append(f"（名前の欄に「{want}」と書いて［保存］）" if want else "（名前の欄を空にして［保存］）")
        lines.append(str(body2.get("message") or ""))
        lines.append(f"共有先に出る名前: {body2.get('label') or ''}")
    else:
        detail = body2.get("detail") if isinstance(body2, dict) else ""
        lines.append(f"（保存できませんでした: {detail or f'HTTP {st2}'}）")
    ctx.session.last_status, ctx.session.last_body = st2, {"_ui_fact": "\n".join(x for x in lines if x)}


_HANDLERS: dict[str, Callable[[_Ctx], None]] = {
    "learning.profile.name": _profile_name,  # 第 25 周 第 5 波（IK-0848 / IK-1007）
    "admin.profile.name": _profile_name,
    "learning.chat.delete_message": lambda c: _vision5.delete_message(c),
    "learning.chat.history": _chat_history,
    "learning.traces.list": lambda c: _vision5.traces_list(c),
    "learning.traces.map_exclude": lambda c: _vision5.map_exclude(c),
    "learning.traces.map_restore": lambda c: _vision5.map_restore(c),
    "learning.records.export": lambda c: _vision5.records_export(c),
    "learning.disclosure.view": lambda c: _vision5.disclosure_view(c),
    "learning.cycle.landing_candidates": lambda c: _vision5.landing_candidates(c),
    "learning.cycle.return_door": lambda c: _vision5.return_door(c),
    "learning.reconstruction.revise": _recon_revise,
    "admin.courses.list": lambda c: _vision5.courses_list(c),
    "admin.release_review.row_status": lambda c: _vision5.row_status(c),
    "admin.release_review.read_evidence": lambda c: _vision5.read_evidence(c),
    "learning.course.list": _course_list,
    "learning.course.enroll": _course_enroll,
    "learning.course.open": _course_open,
    "learning.topic.open": _topic_open,
    "learning.chat.ask": _chat_ask,
    "learning.chat.casual": _chat_casual,
    "learning.chat.backstage": _chat_backstage,
    "learning.chat.usage_help": _chat_usage_help,
    "learning.chat.rewrite": _chat_rewrite,
    "learning.discuss.ask": _discuss_ask,
    "learning.corpus.discuss_ask": _corpus_discuss,
    "learning.check.take": _check_take,
    "learning.source_chunk.open": _source_chunk_open,
    "learning.check.self_check": _check_self_check,
    "learning.discuss.opening": _discuss_opening,
    "learning.cycle.intention": _cycle_intention,
    "learning.cycle.anchor": _cycle_anchor,
    "learning.tension.digest": _digest("traces"),
    "learning.tension.confirm": _trace_action("traces", ("learner_text",)),
    "learning.tension.dismiss": _trace_action("traces"),
    "learning.anchors.digest": _digest("anchor_traces"),
    "learning.anchors.confirm": _trace_action("anchor_traces", ("doubt_type", "anchor_type", "anchor_id")),
    "learning.anchors.dismiss": _trace_action("anchor_traces"),
    "learning.reconstruction.next": _recon_next,
    "learning.reconstruction.submit": _recon_submit,
    "learning.reconstruction.self_check": _recon_self_check,
    "learning.symbol.lookup": _symbol_lookup,
    "learning.descent.ladder": _descent_ladder,
    "learning.element.context": _element_context,
    "learning.personal_network.mine": _network,
    "learning.personal_network.journey": _network_node(()),
    "learning.personal_network.nearby": _network_node(("mode", "center_component_id")),
    "learning.atlas.view": _atlas_view,
    "learning.corpus.domains": _corpus_domains,
    "learning.corpus.documents": _corpus_documents,
    "learning.help.inspect": _help_inspect("/api/learning/help/ui-anchor-events"),
    "learning.voice.speak": _voice_speak,
    "admin.materials.list": _materials_list,
    "admin.materials.get": _materials_get,
    "admin.materials.upload_url": _upload_url,
    "admin.graph_review.open": _graph_open_structure,
    "admin.graph_review.chat": _graph_chat,
    "admin.course_builder.session_create": _cb_session_create,
    "admin.course_builder.chat": _cb_chat,
    "admin.course_builder.register": _cb_register,
    "admin.course.visibility": _course_visibility,
    "admin.materials.visibility": _course_visibility,
    "admin.release_review.placements": _release_placements,
    "admin.release_review.accept": _release_accept,
    "admin.atlas_binding.propose": _atlas_propose,
    "admin.atlas_binding.save": _atlas_save,
    "admin.users.create_student": _create_student,
    "admin.groups.create": _group_create,
    "admin.groups.add_member": _group_add,
    "admin.copilot.chat": _copilot,
    "admin.help.inspect": _help_inspect("/api/admin/assistant/help/ui-anchor-events"),
    "admin.discuss_opening_review.list": _discuss_review,
    "learning.component.context": _component_context,
    "learning.component.context_hop": _component_context_hop,
    "learning.chunk.claim_refs": _claim_refs,
    "learning.chat.ask_selection": _chat_ask_selection,
    "learning.chat.cycle": _chat_cycle,
    "learning.atlas.threads": _atlas_threads,
    "learning.atlas.neighbors": _atlas_neighbors,
    "admin.paper_layer.view": _doc_view,
    "admin.theory_modules.view": _doc_view,
    "admin.theory_modules.related": _doc_view,
    "admin.graph_review.node_chat": _node_chat,
    "admin.deliberation.overview": _deliberation_overview,
    "admin.graph_review.approve_claim": _approve_claim,
    "admin.seminar_brief.view": _seminar_brief,
}

# 検証シミュレーション層（SB層）の行為は別モジュール（契約 = docs/features/simulation_bridge_design.md §14.3）
from uxsim.runner import simlab_exec as _simlab_exec  # noqa: E402

_HANDLERS.update(_simlab_exec.HANDLERS)

# 第 25 周（空白の 4 面）: 面ごとの実行・投影モジュール（契約 = persona_enactment_testing_design.md §22）
from uxsim.runner import atlas_exec as _atlas_exec  # noqa: E402  r25:atlas
from uxsim.runner import share_exec as _share_exec  # noqa: E402  r25:share
from uxsim.runner import doubt_exec as _doubt_exec  # noqa: E402  r25:doubt
from uxsim.runner import studio_exec as _studio_exec  # noqa: E402  r25:studio

_HANDLERS.update(_atlas_exec.HANDLERS)  # r25:atlas
_HANDLERS.update(_share_exec.HANDLERS)  # r25:share
_HANDLERS.update(_doubt_exec.HANDLERS)  # r25:doubt
_HANDLERS.update(_studio_exec.HANDLERS)  # r25:studio（既存の learning.lecture.audio_status / sequence も差し替える）
