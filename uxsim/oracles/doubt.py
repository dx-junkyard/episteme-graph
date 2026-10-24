"""D層・SL層（前提の地図・台帳・疑義・検証提案・反実仮想）と学生の検証状態の一行の審判（第 25 周・面 B）。

決定論・LLM 0 回。応答は ``HttpTrace.response_excerpt``（先頭 2000 字の JSON）を読み、画面の文は
``TranscriptStep.observation``（``uxsim/runner/doubt_exec.py`` の投影 = 画面と同じ欄）を読む。
語彙は製品のガードレールテストから ast で読む（PE6 — 新しい期待表を作らない。製品コードは import しない）:

- 閉世界の禁止語 ``test_stakes_ledger_guardrails.py::TestClosedWorldVocabulary.BANNED``（SL1）
- 煽り語 ``test_doubt_guardrails.py::TestNoHypeLanguage.BANNED``
- 学習者向けの数値キー ``test_doubt_guardrails.py::TestLearnerOpenAssumptionsHideRawNumbers._FORBIDDEN``
- 学習者向けの帰属キー ``test_stakes_ledger_guardrails.py::TestFalsificationProjectionsDropNumericAndAttribution``
  の ``assert "<key>" not in …`` の左辺
- 教員向けの数値キー ``test_doubt_guardrails.py::TestNoRawNumbersExposed`` の ``assert '"<key>"' not in …`` の左辺

A 契約:
- DA1 doubt の 4xx（401/403/404/429 を除く）の ``detail`` が空・内部例外文・日本語を含まない
- DA2 学習者向けの台帳・未検証合意リストの応答に、帰属・数値のキー（上の 2 表）がある
- DA3 教員向けの doubt の応答に、数値のキー（confidence / load_score）がある
- DA4 疑義に投稿者の名前が無い（匿名に見える疑義）
- DA5 取り下げた疑義・却下した前提が、その後の読み直しで行として見えない（P4: 状態遷移で残る）
- DA6 スコープ 0 件の台帳で「直接検証の記帳あり」になっている（全称検証の構造的禁止）
- DA7 コーパス外の確認が空の検証提案が 2xx で通った（SL-8）
- DA8 トピックのあるコース（教員のコース一覧で topic_count が 0 でない）で、コーパス監査が教材 0 件（``documents: 0``）を
  返した — D層の範囲がコースの教材から解決されていない（IK-0852 の型。第 25 周 H9）
- DA9 反実仮想の試算が ``warnings``（前提がグラフ上に見つからない等）を返したのに、画面の投影にその事実が無く
  依存範囲が「なし」に見える（IK-0854 の型。第 25 周 H9）
- DA10 教員の台帳詳細の応答の ``document_id`` が、開いた論文（散布図の点・未検証合意リストの行・原稿スタジオの
  グラフの ``document_id``）と違う — 別の論文の台帳行を読んでいる（IK-0899 の型。第 25 周 第 2 周 H17。runner が
  trace の ``digest["doubt_ledger"]`` に両方を置く）

B 原則:
- DB1 SL1 閉世界・煽り — 応答の表示欄と画面の投影に禁止語がある
- DB2 SL1 の隣接 — 同じ文に「この分野」と「未検証」（または「検証されていない」）が並ぶ（分野レベルの不在言明に読める）
- DB3 教員の台帳の「記帳者」に利用者の内部 ID（UUID）がそのまま出ている
- DB5 画面に共有範囲（Visibility）の英語の内部値（private / group / public）がそのまま出ている（第 25 周 H9）
- DB4 権限の線引き — 同じ教員が、コース単位の編集権限ゲートのある読み（observation-targets / naive-signals）で
  404 になったコース、または一覧で ``role: viewer`` のコースに対して、コース単位の doubt の書き込みが 2xx で通った

E 観測（砂場 DB の読みだけ）:
- DE1 教員が記帳（スコープ / 検証状態 / 覆る条件）した台帳行の ``document_id`` が、run で開いたコースの教材の
  document 集合のどれにも無い（第 25 周 H26: IK-0852 以降 D層の範囲はコースの教材の document 集合で決まり、
  ``epistemic_ledger.course_id`` は範囲に使わない。migration 092 以降は同じ対象に論文ごとの行があるので、
  台帳詳細で開いた論文の document_id で行を引く）

限界: 抜粋が 2000 字で切れた台帳の応答は JSON として読めず、DA5 / DA6 はその応答では判定しない。
"""
from __future__ import annotations

import ast
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator, Optional

from uxsim.config import REPO_ROOT
from uxsim.oracles.findings import FindingFactory, excerpt_json
from uxsim.schema import Finding, HttpTrace, RunMeta, TranscriptStep

