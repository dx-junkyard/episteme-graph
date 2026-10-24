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
    uses_voice: bool = False  # 音声中心のペルソナ（persona_uses_voice）。チャットは音声ループと同じ形で送る

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
                  "document_ref": "documents",
                  # 検証シミュレーション（SB層）: 自分のコード / 配線 / 実行
                  "program_id": "sim_programs", "wiring_id": "sim_wirings", "run_id": "sim_runs"}.get(name)
        return self.latest(plural) if plural else ""


# 行動型の ``uses_voice``（no / sometimes / mostly）のうち、チャットを音声ループで送るとみなす値。
# ``sometimes`` は文字の経路のまま（どちらで送ったかを runner が決めると、ペルソナの選択を差し替えることになる）
VOICE_HABIT_VALUES = ("mostly", "yes", "always", "true")


def persona_uses_voice(habits: Any) -> bool:
    """行動型の癖から、チャットを音声（``intent_mode=casual`` + ``screen_mode=voice``）で送るかを決める。"""
    raw = habits.get("uses_voice") if isinstance(habits, dict) else None
    if raw is True:
        return True
    return str(raw or "").strip().lower() in VOICE_HABIT_VALUES


def is_pseudo_chunk_id(value: Any) -> bool:
    """トピック教材の区画が持つ擬似 id（製品 learning.py の ``topic:{topic_id}``）か。実在のチャンクではない。"""
    return str(value or "").startswith("topic:")


# ----------------------------------------------------------------------------
# 画面の投影
# ----------------------------------------------------------------------------

HARNESS_CLIPPED_MARK = "（ハーネス: 観察の上限でここから先を省略した — 画面にはこの続きがある。製品の表示が切れたのではない）"
HARNESS_PRECONDITION_TEXT = ("（ハーネス: この操作の対象がまだ画面に無いので、行為は行われていない。"
                             "これは画面の文ではない）")


def _clip(text: str, n: int = OBSERVATION_CHARS) -> str:
    """観察の上限で切る。観察全体の長さ（n が大きい）で切るときは、切ったことをハーネスの文で言う（第 25 周 H10:
    頭脳が「途中で切れている」を製品の所見と混同しない）。短い抜粋（n が小さい）は従来どおり「…」。"""
    text = (text or "").strip()
    if len(text) <= n:
        return text
    if n >= 1000:
        return text[: n - len(HARNESS_CLIPPED_MARK) - 1] + "…" + HARNESS_CLIPPED_MARK
    return text[: n - 1] + "…"


# 画面（app.js / admin.js）が表示しない「宛先」のキー。id・*_id・*_ids・stable_key などは UI がリンク解決に使う
# だけで文字として出さない（製品の表示投影 display_projection.ID_KEY_RE と同じ線引き・第 17 周）。
# ペルソナの観察に載せると「UUID が見える」「id=None」の偽の所見になる。
# *_ref / *_refs / *_uuid（step_refs 等）も製品 ID_KEY_RE と同じく宛先として扱う（第 18 周）。
_ADDRESS_KEY_RE = __import__("re").compile(
    r"(?:^|_)(?:id|ids|key|keys|ref|refs|uuid)$|^(?:material_id|document_id|stable_key|href|src|run_id)$")


def strip_address_keys(v: Any) -> Any:
    """入れ子の dict / list から宛先キー（``_ADDRESS_KEY_RE``）を再帰的に落とす。値の文字列は触らない。"""
    if isinstance(v, dict):
        return {k: strip_address_keys(x) for k, x in v.items()
                if not (isinstance(k, str) and _ADDRESS_KEY_RE.search(k))}
    if isinstance(v, list):
        return [strip_address_keys(x) for x in v]
    return v


def _str(v: Any) -> str:
    # 深さの上限を越えた dict / list は JSON で 1 行に印字する。その前に宛先キーを落とす（第 18 周: 教員向け投影の
    # 入れ子に node_ids・module_key・evidence_claim_ids・section_id が文字として残り、実画面に無い ID が見えていた）
    return v if isinstance(v, str) else json.dumps(strip_address_keys(v), ensure_ascii=False)


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
            if isinstance(v, (dict, list)):
                v = strip_address_keys(v)  # 深さによらず同じ線引き（入れ子の JSON 印字にも宛先キーを残さない）
            if isinstance(v, (dict, list)) and depth < 2 and v:
                lines.append(f"{pad}{k}:")
                lines.extend(_generic(v, depth + 1, max_lines - len(lines)))
            elif v not in (None, "", [], {}):
                lines.append(f"{pad}{k}: {_str(v)[:200]}")
    elif isinstance(body, list):
        for i, v in enumerate(body[:8]):
            if isinstance(v, (dict, list)):
                v = strip_address_keys(v)
                if not v:
                    continue  # 宛先キーだけの項目（{"id": …}）は画面に何も描かれない
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


