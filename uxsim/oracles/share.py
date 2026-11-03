"""第 25 周「共有・転用の通し」（face=share）の審判 — A 契約・B 原則・E 観測（決定論・LLM 0 回）。

対象の行為（``uxsim/actions/share_actions.py`` と学生側の既存の行為）:
教員 = ``admin.library.*`` / ``admin.deliberation.identity_*`` / ``admin.deliberation.shared_part_search`` /
``admin.explanations.*`` / ``admin.document_share.*``。学生 = ``learning.component.{context,context_hop,shared_part,
explanations}`` / ``learning.symbol.lookup`` / ``learning.personal_network.journey``。
応答は ``HttpTrace.response_excerpt``（先頭 2000 字）を読み、切れていればキーの有無だけを正規表現で見る。
製品は import しない（test_isolation — 製品の import は principle.py の 1 箇所）。文言の比較は事実文の
**有無**だけで、言い回しの一致は求めない。審判の出力は仮説（PE3）で、数値を書かない（PE7/PE10）。

A 契約:
- A1 拒否応答（422・409）の ``detail`` が空・日本語を含まない・内部例外文（共有・同一性・説明の行為）
- A2 同一性の候補一覧の形 — ``hidden_count`` が整数（真偽でない）・``hidden_unresolved`` が真偽 /
  候補が空なのに事実文（facts）が無い / ``hidden_unresolved`` が真の候補があるのに事実文が無い
- A3 地図との対応の導出の応答に件数報告（``coverage``）が載っている（KR6: 教員にも件数を見せない）
- A4 見えている教材の共有の追加が「見つからない」（404）で返る（所有者だけが変えられることが画面で分からない）
- A5 部品の一覧は開けたのに、その部品の説明バージョンが「見つからない」（404）で返る（説明・承認の共有に届かない）
- A6 見送り（dismissed）にした概念・関係・地図との対応の応答に、見送りの状態か理由が残っていない
- A7 学生の部品の「説明」が「見つからない」（404）で返る（受講しているコースの部品の説明に届かない — 第 25 周 H9）
- A8 地図との対応の導出で、サーバが返した事実文（facts）が画面の観察に 1 つも出ていない（画面が捨てた — IK-0864）
- A9 記号の照会: 教材の式の左辺に現れる記号に「定義が無い」と答えた / 照会した記号がどれも定義を返さなかった
  （IK-0869。左辺の判定は runner が教材の式から決定論で付けた印 ``symbol_lhs:`` を読む）
- A10 ナレッジライブラリのタブから聞いた Copilot の回答に、操作 KB の内部語（expected_revision・HTTP 番号・
  retired・draft・ロールの英字）がそのまま出ている（IK-0865 — 第 25 周 第 2 周の提案）
- A12 同一性リンクの作成（POST /api/admin/deliberation/identity-links・2xx）の応答の ``id`` が、同じ run の前の作成と同じなのに、
  観察が「作成しました」と言っている（既存のリンクを返しただけなのに新しく作ったように読める — IK-0918・第 25 周 第 3 周）
- A13 同一性リンクの行が ``decidable: false``（判断の対象でない — 第 9 波・IK-1076）なのに、理由の事実文
  ``undecidable_note`` が無い / 判断の問い（``confirm_question`` / ``confirm_note`` / ``granularity_note``）を持っている
  （判断できる行はキーを足さない契約なので、``decidable`` が無い行は見ない — 偽所見を作らない）
- A14 説明の一覧で ``can_cite: false``（引用できない — 第 9 波・IK-1077）なのに ``cite_note`` が無い
  （画面はボタンを出さず理由も出さない）。A1 は ``/cite`` の 403 の ``detail`` も見る（日本語の事実文の契約）
- A11 ナレッジライブラリのタブから聞いた Copilot の回答の出所（citations の先頭）が、この画面（ライブラリ）の
  手順でも節でもない（IK-0865 — 案内役が隣の古い項目で答えた）

B 原則:
- B1 学生の部品・記号・旅の応答に内部の宛先キー（entry_id / stable_key / produced_by_run_id / superseded_at /
  candidate_key / shared_part_id / mapping_justification / decided_by）
- B2 学生の応答に数（共通部品の ``other_documents_count``・教員の支持の内訳 ``endorser_count`` 等・``hidden_count``）
- B3 学生の ``concept_ref`` に名前と種別のラベル以外の欄
- B4 教員の同一性の候補で論文の題名が空のリンク（画面は題名の代わりに論文の内部 ID を出す）
- B6 学生の記号の照会・部品の文脈の表示語（``facts`` / ``name`` / ``label`` / 見出し）に TeX の label キー（``eq:…``）
  が出ている（第 9 波・IK-0681 — 是正後は「番号の無い式」・一般ラベルに数える）
- B5 教員の深く検討の同一性リンクの行に表示語（``instance_type_label`` と名前か題名）が無く、画面が論文の内部 ID を出す
  （第 3 波の是正後は表示語で描くので、旧サーバの形の行だけを拾う）

E 観測（砂場 DB の SELECT だけ・engine か URL があるときだけ）:
- E1 学生に「共通部品として」「概念」が見えた名前のエントリが、確定（confirmed）・公開（active）の行として無い
  （候補・見送りの概念が学生に届いた疑い — KR2）
- E2 教員が［見送り］／［却下］した同一性リンク（200）が ``element_identity_links`` に ``rejected`` で残っていない（P4。
  第 8 波: 後で［候補に戻す］・［確定を取り消す］を押したリンクは、その最後の判断だけを見る）
- E3 教員が［見送り］にした概念（200）が ``library_entries`` に見送りの状態と理由で残っていない（KR7）
"""
from __future__ import annotations