ADMIN_PREFIX = "admin.doubt."
LEARNER_PREFIX = "learning.ledger."
# 既存の行為のうち、台帳・検証状態の事実文が出る画面（閉世界の語彙を見る）
RELATED_ACTIONS = ("admin.seminar_brief.view", "learning.discuss.opening", "learning.personal_network.nearby",
                   "admin.simulation.review_shared_run")
LAYERS_ADMIN = ["doubt_d", "stakes_ledger_sl", "frontend_admin_ui"]
LAYERS_LEARNER = ["doubt_d", "stakes_ledger_sl", "frontend_learning_ui"]

_TESTS = REPO_ROOT / "backend" / "tests"
_STAKES = _TESTS / "test_stakes_ledger_guardrails.py"
_DOUBT = _TESTS / "test_doubt_guardrails.py"

INTERNAL_ERROR_WORDS = ("Traceback", "KeyError", "NoneType", "TypeError", "AttributeError", "Exception")
DISPLAY_KEYS = frozenset({"fact_line", "facts", "statement", "label", "notice", "note", "message", "detail",
                          "verification_status_label", "support_fact_line", "evidence_lines_fact", "reason",
                          "kind_label", "reachability_label", "source_label", "title", "text", "summary"})
_JAPANESE = re.compile(r"[぀-ヿ一-鿿]")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
_SENTENCE = re.compile(r"[^。\n]+")
_FIELD_WORD = "この分野"
_UNVERIFIED_WORDS = ("未検証", "検証されていない", "検証記録がない", "検証記録がありません")
_LEARNER_LEDGER_PATH = re.compile(r"^/api/learning/courses/[^/]+/(ledger/|open-assumptions)")
_COURSE_WRITE = re.compile(r"^/api/admin/doubt/courses/([^/]+)/(corpus-audit/run|assumption-mining/run|load/recompute|"
                           r"falsification-candidates/refresh|scope-candidates/refresh)$")
_GATED_READ = re.compile(r"^/api/admin/doubt/courses/([^/]+)/(observation-targets|naive-signals)$")
_AUDIT_RUN = re.compile(r"^/api/admin/doubt/courses/([^/]+)/corpus-audit/run$")
_CF_RESULT = re.compile(r"^/api/admin/doubt/counterfactual/(compute|sessions)$")
_VISIBILITY_RAW = re.compile(r"(?<![A-Za-z0-9_\-])(private|group|public)(?![A-Za-z0-9_\-])")
_LEDGER_WRITE = re.compile(r"^/api/admin/doubt/ledger/([^/]+)/([^/]+)/(scopes|verification-status|falsification-conditions)$")


# ---------------------------------------------------------------------------
# 製品のガードレールテストから語彙を読む（ast・import しない）
# ---------------------------------------------------------------------------
def _class_node(path: Path, cls: str) -> Optional[ast.ClassDef]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None
    return next((n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == cls), None)


def _assigned_strings(path: Path, cls: str, name: str) -> tuple[str, ...]:
    """クラス属性 ``name`` の tuple / frozenset({...}) / set の文字列定数。"""
    node = _class_node(path, cls)
    if node is None:
        return ()
    for stmt in node.body:
        if isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in stmt.targets):
            return tuple(c.value for c in ast.walk(stmt.value) if isinstance(c, ast.Constant) and isinstance(c.value, str))
    return ()


def _not_in_left_strings(path: Path, cls: str) -> tuple[str, ...]:
    """クラス内の ``assert "<s>" not in …`` の左辺の文字列（引用符で囲まれた '"key"' は中身にする）。"""
    node = _class_node(path, cls)
    if node is None:
        return ()
    out: list[str] = []
    for n in ast.walk(node):
        if isinstance(n, ast.Compare) and any(isinstance(op, ast.NotIn) for op in n.ops) \
                and isinstance(n.left, ast.Constant) and isinstance(n.left.value, str):
            out.append(n.left.value.strip("\"'"))
    return tuple(dict.fromkeys(out))


@lru_cache(maxsize=1)
def closed_world_banned() -> tuple[str, ...]:
    return _assigned_strings(_STAKES, "TestClosedWorldVocabulary", "BANNED") + \
        _assigned_strings(_DOUBT, "TestNoHypeLanguage", "BANNED")


@lru_cache(maxsize=1)
def learner_forbidden_keys() -> frozenset[str]:
    keys = set(_assigned_strings(_DOUBT, "TestLearnerOpenAssumptionsHideRawNumbers", "_FORBIDDEN"))
    keys |= {k for k in _not_in_left_strings(_STAKES, "TestFalsificationProjectionsDropNumericAndAttribution")
             if re.fullmatch(r"[a-z_]+", k)}
    return frozenset(keys)