# app.js renderCourseSelect の optgroup の見出し（逐語ミラー）
COURSE_GROUP_MINE = "マイコース"
COURSE_GROUP_ENROLLABLE = "新しく受講可能なコース"
# 投影を上限で切るときの明示（画面は切らないので、切ったことを頭脳に隠さない）
CONTINUES_MARK = "（続きあり）"
# personal-map-home.js の常設注記と空状態の事実文（逐語ミラー）
PERSONAL_MAP_PRIVACY_NOTE = "この地図はあなたにだけ表示されます。成績評価には使用されません。"
PERSONAL_MAP_EMPTY = (
    ("いまここの周り", "まだ論文の理論構成に結びついた記録がありません。教材のどこについての記録か決めると、ここに現れます。"),
    ("いまの地図", "まだ痕跡がありません。学習の中で問いを残すと、ここに現れます。"),
    ("問いからの旅", "まだ問いの記録がありません。学習の中で問いを残すと、ここに現れます。"),
)
# admin.js REFERENCE_HEALTH_CHIP_LABELS / admin-graph-review.js MODULE_RELATED_NONE_TEXT の逐語ミラー
REFERENCE_HEALTH_CHIP_LABELS = {"ok": "参照: 問題なし", "broken": "参照: 切れがあります", "unchecked": "参照: 未確認"}
MODULE_RELATED_NONE_TEXT = "このモジュールと同じ構造のモジュールを持つ他の論文は、このコーパスの中では見つかっていません。"

_FORMULA_PLACEHOLDER_RE = __import__("re").compile(r"\[\[(FORMULA_\d+)\]\]")


def resolve_formula_placeholders(text: str, formulas: Any) -> str:
    """``[[FORMULA_N]]`` を ``chunk.formulas`` の latex で置き換える（app.js ``renderMaterialChunk`` と同じ規則・
    第 3 波 IK-0944）: **id の鍵（``formula.id`` とその正規化形）を先に全部入れ、並び順の鍵 ``FORMULA_<idx>`` は
    その鍵がまだ空いているときだけ**。スライドの formulas はそのスライドが引く式の部分集合なので、並び順の鍵が
    別の式の id を上書きしない。引けないもの（latex が空を含む）は**そのまま残す**。"""
    if not text or not isinstance(formulas, list) or not formulas:
        return text
    by_id: dict[str, str] = {}
    for f in formulas:
        if not isinstance(f, dict) or not str(f.get("id") or "").strip():
            continue
        latex = str(f.get("latex") or f.get("tex") or "").strip()
        fid = str(f.get("id")).strip()
        norm = _norm_evidence_id(fid)
        for key in (fid, norm, fid.replace("[[", "").replace("]]", "")):
            by_id[key] = latex
    for idx, f in enumerate(formulas):
        if not isinstance(f, dict):
            continue
        by_id.setdefault(f"FORMULA_{idx}", str(f.get("latex") or f.get("tex") or "").strip())

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
    # 第 3 波（IK-0944）: id の鍵を先に全部入れ、並び順の鍵（FORMULA_i）は空いているときだけ（app.js renderMaterialChunk）
    formula_by_id: dict[str, dict] = {}
    formula_list = [f for f in chunk.get("formulas") or []]
    for f in formula_list:
        if not isinstance(f, dict) or not f.get("id"):
            continue
        fid = str(f["id"])
        norm = _norm_evidence_id(fid)
        for key in (fid, norm, f"[[{norm}]]"):
            formula_by_id[key] = f
    for idx, f in enumerate(formula_list):
        if not isinstance(f, dict):
            continue
        for key in (f"FORMULA_{idx}", f"[[FORMULA_{idx}]]"):
            formula_by_id.setdefault(key, f)
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
            # app.js（IK-0584）と同じ: 見出しが無い出典は引用文だけを出し、内部 ID を見出しに使わない
            title = item.get("title")
            if not title or title == item.get("summary"):
                return f"〔{label}〕{body}"
            return f"〔{label}: {title}〕{body}"
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


_ANCHOR_EMBED_RE = re.compile(r"!?\[\[(component|claim|equation):([^\]]+)\]\]")