import re
from typing import Any, Iterator, Optional

from uxsim.oracles.findings import FindingFactory, excerpt_json
from uxsim.schema import Finding, HttpTrace, RunMeta, TranscriptStep

COPILOT_ACTION = "admin.copilot.chat"
LIBRARY_TABS = ("knowledge-library", "library")
COPILOT_INTERNAL_WORDS = ("expected_revision", "409", "retired", "draft", "TEACHER", "SYSTEM_ADMIN", "STUDENT")
TEACHER_PREFIXES = ("admin.library.", "admin.deliberation.identity_", "admin.deliberation.shared_part_search",
                    "admin.explanations.", "admin.document_share.")
LEARNER_ACTIONS = ("learning.component.context", "learning.component.context_hop", "learning.component.shared_part",
                   "learning.component.explanations", "learning.symbol.lookup", "learning.personal_network.journey")
LEARNER_FORBIDDEN_KEYS = ("entry_id", "stable_key", "produced_by_run_id", "superseded_at", "candidate_key",
                          "shared_part_id", "mapping_justification", "decided_by")
LEARNER_COUNT_KEYS = ("other_documents_count", "endorser_count", "strong_count", "provisional_count",
                      "expertise_breadth", "hidden_count", "citation_count")
CONCEPT_REF_ALLOWED = frozenset({"name", "entry_type_label"})
INTERNAL_ERROR_WORDS = ("Traceback", "KeyError", "NoneType", "TypeError", "AttributeError", "Exception")
_JAPANESE = re.compile(r"[぀-ヿ一-鿿]")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
# 第 8 波（IK-1062）: restore（候補に戻す）・revoke（確定の取り消し）も読み、同じリンクの最後の判断だけを E2 に渡す
# （見送り → 候補に戻した行は DB で candidate になるのが正しい — 偽所見を作らない）
_LINK_DECIDE_RE = re.compile(r"/identity-links/(?P<id>[^/]+)/(?P<decision>confirm|reject|restore|revoke)$")
_ENTRY_REVIEW_RE = re.compile(r"/library/entries/(?P<id>[^/]+)/review$")

LAYERS = {
    "admin.library.": ["concept_registry", "frontend_admin_ui"],
    "admin.deliberation.": ["deliberation_w", "concept_registry"],
    "admin.explanations.": ["endorsement_c", "lecture_studio"],
    "admin.document_share.": ["auth_visibility", "frontend_admin_ui"],
    "admin.copilot.": ["admin_copilot", "help_kb"],
    "learning.": ["concept_registry", "frontend_learning_ui"],
}


def _layers(action_id: str) -> list[str]:
    best = max((p for p in LAYERS if action_id.startswith(p)), key=len, default="learning.")
    return list(LAYERS[best])


def _is_teacher(step: TranscriptStep) -> bool:
    return step.action_id.startswith(TEACHER_PREFIXES)


def _is_learner(step: TranscriptStep) -> bool:
    return step.action_id in LEARNER_ACTIONS


def _skip(t: HttpTrace) -> bool:
    return t.status is None or (t.error or "").startswith(("precondition:", "unsupported")) or not t.response_excerpt


def _walk(v: Any) -> Iterator[dict]:
    if isinstance(v, dict):
        yield v
        for x in v.values():
            yield from _walk(x)
    elif isinstance(v, list):
        for x in v:
            yield from _walk(x)


def _keys(body: Any, excerpt: str) -> set[str]:
    if body is not None:
        return {str(k) for d in _walk(body) for k in d.keys()}
    return set(re.findall(r'"([A-Za-z_]+)"\s*:', excerpt or ""))