@lru_cache(maxsize=1)
def teacher_forbidden_keys() -> frozenset[str]:
    return frozenset(k for k in _not_in_left_strings(_DOUBT, "TestNoRawNumbersExposed") if re.fullmatch(r"[a-z_]+", k))


# ---------------------------------------------------------------------------
# 読み出し
# ---------------------------------------------------------------------------
def _walk_dicts(v: Any) -> Iterator[dict]:
    if isinstance(v, dict):
        yield v
        for x in v.values():
            yield from _walk_dicts(x)
    elif isinstance(v, list):
        for x in v:
            yield from _walk_dicts(x)


def _texts(v: Any) -> list[str]:
    if isinstance(v, str):
        return [v] if v.strip() else []
    if isinstance(v, list):
        return [x for x in v if isinstance(x, str) and x.strip()]
    return []


def _display_texts(body: Any) -> Iterator[tuple[str, str]]:
    for d in _walk_dicts(body):
        for k, v in d.items():
            if k in DISPLAY_KEYS:
                for t in _texts(v):
                    yield k, t


def _skip(t: HttpTrace) -> bool:
    return t.status is None or t.error.startswith(("precondition:", "unsupported")) or not t.response_excerpt


def _is_target(step: TranscriptStep) -> bool:
    return step.action_id.startswith((ADMIN_PREFIX, LEARNER_PREFIX)) or step.action_id in RELATED_ACTIONS \
        or any(_LEARNER_LEDGER_PATH.match(t.path or "") or (t.path or "").startswith("/api/admin/doubt/") for t in step.http)


def _layers(step: TranscriptStep) -> list[str]:
    return list(LAYERS_LEARNER if step.screen == "learning" or step.action_id.startswith("learning.") else LAYERS_ADMIN)


def _make(factory: FindingFactory, step: TranscriptStep, *, oracle: str, severity: str, hypothesis: str, key: str,
          quote: str = "", server_rows: Optional[dict] = None) -> Finding:
    return factory.make(oracle=oracle, severity=severity, step=step, quote=quote, layers=_layers(step),
                        hypothesis=hypothesis, server_rows=server_rows,
                        fingerprint_key=f"doubt:{key}:{step.action_id}")


# ---------------------------------------------------------------------------
# A 契約
# ---------------------------------------------------------------------------
def _check_detail(step: TranscriptStep, t: HttpTrace, body: Any, factory: FindingFactory) -> list[Finding]:
    path = t.path or ""
    if not (path.startswith("/api/admin/doubt/") or _LEARNER_LEDGER_PATH.match(path)):
        return []
    if not (400 <= (t.status or 0) < 500) or t.status in (401, 403, 404, 429):
        return []
    detail = body.get("detail") if isinstance(body, dict) else None
    where = {"status": t.status, "where": f"{t.method} {t.path}"}
    text = detail if isinstance(detail, str) else ("" if detail is None else str(detail))
    if not text.strip():
        return [_make(factory, step, oracle="A", severity="confused", key="detail_empty", quote=t.response_excerpt,
                      hypothesis=f"台帳・疑義の拒否応答に次の手を言う事実文（detail）が無い（{step.action_id}）",
                      server_rows=where)]
    hit = next((w for w in INTERNAL_ERROR_WORDS if w in text), "")
    if hit:
        return [_make(factory, step, oracle="A", severity="confused", key="detail_internal", quote=text[:200],
                      hypothesis=f"台帳・疑義の拒否応答の detail に内部の例外文が出ている（{step.action_id}）",
                      server_rows=where)]
    if not _JAPANESE.search(text):
        return [_make(factory, step, oracle="A", severity="confused", key="detail_english", quote=text[:200],
                      hypothesis=f"台帳・疑義の拒否応答の detail が日本語の事実文になっていない（{step.action_id}）",
                      server_rows=where)]
    return []


def _keys(body: Any) -> set[str]:
    return {str(k) for d in _walk_dicts(body) for k in d.keys()}


def _check_learner_keys(step: TranscriptStep, t: HttpTrace, body: Any, factory: FindingFactory) -> list[Finding]:
    if not _LEARNER_LEDGER_PATH.match(t.path or "") or not (200 <= (t.status or 0) < 300):
        return []
    forbidden = learner_forbidden_keys()
    hits = sorted(_keys(body) & forbidden) if body is not None else \
        sorted(k for k in forbidden if f'"{k}"' in t.response_excerpt)
    if not hits:
        return []
    return [_make(factory, step, oracle="A", severity="principle", key="learner_keys", quote=t.response_excerpt[:300],
                  hypothesis="学習者向けの台帳・未検証合意リストの応答に、記帳者・根拠の生データ・生の数値のキーが出ている",
                  server_rows={"keys": hits, "where": t.path})]


