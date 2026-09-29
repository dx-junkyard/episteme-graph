"""item オーサリング前の claim 整形（非LLM・決定論・純関数）。

worker が DB から読んだ live claim 行は、分野（cartridge）未指定の解析だと
``concepts`` / ``equation`` / ``evidence_text`` が空のまま、``source_scope`` も
``section_id`` / ``block_id`` だけになる。同じ文書の中には、その主張の出典文
（knowledge_evidence の逐語引用）・親 claim（atomic rewrite の元文）・節見出し
（chunks.source_metadata）・式（knowledge_equations.linked_claim_ids）が残っている。
ここではそれらを**推測せずに**結び付けて、オーサリング入力の空欄を埋める（IK-0479）。

併せて次の 3 つもここに置く（いずれも claim の行・item の行を消さない）:

- ``normalize_claim_text`` — PDF の行末ハイフネーション（``distri-\\nbution``）と
  改行を畳む（IK-0481）。
- ``authoring_skip_reason`` — 問いに答えが入ってしまう claim をオーサリング対象から外す
  理由を返す（IK-0482）。
- ``select_authoring_targets`` — 同じ本文・同じ親子の claim に item を二重に作らない
  （IK-0480。atomic child を優先）。

FastAPI / sqlalchemy / LLM を import しない（SQL は worker.py 側）。
"""

from __future__ import annotations

import re
from typing import Any, Iterable

# --- 本文の正規化（IK-0481） ----------------------------------------------

_HYPHEN_BREAK = re.compile(r"(\w)-[ \t]*\r?\n[ \t]*(\w)")
_WHITESPACE = re.compile(r"\s+")


def normalize_claim_text(value: Any) -> str:
    """PDF 由来の行末ハイフネーションを繋ぎ、空白（改行含む）を 1 個に畳む。

    ``"distri-\\nbution"`` → ``"distribution"``。行末でないハイフン
    （``binary-black-hole``）はそのまま残す。
    """
    text = str(value or "")
    if not text:
        return ""
    text = _HYPHEN_BREAK.sub(r"\1\2", text)
    return _WHITESPACE.sub(" ", text).strip()


def text_key(value: Any) -> str:
    """同一本文判定のキー（正規化 + casefold + 末尾句読点除去）。"""
    return normalize_claim_text(value).casefold().rstrip(" .。")


# --- オーサリング対象からの除外（IK-0482） ---------------------------------

SKIP_UNKNOWN_CLAIM_TYPE = "unknown_claim_type"
SKIP_SHORT_NEGATION = "short_negation"
SKIP_DUPLICATE_TEXT = "duplicate_text"
SKIP_FAMILY_ALREADY_AUTHORED = "family_already_authored"
SKIP_ALREADY_AUTHORED = "already_authored"

SKIP_REASONS = (
    SKIP_UNKNOWN_CLAIM_TYPE,
    SKIP_SHORT_NEGATION,
    SKIP_DUPLICATE_TEXT,
    SKIP_FAMILY_ALREADY_AUTHORED,
    SKIP_ALREADY_AUTHORED,
)

# 短い否定文の上限（文字数）。これより長い否定文は条件・対比を含み得るので除外しない。
SHORT_NEGATION_MAX_CHARS = 60

# 先頭 0〜3 語のあとに否定が来る短文（"The method does not require …" / "does not require …" /
# "X is not Y"）。分野語を含めない。
_NEGATION_HEAD_EN = re.compile(
    r"^(?:[\w\-]+\s+){0,3}?"
    r"(?:(?:does|do|did|is|are|was|were|need|needs|can|will|would|should|has|have)\s+not\b"
    r"|(?:doesn't|don't|didn't|isn't|aren't|wasn't|weren't|cannot|can't|never|no longer)\b)",
    re.IGNORECASE,
)
_NEGATION_TAIL_JA = re.compile(r"(?:ではない|ではありません|しない|しません|ない|ません)[。．.]?$")


def is_short_negation(text: Any) -> bool:
    body = normalize_claim_text(text)
    if not body or len(body) > SHORT_NEGATION_MAX_CHARS:
        return False
    return bool(_NEGATION_HEAD_EN.match(body) or _NEGATION_TAIL_JA.search(body))


def authoring_skip_reason(claim: dict[str, Any]) -> str | None:
    """claim 単体で決まる除外理由（無ければ None）。

    - ``claim_type='unknown'``: 主張の種類が決まっていない claim から「再構成」の問いを
      作ると、何を再構成させるかが定まらない。
    - 短い否定文: 「〜を必要としない」を言い直させる問いは、問い文に答え（否定の対象）が
      そのまま入る（IK-0482 で観測）。
    """
    claim_type = str(claim.get("claim_type") or "").strip().lower()
    if not claim_type or claim_type == "unknown":
        return SKIP_UNKNOWN_CLAIM_TYPE
    if is_short_negation(claim.get("text") or claim.get("normalized_text")):
        return SKIP_SHORT_NEGATION
    return None


