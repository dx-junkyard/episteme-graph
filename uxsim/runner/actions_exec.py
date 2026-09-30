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
from uxsim.runner.state import PersonaSession, material_anchors, material_sentences
from uxsim.schema import HttpTrace

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

    def body_args(self) -> dict:
        """path の穴に使った引数を除いた残り（既定の JSON / query）。"""
        holes = set(path_holes(self.action.api[1])) if self.action.api else set()
        return {k: v for k, v in self.args.items() if k not in holes}


def execute(action_id: str, args: dict, session: PersonaSession, client: EpistemeClient) -> list[HttpTrace]:
    """行為を 1 つ実行し、そのステップの ``HttpTrace`` 列を返す。"""
    client.take_traces()
    action = registry.get(action_id)
    if action is None or action.unsupported or action.api is None:
        session.last_status, session.last_body = None, None
        return [HttpTrace(method="-", path=f"unsupported:{action_id}", error="unsupported")]
    ctx = _Ctx(action, args, session, client)
    handler = _HANDLERS.get(action_id, _default)
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

def _course_list(ctx: _Ctx) -> None:
    status, body = ctx.main()
    for c in _items(body, "courses"):
        if isinstance(c, dict) and c.get("id"):
            ctx.session.remember("enrollable" if c.get("is_enrollable") else "courses", c["id"])


def _course_enroll(ctx: _Ctx) -> None:
    cid = ctx.args.get("course_id") or ctx.session.latest("enrollable") or ctx.session.latest("courses")
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


def _course_open(ctx: _Ctx) -> None:
    s = ctx.session
    cid = (ctx.args.get("course_id") or s.course_id or s.latest("courses") or s.scratch.get("default_course_id")
           or s.latest("enrollable"))
    if not cid:
        ctx.call("GET", "/api/learning/courses/{course_id}")  # precondition を記録させる
        return
    _load_course(ctx, cid)


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
    tid = ctx.args.get("topic_id") or _topic_by_hint(s, ctx.args.get("topic_hint_terms")) or _next_topic(s)
    status, body = ctx.main(overrides={"topic_id": tid} if tid else None)
    if status == 200 and tid:
        s.topic_id = tid
        s.remember("opened_topics", tid)
        s.material = body if isinstance(body, dict) else {}
        for ch in _items(body, "chunks"):
            if not isinstance(ch, dict):
                continue
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
    ov = dict(overrides or {})
    ov.setdefault("topic_id", tid)
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
            if isinstance(src, dict):
                s.remember("chunks", src.get("chunk_id"))
                if src.get("chunk_id"):
                    s.scratch["last_sources"][str(src.get("index") or len(s.scratch["last_sources"]) + 1)] = str(src["chunk_id"])
        confirm = resp.get("anchor_confirm")
        if isinstance(confirm, dict):
            s.remember("anchor_traces", confirm.get("trace_id"))


def _chat_ask(ctx: _Ctx) -> None:
    _chat(ctx, intent_mode="on_path")


def _chat_casual(ctx: _Ctx) -> None:
    _chat(ctx, intent_mode="casual")


def _chat_backstage(ctx: _Ctx) -> None:
    _chat(ctx, backstage=True)


def _chat_usage_help(ctx: _Ctx) -> None:
    _chat(ctx, support_action="usage_help", ui_anchor=ctx.args.get("ui_anchor"))


def _discuss_ask(ctx: _Ctx) -> None:
    _chat(ctx, topic_id=DISCUSSION_TOPIC_ID, intent_mode="discuss",
          discuss_scope=ctx.args.get("discuss_scope") or "course_sources")


def _chat_rewrite(ctx: _Ctx) -> None:
    s = ctx.session
    history = s.chat_history
    target = ctx.args.get("replace_message_id") or next(
        (m.get("id") for m in reversed(history) if m.get("role") == "user" and m.get("id")), "")
    if target:
        idx = next((i for i, m in enumerate(history) if m.get("id") == target), None)
        if idx is not None:
            del history[idx:]  # UI と同じく当該メッセージ以降を捨てる
    _chat(ctx, replace_message_id=target or None)


def _corpus_discuss(ctx: _Ctx) -> None:
    doc = ctx.args.get("document_id") or ctx.session.latest("corpus_documents") or ctx.session.latest("documents")
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
    ctx.main(overrides={"chunk_id": cid} if cid else None)


def _discuss_opening(ctx: _Ctx) -> None:
    status, body = ctx.main()
    for d in _items(body, "documents"):
        if isinstance(d, dict):
            ctx.session.remember("documents", _first(d, "document_id", "id"))


def _cycle_intention(ctx: _Ctx) -> None:
    status, body = ctx.main(json=ctx.body_args())
    if isinstance(body, dict):
        ctx.session.remember("intention_traces", _first(body, "trace_id", "id"))


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