def _check_teacher_numbers(step: TranscriptStep, t: HttpTrace, body: Any, factory: FindingFactory) -> list[Finding]:
    if not (t.path or "").startswith("/api/admin/doubt/") or not (200 <= (t.status or 0) < 300):
        return []
    forbidden = teacher_forbidden_keys()
    hits = sorted(_keys(body) & forbidden) if body is not None else \
        sorted(k for k in forbidden if f'"{k}"' in t.response_excerpt)
    if not hits:
        return []
    return [_make(factory, step, oracle="A", severity="principle", key="teacher_numbers", quote=t.response_excerpt[:300],
                  hypothesis="教員向けの台帳・前提の応答に、confidence / load_score などの生の数値のキーが出ている",
                  server_rows={"keys": hits, "where": t.path})]


def _check_anonymous(step: TranscriptStep, t: HttpTrace, body: Any, factory: FindingFactory) -> list[Finding]:
    if body is None or not (t.path or "").startswith("/api/admin/doubt/"):
        return []
    anon = [str(c.get("id") or "") for d in _walk_dicts(body) for c in (d.get("challenges") or d.get("items") or [])
            if isinstance(c, dict) and "challenge_type" in c and not str(c.get("challenger_name") or "").strip()]
    if not anon:
        return []
    return [_make(factory, step, oracle="A", severity="principle", key="anonymous", quote=t.response_excerpt[:300],
                  hypothesis="疑義の投稿者の名前が空で、匿名の疑義に見える（帰属必須・匿名疑義なし）",
                  server_rows={"challenge_ids": anon[:10]})]


def _check_universal_verification(step: TranscriptStep, t: HttpTrace, body: Any, factory: FindingFactory) -> list[Finding]:
    if not isinstance(body, dict) or not re.match(r"^/api/admin/doubt/ledger/[^/]+/[^/]+$", t.path or "") \
            or t.method != "GET" or t.status != 200:
        return []
    if body.get("verification_status") == "directly_verified" and not (body.get("verification_scopes") or []):
        return [_make(factory, step, oracle="A", severity="principle", key="universal_verification",
                      quote=t.response_excerpt[:300],
                      hypothesis="検証スコープが 0 件の台帳が「直接検証の記帳あり」になっている（全称検証の構造的禁止が破れている）",
                      server_rows={"target": f"{body.get('target_type')}/{body.get('target_id')}"})]
    return []


def _check_external_check(step: TranscriptStep, t: HttpTrace, factory: FindingFactory) -> list[Finding]:
    if t.method != "POST" or not re.match(r"^/api/admin/doubt/challenges/[^/]+/proposals$", t.path or "") \
            or not (200 <= (t.status or 0) < 300):
        return []
    if str(step.args.get("external_check") or "").strip():
        return []
    return [_make(factory, step, oracle="A", severity="principle", key="external_check",
                  hypothesis="コーパス外の確認が空の検証提案が登録された（SL-8 の必須記帳が効いていない）",
                  server_rows={"where": t.path})]