def material_anchors(body: Any) -> list[dict]:
    """教材区画に**本文の埋め込みとして見えている** ⚓（component / claim のチップ・数式カード）を出現順に番号付きで返す。

    画面（app.js ``renderMaterialChunk``）がチップ・数式カードとして描くのは、本文に ``![[kind:id]]`` で埋め込まれ、
    evidence_items（数式は formulas でもよい）で引けるものだけ。埋め込まれていない evidence_items は画面に出ない
    （第 25 周 H1: 全件を 30 個まで列挙していた）。番号は 1 始まり・同じ kind:id は 1 度だけ。区画番号 ``segment`` も持つ。
    """
    out: list[dict] = []
    seen: set[str] = set()
    chunks = body.get("chunks") if isinstance(body, dict) else None
    for si, ch in enumerate(chunks or []):
        if not isinstance(ch, dict):
            continue
        items = [ev for ev in ch.get("evidence_items") or [] if isinstance(ev, dict) and ev.get("kind")]
        by_ref = {f"{ev['kind']}:{_norm_evidence_id(ev.get('id'))}": ev for ev in items}
        by_id: dict[str, dict] = {}
        for ev in items:
            by_id.setdefault(_norm_evidence_id(ev.get("id")), ev)
        formulas: dict[str, dict] = {}
        for idx, f in enumerate(ch.get("formulas") or []):
            if isinstance(f, dict):
                formulas[_norm_evidence_id(f.get("id") or f"FORMULA_{idx}")] = f
        text = str(ch.get("text") or ch.get("content") or "")
        text = re.sub(r"!\[\[equation:\s*\[\[([^\]]+)\]\]\s*\]\]", r"![[equation:\1]]", text)
        for m in _ANCHOR_EMBED_RE.finditer(text):
            kind, norm = m.group(1), _norm_evidence_id(m.group(2))
            item = by_ref.get(f"{kind}:{norm}") or by_id.get(norm)
            formula = formulas.get(norm) if kind == "equation" else None
            if item is None and formula is None:
                continue  # 引けない埋め込みは画面では未解決カード（⚓ ではない）
            key = f"{kind}:{norm}"
            if key in seen:
                continue
            seen.add(key)
            src = item or formula or {}
            # 題名は画面のチップ（renderMaterialEvidenceChip: title || label || id）と同じ文字列で切らない。
            # 数式は画面がチップではなく式として描くので、描かれる式（LaTeX）を題名にする（第 25 周 面 D H6）
            latex = str((formula or {}).get("latex") or (item or {}).get("latex") or "") if kind == "equation" else ""
            title = f"${latex}$" if latex else str(src.get("title") or src.get("label") or src.get("summary") or "")
            out.append({"no": len(out) + 1, "kind": kind, "id": str((item or {}).get("id") or norm),
                        "title": re.sub(r"\s+", " ", title).strip(), "segment": si})
    return out


TOPIC_COMPONENT_LIMIT = 12  # app.js _TOPIC_COMPONENT_LIMIT


def topic_component_evidence(body: Any) -> list[dict]:
    """app.js ``collectTopicComponentEvidence`` の写し（出典タブ「このトピックの論理要素」— 部品だけ・最大 12）。"""
    chunks = [ch for ch in (body.get("chunks") if isinstance(body, dict) else None) or [] if isinstance(ch, dict)]
    embedded = {_norm_evidence_id(m.group(1)) for ch in chunks
                for m in re.finditer(r"!\[\[component:([^\]]+)\]\]", str(ch.get("text") or ""))}
    seen: set[str] = set()
    out: list[dict] = []
    for ch in chunks:
        for item in ch.get("evidence_items") or []:
            if not isinstance(item, dict) or item.get("kind") != "component":
                continue
            norm = _norm_evidence_id(item.get("id"))
            title = str(item.get("title") or item.get("label") or "")
            if not norm or norm in seen or not title:
                continue
            seen.add(norm)
            if len(out) >= TOPIC_COMPONENT_LIMIT:
                continue
            summary = str(item.get("summary") or "")
            out.append({"title": title, "summary": summary if summary != title else "",
                        "has_anchor": norm in embedded})
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


DISCUSS_CHAT_ACTIONS = ("learning.discuss.ask", "learning.corpus.discuss_ask", "learning.chat.cycle")


def stance_line_shown(stance: Any, *, discuss: bool = False) -> bool:
    """app.js ``renderStanceLine`` と同じ条件: 推定（source=inferred）で tutor 以外、かつ議論中でないときだけ出る。"""
    if discuss or not isinstance(stance, dict):
        return False
    return (stance.get("source") == "inferred" and bool(stance.get("stance")) and stance.get("stance") != "tutor"
            and bool(stance.get("label")))


MIRROR_LABEL = "AIによる言い直し"  # app.js renderMirrorBlock の見出しの逐語ミラー
MIRROR_CHIPS = ("そのとおり", "少し違う")