def _make(factory: FindingFactory, step: TranscriptStep, *, oracle: str, severity: str, hypothesis: str,
          key: str, quote: str = "", server_rows: Optional[dict] = None) -> Finding:
    return factory.make(oracle=oracle, severity=severity, step=step, quote=quote, layers=_layers(step.action_id),
                        hypothesis=hypothesis, server_rows=server_rows,
                        fingerprint_key=f"share:{key}:{step.action_id}")


# ---------------------------------------------------------------------------
# A 契約
# ---------------------------------------------------------------------------
def _check_detail(step: TranscriptStep, t: HttpTrace, body: Any, factory: FindingFactory) -> list[Finding]:
    # 第 9 波（IK-1077）: 引用の 403 の detail も日本語の事実文（「この説明は共有されていないため…」）
    cite_403 = t.status == 403 and (t.path or "").endswith("/cite")
    if t.status not in (409, 422) and not cite_403:
        return []
    detail = body.get("detail") if isinstance(body, dict) else None
    where = f"{t.method} {t.path}"
    text = detail if isinstance(detail, str) else ("" if detail is None else str(detail))
    if not text.strip():
        return [_make(factory, step, oracle="A", severity="confused", key="detail_empty",
                      hypothesis=f"共有・同一性・説明の拒否応答に、次の手を言う事実文が無い（{step.action_id}）",
                      server_rows={"status": t.status, "where": where})]
    if any(w in text for w in INTERNAL_ERROR_WORDS):
        return [_make(factory, step, oracle="A", severity="confused", key="detail_internal", quote=text[:200],
                      hypothesis=f"共有・同一性・説明の拒否応答に内部の例外文が出ている（{step.action_id}）",
                      server_rows={"status": t.status, "where": where})]
    if not _JAPANESE.search(text):
        return [_make(factory, step, oracle="A", severity="confused", key="detail_english", quote=text[:200],
                      hypothesis=f"共有・同一性・説明の拒否応答が日本語の事実文になっていない（{step.action_id}）",
                      server_rows={"status": t.status, "where": where})]
    return []


def _check_identity_shape(step: TranscriptStep, t: HttpTrace, body: Any, factory: FindingFactory) -> list[Finding]:
    if t.status != 200 or not (t.path or "").endswith("/identity-candidates") or not isinstance(body, dict):
        return []
    out: list[Finding] = []
    cands = [c for c in body.get("candidates") or [] if isinstance(c, dict)]
    facts = [f for f in body.get("facts") or [] if isinstance(f, str) and f.strip()]
    bad = [c for c in cands
           if isinstance(c.get("hidden_count"), bool) or not isinstance(c.get("hidden_count"), int)
           or (isinstance(c.get("hidden_count"), int) and c["hidden_count"] < 0)
           or not isinstance(c.get("hidden_unresolved"), bool)]
    if bad:
        out.append(_make(factory, step, oracle="A", severity="inconsistent", key="identity_hidden_shape",
                         hypothesis="同一性の候補の「閲覧できない論文由来の候補がある」「参照できない要素がある」の欄の形が"
                                    "契約（整数・真偽）から外れている",
                         server_rows={"entries": [str((c.get("entry") or {}).get("name") or "") for c in bad][:10]}))
    if not cands and not facts:
        out.append(_make(factory, step, oracle="A", severity="confused", key="identity_empty_no_fact",
                         hypothesis="同一性の候補が空なのに、候補が無いことを言う事実文が応答に無い"))
    if any(c.get("hidden_unresolved") is True for c in cands) and len(facts) == 0:
        out.append(_make(factory, step, oracle="A", severity="confused", key="identity_unresolved_no_fact",
                         hypothesis="参照できない要素への候補を落としたのに、落としたことを言う事実文が無い"))
    return out


TEX_LABEL_KEY_RE = re.compile(r"(?<![A-Za-z0-9_])eq:[A-Za-z0-9_]")
TEX_LABEL_DISPLAY_KEYS = ("facts", "name", "label", "title", "heading", "fact_line")