def check_retention(steps: list[TranscriptStep], factory: FindingFactory) -> list[Finding]:
    """DA5: 取り下げた疑義・却下した前提が行として残っているか。

    疑義: 取り下げの POST が 2xx のステップの中で、画面が続けて読み直す台帳（``refreshLedgerSection``）の応答に
    その疑義が ``withdrawn`` で載っているか。前提: 却下の後に同じペルソナが「却下済み（保持）」「すべて」の表示を
    読んだとき、その前提が載っているか（読まなければ判定しない）。
    """
    out: list[Finding] = []
    dismissed: dict[str, dict[str, int]] = {}
    for step in steps:
        pid = step.persona_id
        pending: list[str] = []
        for t in step.http:
            if _skip(t):
                continue
            path = t.path or ""
            m = re.match(r"^/api/admin/doubt/challenges/([^/]+)/withdraw$", path)
            if m and t.method == "POST" and 200 <= (t.status or 0) < 300:
                pending.append(m.group(1))
                continue
            m = re.match(r"^/api/admin/doubt/assumptions/([^/]+)/dismiss$", path)
            if m and t.method == "POST" and 200 <= (t.status or 0) < 300:
                dismissed.setdefault(pid, {})[m.group(1)] = step.seq
                continue
            if t.method != "GET" or t.status != 200:
                continue
            body = excerpt_json(t.response_excerpt)
            if pending and re.match(r"^/api/admin/doubt/ledger/[^/]+/[^/]+$", path) and isinstance(body, dict):
                ids = {str(c.get("id")): str(c.get("status")) for c in body.get("challenges") or [] if isinstance(c, dict)}
                for cid in pending:
                    if cid not in ids:
                        out.append(_make(factory, step, oracle="A", severity="principle", key="withdraw_dropped",
                                         hypothesis="取り下げた疑義が、読み直した台帳の応答から行ごと消えている（P4: 状態遷移で残す）",
                                         server_rows={"challenge_id": cid}))
                    elif ids[cid] != "withdrawn":
                        out.append(_make(factory, step, oracle="A", severity="inconsistent", key="withdraw_status",
                                         hypothesis="取り下げた疑義が、読み直した台帳で取り下げ済みになっていない",
                                         server_rows={"challenge_id": cid, "status": ids[cid]}))
                pending = []
            if re.match(r"^/api/admin/doubt/courses/[^/]+/assumptions$", path) and isinstance(body, dict) \
                    and body.get("status") in ("dismissed", "all"):
                ids = {str(a.get("id")) for a in body.get("items") or [] if isinstance(a, dict)}
                for aid in list(dismissed.get(pid, {})):
                    if aid not in ids:
                        out.append(_make(factory, step, oracle="A", severity="principle", key="dismiss_dropped",
                                         hypothesis="却下した前提が「却下済み（保持）」「すべて」の表示に出てこない（P4）",
                                         server_rows={"assumption_id": aid}))
                    dismissed[pid].pop(aid, None)
    return out


# ---------------------------------------------------------------------------
# B 原則
# ---------------------------------------------------------------------------
def _closed_world_hits(text: str) -> list[str]:
    return [w for w in closed_world_banned() if w and w in text]


def _field_level_sentence(text: str) -> str:
    for sent in _SENTENCE.findall(text or ""):
        if _FIELD_WORD in sent and any(w in sent for w in _UNVERIFIED_WORDS):
            return sent.strip()
    return ""


def _check_closed_world(step: TranscriptStep, body: Any, factory: FindingFactory, where: str) -> list[Finding]:
    out: list[Finding] = []
    texts = [t for _, t in _display_texts(body)] if body is not None else []
    for text in texts:
        hits = _closed_world_hits(text)
        if hits:
            out.append(_make(factory, step, oracle="B", severity="principle", key="closed_world_banned", quote=text[:200],
                             hypothesis="台帳・検証状態の事実文に、分野レベルの不在言明・煽りの語が出ている（SL1 閉世界・D層）",
                             server_rows={"words": hits, "where": where}))
        sent = _field_level_sentence(text)
        if sent:
            out.append(_make(factory, step, oracle="B", severity="principle", key="closed_world_field", quote=sent[:200],
                             hypothesis="検証の不在を「この分野」の範囲で言う文になっている（SL1: 言えるのはこのコーパスの中だけ）",
                             server_rows={"where": where}))
    return out


def _check_observation_text(step: TranscriptStep, factory: FindingFactory) -> list[Finding]:
    text = step.observation or ""
    out: list[Finding] = []
    hits = _closed_world_hits(text)
    if hits:
        out.append(_make(factory, step, oracle="B", severity="principle", key="closed_world_banned_screen", quote=text[:200],
                         hypothesis="画面に、分野レベルの不在言明・煽りの語が出ている（SL1 閉世界・D層）",
                         server_rows={"words": hits}))
    sent = _field_level_sentence(text)
    if sent:
        out.append(_make(factory, step, oracle="B", severity="principle", key="closed_world_field_screen", quote=sent[:200],
                         hypothesis="画面の文言が検証の不在を「この分野」の範囲で言う形になっている（SL1: 言えるのはこのコーパスの中だけ）",
                         server_rows={}))
    return out


def _check_recorder_id(step: TranscriptStep, t: HttpTrace, body: Any, factory: FindingFactory) -> list[Finding]:
    if not isinstance(body, dict) or not re.match(r"^/api/admin/doubt/ledger/[^/]+/[^/]+$", t.path or "") or t.status != 200:
        return []
    # 第 25 周 H26（doubt H25）: 応答は recorded_by（UUID）と recorded_by_label（表示名）の両方を持ち、画面は label を
    # 描く（IK-0905 の是正）。label が無いか、label 自体が UUID 形のときだけ所見にする
    ids = [str(s.get("recorded_by_label") or s.get("recorded_by")) for s in body.get("verification_scopes") or []
           if isinstance(s, dict) and s.get("recorded_by")
           and (not str(s.get("recorded_by_label") or "").strip()
                or _UUID.match(str(s.get("recorded_by_label") or "")))]
    if not ids:
        return []
    return [_make(factory, step, oracle="B", severity="confused", key="recorder_uuid",
                  quote=f"記帳者: {ids[0]}",
                  hypothesis="台帳の詳細のスコープの「記帳者」に、名前ではなく利用者の内部 ID がそのまま出ている",
                  server_rows={"count_is_not_shown": True})]


