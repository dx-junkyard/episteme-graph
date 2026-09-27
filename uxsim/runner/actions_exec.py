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
from uxsim.runner.state import PersonaSession
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


def _load_course(ctx: _Ctx, course_id: str) -> tuple[Optional[int], Any]:
    status, body = ctx.call("GET", "/api/learning/courses/{course_id}", overrides={"course_id": course_id})
    if status == 200 and isinstance(body, dict):
        master = body.get("master_course") if isinstance(body.get("master_course"), dict) else body
        ctx.session.course_id = course_id
        ctx.session.topics = {str(t.get("id")): t for t in master.get("topics") or [] if isinstance(t, dict)}
        ctx.session.topic_ids = list(ctx.session.topics)
        for s in master.get("sources") or []:
            if isinstance(s, dict) and s.get("material_id"):
                ctx.session.remember("materials", s["material_id"])
    return status, body


def _course_open(ctx: _Ctx) -> None:
    cid = ctx.args.get("course_id") or ctx.session.course_id or ctx.session.latest("courses")
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


def _topic_open(ctx: _Ctx) -> None:
    s = ctx.session
    if s.course_id and not s.topic_ids:
        _load_course(ctx, s.course_id)
    tid = ctx.args.get("topic_id") or _next_topic(s)
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
        for src in resp.get("sources") or []:
            if isinstance(src, dict):
                s.remember("chunks", src.get("chunk_id"))
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
    q0 = qs[0] if qs else None
    question = ctx.args.get("question") or (q0.get("question", "") if isinstance(q0, dict) else (q0 or ""))
    ctx.main(json={"answer": str(ctx.args.get("answer", "")), "question": str(question),
                   "check_question": q0 if isinstance(q0, dict) else None})


def _discuss_opening(ctx: _Ctx) -> None:
    status, body = ctx.main()
    for d in _items(body, "documents"):
        if isinstance(d, dict):
            ctx.session.remember("documents", _first(d, "document_id", "id"))


def _cycle_intention(ctx: _Ctx) -> None:
    status, body = ctx.main(json=ctx.body_args())
    if isinstance(body, dict):
        ctx.session.remember("intention_traces", _first(body, "trace_id", "id"))


def _cycle_anchor(ctx: _Ctx) -> None:
    body = ctx.body_args()
    body.setdefault("topic_id", ctx.session.topic_id)
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


def _recon_submit(ctx: _Ctx) -> None:
    resp = ctx.args.get("response")
    if not isinstance(resp, dict):
        resp = {"text": str(resp or "")}
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
                     "chunk_id": ctx.args.get("chunk_id") or s.latest("chunks")})


def _descent_ladder(ctx: _Ctx) -> None:
    s = ctx.session
    if not (ctx.args.get("element_id") or s.latest("elements")):
        return ctx.missing("element_id")
    ctx.main(params={"element_type": ctx.args.get("element_type") or s.latest("element_types") or "claim",
                     "element_id": ctx.args.get("element_id") or s.latest("elements")})


def _element_context(ctx: _Ctx) -> None:
    s = ctx.session
    etype = ctx.args.get("element_type") or s.latest("element_types") or "claim"
    eid = ctx.args.get("element_id") or s.latest("elements")
    if etype == "component":
        ctx.call("GET", "/api/learning/courses/{course_id}/components/{component_id}/context",
                 overrides={"component_id": eid})
    else:
        ctx.main(overrides={"element_type": etype, "element_id": eid})


def _network(ctx: _Ctx) -> None:
    status, body = ctx.main()
    for n in _items(body, "nodes"):
        if isinstance(n, dict):
            ctx.session.remember("nodes", _first(n, "id", "node_id"))


def _network_node(extra: tuple[str, ...]) -> Callable[[_Ctx], None]:
    def run(ctx: _Ctx) -> None:
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
    for n in _items(graph, "nodes"):
        if isinstance(n, dict):
            ctx.session.remember("components", _first(n, "db_id", "component_db_id", "component_id", "id"))


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
    ctx.main(json={"content": str(ctx.args.get("content") or ctx.args.get("message") or "")},
             overrides={"document_id": doc, "session_id": sid})


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
    selected = ctx.args.get("selected_material_ids") or getattr(s, "cb_selected", None) or s.all("materials_selected") or s.all("materials")
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



def _unit_handles(topic: Any) -> list[str]:
    """admin.js の cbDraftUnitHandles と同じ（"U3" 等の handle を重複なく取り出す）。"""
    if not isinstance(topic, dict) or not isinstance(topic.get("units"), list):
        return []
    out: list[str] = []
    for item in topic["units"]:
        handle = item if isinstance(item, str) else (item.get("handle") or item.get("unit") or "") if isinstance(item, dict) else ""
        handle = str(handle or "").strip()
        if handle and handle not in out:
            out.append(handle)
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
                                      "prerequisites": prereqs, "misconceptions": [], "units": _unit_handles(t)})
            idx += 1
    for c in draft.get("concepts") or []:
        name = c if isinstance(c, str) else (c.get("name") if isinstance(c, dict) else "")
        if name:
            payload["concepts"].append({"name": name, "status": "future",
                                        "children": (c.get("children") if isinstance(c, dict) else None) or [], "expanded": False})
    selected = list(getattr(s, "cb_selected", None) or []) or list(s.all("materials_selected") or []) or list(s.all("materials") or [])
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
    "learning.personal_network.nearby": _network_node(("mode",)),
    "learning.atlas.view": _atlas_view,
    "learning.corpus.domains": _corpus_domains,
    "learning.corpus.documents": _corpus_documents,
    "learning.help.inspect": _help_inspect("/api/learning/help/ui-anchor-events"),
    "learning.voice.speak": _voice_speak,
    "admin.materials.list": _materials_list,
    "admin.materials.get": _materials_get,
    "admin.materials.upload_url": _upload_url,
    "admin.graph_review.open": _graph_open,
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
}