def _check_learner_tex_label(step: TranscriptStep, t: HttpTrace, body: Any, factory: FindingFactory) -> list[Finding]:
    """B6（第 9 波・IK-0681）。表示語の欄だけを見る（番地キー・本文は見ない — 偽所見を作らない）。"""
    if body is None or step.action_id not in ("learning.symbol.lookup", "learning.component.context",
                                              "learning.component.context_hop"):
        return []
    hits: list[str] = []
    for d in _walk(body):
        for k in TEX_LABEL_DISPLAY_KEYS:
            v = d.get(k)
            vals = v if isinstance(v, list) else [v]
            hits += [str(x) for x in vals if isinstance(x, str) and TEX_LABEL_KEY_RE.search(x)]
    if not hits:
        return []
    return [_make(factory, step, oracle="B", severity="principle", key="learner_tex_label", quote=hits[0][:200],
                  hypothesis="学生の記号の吹き出し・部品の文脈の表示語に、TeX の式の label キー（eq:…）がそのまま出ている")]


UNDECIDABLE_QUESTION_KEYS = ("confirm_question", "confirm_note", "granularity_note")


def _check_decidable_shape(step: TranscriptStep, t: HttpTrace, body: Any, factory: FindingFactory) -> list[Finding]:
    """A13 / A14（第 9 波・IK-1076 / IK-1077）。切れた応答（body が読めない）は見ない。"""
    if t.status != 200 or body is None or t.method.upper() != "GET":
        return []
    path = t.path or ""
    out: list[Finding] = []
    if path.endswith(("/identity-links", "/identity-candidates")):
        rows = [d for d in _walk(body) if d.get("decidable") is False]
        no_note = [d for d in rows if not str(d.get("undecidable_note") or "").strip()]
        asks = [d for d in rows if any(d.get(k) for k in UNDECIDABLE_QUESTION_KEYS)]
        if no_note:
            out.append(_make(factory, step, oracle="A", severity="confused", key="undecidable_no_note",
                             hypothesis="判断の対象でない同一性リンクの行に、判断できない理由の事実文が無い"
                                        "（画面はボタンを出さず、理由も出さない）",
                             server_rows={"where": f"{t.method} {path}"}))
        if asks:
            out.append(_make(factory, step, oracle="A", severity="inconsistent", key="undecidable_asks",
                             hypothesis="判断の対象でない同一性リンクの行が、確定の問いや確認の文を持っている",
                             server_rows={"where": f"{t.method} {path}",
                                          "keys": sorted({k for d in asks for k in UNDECIDABLE_QUESTION_KEYS if d.get(k)})}))
    if path.endswith("/explanations"):
        exps = body if isinstance(body, list) else (body.get("explanations") if isinstance(body, dict) else None)
        bad = [e for e in exps or [] if isinstance(e, dict) and e.get("can_cite") is False
               and not str(e.get("cite_note") or "").strip()]
        if bad:
            out.append(_make(factory, step, oracle="A", severity="confused", key="cite_no_note",
                             hypothesis="引用できない説明に、引用できない理由の事実文が無い（［別コースで引用］が黙って消える）",
                             server_rows={"where": f"{t.method} {path}"}))
    return out


def _check_derive(step: TranscriptStep, t: HttpTrace, body: Any, factory: FindingFactory) -> list[Finding]:
    if t.status != 200 or not (t.path or "").endswith("/atlas-links/derive"):
        return []
    if "coverage" in _keys(body, t.response_excerpt):
        return [_make(factory, step, oracle="A", severity="principle", key="derive_coverage",
                      hypothesis="地図との対応の導出の応答に件数報告（coverage）が載っている（教員にも件数を見せない規律）")]
    return []


def _check_gate_404(step: TranscriptStep, factory: FindingFactory) -> list[Finding]:
    """A4 / A5: 同じステップの中で前の呼び出しが通ったのに、本題の呼び出しが 404。"""
    out: list[Finding] = []
    if step.action_id == "admin.explanations.open":
        listed = any(t.status == 200 and (t.path or "").endswith("/theory-components") for t in step.http)
        miss = next((t for t in step.http if t.status == 404 and (t.path or "").endswith("/explanations")), None)
        if listed and miss is not None:
            out.append(_make(factory, step, oracle="A", severity="blocked", key="explanations_404",
                             quote=miss.response_excerpt[:200],
                             hypothesis="原稿スタジオで部品の一覧は開けたのに、その部品の「説明・承認の共有」が"
                                        "「見つからない」で開けない（コースに属さない部品は説明・承認に届かない疑い）",
                             server_rows={"where": f"{miss.method} {miss.path}"}))
    if step.action_id == "admin.document_share.add":
        miss = next((t for t in step.http if t.status == 404 and (t.path or "").endswith("/groups")
                     and t.method.upper() == "POST"), None)
        if miss is not None:
            out.append(_make(factory, step, oracle="A", severity="confused", key="share_add_404",
                             quote=miss.response_excerpt[:200],
                             hypothesis="共有を開けた（閲覧できる）教材にグループ共有を追加すると「見つからない」が返る"
                                        "（変更は所有者だけという理由が画面に出ない疑い）",
                             server_rows={"where": f"{miss.method} {miss.path}"}))
    return out