def _mirror_lines(body: dict) -> list[str]:
    """app.js ``renderMirrorBlock`` と同じ: mirror.text があるときだけ、回答本文の前に見出し付きの枠とチップを描く。

    本文末尾の ``MIRROR_MOVED_NOTE``（「…『AIによる言い直し』の枠に示しています」）はこの枠を指す。投影が枠を描かないと
    実画面に無い枠を指す文に見える（第 18 周・ハーネス側）。注記文そのものは製品の文言なので落とさない。"""
    mirror = body.get("mirror")
    text = str(mirror.get("text") or "").strip() if isinstance(mirror, dict) else ""
    if not text:
        return []
    return [f"〔{MIRROR_LABEL}〕{text}", "  " + " ".join(f"ボタン: {c}" for c in MIRROR_CHIPS)]


def _chat(body: dict, *, discuss: bool = False) -> list[str]:
    out = _mirror_lines(body) + ["AI の回答:", str(body.get("answer", ""))]
    stance = body.get("stance") or {}
    if stance_line_shown(stance, discuss=discuss):
        # 画面は「{label}答えました。」の 1 行（tutor・明示の様相・楽屋・議論では何も出ない — 第 17 周）
        out.append(f"〔回答の調子: {stance.get('label')}答えました〕")
    grounding = {"course_material": "教材に基づく", "other_material": "別の資料に基づく",
                 "model_generated": "出典を追えない AI の説明"}.get(body.get("content_grounding") or "", "")
    if grounding:
        out.append(f"〔出所: {grounding}〕")
    for i, s in enumerate(body.get("sources") or [], 1):
        if isinstance(s, dict):
            # 番号は DTO の index（セッション内で安定・不連続あり = IK-0432）。位置番号で描くと本文の [出典N] とずれる
            n = s.get("index") or i
            out.append(f"出典{n}: {s.get('source_title') or s.get('title') or ''} {s.get('meta') or ''}".rstrip())
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
            # 第 25 周 share H27: Copilot の navigate（label なし）は admin-assistant.js が［そのタブを開く］を描く。
            # label の無い行為を空の「ボタン:」で描かない
            label = str(a.get("label") or "").strip() or ("そのタブを開く" if a.get("type") == "navigate" else "")
            if label:
                out.append(f"ボタン: {label}")
    for c in body.get("manual_citations") or []:
        if isinstance(c, dict):
            out.append(f"マニュアル: {c.get('title', '')}")
    if body.get("degraded"):
        out.append("〔この回答は縮退した固定文です〕")
    return out


def component_hop_targets(graph: Any) -> list[str]:
    """文脈図で移動できる隣の部品の id（画面の「旅」と同じく navigable な theory_component・上位 → 下位の順・重複なし・
    中心を除く）。投影の「番号 N」と hop の引数の引き直しが同じ並びを使う。"""
    if not isinstance(graph, dict):
        return []
    focus = str((graph.get("focus") or {}).get("id") or "") if isinstance(graph.get("focus"), dict) else ""
    out: list[str] = []
    for lane in ("upper", "lower"):
        for it in graph.get(lane) or []:
            if not (isinstance(it, dict) and it.get("element_type") == "theory_component" and it.get("navigable")):
                continue
            hid = str(it.get("id") or "").strip()
            if hid and hid != focus and hid not in out:
                out.append(hid)
    return out


def _component_context_lines(body: dict) -> list[str]:
    """部品の文脈（画面の統一パーツカード + 文脈の図）。ラベル・事実文だけを並べ、数値を足さない。"""
    head, details, shared = _component_instance_lines(body)
    lines = ["部品の文脈:"] + head
    graph = body.get("graph") if isinstance(body.get("graph"), dict) else None
    if graph is None:
        # 画面は graph が無ければ「グラフで見る」を出さない（カード・内訳・共通部品のタブは出す）
        return lines + details + shared
    focus = graph.get("focus") if isinstance(graph.get("focus"), dict) else {}
    lines.append(f"文脈の図の中心: {focus.get('label', '')}")
    hop_ids = component_hop_targets(graph)
    for lane, name in (("upper", "上位"), ("lower", "下位")):
        items = [it for it in graph.get(lane) or [] if isinstance(it, dict)]
        lines.append(f"{name}:" if items else f"{name}: なし")
        for it in items:
            # 移動できる部品（navigable な theory_component で id あり）にだけ番号を添える。画面は id を見せないので
            # 観察にも id を出さない（第 25 周 share H2: 上位・下位の UUID が漏れと読まれた）。番号は
            # ``component_hop_targets`` の並び（hop の component_id に番号を渡すと引き直す）。
            hid = str(it.get("id") or "").strip()
            mark = (f"（移動できる・番号 {hop_ids.index(hid) + 1}）" if hid in hop_ids
                    and it.get("element_type") == "theory_component" and it.get("navigable") else "")
            lines.append(f"- {it.get('relation_label', '')} {it.get('label', '')}{mark}")
    # app.js componentContextExitsHtml と同じ: 次の一歩の事実文（graph.exits）をレーン順に、同じ文は 1 回だけ描く
    exits = graph.get("exits") if isinstance(graph.get("exits"), dict) else {}
    seen: set[str] = set()
    for lane in ("upper", "lower"):
        fact = str(exits.get(lane) or "").strip()
        if fact and fact not in seen:
            seen.add(fact)
            lines.append(f"（{fact}）")
    return lines + details + shared + ["［グラフで見る］"]