def check_permission_lines(steps: list[TranscriptStep], factory: FindingFactory) -> list[Finding]:
    """DB4: コース単位の編集権限の線引きが、読みと書きで食い違っていないか（同じ教員・同じコース）。"""
    out: list[Finding] = []
    denied: dict[str, set[str]] = {}
    viewer: dict[str, set[str]] = {}
    writes: list[tuple[TranscriptStep, str, str]] = []
    for step in steps:
        pid = step.persona_id
        for t in step.http:
            path = t.path or ""
            m = _GATED_READ.match(path)
            if m and t.status == 404:
                denied.setdefault(pid, set()).add(m.group(1))
            if path == "/api/admin/courses" and t.status == 200:
                body = excerpt_json(t.response_excerpt)
                for c in body if isinstance(body, list) else []:
                    if isinstance(c, dict) and c.get("role") == "viewer" and c.get("id"):
                        viewer.setdefault(pid, set()).add(str(c["id"]))
            m = _COURSE_WRITE.match(path)
            if m and t.method == "POST" and 200 <= (t.status or 0) < 300:
                writes.append((step, m.group(1), m.group(2)))
    seen: set[tuple[str, str, str]] = set()
    for step, course, what in writes:
        pid = step.persona_id
        why = "denied" if course in denied.get(pid, set()) else "viewer" if course in viewer.get(pid, set()) else ""
        if not why or (pid, course, what) in seen:
            continue
        seen.add((pid, course, what))
        out.append(_make(factory, step, oracle="B", severity="inconsistent", key=f"permission_{why}",
                         hypothesis="同じ教員が編集権限を持たないコース（観測系の主張の一覧は 404・または閲覧のみ）に対して、"
                                    "前提の地図のコース単位の操作が通った（コース直指定の経路の権限ゲートが揃っていない）",
                         server_rows={"course_id": course, "operation": what, "evidence": why}))
    return out


def check_audit_scope(steps: list[TranscriptStep], factory: FindingFactory) -> list[Finding]:
    """DA8: トピックのあるコースで、コーパス監査が教材 0 件を返した（同じ教員の一覧で判定する）。"""
    out: list[Finding] = []
    with_topics: dict[str, set[str]] = {}
    seen: set[tuple[str, str]] = set()
    for step in steps:
        pid = step.persona_id
        for t in step.http:
            path = t.path or ""
            if path == "/api/admin/courses" and t.status == 200:
                body = excerpt_json(t.response_excerpt)
                for c in body if isinstance(body, list) else []:
                    if isinstance(c, dict) and c.get("id") and c.get("topic_count"):
                        with_topics.setdefault(pid, set()).add(str(c["id"]))
                continue
            m = _AUDIT_RUN.match(path)
            if not m or t.method != "POST" or not (200 <= (t.status or 0) < 300):
                continue
            body = excerpt_json(t.response_excerpt)
            course = m.group(1)
            if not isinstance(body, dict) or body.get("documents") not in (0, None) or "documents" not in body:
                continue
            if course not in with_topics.get(pid, set()) or (pid, course) in seen:
                continue
            seen.add((pid, course))
            out.append(_make(factory, step, oracle="A", severity="inconsistent", key="audit_no_documents",
                             quote=t.response_excerpt[:200],
                             hypothesis="トピックのあるコースでコーパス監査が教材を一つも読まなかった — 前提の地図の範囲が"
                                        "コースの教材から解決されていない（成果の行の course_id 列で絞っている疑い）",
                             server_rows={"course_id": course, "skipped": bool(body.get("skipped"))}))
    return out