def _check_dismiss_kept(step: TranscriptStep, t: HttpTrace, body: Any, factory: FindingFactory) -> list[Finding]:
    if t.status != 200 or not isinstance(body, dict) or t.method.upper() != "POST":
        return []
    path = t.path or ""
    target = None
    for suffix, key in (("/review", "entry"), ("/decide", "relation"), ("/decide", "link")):
        if path.endswith(suffix) and isinstance(body.get(key), dict):
            target = body[key]
            break
    if not isinstance(target, dict):
        return []
    status = str(target.get("review_status") or target.get("status") or "")
    if status != "dismissed":
        return []
    if not str(target.get("review_note") or "").strip():
        return [_make(factory, step, oracle="A", severity="inconsistent", key="dismiss_reason_lost",
                      hypothesis="見送りにした概念・関係・対応の応答に、見送りの理由が残っていない",
                      server_rows={"where": f"{t.method} {path}"})]
    return []


SYMBOL_LHS_NOTE = "symbol_lhs:"  # uxsim/runner/share_exec.py の同名定数と一致（テストで固定）


def _check_learner_explanations_404(step: TranscriptStep, factory: FindingFactory) -> list[Finding]:
    """A7: 学生の部品の説明（app.js showComponentExplanations）が 404。画面は「説明を取得できませんでした。」の 1 行。"""
    if step.action_id != "learning.component.explanations":
        return []
    miss = next((t for t in step.http if t.status == 404 and (t.path or "").endswith("/explanations")), None)
    if miss is None:
        return []
    return [_make(factory, step, oracle="A", severity="blocked", key="learner_explanations_404",
                  quote=(miss.response_excerpt or "")[:200],
                  hypothesis="学生が受講中のコースで部品の説明を開くと「見つからない」が返り、画面は「説明を取得できません"
                             "でした」だけを出す（説明の権限・絞り込みが受講ではなく別の条件を見ている疑い）",
                  server_rows={"where": f"{miss.method} {miss.path}"})]


def _check_derive_facts_dropped(step: TranscriptStep, factory: FindingFactory) -> list[Finding]:
    """A8: 導出の応答の facts が観察（画面の投影）に 1 つも現れない。"""
    if step.action_id != "admin.library.atlas_links_derive":
        return []
    for t in step.http:
        if t.status != 200 or not (t.path or "").endswith("/atlas-links/derive"):
            continue
        body = excerpt_json(t.response_excerpt)
        facts = [f.strip() for f in (body.get("facts") if isinstance(body, dict) else None) or []
                 if isinstance(f, str) and f.strip()]
        if facts and not any(f in (step.observation or "") for f in facts):
            return [_make(factory, step, oracle="A", severity="confused", key="derive_facts_dropped",
                          quote=facts[0][:200],
                          hypothesis="地図との対応の導出でサーバが結果の事実文（候補が無い理由など）を返したのに、"
                                     "画面はそれを描かず、なぜ候補が増えないのかが読めない")]
    return []


def _symbol_answered_no_definition(t: HttpTrace) -> Optional[bool]:
    """記号の照会の応答に定義の文が無いか（読めない応答は None）。"""
    if t.status != 200 or not (t.path or "").endswith("/symbols/lookup"):
        return None
    body = excerpt_json(t.response_excerpt)
    if not isinstance(body, dict):
        return None
    definition = body.get("definition") if isinstance(body.get("definition"), dict) else {}
    return not str(definition.get("text") or "").strip()


def _check_symbol_definitions(steps: list[TranscriptStep], factory: FindingFactory) -> list[Finding]:
    """A9: 左辺に現れる記号に定義が無い（ステップごと）／照会した記号がどれも定義を返さない（run で 1 件）。"""
    out: list[Finding] = []
    answered: list[tuple[TranscriptStep, bool]] = []
    for step in steps:
        if step.action_id != "learning.symbol.lookup":
            continue
        verdicts = [v for v in (_symbol_answered_no_definition(t) for t in step.http) if v is not None]
        if not verdicts:
            continue
        none_defined = all(verdicts)
        answered.append((step, none_defined))
        lhs = [n for n in step.runner_notes or [] if str(n).startswith(SYMBOL_LHS_NOTE)]
        if none_defined and lhs:
            out.append(_make(factory, step, oracle="A", severity="confused", key="symbol_lhs_no_definition",
                             quote=str(lhs[0])[len(SYMBOL_LHS_NOTE):][:120],
                             hypothesis="教材に定義の式（その記号が左辺の式）が見えている記号をタップしたのに、"
                                        "「定義が見つからない」と答えた（式だけの定義を照会が拾っていない疑い）"))
    if len(answered) >= 2 and all(nd for _, nd in answered):
        out.append(_make(factory, answered[-1][0], oracle="A", severity="confused", key="symbol_all_no_definition",
                         hypothesis="この run で照会した記号のどれにも定義の文が返らなかった（記号の照会が定義を引けていない疑い）"))
    return out