def _digest(kind: str) -> Callable[[_Ctx], None]:
    def run(ctx: _Ctx) -> None:
        status, body = ctx.main()
        for it in _items(body, "items", "candidates", "digest"):
            if isinstance(it, dict):
                ctx.session.remember(kind, _first(it, "trace_id", "id"))
    return run


def _trace_action(kind: str, json_keys: tuple[str, ...] = ()) -> Callable[[_Ctx], None]:
    def run(ctx: _Ctx) -> None:
        tid = ctx.args.get("trace_id") or ctx.session.latest(kind)
        body = {k: ctx.args[k] for k in json_keys if ctx.args.get(k) not in (None, "")}
        ctx.main(json=body, overrides={"trace_id": tid} if tid else None)
    return run


def _recon_next(ctx: _Ctx) -> None:
    status, body = ctx.main()
    if isinstance(body, dict):
        item = body.get("item") if isinstance(body.get("item"), dict) else body
        ctx.session.remember("recon_items", _first(item, "item_id", "id"))
        ctx.session.scratch["recon_space"] = [o for o in item.get("response_space") or [] if isinstance(o, dict)]


def _recon_submit(ctx: _Ctx) -> None:
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
    status, body = ctx.main(json={"course_id": ctx.session.course_id, "response": resp,
                                  "revision_of": ctx.args.get("revision_of")})
    if isinstance(body, dict):
        ctx.session.remember("recons", _first(body, "recon_id", "reconstruction_id", "id"))


def _recon_self_check(ctx: _Ctx) -> None:
    ctx.main(json={"result": str(ctx.args.get("result") or "agreed")})


def _symbol_lookup(ctx: _Ctx) -> None:
    s = ctx.session
    if not (ctx.args.get("symbol") or s.latest("symbols_seen")):
        return ctx.missing("symbol")
    ctx.main(params={"symbol": ctx.args.get("symbol") or s.latest("symbols_seen"),
                     "equation_id": ctx.args.get("equation_id") or s.latest("equations"),
                     "chunk_id": ctx.args.get("chunk_id") or s.latest("chunks"),
                     # 実フロント（openSymbolLookup）と同じく表示中トピックを添える（第 15 周: 別論文の定義を返す取り違えの是正が効く条件）
                     "topic_id": getattr(s, "topic_id", None)})


def _descent_ladder(ctx: _Ctx) -> None:
    s = ctx.session
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
        ctx.call("GET", "/api/learning/courses/{course_id}/components/{component_id}/context",
                 overrides={"component_id": eid})
    else:
        # 実フロントは表示中トピックを添える（式 ID の論文またぎの衝突をトピックの論文で解く）
        ctx.main(overrides={"element_type": etype, "element_id": eid},
                 params={"topic_id": s.topic_id} if getattr(s, "topic_id", None) else None)


def _network(ctx: _Ctx) -> None:
    status, body = ctx.main()
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
        ctx.main(params=params)
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
    return run


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
    for n in _items(graph, "nodes"):
        if isinstance(n, dict):
            ctx.session.remember("components", _first(n, "db_id", "component_db_id", "component_id", "id"))
            nid = _first(n, "id", "node_id")
            tgt = deliberation_target_id(n)
            if nid:
                targets[nid] = tgt


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
    ctx.main(json={"visibility": str(ctx.args.get("visibility") or "public"),
                   "group_id": ctx.args.get("group_id")})


def _release_placements(ctx: _Ctx) -> None:
    status, body = ctx.main()
    for doc in _items(body, "documents"):
        for p in _items(doc, "placements"):
            if isinstance(p, dict):
                ctx.session.remember("placements", _first(p, "id", "placement_id"))


def _release_accept(ctx: _Ctx) -> None:
    presented = ctx.session.all("placements")
    ctx.main(json={"presented_placement_ids": presented} if presented else {})


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
    # 第 15 周: ペルソナは ⚓ の番号（"1" / "⚓1"）や element_ref で部品を指すことがある。
    # 番号・一覧の id を教材の ⚓ 一覧から部品 id に引き直す（画面のチップと同じ対象）。製品を素の番号で叩かない。
    ref = cid or str(ctx.args.get("element_ref") or "").strip()
    if ref:
        anchors = [a for a in material_anchors(s.material) if a.get("kind") in ("component", "theory_component")]
        m = re.fullmatch(r"(?:⚓\s*)?(\d+)", ref)
        hit = next((a for a in material_anchors(s.material) if (m and a["no"] == int(m.group(1))) or a["id"] == ref), None)
        if hit is not None:
            if hit.get("kind") not in ("component", "theory_component"):
                return ctx.missing("component_anchor")  # 主張・式の ⚓ は部品の文脈では開けない
            cid = hit["id"]
        elif m:
            return ctx.missing("component_anchor")  # 一覧に無い番号
        elif not anchors and not s.latest("components"):
            return ctx.missing("component_id")
    cid = cid or s.latest("components")
    if not cid:
        return ctx.missing("component_id")
    status, body = ctx.main(overrides={"component_id": cid})
    if status == 200:
        s.scratch["component_center"] = str(cid)
        _remember_component_graph(s, body)