def check_counterfactual_warnings(steps: list[TranscriptStep], factory: FindingFactory) -> list[Finding]:
    """DA9: 試算の warnings が画面に出ず、依存範囲が「なし」に見える。"""
    out: list[Finding] = []
    for step in steps:
        for t in step.http:
            if not _CF_RESULT.match(t.path or "") or t.method != "POST" or not (200 <= (t.status or 0) < 300):
                continue
            body = excerpt_json(t.response_excerpt)
            result = body.get("result") if isinstance(body, dict) and isinstance(body.get("result"), dict) else body
            warnings = [w for w in (result or {}).get("warnings") or [] if isinstance(w, str) and w.strip()] \
                if isinstance(result, dict) else []
            if not warnings:
                continue
            obs = step.observation or ""
            if any(w in obs for w in warnings):
                continue
            out.append(_make(factory, step, oracle="A", severity="inconsistent", key="cf_warnings_hidden",
                             quote=warnings[0][:200],
                             hypothesis="反実仮想の試算が「前提をグラフ上に見つけられなかった」等の警告を返したのに、画面には"
                                        "その事実が無く、依存する範囲が「なし」と読める（失敗と該当なしが区別できない）",
                             server_rows={"where": t.path}))
            break
    return out


def _check_visibility_raw(step: TranscriptStep, factory: FindingFactory) -> list[Finding]:
    """DB5: 画面に Visibility の英語の内部値がそのまま出ている（ハーネスの文は除く）。"""
    text = "\n".join(line for line in (step.observation or "").splitlines() if "ハーネス:" not in line)
    m = _VISIBILITY_RAW.search(text)
    if not m:
        return []
    start = max(0, m.start() - 40)
    return [_make(factory, step, oracle="B", severity="confused", key="visibility_raw", quote=text[start:m.end() + 40],
                  hypothesis="画面に共有範囲の内部値（private / group / public）が英語のまま出ている（Visibility は"
                             "日本語の語で示す）",
                  server_rows={"value": m.group(1)})]


def check_ledger_document(steps: list[TranscriptStep], factory: FindingFactory) -> list[Finding]:
    """DA10: 台帳詳細の応答の document_id が開いた論文と違う。"""
    out: list[Finding] = []
    seen: set[tuple[str, str]] = set()
    for step in steps:
        for t in step.http:
            dg = (t.digest or {}).get("doubt_ledger") if isinstance(t.digest, dict) else None
            if not isinstance(dg, dict) or not (200 <= (t.status or 0) < 300):
                continue
            want, got = str(dg.get("requested_document_id") or ""), str(dg.get("response_document_id") or "")
            if not want or not got or want == got or (want, got) in seen:
                continue
            seen.add((want, got))
            out.append(_make(factory, step, oracle="A", severity="inconsistent", key="ledger_other_document",
                             hypothesis="台帳の詳細が、開いた論文とは別の論文の台帳行を返している（同じ ID の対象が論文の間で"
                                        "取り違えられている）",
                             server_rows={"requested_document_id": want, "response_document_id": got,
                                          "target_id": str(dg.get("target_id") or "")}))
    return out


def check(steps: list[TranscriptStep], factory: FindingFactory) -> list[Finding]:
    """A・B の規則を面 B の行為（と台帳の事実文が出る既存の行為）にかける。"""
    out: list[Finding] = []
    for step in steps:
        if not _is_target(step):
            continue
        if step.action_id.startswith((ADMIN_PREFIX, LEARNER_PREFIX)):
            out += _check_observation_text(step, factory)
        out += _check_visibility_raw(step, factory)
        for t in step.http:
            if _skip(t) or (t.status or 0) >= 500:
                continue
            body = excerpt_json(t.response_excerpt)
            out += _check_detail(step, t, body, factory)
            out += _check_learner_keys(step, t, body, factory)
            out += _check_teacher_numbers(step, t, body, factory)
            out += _check_anonymous(step, t, body, factory)
            out += _check_universal_verification(step, t, body, factory)
            out += _check_external_check(step, t, factory)
            out += _check_recorder_id(step, t, body, factory)
            if 200 <= (t.status or 0) < 300:
                out += _check_closed_world(step, body, factory, f"{t.method} {t.path}")
    out += check_retention(steps, factory)
    out += check_permission_lines(steps, factory)
    out += check_audit_scope(steps, factory)
    out += check_counterfactual_warnings(steps, factory)
    out += check_ledger_document(steps, factory)
    return out


# ---------------------------------------------------------------------------
# E 観測（砂場 DB の読みだけ）
# ---------------------------------------------------------------------------
def written_ledger_targets(steps: Optional[list[TranscriptStep]]) -> list[dict]:
    """教員が 2xx で記帳した台帳の対象（target_type / target_id / 何を記帳したか）。"""
    out: dict[tuple[str, str], dict] = {}
    for step in steps or []:
        for t in step.http:
            m = _LEDGER_WRITE.match(t.path or "")
            if m and 200 <= (t.status or 0) < 300:
                key = (m.group(1), m.group(2))
                out.setdefault(key, {"target_type": key[0], "target_id": key[1], "what": [], "seq": step.seq})
                out[key]["what"].append(m.group(3))
    return list(out.values())