# ---------------------------------------------------------------------------
# B 原則
# ---------------------------------------------------------------------------
def _check_learner_keys(step: TranscriptStep, t: HttpTrace, body: Any, factory: FindingFactory) -> list[Finding]:
    keys = _keys(body, t.response_excerpt)
    out: list[Finding] = []
    hits = [k for k in LEARNER_FORBIDDEN_KEYS if k in keys]
    if hits:
        out.append(_make(factory, step, oracle="B", severity="principle", key="learner_internal_keys",
                         quote=", ".join(hits),
                         hypothesis="学生に返る部品・記号・旅の応答に、概念レジストリの内部の宛先（ID・同一性の鍵・判断の記録）"
                                    "の欄が載っている"))
    counts = []
    if body is not None:
        for d in _walk(body):
            for k in LEARNER_COUNT_KEYS:
                v = d.get(k)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    counts.append(k)
    else:
        counts = [k for k in LEARNER_COUNT_KEYS if re.search(rf'"{k}"\s*:\s*\d', t.response_excerpt or "")]
    if counts:
        out.append(_make(factory, step, oracle="B", severity="principle", key="learner_counts",
                         quote=", ".join(sorted(set(counts))),
                         hypothesis="学生に返る部品・説明の応答に数（ほかに使っている論文の数・教員の支持の内訳・隠した件数）が載っている"))
    if body is not None:
        for d in _walk(body):
            ref = d.get("concept_ref")
            if isinstance(ref, dict):
                extra = sorted(set(ref) - CONCEPT_REF_ALLOWED)
                if extra:
                    out.append(_make(factory, step, oracle="B", severity="principle", key="concept_ref_fields",
                                     quote=", ".join(extra),
                                     hypothesis="学生の記号の意味の「概念」に、名前と種別のラベル以外の欄が載っている"))
                    break
    return out


def _check_teacher_ids(step: TranscriptStep, t: HttpTrace, body: Any, factory: FindingFactory) -> list[Finding]:
    if t.status != 200 or not isinstance(body, dict):
        return []
    out: list[Finding] = []
    if (t.path or "").endswith("/identity-candidates"):
        blank = [l for c in body.get("candidates") or [] if isinstance(c, dict)
                 for l in c.get("links") or [] if isinstance(l, dict) and not str(l.get("document_title") or "").strip()]
        if blank:
            out.append(_make(factory, step, oracle="B", severity="principle", key="identity_blank_title",
                             hypothesis="同一性の候補のリンクに論文の題名が無く、画面は題名の代わりに論文の内部 ID を出す"))
    if (t.path or "").endswith("/identity-links") and t.method.upper() == "GET":
        # 第 3 波（IK-0916）: 是正後の画面は instance_type_label / instance_label / instance_document_title で描き、
        # 内部 ID は title 属性だけ。表示語の無い行（旧サーバの形）だけを拾う
        raw = [l for l in body.get("identity_links") or [] if isinstance(l, dict)
               and _UUID.match(str(l.get("instance_document_id") or ""))
               and not (l.get("instance_type_label") and (l.get("instance_label") or l.get("instance_document_title")))]
        if raw:
            out.append(_make(factory, step, oracle="B", severity="principle", key="dlb_link_doc_id",
                             hypothesis="深く検討の同一性リンクの行で、画面が論文の内部 ID と英語の要素型をそのまま出している"))
    return out