# app.js の逐語ミラー（MATERIAL_EVIDENCE_EQUATION_ROLE_LABELS / MATERIAL_LIBRARY_STATUS_LABELS / element-vocab の種別名）
COMPONENT_KIND_LABEL = "論理要素"
EQUATION_ROLE_LABELS = {"input": "入力", "intermediate": "中間", "output": "出力", "constraint": "制約",
                        "definition": "定義", "linked": "関連"}
LIBRARY_STATUS_LABELS = {"standard": "標準", "field_standard": "分野標準", "emerging_common": "共通化しつつある",
                         "novel": "新規", "unknown": "未評価"}
COMPONENT_POSITION_EMPTY = "この論文での位置づけはまだ登録されていません。"
SHARED_PART_EMPTY = "共通部品としての情報はまだ登録されていません。"


def shared_pane_lines(shared_part: dict) -> list[str]:
    """app.js ``renderContextSharedPane``（第 3 波・IK-0917 / IK-0920）: 見出し ``heading`` → 名前 → 別名 → 要約 →
    標準化状況 → 他の論文の題名の列挙（``other_documents_lead`` の下に ``other_documents[].title``・
    ``other_documents_truncated`` なら「ほか」）／無ければ ``other_documents_note``。件数（旧 ``other_documents_count``）は
    描かない。何も無ければ固定文。"""
    entry = shared_part.get("entry") if isinstance(shared_part.get("entry"), dict) else {}
    part: list[str] = []
    if shared_part.get("heading"):
        part.append(str(shared_part["heading"]))
    if entry.get("name"):
        part.append(str(entry["name"]))
    if entry.get("aliases"):
        part.append("別名: " + " ・ ".join(str(a) for a in entry["aliases"]))
    if entry.get("summary"):
        part.append(str(entry["summary"]))
    if entry.get("standardization_status"):
        st = str(entry["standardization_status"])
        part.append(f"標準化状況: {LIBRARY_STATUS_LABELS.get(st, st)}")
    docs = [str(d.get("title")) for d in entry.get("other_documents") or [] if isinstance(d, dict) and d.get("title")]
    if docs:
        if entry.get("other_documents_lead"):
            part.append(str(entry["other_documents_lead"]))
        part += [f"- {t}" for t in docs] + (["- ほか"] if entry.get("other_documents_truncated") else [])
    elif entry.get("other_documents_note"):
        part.append(str(entry["other_documents_note"]))
    return part or [SHARED_PART_EMPTY]