_DOUBT_COURSE = re.compile(r"^/api/admin/doubt/courses/([^/]+)/")


def run_doubt_courses(steps: Optional[list[TranscriptStep]]) -> list[str]:
    """run で doubt の画面に開いたコース（``/api/admin/doubt/courses/{id}/…`` を 2xx で読んだもの）。"""
    seen: dict[str, None] = {}
    for step in steps or []:
        for t in step.http:
            m = _DOUBT_COURSE.match(t.path or "")
            if m and 200 <= (t.status or 0) < 300:
                seen.setdefault(m.group(1), None)
    return list(seen)


def ledger_documents_by_target(steps: Optional[list[TranscriptStep]]) -> dict[str, str]:
    """台帳詳細の digest（runner の ``doubt_ledger``）から、対象 id → 開いた論文の document_id（最後のもの）。"""
    out: dict[str, str] = {}
    for step in steps or []:
        for t in step.http:
            dg = (t.digest or {}).get("doubt_ledger") if isinstance(t.digest, dict) else None
            if isinstance(dg, dict) and dg.get("target_id"):
                doc = str(dg.get("response_document_id") or dg.get("requested_document_id") or "")
                if doc:
                    out[str(dg["target_id"])] = doc
    return out


def collect_ledger_documents(database_url: str, targets: list[dict], courses: list[str],
                             engine: Any = None) -> list[dict]:
    """記帳した台帳行の document_id と、run のコースの教材の document 集合に入っているか（読みだけ）。

    行は (document_id, target_type, target_id) で引く — document_id が分かっていればそれで絞る（migration 092
    以降、document_id を絞らない ``.first()`` は別の論文の行を拾いうる — 第 25 周 H26）。
    """
    from sqlalchemy import create_engine, text  # 遅延 import

    eng = engine or create_engine(database_url)
    rows: list[dict] = []
    with eng.connect() as conn:
        course_docs: set[str] = set()
        for cid in courses:
            for r in conn.execute(text(
                    "SELECT DISTINCT d.id::text AS id FROM learning_courses c "
                    "CROSS JOIN LATERAL jsonb_array_elements(COALESCE(c.data->'sources', '[]'::jsonb)) AS src "
                    "JOIN documents d ON (d.id::text = src->>'material_id' OR d.source_path = src->>'material_id') "
                    "WHERE c.id::text = :cid"), {"cid": cid}).mappings().all():
                course_docs.add(str(r["id"]))
        for tgt in targets:
            sql = ("SELECT target_type, target_id, COALESCE(document_id::text, '') AS document_id "
                   "FROM epistemic_ledger WHERE target_type = :tt AND target_id = :tid")
            params = {"tt": tgt["target_type"], "tid": tgt["target_id"]}
            if tgt.get("document_id"):
                sql += " AND document_id::text = :doc"
                params["doc"] = tgt["document_id"]
            for r in conn.execute(text(sql), params).mappings().all():
                rows.append({**dict(r), "what": tgt["what"], "in_course": str(r["document_id"]) in course_docs})
    return rows


def check_observation(meta: Optional[RunMeta], factory: FindingFactory, database_url: str = "", engine: Any = None,
                      *, steps: Optional[list[TranscriptStep]] = None) -> list[Finding]:
    """E の規則。DB の URL も engine も無ければ空（未実施）。"""
    if meta is None or not (database_url or engine is not None):
        return []
    targets = written_ledger_targets(steps)
    courses = run_doubt_courses(steps)
    if not targets or not courses:
        return []  # コースを開いていない run では「コースの教材の外」を言えない
    docs = ledger_documents_by_target(steps)
    targets = [{**t, "document_id": docs.get(t["target_id"], "")} for t in targets]
    try:
        rows = collect_ledger_documents(database_url, targets, courses, engine)
    except Exception:  # noqa: BLE001 — 読めなければ未実施
        return []
    orphan = [{"target": f"{r['target_type']}/{r['target_id']}", "document_id": r["document_id"], "written": r["what"]}
              for r in rows if not r["in_course"]]
    if not orphan:
        return []
    return [factory.make(oracle="E", severity="inconsistent", screen="", affordance="",
                         hypothesis="教員が記帳した台帳行の論文が、開いたコースの教材のどれでもなく、そのコースの前提の地図・"
                                    "未検証合意リストに載らない",
                         server_rows={"ledger_rows": orphan[:10]}, layers=["doubt_d", "stakes_ledger_sl"],
                         fingerprint_key="doubt:e:ledger_course")]