# ---------------------------------------------------------------------------
# 入口（A・B）
# ---------------------------------------------------------------------------
def _check_copilot_library(step: TranscriptStep, factory: FindingFactory) -> list[Finding]:
    """A10・A11（第 25 周 第 2 波）。ナレッジライブラリのタブから聞いたときだけ（tab 引数で判定）。"""
    if step.action_id != COPILOT_ACTION or str((step.args or {}).get("tab") or "") not in LIBRARY_TABS:
        return []
    out: list[Finding] = []
    for t in step.http:
        if _skip(t) or t.status != 200 or not t.path.endswith("/assistant/chat"):
            continue
        body = excerpt_json(t.response_excerpt)
        answer = str(body.get("answer") or "") if isinstance(body, dict) else ""
        hits = [w for w in COPILOT_INTERNAL_WORDS if re.search(rf"(?<![A-Za-z0-9_]){re.escape(w)}(?![A-Za-z0-9_])", answer)]
        if hits:
            out.append(_make(factory, step, oracle="A", severity="confused", key="copilot_internal_words",
                             quote=answer[:200], hypothesis="Copilot の回答に操作手順の内部語がそのまま出ている",
                             server_rows={"words": hits}))
        cites = [c for c in (body.get("citations") or []) if isinstance(c, dict)] if isinstance(body, dict) else []
        first = str((cites[0] if cites else {}).get("doc") or "")
        if first and "library" not in first:
            out.append(_make(factory, step, oracle="A", severity="confused", key="copilot_off_screen_source",
                             hypothesis="ナレッジライブラリの画面から聞いたのに、回答の出所がこの画面の手順でも節でもない",
                             server_rows={"first_citation": first[:200]}))
    return out


_IDENTITY_CREATE = re.compile(r"^/api/admin/deliberation/identity-links$")


def _check_identity_recreate(steps: list[TranscriptStep], factory: FindingFactory) -> list[Finding]:
    """A12（IK-0918）: 作成の応答の id が前の作成の id と同じなのに、観察が「作成しました」と言う。"""
    out: list[Finding] = []
    seen: set[str] = set()
    for step in steps:
        for t in step.http:
            if t.method != "POST" or not _IDENTITY_CREATE.match(t.path or "") or not (200 <= (t.status or 0) < 300):
                continue
            body = excerpt_json(t.response_excerpt)
            lid = str(body.get("id") or "") if isinstance(body, dict) else ""
            if not lid:
                m = re.search(r'"id"\s*:\s*"([^"]+)"', t.response_excerpt or "")
                lid = m.group(1) if m else ""
            if not lid:
                continue
            if lid in seen and "作成しました" in (step.observation or ""):
                out.append(_make(factory, step, oracle="A", severity="inconsistent", key="identity_recreate",
                                 quote="作成しました",
                                 hypothesis="同一性リンクの作成で既存のリンクが返った（応答の id が前の作成と同じ）のに、"
                                            "画面が新しく作成したと言っている",
                                 server_rows={"where": f"{t.method} {t.path}", "same_link_as_before": True}))
            seen.add(lid)
    return out


def check(steps: list[TranscriptStep], factory: FindingFactory) -> list[Finding]:
    out: list[Finding] = _check_symbol_definitions(steps, factory)
    out += _check_identity_recreate(steps, factory)
    for step in steps:
        out += _check_copilot_library(step, factory)
    for step in steps:
        teacher, learner = _is_teacher(step), _is_learner(step)
        if not (teacher or learner):
            continue
        if teacher:
            out += _check_gate_404(step, factory)
            out += _check_derive_facts_dropped(step, factory)
        else:
            out += _check_learner_explanations_404(step, factory)
        for t in step.http:
            if _skip(t) or (t.status or 0) >= 500:
                continue
            body = excerpt_json(t.response_excerpt)
            if teacher:
                out += _check_detail(step, t, body, factory)
                out += _check_identity_shape(step, t, body, factory)
                out += _check_derive(step, t, body, factory)
                out += _check_dismiss_kept(step, t, body, factory)
                out += _check_teacher_ids(step, t, body, factory)
                out += _check_decidable_shape(step, t, body, factory)
            elif t.status == 200:
                out += _check_learner_keys(step, t, body, factory)
                out += _check_learner_tex_label(step, t, body, factory)
    return out


# ---------------------------------------------------------------------------
# E 観測（砂場 DB）
# ---------------------------------------------------------------------------
def learner_visible_names(steps: list[TranscriptStep]) -> list[dict]:
    """学生に見えた共通部品（shared_part.entry.name）と概念（concept_ref.name）の名前（読めた応答だけ）。"""
    out: list[dict] = []
    for s in steps:
        if not _is_learner(s):
            continue
        for t in s.http:
            if t.status != 200:
                continue
            body = excerpt_json(t.response_excerpt)
            for d in _walk(body):
                sp = d.get("shared_part")
                if isinstance(sp, dict) and isinstance(sp.get("entry"), dict) and sp["entry"].get("name"):
                    out.append({"name": str(sp["entry"]["name"]), "via": "shared_part", "seq": s.seq})
                ref = d.get("concept_ref")
                if isinstance(ref, dict) and ref.get("name"):
                    out.append({"name": str(ref["name"]), "via": "concept_ref", "seq": s.seq})
    return out


