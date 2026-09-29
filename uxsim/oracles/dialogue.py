"""審判 A・B の追加 — 対話の往復をまたいで見る検査（決定論・LLM 0 回）。

1 ペルソナ・1 セッションの transcript を順に読み、1 往復だけでは見えない食い違いを拾う:

a. 出典番号の振り直し（A）: 同じ会話（同じ chat の path）で、同じ ``chunk_id`` が後のターンで別の
   ``sources[].index`` になり、前のターンの回答本文に ``[出典N]`` がある — 履歴に残る過去の回答の
   番号が別のチャンクを指しうる（第 7 周 製品頭脳の観測⑤）。ペルソナごとに 1 件。
b. 区別できない出典（A）: 1 応答に同じ表示題の出典が 3 件以上あり、題以外に区別できる欄
   （``runner/digest.py::DISTINGUISH_KEYS``）が無いか全部同じ（IK-0381 型）。
c. 古い回答の再送（A）: 同じペルソナの続けての ``learning.chat.ask`` 2 回が、違う ``message`` に
   逐語で同じ ``answer`` を返した（第 7 周 st-06 の観測①）。縮退（degraded）は審判 A が別に見るので除く。
d. 学習者向け応答の数値の欄（B）: 学習画面の応答で、値が数の欄のうち位置・ID・時刻の許可リストに
   無いもの（``score`` / ``weight`` / ``confidence`` / 件数など）。欄の名前だけを見る（文字列の中の
   数は見ない — 教材の引用を拾わない）。ペルソナごとに 1 件。UI に描かれるかは browser runner の範囲。
e. precondition の連鎖（A・ハーネス）: 同じペルソナの 3 手以上が続けて ``precondition:*`` で終わった —
   runner の状態解決の穴で、製品の欠陥と取り違えないよう仮説の頭に「ハーネス:」を付ける。

応答の要約は ``HttpTrace.digest``（runner が応答全体から取る）を優先し、無い古い transcript では
``response_excerpt`` から読める範囲で代える（抜粋に出典が入っていなければ a・b は見送る）。
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

from uxsim.oracles.findings import FindingFactory, excerpt_json
from uxsim.runner.digest import (DISTINGUISH_KEYS, chat_record, chat_record_from_excerpt, numeric_keys,
                                 numeric_keys_from_excerpt)
from uxsim.schema import Finding, HttpTrace, TranscriptStep

CHAT_ACTIONS = ("learning.chat.", "learning.discuss.ask", "learning.corpus.discuss_ask")
CHAT_PATH_RE = re.compile(r"/chat$")
INDISTINCT_MIN = 3
PRECONDITION_CHAIN = 3
STALE_MIN_PREFIX = 200  # digest が無く本文が抜粋で切れているとき、一致とみなす最短の長さ

# 学習者向け DTO で数であってよい欄（位置・順序・版・座標・ID・時刻）。値を見せる欄ではない
NUMERIC_ALLOWLIST = frozenset({
    "index", "position", "chapter_index", "progress_pct", "seq", "slide_index", "segment_index", "chunk_index",
    "order", "order_index", "display_order", "level", "depth", "version", "skeleton_version", "revision",
    "page", "page_start", "page_end", "x", "y", "width", "height", "scroll_offset",
    # 描画の幾何（地図の SVG 座標系・点の数は表示上の量であって学習者に見せる値ではない）
    "w", "h", "viewBox", "dots", "cx", "cy", "r",
    # 学習者向けランドスケープの LS8 コーパス事実行「登録済み論文 N 件」（core/landscape/projection.py が設計で
    # 明示。分野の厚みではなくコースの登録数）
    "source_document_count", "placed_document_count",
})

HYP_RENUMBER = "回答本文の [出典N] が後のターンで別のチャンクを指す（番号がターンごとに振り直される）"
HYP_INDISTINCT = "1 つの回答に同じ表示の出典が並び、どれがどの根拠か区別できない"
HYP_STALE = "追いの質問に直前の回答が逐語で返った"
HYP_NUMERIC = "学習者向け応答に数値の項目が載っている（UI に出るかは browser runner で確認）"


def _allowed_numeric(key: str) -> bool:
    k = key.lower()
    return (k in NUMERIC_ALLOWLIST or k == "id" or k.endswith(("_id", "_ids", "_at", "_ts", "timestamp"))
            or k in ("ts", "time", "created", "updated"))


def _by_session(steps: Iterable[TranscriptStep]) -> dict[tuple[str, int], list[TranscriptStep]]:
    out: dict[tuple[str, int], list[TranscriptStep]] = {}
    for s in sorted(steps, key=lambda x: x.seq):
        out.setdefault((s.persona_id, s.session), []).append(s)
    return out


def _chat_trace(step: TranscriptStep) -> Optional[HttpTrace]:
    for t in step.http:
        if t.status == 200 and CHAT_PATH_RE.search(t.path or ""):
            return t
    return None


def chat_of(trace: HttpTrace) -> Optional[dict]:
    """trace の回答の要約（digest → 抜粋の順）。"""
    rec = (trace.digest or {}).get("chat")
    if isinstance(rec, dict):
        return rec
    body = excerpt_json(trace.response_excerpt)
    return chat_record(body) if body is not None else chat_record_from_excerpt(trace.response_excerpt)


def _index_map(rec: dict) -> dict[str, int]:
    """chunk_id → 番号。製品が index を埋めていなければ（全部 0）並び順で代える。"""
    sources = [s for s in rec.get("sources") or [] if isinstance(s, dict) and s.get("chunk_id")]
    use_pos = all(not s.get("index") for s in sources)
    out: dict[str, int] = {}
    for s in sources:
        out.setdefault(str(s["chunk_id"]), int(s.get("position") or 0) if use_pos else int(s.get("index") or 0))
    return out


# ----------------------------------------------------------------------------
# a. 出典番号の振り直し
# ----------------------------------------------------------------------------

def _check_renumbering(seq: list[TranscriptStep], factory: FindingFactory) -> list[Finding]:
    turns: dict[str, list[tuple[TranscriptStep, dict, dict[str, int]]]] = {}
    for step in seq:
        if not step.action_id.startswith(CHAT_ACTIONS):
            continue
        t = _chat_trace(step)
        rec = chat_of(t) if t is not None else None
        if not rec or not rec.get("sources_known"):
            continue
        cur = _index_map(rec)
        for prev_step, prev_rec, prev_map in turns.get(t.path, []):
            markers = set(prev_rec.get("answer_markers") or [])
            if not markers:
                continue
            moved = [(c, prev_map[c], j) for c, j in cur.items() if c in prev_map and prev_map[c] != j]
            if not moved:
                continue
            # 過去の本文が実際に引いている番号で動いたものを先に示す（より直接の根拠）
            moved.sort(key=lambda m: (m[1] not in markers, m[1]))
            chunk, old, new = moved[0]
            quote = (f"seq {prev_step.seq} の回答は [出典{old}] = chunk {chunk} を引いていたが、seq {step.seq} では同じ"
                     f" chunk が [出典{new}]（過去の本文の出典番号: {sorted(markers)}）")
            return [factory.make(oracle="A", severity="inconsistent", step=step, hypothesis=HYP_RENUMBER,
                                 quote=quote, steps=[prev_step.seq, step.seq],
                                 server_rows={f"renumbered[{step.persona_id}]": [
                                     {"chunk_id": c, "before": o, "after": n} for c, o, n in moved[:10]]})]
        turns.setdefault(t.path, []).append((step, rec, cur))
    return []


# ----------------------------------------------------------------------------
# b. 区別できない出典
# ----------------------------------------------------------------------------

def _check_indistinct(step: TranscriptStep, factory: FindingFactory) -> list[Finding]:
    t = _chat_trace(step)
    rec = chat_of(t) if t is not None else None
    if not rec or not rec.get("sources_known"):
        return []
    groups: dict[str, list[dict]] = {}
    for s in rec.get("sources") or []:
        if isinstance(s, dict):
            groups.setdefault(str(s.get("source_title") or ""), []).append(s)
    for title, items in groups.items():
        if len(items) < INDISTINCT_MIN:
            continue
        signatures = {tuple(str(s.get(k) or "") for k in DISTINGUISH_KEYS) for s in items}
        if len(signatures) == 1:
            shown = title or "（題なし）"
            extra = [k for k in DISTINGUISH_KEYS if items[0].get(k)]
            return [factory.make(oracle="A", severity="confused", step=step, hypothesis=HYP_INDISTINCT,
                                 quote=f"出典 {len(items)} 件が「{shown[:80]}」"
                                       + (f"（{', '.join(extra)} も同じ）" if extra else "（節・頁などの欄なし）"),
                                 layers=["rag_chat", "frontend_learning_ui"])]
    return []


# ----------------------------------------------------------------------------
# c. 古い回答の再送
# ----------------------------------------------------------------------------

def _same_answer(a: dict, b: dict) -> bool:
    if a.get("answer_sha") and b.get("answer_sha"):
        return a["answer_sha"] == b["answer_sha"] and bool(a.get("answer_head"))
    pa, pb = a.get("answer_prefix") or a.get("answer_head") or "", b.get("answer_prefix") or b.get("answer_head") or ""
    n = min(len(pa), len(pb))
    return n >= STALE_MIN_PREFIX and pa[:n] == pb[:n]


def _check_stale(seq: list[TranscriptStep], factory: FindingFactory) -> list[Finding]:
    out: list[Finding] = []
    prev: Optional[tuple[TranscriptStep, dict]] = None
    for step in seq:
        if step.action_id != "learning.chat.ask":
            continue
        t = _chat_trace(step)
        rec = chat_of(t) if t is not None else None
        if not rec or rec.get("degraded"):
            prev = None
            continue
        if prev is not None:
            p_step, p_rec = prev
            m1 = str(p_step.args.get("message") or "").strip()
            m2 = str(step.args.get("message") or "").strip()
            if m1 != m2 and _same_answer(p_rec, rec):
                out.append(factory.make(oracle="A", severity="inconsistent", step=step, hypothesis=HYP_STALE,
                                        quote=f"問い1「{m1[:60]}」/ 問い2「{m2[:60]}」→ 同じ回答「"
                                              f"{str(rec.get('answer_head') or '')[:80]}」",
                                        steps=[p_step.seq, step.seq]))
        prev = (step, rec)
    return out


# ----------------------------------------------------------------------------
# d. 学習者向け応答の数値の欄
# ----------------------------------------------------------------------------

def _trace_numeric_keys(t: HttpTrace) -> list[str]:
    keys = (t.digest or {}).get("numeric_keys")
    if isinstance(keys, list):
        return [str(k) for k in keys]
    body = excerpt_json(t.response_excerpt)
    return numeric_keys(body) if body is not None else numeric_keys_from_excerpt(t.response_excerpt)


def _check_numeric_keys(seq: list[TranscriptStep], factory: FindingFactory) -> list[Finding]:
    by_action: dict[str, set[str]] = {}
    hit_steps: list[TranscriptStep] = []
    for step in seq:
        if step.screen != "learning" or step.action_id == "auth.login" or step.action_id.startswith("unsupported"):
            continue
        found: set[str] = set()
        for t in step.http:
            if t.status != 200:
                continue
            found |= {k for k in _trace_numeric_keys(t) if not _allowed_numeric(k)}
        if found:
            by_action.setdefault(step.action_id, set()).update(found)
            hit_steps.append(step)
    if not hit_steps:
        return []
    first = hit_steps[0]
    keys = sorted(set().union(*by_action.values()))
    return [factory.make(oracle="B", severity="principle", step=first, hypothesis=HYP_NUMERIC, affordance="",
                         quote="数値の欄: " + ", ".join(keys), steps=[s.seq for s in hit_steps],
                         server_rows={f"numeric_keys[{first.persona_id}]": {a: sorted(v) for a, v in
                                                                             sorted(by_action.items())}})]


# ----------------------------------------------------------------------------
# e. precondition の連鎖
# ----------------------------------------------------------------------------

def _ends_with_precondition(step: TranscriptStep) -> str:
    if not step.http:
        return ""
    err = step.http[-1].error or ""
    return err.split(":", 1)[1] if err.startswith("precondition:") else ""


def _check_precondition_chain(seq: list[TranscriptStep], factory: FindingFactory) -> list[Finding]:
    chain: list[tuple[TranscriptStep, str]] = []

    def emit() -> list[Finding]:
        names = sorted({n for _, n in chain})
        return [factory.make(
            oracle="A", severity="blocked", step=chain[0][0],
            hypothesis=(f"ハーネス: 必要な状態（{', '.join(names)}）が runner に無いまま {len(chain)} 手続き、"
                        "HTTP を出さずに終わった（製品の欠陥ではなく runner の状態解決の穴の可能性）"),
            quote=" → ".join(f"{s.seq}:{s.action_id}({n})" for s, n in chain)[:400],
            steps=[s.seq for s, _ in chain], layers=["cycle_verification"])]

    for step in seq:
        if step.action_id == "auth.login":
            continue
        name = _ends_with_precondition(step)
        if name:
            chain.append((step, name))
            continue
        if len(chain) >= PRECONDITION_CHAIN:
            return emit()
        chain = []
    return emit() if len(chain) >= PRECONDITION_CHAIN else []


def check(steps: list[TranscriptStep], factory: FindingFactory) -> list[Finding]:
    out: list[Finding] = []
    flagged_renumber: set[str] = set()
    flagged_numeric: set[str] = set()
    flagged_chain: set[str] = set()
    for (persona, _session), seq in _by_session(steps).items():
        if persona not in flagged_renumber:
            found = _check_renumbering(seq, factory)
            if found:
                flagged_renumber.add(persona)
                out += found
        for step in seq:
            if step.action_id.startswith(CHAT_ACTIONS):
                out += _check_indistinct(step, factory)
        out += _check_stale(seq, factory)
        if persona not in flagged_numeric:
            found = _check_numeric_keys(seq, factory)
            if found:
                flagged_numeric.add(persona)
                out += found
        if persona not in flagged_chain:
            found = _check_precondition_chain(seq, factory)
            if found:
                flagged_chain.add(persona)
                out += found
    return out