def _hop_target(graph: Any, wanted: str = "") -> str:
    """直前の文脈図で移動できる隣の部品（画面の「旅」と同じく navigable な theory_component だけ）。"""
    if not isinstance(graph, dict):
        return ""
    focus = str((graph.get("focus") or {}).get("id") or "") if isinstance(graph.get("focus"), dict) else ""
    cands = [str(it["id"]) for lane in ("upper", "lower") for it in graph.get(lane) or []
             if isinstance(it, dict) and it.get("element_type") == "theory_component" and it.get("navigable")
             and it.get("id") and str(it["id"]) != focus]
    if wanted:
        return wanted if wanted in cands else ""
    return cands[0] if cands else ""


def _component_context_hop(ctx: _Ctx) -> None:
    s = ctx.session
    graph = s.scratch.get("last_component_graph")
    target = _hop_target(graph, str(ctx.args.get("component_id") or ""))
    if not target:
        return ctx.missing("adjacent_component")
    status, body = ctx.main(overrides={"component_id": target})
    if status == 200:
        s.scratch["component_center"] = target
        _remember_component_graph(s, body)


def _claim_refs(ctx: _Ctx) -> None:
    cid = ctx.args.get("chunk_id") or ctx.session.latest("chunks")
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
    ref = str(ctx.args.get("selection_ref") or "").strip()
    if ref:
        m = re.fullmatch(r"(\d+)\s*[:：-]\s*(\d+)", ref)
        if not m or int(m.group(1)) >= len(sents) or not (1 <= int(m.group(2)) <= len(sents[int(m.group(1))])):
            return ctx.missing("selection_ref")
        ctx.args["selection_text"] = sents[int(m.group(1))][int(m.group(2)) - 1]
        ctx.args["selection_segment_id"] = int(m.group(1))
    text = str(ctx.args.get("selection_text") or "").strip()
    if not text:
        return ctx.missing("selection_text")
    norm = lambda x: re.sub(r"[\s*$]+", "", x)  # 画面で選ぶ文は描画後（強調・数式の区切りは見えない）
    hits = [i for i, ss in enumerate(sents) if norm(text) in norm("".join(ss))]
    if sents and not hits:
        return ctx.missing("selection_text_not_in_material")
    if hits and not isinstance(ctx.args.get("selection_segment_id"), int):
        ctx.args["selection_segment_id"] = hits[0]
    seg = ctx.args.get("selection_segment_id")
    _chat(ctx, intent_mode="on_path", selection_segment_id=seg if isinstance(seg, int) else None)


_CYCLE_MODES = ("elicit", "diff")


def _chat_cycle(ctx: _Ctx) -> None:
    """discuss.js と同じく議論中の会話に cycle_mode を添えて送る（不正値はそのまま送り 422 を見せる）。"""
    mode = str(ctx.args.get("cycle_mode") or "").strip()
    if not mode:
        return ctx.missing("cycle_mode")
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


def _node_chat(ctx: _Ctx) -> None:
    """admin-graph-review.js の openNodeChat と同じ: W層セッション（theory_component）→ messages。

    screen_context は画面の getScreenContext と同じ形（selection に document_id / node_id / component_id /
    graph_layer、view に mode / layer）。
    """
    s = ctx.session
    doc = _doc_of(ctx)
    targets = s.maps.get("node_targets") or {}
    node_arg = str(ctx.args.get("node_id") or "")
    comp = str(ctx.args.get("component_id") or "")
    if comp in targets:  # ノード id が component_id に渡された（画面はノードから実体要素を解決する）
        node_arg, comp = node_arg or comp, targets[comp]
    if not comp and node_arg:
        comp = targets.get(node_arg, node_arg if _UUID_RE.match(node_arg) else "")
    if not comp and not node_arg:
        comp = str(s.latest("components") or "")
        if comp in targets:
            node_arg, comp = comp, targets[comp]
    if not comp:
        # 画面は解決できないノードではリクエストせず事実文を出す
        return ctx.missing("component_id")
    key = f"{doc}|{comp}"
    sid = s.maps.setdefault("node_sessions", {}).get(key)
    if not sid:
        status, body = ctx.call("POST", "/api/admin/deliberation/sessions",
                                json={"scope": "document", "element_type": "theory_component",
                                      "element_id": comp, "document_id": doc or None, "title": ""})
        sess = body.get("session") if isinstance(body, dict) else None
        sid = str(sess.get("id")) if isinstance(sess, dict) and sess.get("id") else ""
        if not sid:
            return
        s.maps["node_sessions"][key] = sid
    node_id = node_arg or comp
    screen = graph_screen_context(doc, node_id=node_id, component_id=comp, layer="main")
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


_HANDLERS: dict[str, Callable[[_Ctx], None]] = {
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