def teacher_decisions(steps: list[TranscriptStep]) -> dict[str, list[dict]]:
    links: list[dict] = []
    entries: list[dict] = []
    for s in steps:
        if not _is_teacher(s):
            continue
        for t in s.http:
            if t.status != 200 or t.method.upper() != "POST":
                continue
            m = _LINK_DECIDE_RE.search(t.path or "")
            if m:
                links.append({**m.groupdict(), "seq": s.seq})
            m = _ENTRY_REVIEW_RE.search(t.path or "")
            body = excerpt_json(t.response_excerpt)
            entry = body.get("entry") if isinstance(body, dict) else None
            if m and isinstance(entry, dict) and entry.get("review_status") == "dismissed":
                entries.append({"id": m.group("id"), "seq": s.seq})
    last = {l["id"]: i for i, l in enumerate(links)}
    links = [l for i, l in enumerate(links) if last[l["id"]] == i and l["decision"] in ("confirm", "reject")]
    return {"links": links, "entries": entries}


def collect(database_url: str, names: list[dict], decisions: dict, engine: Any = None) -> dict:
    """E の材料（SELECT のみ）。"""
    from sqlalchemy import create_engine, text  # 遅延 import

    eng = engine or create_engine(database_url)
    rows: dict = {"names": [], "links": [], "entries": []}
    with eng.connect() as conn:
        for n in names:
            got = conn.execute(text(
                "SELECT review_status, status FROM library_entries WHERE name = :name"), {"name": n["name"]}).mappings().all()
            rows["names"].append({**n, "rows": [dict(r) for r in got]})
        for d in decisions.get("links") or []:
            got = conn.execute(text("SELECT status FROM element_identity_links WHERE id::text = :id"),
                               {"id": d["id"]}).mappings().all()
            rows["links"].append({**d, "status": got[0]["status"] if got else None, "found": bool(got)})
        for d in decisions.get("entries") or []:
            got = conn.execute(text("SELECT review_status, review_note FROM library_entries WHERE id::text = :id"),
                               {"id": d["id"]}).mappings().all()
            rows["entries"].append({**d, "row": dict(got[0]) if got else None})
    return rows


def check_rows(rows: dict, factory: FindingFactory) -> list[Finding]:
    out: list[Finding] = []

    def make(hypothesis: str, server_rows: dict, key: str, screen: str) -> Finding:
        return factory.make(oracle="E", severity="principle" if key == "candidate_visible" else "inconsistent",
                            screen=screen, affordance="", hypothesis=hypothesis, server_rows=server_rows,
                            layers=["concept_registry", "deliberation_w"], fingerprint_key=f"share:e:{key}")

    leaked = [{"name": n["name"], "via": n["via"], "seq": n["seq"],
               "statuses": [(r.get("review_status"), r.get("status")) for r in n["rows"]]}
              for n in rows.get("names") or []
              if not any(r.get("review_status") == "confirmed" and r.get("status") == "active" for r in n["rows"])]
    if leaked:
        out.append(make("学生に共通部品・概念として見えた名前に、確定かつ公開のライブラリエントリが無い"
                        "（候補か見送りの概念が学生に届いた疑い）", {"shown": leaked[:10]}, "candidate_visible", "learning"))
    lost = [d for d in rows.get("links") or [] if d.get("decision") == "reject" and d.get("status") != "rejected"]
    if lost:
        out.append(make("教員が見送り・却下した同一性リンクが、却下の状態で残っていない（消えたか別の状態）",
                        {"links": lost[:10]}, "link_reject_lost", "admin"))
    bad = [d for d in rows.get("entries") or [] if not d.get("row") or d["row"].get("review_status") != "dismissed"
           or not str(d["row"].get("review_note") or "").strip()]
    if bad:
        out.append(make("教員が見送った概念が、見送りの状態と理由で残っていない", {"entries": bad[:10]},
                        "entry_dismiss_lost", "admin"))
    return out


def check_observation(meta: Optional[RunMeta], factory: FindingFactory, database_url: str = "", engine: Any = None,
                      *, steps: Optional[list[TranscriptStep]] = None) -> list[Finding]:
    """E の規則。DB の URL も engine も無ければ空（未実施）。"""
    if not steps or not (database_url or engine is not None):
        return []
    names = learner_visible_names(steps)
    decisions = teacher_decisions(steps)
    if not names and not decisions["links"] and not decisions["entries"]:
        return []
    try:
        rows = collect(database_url, names, decisions, engine)
    except Exception:  # noqa: BLE001 — 読めなければ未実施
        return []
    return check_rows(rows, factory)
