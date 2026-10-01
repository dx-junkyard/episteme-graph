"""ペルソナ 1 人・1 セッションの状態と、応答の「画面の投影」。

投影は DTO をペルソナが読む日本語に整形したもの。**内部 ID は隠さない**（§7.3 — 隠すと
内部 ID が画面に漏れる欠陥が審判 B から見えなくなる）。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

OBSERVATION_CHARS = 6000


@dataclass
class PersonaSession:
    """行為の path テンプレート（``{course_id}`` 等）を解決するための既知の状態。"""

    persona_id: str
    username: str = ""
    password: str = ""
    role: str = "STUDENT"
    session_no: int = 1
    token: str = ""
    user: dict = field(default_factory=dict)
    course_id: str = ""
    topic_id: str = ""
    topic_ids: list[str] = field(default_factory=list)
    material: dict = field(default_factory=dict)
    histories: dict[str, list[dict]] = field(default_factory=dict)  # UI が送るクライアント側の履歴（topic 別）
    topics: dict[str, dict] = field(default_factory=dict)  # 開いたコースのトピック（id → DTO）
    last_status: Optional[int] = None
    last_body: Any = None
    last_action: str = ""
    last_answer: str = ""
    course_draft: dict = field(default_factory=dict)
    known: dict[str, list[str]] = field(default_factory=dict)
    maps: dict[str, dict[str, str]] = field(default_factory=dict)  # 例: graph_sessions {document_id: session_id}
    scratch: dict[str, Any] = field(default_factory=dict)  # 直前の提案など、ID 以外の一時データ
    seq_counter: int = 0

    def history_for(self, topic_id: str) -> list[dict]:
        """トピックごとの会話履歴（UI の state.chatMessages 相当）。"""
        return self.histories.setdefault(topic_id or "", [])

    @property
    def chat_history(self) -> list[dict]:
        return self.history_for(self.topic_id)

    def remember(self, kind: str, value: Any) -> None:
        """既知 ID を末尾に積む（重複は末尾へ移す）。"""
        if value in (None, ""):
            return
        v = str(value)
        items = self.known.setdefault(kind, [])
        if v in items:
            items.remove(v)
        items.append(v)

    def latest(self, kind: str) -> str:
        items = self.known.get(kind) or []
        return items[-1] if items else ""

    def all(self, kind: str) -> list[str]:
        return list(self.known.get(kind) or [])

    def next_message_id(self) -> str:
        self.seq_counter += 1
        return f"uxsim-{self.persona_id}-{self.session_no}-{self.seq_counter}"

    def resolve(self, name: str, args: dict) -> str:
        """path の穴 ``name`` を引数 → 状態の順で埋める。埋まらなければ空文字。"""
        if args.get(name) not in (None, ""):
            return str(args[name])
        direct = {"course_id": self.course_id, "topic_id": self.topic_id}
        if direct.get(name):
            return direct[name]
        plural = {"material_id": "materials", "document_id": "documents", "component_id": "components",
                  "task_id": "tasks", "trace_id": "traces", "item_id": "recon_items", "recon_id": "recons",
                  "chunk_id": "chunks", "group_id": "groups", "node_id": "nodes", "element_id": "elements",
                  "element_type": "element_types", "claim_id": "claims",
                  "document_ref": "documents"}.get(name)
        return self.latest(plural) if plural else ""


# ----------------------------------------------------------------------------
# 画面の投影
# ----------------------------------------------------------------------------

def _clip(text: str, n: int = OBSERVATION_CHARS) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def _str(v: Any) -> str:
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)


# 画面（app.js / admin.js）が表示しない「宛先」のキー。id・*_id・*_ids・stable_key などは UI がリンク解決に使う
# だけで文字として出さない（製品の表示投影 display_projection.ID_KEY_RE と同じ線引き・第 17 周）。
# ペルソナの観察に載せると「UUID が見える」「id=None」の偽の所見になる。
_ADDRESS_KEY_RE = __import__("re").compile(r"(?:^|_)(?:id|ids|key|keys)$|^(?:material_id|document_id|stable_key|href|src|run_id)$")


def _generic(body: Any, depth: int = 0, max_lines: int = 160) -> list[str]:
    """DTO を「項目: 値」の行に平たく並べる（深さ 2 まで・リストは先頭 8 件）。宛先キー（id 等）は画面と同じく出さない。"""
    lines: list[str] = []
    pad = "  " * depth
    if isinstance(body, dict):
        for k, v in body.items():
            if len(lines) >= max_lines:
                break
            if isinstance(k, str) and _ADDRESS_KEY_RE.search(k):
                continue
            if isinstance(v, (dict, list)) and depth < 2 and v:
                lines.append(f"{pad}{k}:")
                lines.extend(_generic(v, depth + 1, max_lines - len(lines)))
            elif v not in (None, "", [], {}):
                lines.append(f"{pad}{k}: {_str(v)[:200]}")
    elif isinstance(body, list):
        for i, v in enumerate(body[:8]):
            if isinstance(v, (dict, list)) and depth < 2:
                lines.append(f"{pad}- ({i + 1})")
                # 1 項目あたりの行数に上限を置き、後ろの項目が空行「- (3)」にならないようにする
                # （第 5 周: 教材一覧の 3・4 本目が空に見え、教員が選べなかった — ハーネス側の欠陥）
                lines.extend(_generic(v, depth + 1, min(14, max_lines - len(lines))))
            else:
                lines.append(f"{pad}- {_str(v)[:200]}")
        if len(body) > 8:
            lines.append(f"{pad}- …ほか")
    else:
        lines.append(pad + _str(body)[:1500])
    return lines


# admin.js REFERENCE_HEALTH_CHIP_LABELS / admin-graph-review.js MODULE_RELATED_NONE_TEXT の逐語ミラー
REFERENCE_HEALTH_CHIP_LABELS = {"ok": "参照: 問題なし", "broken": "参照: 切れがあります", "unchecked": "参照: 未確認"}
MODULE_RELATED_NONE_TEXT = "このモジュールと同じ構造のモジュールを持つ他の論文は、このコーパスの中では見つかっていません。"

_FORMULA_PLACEHOLDER_RE = __import__("re").compile(r"\[\[(FORMULA_\d+)\]\]")


def resolve_formula_placeholders(text: str, formulas: Any) -> str:
    """``[[FORMULA_N]]`` を ``chunk.formulas`` の latex で置き換える（画面の renderMaterialChunk と同じ規則:
    ``formula.id`` か位置 ``FORMULA_<idx>`` で引く）。引けないものは**そのまま残す**。"""
    if not text or not isinstance(formulas, list) or not formulas:
        return text
    by_id: dict[str, str] = {}
    for idx, f in enumerate(formulas):
        if not isinstance(f, dict):
            continue
        latex = str(f.get("latex") or f.get("tex") or "").strip()
        if not latex:
            continue
        by_id[f"FORMULA_{idx}"] = latex
        fid = str(f.get("id") or "").strip()
        if fid:
            by_id[fid] = latex
            by_id[fid.replace("[[", "").replace("]]", "")] = latex

    def _sub(m: "re.Match[str]") -> str:  # noqa: F821
        latex = by_id.get(m.group(1))
        return f"${latex}$" if latex else m.group(0)

    return _FORMULA_PLACEHOLDER_RE.sub(_sub, text)


# 根拠リンク系 kind の表示名（frontend/public/js/element-vocab.js KIND_LABELS の逐語ミラー。
# 画面の materialEvidenceKindLabel と同じく未知キーはそのまま返す）
MATERIAL_KIND_LABELS = {
    "component": "論理要素", "claim": "主張", "equation": "数式", "figure": "図", "source": "出典",
    "evidence": "根拠箇所", "derivation": "導出", "shared_part": "共通部品",
}
MISSING_EMBED_SUMMARY = "このIDに対応する教材要素を取得できませんでした。"
_EMBED_BLOCK_RE = re.compile(r"!\[\[([a-z_]+):([^\]]+)\]\]")
_EMBED_INLINE_RE = re.compile(r"\[\[([a-z_]+):([^\]]+)\]\]")
_PLACEHOLDER_RE = re.compile(r"\[\[([^\[\]:]+)\]\]")
_SENTINEL_RE = re.compile("\x00(EMBED|MATH|FIGURE)_(\\d+)\x00")


def _norm_evidence_id(value: Any) -> str:
    """画面の normalizeMaterialEvidenceId と同じ正規化（外側の [[ ]] を 2 重まで外す・eq_eq_ → eq_）。"""
    s = str(value if value is not None else "").strip()
    for _ in range(2):
        if s.startswith("[["):
            s = s[2:]
        if s.endswith("]]"):
            s = s[:-2]
    return re.sub(r"^(?:eq_){2,}", "eq_", s.strip(), flags=re.IGNORECASE)


def _short_summary(text: Any) -> str:
    """画面の shortMaterialEvidenceSummary（空白を畳み 260 字で切る）。"""
    t = re.sub(r"\s+", " ", str(text if text is not None else "")).strip()
    return t[:260].strip() + "..." if len(t) > 260 else t


def _equation_body(formula: Optional[dict]) -> str:
    """画面の renderMaterialEquationBody の文字版（LaTeX → plain_text → raw_text）。"""
    if not isinstance(formula, dict):
        return ""
    latex = str(formula.get("latex") or formula.get("summary") or "")
    if latex:
        mark = str(formula.get("reconstructed_mark") or "") if formula.get("reconstructed") else ""
        return f"$${latex}$$" + (f"{mark}" if mark else "")
    for key in ("plain_text", "raw_text"):
        if formula.get(key):
            return str(formula[key])
    return ""


def _figure_card(figure: dict) -> str:
    """画面の renderMaterialFigureCard の文字版。"""
    caption = str(figure.get("caption") or "")
    explanation = str(figure.get("explanation") or "")
    head = "〔図〕" if figure.get("image_url") else "〔図: この図の画像を取得できませんでした。〕"
    return head + (f" {caption}" if caption else "") + (f"（{explanation}）" if explanation else "")


def _missing_embed(kind: str, raw_id: str) -> str:
    # 画面の renderMaterialMissingEmbed と同じく kind:id をそのまま見せる（製品の未解決を隠さない）
    return f"〔未解決 {kind}:{raw_id} — {MISSING_EMBED_SUMMARY}〕"


def render_material_text(chunk: Any) -> str:
    """教材区画 1 つを画面（app.js ``renderMaterialChunk``）と同じ規則で文字に投影する。

    - ``![[kind:id]]`` / ``[[kind:id]]`` は ``chunk.evidence_items``（"kind:id" 優先・同一 ID の別 kind へ
      フォールバック）で解決: component / claim → ⚓ チップ、equation → 数式（無ければ「数式は準備中です」）、
      figure → 画像なしの図カード、source 等 → 種別付きカード。解決できないものは**未解決カード**（kind:id を出す）。
    - ``[[X]]``（コロンなし）は ``chunk.formulas``（id / 位置 FORMULA_i）→ ``chunk.figures``（FIGURE_(i+1) / figure_id）で
      解決し、どちらにも無ければ**そのまま残す**（画面と同じ — IK-0389 を審判から隠さない）。
    - ``drop_unresolved_embeds`` が真なら未解決カードを描かない（画面のレクチャースライドと同じ）。
    """
    if not isinstance(chunk, dict):
        return ""
    text = str(chunk.get("text") or chunk.get("content") or "")
    drop_unresolved = bool(chunk.get("drop_unresolved_embeds"))
    formula_by_id: dict[str, dict] = {}
    for idx, f in enumerate(chunk.get("formulas") or []):
        if not isinstance(f, dict):
            continue
        fid = str(f.get("id") or f"FORMULA_{idx}")
        norm = _norm_evidence_id(fid)
        for key in (fid, norm, f"[[{norm}]]", f"FORMULA_{idx}", f"[[FORMULA_{idx}]]"):
            formula_by_id[key] = f
    figure_by_id: dict[str, dict] = {}
    for idx, fig in enumerate(chunk.get("figures") or []):
        if not isinstance(fig, dict):
            continue
        figure_by_id[f"FIGURE_{idx + 1}"] = fig
        figure_by_id[f"[[FIGURE_{idx + 1}]]"] = fig
        if fig.get("figure_id"):
            figure_by_id[str(fig["figure_id"])] = fig
    by_ref: dict[str, dict] = {}
    by_id: dict[str, dict] = {}
    for item in chunk.get("evidence_items") or []:
        if not isinstance(item, dict) or not item.get("kind"):
            continue
        norm = _norm_evidence_id(item.get("id"))
        by_ref[f"{item['kind']}:{norm}"] = item
        by_id.setdefault(norm, item)

    def lookup(kind: str, norm: str) -> Optional[dict]:
        return by_ref.get(f"{kind}:{norm}") or by_id.get(norm)

    embeds: list[tuple[str, str]] = []
    maths: list[str] = []
    figs: list[dict] = []

    def keep_embed(m: "re.Match[str]") -> str:
        embeds.append((m.group(1), m.group(2)))
        return f"\x00EMBED_{len(embeds) - 1}\x00"

    def keep_placeholder(m: "re.Match[str]") -> str:
        raw, pid = m.group(0), m.group(1)
        formula = formula_by_id.get(raw) or formula_by_id.get(pid) or formula_by_id.get(_norm_evidence_id(pid))
        if formula is not None:
            maths.append(str(formula.get("latex") or formula.get("summary") or pid))
            return f"\x00MATH_{len(maths) - 1}\x00"
        figure = figure_by_id.get(raw) or figure_by_id.get(pid)
        if figure is not None:
            figs.append(figure)
            return f"\x00FIGURE_{len(figs) - 1}\x00"
        return raw

    out = text.replace("\r\n", "\n").replace("\r", "\n")
    out = re.sub(r"<br\s*/?>", "\n", out, flags=re.IGNORECASE)
    out = re.sub(r"!\[\[equation:\s*\[\[([^\]]+)\]\]\s*\]\]", r"![[equation:\1]]", out)
    out = re.sub(r"\[\[equation:\s*\[\[([^\]]+)\]\]\s*\]\]", r"[[equation:\1]]", out)
    out = _EMBED_BLOCK_RE.sub(keep_embed, out)
    out = _EMBED_INLINE_RE.sub(keep_embed, out)
    out = _PLACEHOLDER_RE.sub(keep_placeholder, out)

    def render_embed(kind: str, raw_id: str) -> str:
        norm = _norm_evidence_id(raw_id)
        item = lookup(kind, norm)
        if kind == "equation":
            formula = formula_by_id.get(raw_id) or formula_by_id.get(norm)
            if formula is None and item and (item.get("latex") or item.get("plain_text") or item.get("raw_text")):
                formula = item
            body = _equation_body(formula)
            return body if body else "〔数式は準備中です〕"
        if kind == "figure":
            if item and item.get("kind") == "figure":
                title = item.get("title") or f"図: {item.get('caption') or norm}"
                summary = _short_summary(item.get("caption") or item.get("summary")) or "この図の画像は現在配信対象ではありません。"
                return f"〔{MATERIAL_KIND_LABELS['figure']}: {title}〕{summary}"
            return "" if drop_unresolved else _missing_embed(kind, raw_id)
        if kind in ("component", "claim"):
            if item:
                return f"〔⚓ {item.get('title') or item.get('label') or item.get('id') or norm}〕"
            return "" if drop_unresolved else _missing_embed(kind, raw_id)
        if item:
            label = MATERIAL_KIND_LABELS.get(str(item.get("kind")), str(item.get("kind")))
            body = f"$${item['latex']}$$" if item.get("latex") else (
                _short_summary(item.get("summary")) or "この教材要素に紐づく根拠です。")
            return f"〔{label}: {item.get('title') or item.get('id')}〕{body}"
        return "" if drop_unresolved else _missing_embed(kind, raw_id)

    def expand(m: "re.Match[str]") -> str:
        kind, idx = m.group(1), int(m.group(2))
        if kind == "EMBED":
            return render_embed(*embeds[idx])
        if kind == "MATH":
            return f"${maths[idx]}$"
        return _figure_card(figs[idx])

    return _SENTINEL_RE.sub(expand, out)


ANCHOR_KINDS = ("component", "claim", "equation")


def material_anchors(body: Any) -> list[dict]:
    """教材区画に見えている ⚓（evidence_items の component / claim / equation）を画面の出現順に番号付きで返す。

    ペルソナが「どの ⚓ を開くか」を選べるようにするための一覧（第 14 周: 常に最後の要素が開いていた）。
    番号は 1 始まり・同じ kind:id は 1 度だけ。区画番号 ``segment`` も持つ。
    """
    out: list[dict] = []
    seen: set[str] = set()
    chunks = body.get("chunks") if isinstance(body, dict) else None
    for si, ch in enumerate(chunks or []):
        if not isinstance(ch, dict):
            continue
        for ev in ch.get("evidence_items") or []:
            if not isinstance(ev, dict) or ev.get("kind") not in ANCHOR_KINDS or not ev.get("id"):
                continue
            key = f"{ev['kind']}:{ev['id']}"
            if key in seen:
                continue
            seen.add(key)
            title = str(ev.get("title") or ev.get("label") or ev.get("summary") or "")
            out.append({"no": len(out) + 1, "kind": ev["kind"], "id": str(ev["id"]),
                        "title": re.sub(r"\s+", " ", title).strip()[:60], "segment": si})
    return out


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？.!?])\s*")


def material_sentences(body: Any) -> list[list[str]]:
    """教材の区画ごとの文の列（区画番号 = chunks の位置・文番号 = 1 始まり）。選択質問の実在検査に使う。"""
    out: list[list[str]] = []
    for ch in (body.get("chunks") if isinstance(body, dict) else None) or []:
        text = render_material_text(ch) if isinstance(ch, dict) else ""
        sents = [x.strip() for x in _SENTENCE_SPLIT_RE.split(text) if len(x.strip()) >= 4]
        out.append(sents)
    return out


def _chat(body: dict) -> list[str]:
    out = ["AI の回答:", str(body.get("answer", ""))]
    stance = body.get("stance") or {}
    if isinstance(stance, dict) and stance.get("label"):
        out.append(f"〔回答の調子: {stance.get('label')}〕")
    grounding = {"course_material": "教材に基づく", "other_material": "別の資料に基づく",
                 "model_generated": "出典を追えない AI の説明"}.get(body.get("content_grounding") or "", "")
    if grounding:
        out.append(f"〔出所: {grounding}〕")
    for i, s in enumerate(body.get("sources") or [], 1):
        if isinstance(s, dict):
            # 番号は DTO の index（セッション内で安定・不連続あり = IK-0432）。位置番号で描くと本文の [出典N] とずれる
            n = s.get("index") or i
            out.append(f"出典{n}: {s.get('source_title') or s.get('title') or ''} {s.get('meta') or ''}".rstrip())
    if isinstance(body.get("mirror"), dict):
        out.append(f"〔鏡〕{body['mirror'].get('text', '')}")
    confirm = body.get("anchor_confirm")
    if isinstance(confirm, dict):
        # 画面（app.js renderAnchorConfirmPrompt）は見出し（prompt）とボタンだけを描き、発言全文は再掲しない。
        # 投影も同じにする（第 15 周: 発言全文 + 6 選択肢の括弧列挙は投影が作っていた）。
        opts = [str(o.get("label", "")) for o in confirm.get("options") or [] if isinstance(o, dict)]
        head = confirm.get("prompt") or "この疑問はどれに近いですか？"
        out.append(f"確認: {head}")
        for o in opts:
            out.append(f"  選択肢: {o}")
    for a in body.get("next_actions") or []:
        if isinstance(a, dict):
            out.append(f"ボタン: {a.get('label', '')}")
    for c in body.get("manual_citations") or []:
        if isinstance(c, dict):
            out.append(f"マニュアル: {c.get('title', '')}")
    if body.get("degraded"):
        out.append("〔この回答は縮退した固定文です〕")
    return out


def _component_context_lines(body: dict) -> list[str]:
    """部品の文脈（画面の統一パーツカード + 文脈の図）。ラベル・事実文だけを並べ、数値を足さない。"""
    lines = ["部品の文脈:"]
    graph = body.get("graph") if isinstance(body.get("graph"), dict) else None
    rest = {k: v for k, v in body.items() if k != "graph"}
    lines.extend(_generic(rest, max_lines=80))
    if graph is None:
        lines.append("文脈の図: 表示されなかった")
        return lines
    focus = graph.get("focus") if isinstance(graph.get("focus"), dict) else {}
    lines.append(f"文脈の図の中心: {focus.get('label', '')}")
    for lane, name in (("upper", "上位"), ("lower", "下位")):
        items = [it for it in graph.get(lane) or [] if isinstance(it, dict)]
        lines.append(f"{name}:" if items else f"{name}: なし")
        for it in items:
            mark = "（移動できる）" if it.get("navigable") and it.get("element_type") == "theory_component" else ""
            # 移動できる部品だけ手掛かり（id）を添える（旅の hop の引数に使う）。画面は id を見せないが、
            # 動かせる相手を指す手段としてだけ残す。移動できない行には id を出さない（「id=None」の偽所見の防止）
            handle = f" id={it.get('id', '')}" if mark else ""
            lines.append(f"- {it.get('relation_label', '')} {it.get('label', '')}{mark}{handle}")
    return lines


def _error(status: int, body: Any) -> str:
    detail = body.get("detail") if isinstance(body, dict) else body
    return f"エラーが表示された（HTTP {status}）: {_str(detail)[:600] if detail not in (None, '') else '（詳細なし）'}"


def project_observation(action_id: str, status: Optional[int], body: Any) -> str:
    """応答をペルソナが読む画面の文に投影する（最大 ~1500 字）。"""
    if action_id.startswith("unsupported:"):
        return "この操作は画面に見当たらない（実行できなかった）。"
    if status is None and isinstance(body, dict) and body.get("_precondition"):
        return f"この操作に必要なものがまだ画面に無い（{body['_precondition']}）。"
    if status is None:
        return "画面が応答しなかった（接続できない / 時間切れ）。"
    if status >= 400:
        return _clip(_error(status, body))
    if isinstance(body, dict) and "answer" in body and action_id.startswith(
            ("learning.chat", "learning.discuss.ask", "learning.corpus.discuss_ask", "admin.course_builder.chat",
             "admin.copilot")):
        lines = _chat(body)
        if action_id == "admin.course_builder.chat" and isinstance(body.get("course_draft"), dict):
            draft = body["course_draft"]
            # 画面（admin.js）と同じく章の中のトピックも数える。第 5 周で上位の topics だけを見て
            # 「トピック: 空」と投影し、教員ペルソナが 4 ターン混乱して諦めた（ハーネス側の欠陥）。
            chapters = [c for c in (draft.get("chapters") or []) if isinstance(c, dict)]
            top_titles = [t.get("title", "") if isinstance(t, dict) else str(t) for t in (draft.get("topics") or [])]
            if chapters:
                parts = []
                for c in chapters:
                    tps = [t.get("title", "") if isinstance(t, dict) else str(t) for t in (c.get("topics") or [])]
                    parts.append(f"{c.get('title', '')}〔{'、'.join(tps[:8]) or 'トピックなし'}〕")
                lines.append(f"コースの下書き: {draft.get('title', '')}（章: {' / '.join(parts)}）")
            else:
                lines.append(f"コースの下書き: {draft.get('title', '')}（トピック: {'、'.join(top_titles[:12]) or 'なし'}）")
        return _clip("\n".join(lines))
    if isinstance(body, dict) and body.get("_binary"):
        return f"音声・画像が返ってきた（{body['_binary']}）。"
    if action_id == "learning.course.list" and isinstance(body, list):
        lines = ["コース一覧:"]
        for c in body[:20]:
            if isinstance(c, dict):
                tag = "（受講可能）" if c.get("is_enrollable") else ""
                lines.append(f"- {c.get('title', '')}{tag} id={c.get('id', '')}")
        return _clip("\n".join(lines) if len(lines) > 1 else "コースが 1 つも表示されていない。")
    if action_id == "admin.materials.list" and isinstance(body, list):
        # 教材管理の行（admin.js renderMaterials）: 題名・状態・開示範囲・参照の健全性チップの文言
        lines = ["教材一覧:"]
        for m in body[:20]:
            if not isinstance(m, dict):
                continue
            health = m.get("reference_health") if isinstance(m.get("reference_health"), dict) else None
            chip = ""
            if health:
                chip = "・" + REFERENCE_HEALTH_CHIP_LABELS.get(str(health.get("status") or "unchecked"),
                                                               REFERENCE_HEALTH_CHIP_LABELS["unchecked"])
            lines.append(f"- {m.get('title', '')}（{m.get('filename', '')}・{m.get('status', '')}・"
                         f"{m.get('visibility', '')}{chip}）document_id={m.get('document_id', '')}")
        return _clip("\n".join(lines) if len(lines) > 1 else "教材が 1 つも表示されていない。")
    if action_id == "admin.theory_modules.related" and isinstance(body, dict):
        # 画面（admin-graph-review.js relatedModulesHtml）はモジュールの詳細ペインに題名と事実文だけを出す。
        # module_key（m2:…）は内部表現なので投影しない（TM12）
        facts = [str(f) for f in body.get("facts") or [] if str(f or "").strip()]
        lines = ["同じ構造のモジュールを持つ論文:"]
        if body.get("available") is False:
            return _clip("\n".join(lines + facts))
        for i, mod in enumerate(body.get("modules") or [], 1):
            if not isinstance(mod, dict):
                continue
            titles = [str(d.get("title") or "").strip() for d in mod.get("documents") or [] if isinstance(d, dict)]
            titles = [t for t in titles if t]
            lines.append(f"- モジュール{i}: " + ("、".join(titles) if titles else
                                                   "" if facts else MODULE_RELATED_NONE_TEXT))
        lines.extend(facts)
        return _clip("\n".join(lines))
    if action_id == "learning.reconstruction.next" and isinstance(body, dict):
        item = body.get("item") if isinstance(body.get("item"), dict) else body
        space = [o for o in item.get("response_space") or [] if isinstance(o, dict)]
        if space:
            # 画面（reconstruction.js）はラジオの label だけを見せる（op_* の id は value 属性で見えない）
            rest = {k: v for k, v in item.items() if k != "response_space"}
            lines = ["再構成の問い:"] + _generic(rest, max_lines=40) + ["選択肢（option にラベルか番号を渡す）:"]
            lines += [f"- {i}. {o.get('label', '')}" for i, o in enumerate(space, 1)]
            return _clip("\n".join(lines))
    if action_id == "learning.course.enroll" and isinstance(body, dict):
        # 画面（app.js enrollCourse）はコースを開き、enrolled が真のときだけ notice を一度出す。
        # 応答のフラグ（is_template / is_enrollable / visibility）は描かない（§7.3・第 14 周）
        lines = [f"コース: {body.get('title', '')}"]
        if body.get("enrolled") and body.get("notice"):
            lines.append(f"（お知らせ: {body['notice']}）")
        return _clip("\n".join(lines))
    if action_id == "learning.course.open" and isinstance(body, dict):
        master = body.get("master_course") if isinstance(body.get("master_course"), dict) else body
        topics = [t for t in (master.get("topics") or []) if isinstance(t, dict)]
        if topics:
            # 画面のサイドバーと同じく章とトピックを全部並べる（_generic の 8 件上限で第 4 章が「…ほか」に
            # 隠れ、ペルソナが後半のトピックを選べなかった — 第 8 周のハーネス側の欠陥）
            chapters = [c.get("title", "") if isinstance(c, dict) else str(c) for c in (master.get("chapters") or [])]
            lines = [f"コース: {master.get('title', '')}"]
            current = None
            for t in topics:
                ci = t.get("chapter_index")
                if ci != current:
                    current = ci
                    if isinstance(ci, int) and 0 <= ci < len(chapters):
                        lines.append(f"{chapters[ci]}:")
                mark = "（確認問題あり）" if t.get("check_questions") else ""
                lines.append(f"- {t.get('id', '')} {t.get('title', '')} [{t.get('status', '')}]{mark}")
            rest = {k: v for k, v in master.items() if k not in ("topics", "chapters", "title")}
            lines.extend(_generic(rest, max_lines=60))
            return _clip("\n".join(lines))
    if action_id == "learning.landscape.view" and isinstance(body, dict) and isinstance(body.get("documents"), list):
        # 画面の「分野の中の位置づけ」と同じ粒度: 論文ごとに（分野 / 概念 / 観点 / 関連の強さ / 出所ラベル / 理由）。
        # _generic の 1 項目 14 行の上限で placements が途中で切れ、出所ラベル（AI推定 / 教員確認）が読めなかった（第 10 周）
        lines = ["分野の中の位置づけ:"]
        for d in body.get("domains") or []:
            if isinstance(d, dict):
                lines.append(f"- 分野: {d.get('domain_name', '')}（骨格 版 {d.get('frozen_version', '')}）"
                             + ("（このコースの地図）" if d.get("is_course_map") else ""))
                for f in d.get("facts") or []:
                    lines.append(f"  {f}")
        for doc in body["documents"]:
            if not isinstance(doc, dict):
                continue
            lines.append(f"論文: {doc.get('title', '')}")
            pls = [p for p in (doc.get("placements") or []) if isinstance(p, dict)]
            if not pls:
                lines.append("  （配置なし）")
            for p in pls:
                lines.append(f"  - {p.get('node_label', '')}（{p.get('perspective_label', '')}・{p.get('weight_label', '')}・"
                             f"{p.get('provenance_label', '')}）: {str(p.get('reason', ''))[:160]}")
        for key in ("unplaced_documents", "facts"):
            for x in body.get(key) or []:
                lines.append(f"{'配置されていない論文' if key == 'unplaced_documents' else '注記'}: "
                             f"{x.get('title', '') if isinstance(x, dict) else x}")
        rest = {k: v for k, v in body.items() if k not in ("documents", "domains", "unplaced_documents", "facts")}
        lines.extend(_generic(rest, max_lines=30))
        return _clip("\n".join(lines))
    if action_id in ("learning.component.context", "learning.component.context_hop") and isinstance(body, dict):
        return _clip("\n".join(_component_context_lines(body)))
    if action_id == "learning.chunk.claim_refs" and isinstance(body, dict):
        claims = [c for c in body.get("claims") or [] if isinstance(c, dict)]
        lines = ["この出典に紐づく主張:"] + [f"- {c.get('label', '')}（{c.get('claim_type', '')}）id={c.get('id', '')}"
                                             for c in claims]
        return _clip("\n".join(lines) if claims else "この出典に紐づく主張は表示されなかった。")
    if action_id == "learning.atlas.threads" and isinstance(body, dict):
        threads = body.get("threads")
        if not isinstance(threads, dict):
            lines = ["推定の糸: この地図には表示されていない（切り替えが出ていない）。"]
        else:
            lines = [f"推定の糸（AIによる推定（未確認）・骨格 版{threads.get('skeleton_version', '')}）:"]
            for it in threads.get("items") or []:
                if isinstance(it, dict):
                    lines.append(f"- {it.get('from_label', '')} ⋯ {it.get('to_label', '')}（{it.get('nearness_label', '')}）")
        rest = {k: v for k, v in body.items() if k != "threads"}
        lines.extend(_generic(rest, max_lines=60))
        return _clip("\n".join(lines))
    if action_id == "learning.atlas.neighbors" and isinstance(body, dict):
        if not body.get("available"):
            return _clip(f"近くにある概念は表示されなかった。{body.get('note') or ''}".strip())
        here = body.get("here") if isinstance(body.get("here"), dict) else {}
        lines = [f"いまの場所: {here.get('label', '')}（{here.get('region_label', '')}）", "近くにある概念:"]
        rel = {"edge": "直接つながる", "sibling": "同じ領域"}
        for n in body.get("neighbors") or []:
            if isinstance(n, dict):
                lines.append(f"- {n.get('label', '')}（{n.get('region_label', '')}・{rel.get(n.get('relation'), n.get('relation', ''))}）")
        return _clip("\n".join(lines))
    if action_id == "learning.topic.open" and isinstance(body, dict):
        lines = ["教材:"]
        # 準備中／未生成の事実文（IK-0375）は画面と同じく本文の上に出す（第 6 周: DTO にはあったが投影が落としていた）
        notice = body.get("preparation_notice")
        if isinstance(notice, str) and notice.strip():
            lines.append(f"（お知らせ: {notice.strip()}）")
        for ch in body.get("chunks") or []:
            if isinstance(ch, dict):
                # 画面は本文を切り詰めない。投影で切ると「途中で切れている」という偽の friction が出る（第 7 周）
                text = str(ch.get("text") or ch.get("content") or "")
                # 画面（app.js renderMaterialChunk）と同じく [[FORMULA_N]] を chunk.formulas で解決する。
                # formulas に無いものは置換しない（製品側の未解決 = IK-0389 を審判から隠さない）。
                # 画面（app.js renderMaterialChunk）と同じ規則で [[FORMULA_N]] / [[FIGURE_N]] / ![[kind:id]] を
                # chunk の formulas / figures / evidence_items で解決する。引けない埋め込みは画面と同じ未解決カード
                # （kind:id を出す）にし、引けないプレースホルダーは残す（製品側の未解決 = IK-0389 を隠さない）。
                lines.append(render_material_text({**ch, "text": text})[:3000])
        anchors = material_anchors(body)
        if anchors:
            # 画面の ⚓ チップを番号で選べるように列挙する（element_ref に番号か id を渡す）
            lines.append("見えている ⚓（element_ref に番号を渡すと開ける）:")
            for a in anchors[:30]:
                lines.append(f"- ⚓{a['no']} {MATERIAL_KIND_LABELS.get(a['kind'], a['kind'])}: {a['title']}（区画{a['segment']}）")
        return _clip("\n".join(lines))
    lines = _generic(body)
    return _clip("\n".join(lines) if lines else "（画面に何も表示されなかった）")