# --- 二重オーサリングの防止（IK-0480） -------------------------------------


def select_authoring_targets(
    claims: list[dict[str, Any]],
    *,
    authored: Iterable[dict[str, Any]] = (),
    limit: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """オーサリングする claim を選び、除外理由ごとの件数を返す。

    ``claims`` は非 retired item を持たない候補（``_fetch_authorable_claims`` の出力）、
    ``authored`` は同じ文書で既に item を持つ claim（``id`` / ``parent_claim_id`` /
    ``text``）。規則:

    1. 単体の除外理由（``authoring_skip_reason``）があれば外す。
    2. 既に item を持つ claim と本文が同じなら外す（``duplicate_text``）。
    3. 親または子が既に item を持つなら外す（``family_already_authored``）。
    4. 候補どうしでは atomic child（``parent_claim_id`` あり）を親より先に採る。
       同じ本文の 2 件目・採った claim の親／子は 2・3 と同じ理由で外す。

    行は消さない（外すのはこの 1 回のオーサリング対象からだけ）。
    """
    counts: dict[str, int] = {reason: 0 for reason in SKIP_REASONS}
    taken_keys: set[str] = set()
    taken_ids: set[str] = set()
    taken_parent_ids: set[str] = set()
    for row in authored or ():
        cid = str(row.get("id") or "")
        if cid:
            taken_ids.add(cid)
        pid = str(row.get("parent_claim_id") or "")
        if pid:
            taken_parent_ids.add(pid)
        key = text_key(row.get("text"))
        if key:
            taken_keys.add(key)

    indexed = list(enumerate(claims or []))
    # atomic child を先に（同じ順位の中では入力順＝created_at 順を保つ）。
    indexed.sort(key=lambda pair: (0 if pair[1].get("parent_claim_id") else 1, pair[0]))

    picked: list[tuple[int, dict[str, Any]]] = []
    for index, claim in indexed:
        reason = authoring_skip_reason(claim)
        cid = str(claim.get("id") or "")
        pid = str(claim.get("parent_claim_id") or "")
        key = text_key(claim.get("text") or claim.get("normalized_text"))
        if reason is None and cid and cid in taken_ids:
            reason = SKIP_ALREADY_AUTHORED
        if reason is None and key and key in taken_keys:
            reason = SKIP_DUPLICATE_TEXT
        if reason is None and ((pid and pid in taken_ids) or (cid and cid in taken_parent_ids)):
            reason = SKIP_FAMILY_ALREADY_AUTHORED
        if reason is not None:
            counts[reason] += 1
            continue
        if len(picked) >= max(0, int(limit)):
            continue
        picked.append((index, claim))
        if cid:
            taken_ids.add(cid)
        if pid:
            taken_parent_ids.add(pid)
        if key:
            taken_keys.add(key)
    picked.sort(key=lambda pair: pair[0])
    return [claim for _, claim in picked], {k: v for k, v in counts.items() if v}


# --- 空欄の補完（IK-0479） -------------------------------------------------

_TOKEN = re.compile(r"\w{3,}", re.UNICODE)
EVIDENCE_OVERLAP_MIN = 0.6


def _tokens(value: Any) -> set[str]:
    return {t.casefold() for t in _TOKEN.findall(normalize_claim_text(value))}


def pick_evidence_text(
    claim_text: Any, evidences: Iterable[dict[str, Any]], *, containing_only: bool = False
) -> str:
    """同じ block の逐語引用から claim の出典文を 1 つ選ぶ（無ければ空）。

    1. claim 本文を丸ごと含む引用（短い方＝文単位の引用を優先）。
    2. 語の重なり（claim 側の語のうち引用に現れる割合）が ``EVIDENCE_OVERLAP_MIN`` 以上の
       文単位の引用のうち最大のもの。
    ブロック全体の引用（``source_quote``）は 2 の対象にしない（何でも重なるため）。
    """
    body = normalize_claim_text(claim_text).casefold()
    rows = [
        (str(e.get("evidence_role") or ""), normalize_claim_text(e.get("evidence_text")))
        for e in (evidences or ())
    ]
    rows = [(role, text) for role, text in rows if text]
    if not body or not rows:
        return ""
    containing = [text for _, text in rows if body.rstrip(" .") in text.casefold()]
    if containing:
        return min(containing, key=len)
    if containing_only:
        return ""
    claim_tokens = _tokens(body)
    if not claim_tokens:
        return ""
    best_text, best_score = "", 0.0
    for role, text in rows:
        if role == "source_quote":
            continue
        score = len(claim_tokens & _tokens(text)) / len(claim_tokens)
        if score > best_score or (score == best_score and best_text and len(text) < len(best_text)):
            best_text, best_score = text, score
    return best_text if best_score >= EVIDENCE_OVERLAP_MIN else ""


def pick_equation(claim: dict[str, Any], equations: Iterable[dict[str, Any]], parent: dict | None = None) -> dict:
    """``linked_claim_ids`` が claim（UUID / agent ID / 親の ID）を指す式を 1 つ選ぶ。"""
    refs = {
        str(claim.get("id") or ""),
        str(claim.get("agent_claim_id") or ""),
    }
    if parent:
        refs |= {str(parent.get("id") or ""), str(parent.get("agent_claim_id") or "")}
    refs.discard("")
    if not refs:
        return {}
    linked = []
    for eq in equations or ():
        ids = {str(x) for x in (eq.get("linked_claim_ids") or []) if x}
        if ids & refs and (eq.get("latex") or eq.get("label")):
            linked.append(eq)
    if not linked:
        return {}
    linked.sort(key=lambda e: (str(e.get("label") or ""), str(e.get("agent_equation_id") or "")))
    eq = linked[0]
    return {
        "label": str(eq.get("label") or ""),
        "latex": str(eq.get("latex") or ""),
        "defined_symbols": [str(s) for s in (eq.get("defined_symbols") or []) if str(s).strip()],
        "relation_type": "",
        "source": "knowledge_equations",
    }


SECTION_TITLE_MIN_CHARS = 3
SECTION_TITLE_MAX_LINES = 2


def clean_section_title(value: Any) -> str:
    """節見出しを整える。崩れた見出し（1〜2 文字・3 行以上の断片）は使わない。

    PDF の抽出では見出しに図中の文字や表の列名が混ざることがある
    （``"z"`` / ``"K\\nDE\\nK-essence-like\\n…"``）。空を返せば入力に載らない。
    """
    raw = str(value or "")
    if not raw.strip():
        return ""
    if len([line for line in raw.splitlines() if line.strip()]) > SECTION_TITLE_MAX_LINES:
        return ""
    title = normalize_claim_text(raw)
    return title if len(title) >= SECTION_TITLE_MIN_CHARS else ""


def enrich_claim(
    claim: dict[str, Any],
    *,
    parent: dict[str, Any] | None = None,
    evidences: Iterable[dict[str, Any]] = (),
    equations: Iterable[dict[str, Any]] = (),
    section_title: str = "",
) -> dict[str, Any]:
    """claim の空欄だけを埋めた新しい dict を返す（入力は mutate しない）。

    既に値のある欄は上書きしない。補った欄は ``enriched_fields`` に名前を残す。
    """
    out = dict(claim)
    enriched: list[str] = []
    for field in ("text", "normalized_text"):
        if out.get(field):
            out[field] = normalize_claim_text(out[field])
    if not out.get("concepts") and parent and isinstance(parent.get("concepts"), list) and parent["concepts"]:
        out["concepts"] = list(parent["concepts"])
        enriched.append("concepts")
    equation = out.get("equation") if isinstance(out.get("equation"), dict) else {}
    if not (equation.get("latex") or equation.get("label")):
        parent_eq = parent.get("equation") if parent and isinstance(parent.get("equation"), dict) else {}
        if parent_eq.get("latex") or parent_eq.get("label"):
            out["equation"] = dict(parent_eq)
            enriched.append("equation")
        else:
            picked = pick_equation(out, equations, parent)
            if picked:
                out["equation"] = picked
                enriched.append("equation")
    if not str(out.get("evidence_text") or "").strip():
        # 出典文の優先順: claim 本文を丸ごと含む逐語引用 → 親 claim（atomic rewrite の元文）
        # → 語の重なりが十分な文単位の逐語引用。
        evidence = pick_evidence_text(out.get("text"), evidences, containing_only=True)
        if not evidence and parent:
            evidence = normalize_claim_text(parent.get("evidence_text") or parent.get("text"))
        if not evidence:
            evidence = pick_evidence_text(out.get("text"), evidences)
        if evidence:
            out["evidence_text"] = evidence
            enriched.append("evidence_text")
    else:
        out["evidence_text"] = normalize_claim_text(out["evidence_text"])
    scope = dict(out.get("source_scope")) if isinstance(out.get("source_scope"), dict) else {}
    section_title = clean_section_title(section_title)
    if section_title and not str(scope.get("section_title") or "").strip():
        scope["section_title"] = section_title
        out["source_scope"] = scope
        enriched.append("source_scope.section_title")
    out["enriched_fields"] = enriched
    return out