def _component_instance_lines(body: dict) -> tuple[list[str], list[str], list[str]]:
    """app.js ``renderComponentContextPanel`` が描く欄だけ（第 25 周 H4: DTO をキー名ごとダンプしていた —
    component_type・provenance・英語のキー名は画面に出ない）。

    戻り値は（カードの見出しと位置づけ / 内訳 ``renderContextInstanceDetails`` / 「共通部品として」タブ）。
    """
    instance = body.get("instance") if isinstance(body.get("instance"), dict) else {}
    component = instance.get("component") if isinstance(instance.get("component"), dict) else {}
    in_paper = instance.get("in_paper") if isinstance(instance.get("in_paper"), dict) else {}
    graph = body.get("graph") if isinstance(body.get("graph"), dict) else {}
    focus = graph.get("focus") if isinstance(graph.get("focus"), dict) else {}
    label = str(component.get("label") or focus.get("label") or "")
    summary = str(in_paper.get("narrative_role") or component.get("teaching_takeaway") or component.get("summary") or "")
    shared_part = body.get("shared_part") if isinstance(body.get("shared_part"), dict) else None
    head = [f"見出し: {COMPONENT_KIND_LABEL} ・ {label}" if label else f"見出し: {COMPONENT_KIND_LABEL}",
            "タブ: ［この論文では］" + ("［共通部品として］" if shared_part else ""),
            f"この論文での位置づけ: {summary or COMPONENT_POSITION_EMPTY}"]
    details: list[str] = []
    if in_paper.get("graph_summary_excerpt"):
        details.append(str(in_paper["graph_summary_excerpt"]))
    supports = instance.get("supports") if isinstance(instance.get("supports"), dict) else {}
    counts = [f"{name} {len(supports.get(key) or [])}件" for name, key in
              (("前提", "preconditions"), ("入力", "inputs"), ("出力", "outputs"), ("注意", "cautions"),
               ("数式", "equations"), ("主張", "claims")) if supports.get(key)]
    if counts:
        details.append(" · ".join(counts))  # 画面の摘要行（学習内容の構成数 — 評価の数値ではない）
    for name, key in (("前提", "preconditions"), ("入力", "inputs"), ("出力", "outputs"), ("注意", "cautions")):
        texts = [str(it.get("text")) for it in supports.get(key) or [] if isinstance(it, dict) and it.get("text")]
        if texts:
            details.append(f"{name}:")
            details += [f"- {t}" for t in texts]
    eqs = []
    for eq in supports.get("equations") or []:
        if not isinstance(eq, dict):
            continue
        role = EQUATION_ROLE_LABELS.get(str(eq.get("role") or ""), "")
        # 画面は resolver で latex が引ければ式を描き、引けなければサーバ同梱の式本文（eq.latex — IK-0894・第 2 波）、
        # それも無ければ表示名（$…$ は数式区間）。旧サーバは label（無ければ id）
        text = f"${eq['latex']}$" if eq.get("latex") else str(eq.get("label") or eq.get("id") or "")
        eqs.append(f"- {role + ' ' if role else ''}{text}" + (f"（{eq['label_note']}）" if eq.get("label_note") else ""))
    if eqs:
        details += ["数式:"] + eqs
    deps = []
    for d in supports.get("dependencies") or []:
        if isinstance(d, dict):
            t = str(d.get("reason") or "")
            if d.get("target_label"):
                t = f"{d['target_label']} — {t}" if t else str(d["target_label"])
            if t:
                deps.append(f"- {t}")
    if deps:
        details += ["関連:"] + deps
    claims = [f"- {c['excerpt']}" for c in supports.get("claims") or [] if isinstance(c, dict) and c.get("excerpt")]
    if claims:
        details += ["主張:"] + claims
    doc = in_paper.get("document") if isinstance(in_paper.get("document"), dict) else {}
    if doc.get("title"):
        details.append(f"出典論文: {doc['title']}")
    expl = instance.get("explanation") if isinstance(instance.get("explanation"), dict) else {}
    if expl.get("body") or expl.get("title"):
        details.append("教員の説明:")
        details += [str(expl[k]) for k in ("title", "body", "endorsement_label") if expl.get(k)]
    shared: list[str] = []
    if shared_part is not None:
        shared.append("「共通部品として」タブ:")
        shared += shared_pane_lines(shared_part)
    elif body.get("shared_part_note"):
        # IK-0863: 「共通部品として」が無いときはサーバの事実文がパネルの末尾に出る（app.js renderComponentContextPanel）
        shared.append(str(body["shared_part_note"]))
    return head, details, shared


def _error(status: int, body: Any) -> str:
    detail = body.get("detail") if isinstance(body, dict) else body
    return f"エラーが表示された（HTTP {status}）: {_str(detail)[:600] if detail not in (None, '') else '（詳細なし）'}"


#: personal-map-home.js REF_KIND_LABEL の逐語ミラー（旅の各歩の種別チップ）
JOURNEY_REF_KIND_LABEL = {"graph_node": "理論構成", "shared_part": "共通部品（専門家確認済み）", "document": "教材",
                          "atlas_node": "地図", "personal_node": "あなたの痕跡"}


def journey_lines(body: dict) -> list[str]:
    """personal-map-home.js renderJourneyArea の写し（第 25 周 share H25: 応答のキーを素で並べ「truncated: false」が
    出た）。steps が空なら notice と facts（「この記録について」）、notice も無ければ何も出さない。truncated が真の
    ときだけ「（途中まで）」。"""
    steps = [x for x in body.get("steps") or [] if isinstance(x, dict)]
    if not steps:
        if not body.get("notice"):
            return ["（旅の経路は表示されなかった）"]
        lines = ["旅の経路", str(body["notice"])]
        facts = [str(f) for f in body.get("facts") or [] if f]
        if facts:
            lines += ["この記録について:"] + facts
        return lines
    lines = ["旅の経路"]
    for st in steps:
        ref = st.get("ref") if isinstance(st.get("ref"), dict) else {}
        kind = JOURNEY_REF_KIND_LABEL.get(str(ref.get("kind") or ""), "")
        lines.append(f"- {st.get('fact') or ''}" + (f"［{kind}］" if kind else ""))
    if body.get("frontier_note"):
        lines += [str(body["frontier_note"]), "[別の問いから旅に出る]"]
    if body.get("truncated"):
        lines.append("（途中まで）")
    return lines


def project_observation(action_id: str, status: Optional[int], body: Any) -> str:
    """応答をペルソナが読む画面の文に投影する（最大 ~1500 字）。"""
    if action_id.startswith("unsupported:"):
        return "この操作は画面に見当たらない（実行できなかった）。"
    if action_id.startswith(("learning.simlab.", "admin.simulation.")):
        # 検証シミュレーション（SB層）の画面は uxsim/runner/simlab_exec.py が投影する（エラーも含めて 1 箇所）
        from uxsim.runner import simlab_exec  # 遅延 import（循環回避）

        return simlab_exec.project(action_id, status, body)
    if action_id.startswith(("admin.lecture_studio.", "admin.figure_studio.", "learning.lecture.")):  # r25:studio
        from uxsim.runner import studio_exec  # 遅延 import（循環回避）

        shown = studio_exec.project(action_id, status, body)
        if shown is not None:
            return shown
    if isinstance(body, dict) and body.get("_ui_fact"):
        # 画面がサーバの応答を事実文に置き換えて出す縮退（例: ノード対話の 404 / 422）
        return _clip(str(body["_ui_fact"]))
    if status is None and isinstance(body, dict) and body.get("_precondition"):
        # 第 25 周 H1: runner の状態名（ledger_target 等）を頭脳へ渡さない — 製品の画面文と読まれて内部語の所見になった。
        # 状態名は HttpTrace.error（``precondition:<name>``）として transcript に残る
        return HARNESS_PRECONDITION_TEXT
    if status is None:
        return "画面が応答しなかった（接続できない / 時間切れ）。"
    if status >= 400:
        return _clip(_error(status, body))
    from uxsim.runner import vision5_exec  # 遅延 import（循環回避）— 第 23 周の画面（扉・記録・リリース前の確認ほか）

    shown = vision5_exec.project(action_id, status, body)
    if shown:
        return _clip(shown)
    if isinstance(body, dict) and "answer" in body and action_id.startswith(
            ("learning.chat", "learning.discuss.ask", "learning.corpus.discuss_ask", "admin.course_builder.chat",
             "admin.copilot")):
        lines = _chat(body, discuss=action_id in DISCUSS_CHAT_ACTIONS)
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
        # 画面（app.js renderCourseSelect）は受講中・所有を「マイコース」、未受講を「新しく受講可能なコース」の
        # 見出しで分ける（第 23 周: 見出しを落として受講可能側だけに印があるように見せていた）
        rows = [c for c in body[:20] if isinstance(c, dict)]
        own = [c for c in rows if not c.get("is_enrollable")]
        avail = [c for c in rows if c.get("is_enrollable")]
        if not rows:
            return "コースが 1 つも表示されていない。"
        lines = ["コースの選択:"]
        if not own:
            lines.append("（受講するコースを選択...）")
        # 第 25 周 H23（atlas）: 「id=1bad7d9f」は投影の印で、学生が「題名の後ろの英数字は何か」と読んだ。
        # 画面は題名だけを描く。行為の指し先は表示順の番号か題名（actions_exec.resolve_course_hint）で引く
        n = 0
        for head, group in ((COURSE_GROUP_MINE, own), (COURSE_GROUP_ENROLLABLE, avail)):
            if group:
                lines.append(f"{head}:")
                for c in group:
                    n += 1
                    lines.append(f"{n}. {c.get('title', '')}")
        return _clip("\n".join(lines))
    if action_id == "learning.source_chunk.open" and isinstance(body, dict):
        # 画面（app.js の出典ポップアップ）は節名と本文を切らずに描く（第 23 周: 本文が 200 字で切れていた）
        lines = [str(body.get("section") or "").strip()]
        lines.append(render_material_text({"text": body.get("text"), "formulas": body.get("formulas"),
                                           "figures": body.get("figures")}))
        text = "\n".join(x for x in lines if x)
        if len(text) > OBSERVATION_CHARS - len(CONTINUES_MARK):
            text = text[: OBSERVATION_CHARS - len(CONTINUES_MARK) - 1] + CONTINUES_MARK
        return text or "（出典の本文は表示されなかった）"
    if action_id == "learning.personal_network.mine" and isinstance(body, dict) and not body.get("nodes"):
        # 画面（personal-map-home.js）は空のとき各タブに事実文を描く（第 23 周: 投影が無言だった）
        return "\n".join(["わたしの地図（タブ: いまここの周り / いまの地図 / 問いからの旅）", PERSONAL_MAP_PRIVACY_NOTE]
                         + [f"{tab}: {msg}" for tab, msg in PERSONAL_MAP_EMPTY])
    if action_id == "learning.personal_network.journey" and isinstance(body, dict):
        return _clip("\n".join(journey_lines(body)))
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
                         f"{m.get('visibility', '')}{chip}）")
        # 第 25 周 H9: 画面は document_id を出さない。行為の引数に使う番地は末尾の欄に分ける
        ids = [f"{m.get('title', '')}={m.get('document_id')}" for m in body[:20]
               if isinstance(m, dict) and m.get("document_id")]
        if ids:
            lines.append("（操作に使う番号 — 画面には出ない。document_id に渡す）: " + " / ".join(ids))
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
        # 出典タブ「分野の中の位置づけ」（app.js buildPaperPlacementHtml）と同じ欄・同じ形（第 25 周 H15: 分野の行・
        # placed_document_count など画面に出ないキーをダンプしていた）。正本は atlas_exec.paper_placement_section_lines
        from uxsim.runner import atlas_exec  # 遅延 import（循環回避）

        lines = atlas_exec.paper_placement_section_lines(body)
        return _clip("\n".join(lines) if lines else
                     "（出典タブに「分野の中の位置づけ」の節は出ていない — 配置のある論文が無い）")
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
            # 版の呼び名は IK-0942 の「地図 版 X」（atlas-threads-layer.js factText の写し）
            lines = [f"推定の糸（AIによる推定（未確認）・地図 版 {threads.get('skeleton_version', '')}）:"]
            for it in threads.get("items") or []:
                if isinstance(it, dict):
                    lines.append(f"- {it.get('from_label', '')} ⋯ {it.get('to_label', '')}（{it.get('nearness_label', '')}）")
        # 第 25 周 H22（atlas）: 応答の残り（skeleton_version / cartridge / nodes …）を _generic でダンプしない。
        # 製品の atlas-threads-layer.js は糸の点線と事実文（「AIによる推定（未確認）・地図 版X」）だけを描く
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
            # app.js renderMaterial（第 3 波・IK-0947）: 区画ゼロなら「読み込み中」と言わずこの文だけを教材区画に
            # （.material-notice の段落 — 区画があるときも本文の上に同じ段落）
            lines.append(notice.strip())
        elif not [ch for ch in body.get("chunks") or [] if isinstance(ch, dict)]:
            lines.append("教材を読み込み中…")
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
            # 本文に埋め込まれて画面に出る ⚓ チップ・数式カードだけを出現順に（element_ref に番号か id を渡す）。
            # 埋め込まれていない根拠は画面に出ないので列挙しない（第 25 周 H1）
            lines.append("本文の ⚓（element_ref に番号を渡すと開ける）:")
            for a in anchors:
                lines.append(f"- ⚓{a['no']} {MATERIAL_KIND_LABELS.get(a['kind'], a['kind'])}: {a['title']}（区画{a['segment']}）")
        comps = topic_component_evidence(body)
        if comps:
            # 出典タブ「このトピックの論理要素」（app.js collectTopicComponentEvidence）— 部品だけ
            lines.append("出典タブ「このトピックの論理要素」:")
            for c in comps:
                lines.append(f"- {c['title']}" + (f" — {c['summary']}" if c["summary"] else "")
                             + (" ［教材の該当箇所へ］" if c["has_anchor"] else ""))
        return _clip("\n".join(lines))
    lines = _generic(body)
    return _clip("\n".join(lines) if lines else "（画面に何も表示されなかった）")


DISCUSS_TOPIC_ID = "_discussion"


def learner_focus_params(session: "PersonaSession") -> dict:
    """app.js focusQuerySuffix（第 25 周 第 2 波）: 学習者の部品の説明・台帳の一行・部品の文脈に付く
    ``topic_id`` と焦点論文の ``document_id``（議論中は discuss の起点の論文、ほかは表示中のトピックの教材）。"""
    out: dict = {}
    if session.topic_id:
        out["topic_id"] = session.topic_id
    if session.topic_id == DISCUSS_TOPIC_ID:
        doc = session.scratch.get("discuss_focus_document_id") or ""
    else:
        doc = (session.material or {}).get("document_id") if isinstance(session.material, dict) else ""
    if doc:
        out["document_id"] = str(doc)
    return out
