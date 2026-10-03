"""検証シミュレーション層（SB層）の審判 — A 契約・B 原則・E 観測（決定論・LLM 0 回）。

対象ステップは行為 id が ``learning.simlab.`` / ``admin.simulation.`` で始まるもの。応答は
``HttpTrace.response_excerpt``（先頭 2000 字の JSON）を読み、切れていれば正規表現で拾える範囲だけを見る。
事実文・語彙は製品の正本 ``backend/core/simulation_bridge/schema.py`` を import して使う（PE6 — 文字列を写さない）。
言語・内部 ID・採点語彙の判定は ``principle.py`` の関数を再利用する（重複実装しない）。

A 契約:
- A1 学習者の run DTO の ``prediction`` / ``inputs.values`` / ``result.theory_outputs`` / ``result.observed`` が
  リスト形（記号キーの写像で返っていない — IK-0598 の学習者側）
- A2 ``agreement_level`` / ``separation_level`` が語彙内
- A3 ``status`` が failed / rejected の run に、次の手を言う事実文（``result.facts`` か ``statement``）がある
- A4 4xx（422 等）の ``detail`` が空・日本語を含まない・内部例外文（Traceback / KeyError / NoneType）でない
- A5 生成応答に ``program.source_code`` と ``program.io_contract.inputs`` がある

B 原則:
- B1 SB1 閉世界 — 事実文に「検証された / 検証済み / 実証 / 証明」が出るのに同じ応答に FACT_NOT_VERIFICATION が無い
- B2 SB14 — run DTO に予想の当否を言うキーが無い・``labels`` は agreement / separation / reference の 3 キーだけ
- B3 UC2 — SB の応答の事実文に採点語彙（``principle.scoring_vocab()``）が無い
- B4 SB12 — 教員側（admin.simulation.*）の応答に owner_user_id / user_id / email / username キーが無い
- B5 SB10 — 復元式入力のモジュールから生成した program の facts に FACT_RECONSTRUCTED_INPUTS がある
- B6 SB5 — 改訂案の ``changes[].entry_id`` が同じペルソナが見た部品目録の ``entry_id`` に含まれる
- B7 言語 — statement / labels / theory_object（学習側）と facts / label（教員側）に英語の文
  （``principle.check`` が学習側の facts / label を既に見ているので重ねない）
- B8 内部 ID — observation_targets[].label / modules[].label / inputs・outputs[].display_label に内部 ID
- B9 数値 — 学習者向けの overview / module の facts に件数（N 件 / N 個 / N 本）。本人の run の metrics は
  SB6 の読み替え（§14.1）で対象外

E 観測（砂場 DB の読みだけ）:
- E1 ``interest_traces`` kind=verification_attempt — 成功した学習者の run があるのに痕跡が無い / payload に
  数値の欄 / agreement_level が語彙外
- E2 ``llm_usage_events`` の学習者向け codegen・revision の件数が学生ペルソナ数を超える（数は server_rows だけ）
- E3 ``simulation_runs`` に owner_user_id が NULL の新規行

限界: 抜粋が 2000 字で切れた応答（コード全文を含む生成応答など）は JSON として読めず、キーの有無だけを
正規表現で見る（io_contract の検査は読めたときだけ）。
"""
from __future__ import annotations

import re
from typing import Any, Iterator, Optional

from uxsim.oracles import principle
from uxsim.oracles.findings import FindingFactory, excerpt_json
from uxsim.schema import Finding, HttpTrace, RunMeta, TranscriptStep

# 製品の正本（読み取りのみ）。製品の import は principle.py の 1 箇所に限る（test_isolation）
sb = principle.SIMULATION_SCHEMA

LEARNER_PREFIX = "learning.simlab."
ADMIN_PREFIX = "admin.simulation."
SB_PREFIXES = (LEARNER_PREFIX, ADMIN_PREFIX)
LAYERS_LEARNER = ["simulation_bridge_sb", "frontend_learning_ui"]
LAYERS_ADMIN = ["simulation_bridge_sb", "frontend_admin_ui"]

RUN_LIST_FIELDS = (("prediction",), ("inputs", "values"), ("result", "theory_outputs"), ("result", "observed"))
FAILED_STATUSES = (sb.RUN_STATUS_FAILED, sb.RUN_STATUS_REJECTED)
INTERNAL_ERROR_WORDS = ("Traceback", "KeyError", "NoneType", "TypeError", "AttributeError", "Exception")
VERIFIED_WORDS = ("検証された", "検証済み", "実証", "証明")
PREDICTION_VERDICT_KEYS = ("prediction_match", "prediction_label", "正解", "的中", "score")
ALLOWED_LABEL_KEYS = frozenset({"agreement", "separation", "reference"})
PERSONAL_KEYS = ("owner_user_id", "user_id", "email", "username")
EXTRA_ID_PREFIXES = ("claim:", "span_", "eq_tex_", "tex_b", "（本文を特定できない要素）")
NUMERIC_TRACE_KEYS = frozenset({"value", sb.METRIC_RELATIVE_DIFFERENCE, sb.METRIC_SEPARATION_RATIO,
                                sb.METRIC_REFERENCE_DIFFERENCE, "metrics", "by_symbol"})
_COUNT_RE = re.compile(r"(?<![\w.])\d+\s*(?:件|個|本)")
_JAPANESE = re.compile(r"[぀-ヿ一-鿿]")
_LEVEL_RE = re.compile(r'"(agreement_level|separation_level)"\s*:\s*"([^"]*)"')
_MAPPING_FIELD_RE = re.compile(r'"(prediction|values|theory_outputs|observed)"\s*:\s*\{')


# ---------------------------------------------------------------------------
# 応答の読み出し
# ---------------------------------------------------------------------------
def _sb_steps(steps: list[TranscriptStep]) -> Iterator[TranscriptStep]:
    for s in steps:
        if s.action_id.startswith(SB_PREFIXES):
            yield s


def _skip(t: HttpTrace) -> bool:
    return t.status is None or t.error.startswith(("precondition:", "unsupported")) or not t.response_excerpt


def _walk_dicts(v: Any) -> Iterator[dict]:
    if isinstance(v, dict):
        yield v
        for x in v.values():
            yield from _walk_dicts(x)
    elif isinstance(v, list):
        for x in v:
            yield from _walk_dicts(x)


def _walk_keys(v: Any) -> Iterator[str]:
    for d in _walk_dicts(v):
        yield from (str(k) for k in d.keys())


def _is_run(d: dict) -> bool:
    return "program_id" in d and "status" in d and ("result" in d or "inputs" in d)


def _is_program(d: dict) -> bool:
    return "source_code" in d and "module_key" in d


def _is_module(d: dict) -> bool:
    return "module_key" in d and "reconstructed_inputs" in d


def _get(d: dict, path: tuple[str, ...]) -> Any:
    cur: Any = d
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _texts(v: Any) -> list[str]:
    if isinstance(v, str):
        return [v] if v.strip() else []
    if isinstance(v, list):
        return [x for x in v if isinstance(x, str) and x.strip()]
    if isinstance(v, dict):
        return [x for x in v.values() if isinstance(x, str) and x.strip()]
    return []


def _fact_texts(body: Any) -> Iterator[tuple[str, str]]:
    """事実文として学習者・教員に見える欄（facts / statement / labels / detail）。"""
    for d in _walk_dicts(body):
        for key in ("facts", "statement", "labels", "detail"):
            for t in _texts(d.get(key)):
                yield key, t


def _layers(step: TranscriptStep) -> list[str]:
    return list(LAYERS_ADMIN if step.action_id.startswith(ADMIN_PREFIX) else LAYERS_LEARNER)


def _make(factory: FindingFactory, step: TranscriptStep, *, oracle: str, severity: str, hypothesis: str,
          quote: str = "", key: str = "", server_rows: Optional[dict] = None) -> Finding:
    return factory.make(oracle=oracle, severity=severity, step=step, quote=quote, layers=_layers(step),
                        hypothesis=hypothesis, server_rows=server_rows,
                        fingerprint_key=f"sb:{key}:{step.action_id}" if key else "")


# ---------------------------------------------------------------------------
# A 契約
# ---------------------------------------------------------------------------
def _check_detail(step: TranscriptStep, t: HttpTrace, body: Any, factory: FindingFactory) -> list[Finding]:
    if not (400 <= (t.status or 0) < 500) or t.status in (401, 403, 404, 429):
        return []
    detail = body.get("detail") if isinstance(body, dict) else None
    where = f"{t.method} {t.path}"
    if detail is None or (isinstance(detail, str) and not detail.strip()):
        return [_make(factory, step, oracle="A", severity="confused", key="detail_empty", quote=t.response_excerpt,
                      hypothesis=f"検証シミュレーションの拒否応答に次の手を言う事実文（detail）が無い（{step.action_id}）", server_rows={"status": t.status, "where": where})]
    text = detail if isinstance(detail, str) else str(detail)
    hit = next((w for w in INTERNAL_ERROR_WORDS if w in text), "")
    if hit:
        return [_make(factory, step, oracle="A", severity="confused", key="detail_internal", quote=text[:200],
                      hypothesis=f"検証シミュレーションの拒否応答の detail に内部の例外文が出ている（{step.action_id}）", server_rows={"status": t.status, "where": where})]
    if not _JAPANESE.search(text):
        return [_make(factory, step, oracle="A", severity="confused", key="detail_english", quote=text[:200],
                      hypothesis=f"検証シミュレーションの拒否応答の detail が日本語の事実文になっていない（{step.action_id}）", server_rows={"status": t.status, "where": where})]
    return []


def _check_run_contract(step: TranscriptStep, run: dict, factory: FindingFactory) -> list[Finding]:
    out: list[Finding] = []
    learner = step.action_id.startswith(LEARNER_PREFIX)
    if learner:
        bad = [".".join(p) for p in RUN_LIST_FIELDS if isinstance(_get(run, p), dict)]
        if bad:
            out.append(_make(factory, step, oracle="A", severity="inconsistent", key="run_mapping",
                             quote=", ".join(bad),
                             hypothesis="学習者の実行記録の値が記号キーの写像で返っている（リスト形の契約から外れている）"))
    comparison = _get(run, ("result", "comparison"))
    if isinstance(comparison, dict):
        bad_levels = []
        if comparison.get("agreement_level") not in sb.AGREEMENT_LEVELS:
            bad_levels.append(f"agreement_level={comparison.get('agreement_level')}")
        if comparison.get("separation_level") not in sb.SEPARATION_LEVELS:
            bad_levels.append(f"separation_level={comparison.get('separation_level')}")
        if bad_levels:
            out.append(_make(factory, step, oracle="A", severity="inconsistent", key="level_vocab",
                             quote=", ".join(bad_levels),
                             hypothesis="実行記録の一致・分離の段階が製品の語彙の外の値になっている"))
    if run.get("status") in FAILED_STATUSES:
        result = run.get("result") if isinstance(run.get("result"), dict) else {}
        if not (_texts(result.get("facts")) or _texts(result.get("statement"))):
            out.append(_make(factory, step, oracle="A", severity="confused", key="failed_no_fact",
                             quote=str(run.get("status")),
                             hypothesis="失敗した実行に、何が起きて次に何をすればよいかの事実文が無い"))
    return out


def _check_generate(step: TranscriptStep, t: HttpTrace, body: Any, factory: FindingFactory) -> list[Finding]:
    if t.status != 200 or "/programs/generate" not in (t.path or ""):
        return []
    if body is None:  # 抜粋が切れている（コード全文は長い）— キーの有無だけを見る
        if '"source_code": "' in t.response_excerpt and '"source_code": ""' not in t.response_excerpt:
            return []
        return [_make(factory, step, oracle="A", severity="inconsistent", key="generate_shape",
                      hypothesis="生成の応答にコード本文（program.source_code）が無い")]
    program = body.get("program") if isinstance(body, dict) else None
    missing = []
    if not isinstance(program, dict) or not str(program.get("source_code") or "").strip():
        missing.append("program.source_code")
    if not isinstance(program, dict) or not _get(program, ("io_contract", "inputs")):
        missing.append("program.io_contract.inputs")
    if not missing:
        return []
    return [_make(factory, step, oracle="A", severity="inconsistent", key="generate_shape", quote=", ".join(missing),
                  hypothesis="生成の応答にコード本文か入力の契約が無い")]


def _check_excerpt_contract(step: TranscriptStep, t: HttpTrace, factory: FindingFactory) -> list[Finding]:
    """抜粋が切れて JSON として読めないときに拾える範囲（段階の語彙・写像形）。"""
    out: list[Finding] = []
    bad = [f"{k}={v}" for k, v in _LEVEL_RE.findall(t.response_excerpt)
           if v not in (sb.AGREEMENT_LEVELS if k == "agreement_level" else sb.SEPARATION_LEVELS)]
    if bad:
        out.append(_make(factory, step, oracle="A", severity="inconsistent", key="level_vocab", quote=", ".join(bad),
                         hypothesis="実行記録の一致・分離の段階が製品の語彙の外の値になっている"))
    if step.action_id.startswith(LEARNER_PREFIX) and '"program_id"' in t.response_excerpt:
        fields = sorted(set(_MAPPING_FIELD_RE.findall(t.response_excerpt)))
        if fields:
            out.append(_make(factory, step, oracle="A", severity="inconsistent", key="run_mapping",
                             quote=", ".join(fields),
                             hypothesis="学習者の実行記録の値が記号キーの写像で返っている（リスト形の契約から外れている）"))
    return out


# ---------------------------------------------------------------------------
# B 原則
# ---------------------------------------------------------------------------
def _check_closed_world(step: TranscriptStep, t: HttpTrace, body: Any, factory: FindingFactory) -> list[Finding]:
    texts = [x for _, x in _fact_texts(body)]
    hit = next((w for x in texts for w in VERIFIED_WORDS if w in x), "")
    if not hit or any(sb.FACT_NOT_VERIFICATION in x for x in texts):
        return []
    quote = next(x for x in texts if hit in x)
    return [_make(factory, step, oracle="B", severity="principle", key="closed_world", quote=quote[:200],
                  hypothesis="シミュレーションの結果を検証済みと読める文が出ているのに、一致は検証ではないという事実文が無い")]


def _check_run_principles(step: TranscriptStep, run: dict, factory: FindingFactory) -> list[Finding]:
    out: list[Finding] = []
    keys = set(_walk_keys(run))
    verdict = sorted(k for k in PREDICTION_VERDICT_KEYS if k in keys)
    if verdict:
        out.append(_make(factory, step, oracle="B", severity="principle", key="prediction_verdict",
                         quote=", ".join(verdict),
                         hypothesis="実行記録に予想の当否を言う欄が出ている（予想は並置するだけで採点しない）"))
    labels = _get(run, ("result", "labels"))
    if step.action_id.startswith(LEARNER_PREFIX) and isinstance(labels, dict):
        extra = sorted(set(labels) - ALLOWED_LABEL_KEYS)
        if extra:
            out.append(_make(factory, step, oracle="B", severity="principle", key="label_keys",
                             quote=", ".join(extra),
                             hypothesis="実行記録の段階ラベルに一致・分離・記載値との差以外の欄が出ている"))
    return out


def _check_scoring(step: TranscriptStep, body: Any, factory: FindingFactory) -> list[Finding]:
    vocab = principle.scoring_vocab()
    for key, text in _fact_texts(body):
        hit = next((w for w in vocab if w in text), "")
        if hit:
            return [_make(factory, step, oracle="B", severity="principle", key="scoring", quote=f"{key}: {hit}",
                          hypothesis="検証シミュレーションの事実文に正誤・点数の語彙が出ている")]
    return []


def _check_personal_keys(step: TranscriptStep, t: HttpTrace, body: Any, factory: FindingFactory) -> list[Finding]:
    if not step.action_id.startswith(ADMIN_PREFIX):
        return []
    if body is not None:
        keys = set(_walk_keys(body))
        hits = [k for k in PERSONAL_KEYS if k in keys]
    else:
        hits = [k for k in PERSONAL_KEYS if f'"{k}":' in t.response_excerpt or f'"{k}" :' in t.response_excerpt]
    if not hits:
        return []
    return [_make(factory, step, oracle="B", severity="principle", key="personal_keys", quote=", ".join(hits),
                  hypothesis="教員に見える共有された実行の応答に学習者の識別子（利用者 ID・メール・ユーザー名）が出ている")]


def _check_reconstructed(step: TranscriptStep, body: Any, recon: dict[str, bool],
                         factory: FindingFactory) -> list[Finding]:
    for d in _walk_dicts(body):
        if not _is_program(d):
            continue
        from_module = recon.get(str(d.get("module_key") or ""))
        from_program = bool(_get(d, ("provenance", "reconstructed_inputs")))
        if not (from_module or from_program):
            continue
        facts = _texts(d.get("facts")) + _texts(_get(d, ("provenance", "facts")))
        if isinstance(body, dict):
            facts += _texts(body.get("facts"))
        if any(sb.FACT_RECONSTRUCTED_INPUTS in x for x in facts):
            continue
        return [_make(factory, step, oracle="B", severity="principle", key="reconstructed_mark",
                      quote=str(d.get("module_key") or ""),
                      hypothesis="PDF から復元された式を入力に持つモジュールのコードに、復元式の印（事実文）が無い")]
    return []


def _check_revision_catalog(step: TranscriptStep, body: Any, catalog: set[str],
                            factory: FindingFactory) -> list[Finding]:
    if not catalog or not isinstance(body, dict):
        return []
    changes = _get(body, ("proposal", "changes")) or []
    outside = [str(c.get("entry_id")) for c in changes
               if isinstance(c, dict) and c.get("entry_id") and str(c.get("entry_id")) not in catalog]
    if not outside:
        return []
    return [_make(factory, step, oracle="B", severity="principle", key="revision_catalog",
                  server_rows={"entry_ids_outside_catalog": outside},
                  hypothesis="改訂案に、画面で見た部品目録に無い部品が含まれている")]


def _language_fields(step: TranscriptStep, body: Any) -> Iterator[tuple[str, str]]:
    learner = step.action_id.startswith(LEARNER_PREFIX)
    for d in _walk_dicts(body):
        keys = ("statement", "labels", "theory_object") if learner else ("facts", "statement", "labels", "label",
                                                                          "theory_object")
        for key in keys:
            for t in _texts(d.get(key)):
                yield key, t
        if _is_module(d):
            # まとまりの一覧で学習者が実際に読む欄（第 21 周: 「番号なし: Defines the …」の断片で全員が詰まった）
            yield "modules.label", str(d.get("label") or "")
            for side in ("inputs", "outputs"):
                for io in d.get(side) or []:
                    if isinstance(io, dict):
                        yield f"modules.{side}.display_label", str(io.get("display_label") or "")
            for a in d.get("assumptions") or []:
                if isinstance(a, dict):
                    yield "modules.assumptions.text", str(a.get("text") or "")


# 英語の断片（文の途中で切れた生成文 — 「番号なし: Defines the DCF correction factor as Q =」のような 40 字切り）。
# principle._ENGLISH_SENTENCE はピリオドで終わる文しか拾わない（第 21 周の見逃し）。TeX（バックスラッシュ）を含む窓は数式とみなして除く。
_ENGLISH_FRAGMENT = re.compile(r"(?<![\\A-Za-z])[A-Za-z][A-Za-z'\-]*(?:\s+[A-Za-z][A-Za-z'\-]*){3,}")


def _english_fragment(text: str) -> str:
    for m in _ENGLISH_FRAGMENT.finditer(text or ""):
        chunk = m.group(0)
        if "\\" in chunk:
            continue
        return chunk.strip()[:80]
    return ""


def _check_language(step: TranscriptStep, body: Any, language: str, factory: FindingFactory) -> list[Finding]:
    fragment_hit: tuple[str, str] | None = None
    for key, text in _language_fields(step, body):
        hit = principle._language_hit(language, "facts", text)  # 判定は事実文と同じ（キーの限定は呼び出し側）
        if hit:
            what = ("日本語話者に見える検証シミュレーションの文に英語の文が混じっている" if language != "en"
                    else "英語話者に検証シミュレーションの日本語の固定文が出ている（言語不一致）")
            return [_make(factory, step, oracle="B", severity="principle", key="language", quote=f"{key}: {hit}",
                          hypothesis=what)]
        if language != "en" and fragment_hit is None:
            frag = _english_fragment(text)
            if frag:
                fragment_hit = (key, frag)
    if fragment_hit is not None:
        key, frag = fragment_hit
        return [_make(factory, step, oracle="B", severity="principle", key="language_fragment", quote=f"{key}: {frag}",
                      hypothesis="日本語話者に見える検証シミュレーションのラベル・前提に、途中で切れた英語の生成文の断片が混じっている")]
    return []


def _sb_internal_id(text: str) -> str:
    hit = principle._internal_id(text)
    if hit:
        return hit
    return next((p for p in EXTRA_ID_PREFIXES if p in text), "")


def _check_internal_ids(step: TranscriptStep, body: Any, factory: FindingFactory) -> list[Finding]:
    if not isinstance(body, dict):
        return []
    candidates: list[tuple[str, str]] = []
    for target in body.get("observation_targets") or []:
        if isinstance(target, dict):
            candidates.append(("observation_targets.label", str(target.get("label") or "")))
    for d in _walk_dicts(body):
        if _is_module(d):
            candidates.append(("modules.label", str(d.get("label") or "")))
            for side in ("inputs", "outputs"):
                for io in d.get(side) or []:
                    if isinstance(io, dict):
                        candidates.append((f"modules.{side}.display_label", str(io.get("display_label") or "")))
    for where, text in candidates:
        hit = _sb_internal_id(text) if text else ""
        if hit:
            return [_make(factory, step, oracle="B", severity="principle", key="internal_id", quote=f"{where}: {hit}",
                          hypothesis="検証シミュレーションの画面のラベル（モジュール・観測系の主張・入出力）に内部 ID が出ている")]
    return []


def _check_counts(step: TranscriptStep, body: Any, factory: FindingFactory) -> list[Finding]:
    if not step.action_id.startswith(LEARNER_PREFIX) or not isinstance(body, dict):
        return []
    facts: list[str] = []
    if "modules" in body or "parts_catalog" in body:  # overview
        facts += _texts(body.get("facts"))
    for d in _walk_dicts(body):
        if _is_module(d):
            facts += _texts(d.get("facts"))
    for text in facts:
        m = _COUNT_RE.search(text)
        if m:
            return [_make(factory, step, oracle="B", severity="principle", key="counts", quote=text[:200],
                          hypothesis="学習者向けの検証シミュレーションの事実文に件数が出ている")]
    return []


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def check(steps: list[TranscriptStep], factory: FindingFactory) -> list[Finding]:
    """A・B の規則を SB の行為にかける。同じペルソナの中で見た部品目録・モジュールの復元の印を覚えて照合する。"""
    out: list[Finding] = []
    domain = factory.meta.domain if factory.meta else ""
    catalog: dict[str, set[str]] = {}
    recon: dict[str, dict[str, bool]] = {}
    for step in _sb_steps(steps):
        seen_catalog = catalog.setdefault(step.persona_id, set())
        seen_recon = recon.setdefault(step.persona_id, {})
        language = principle._persona_language(domain, step.persona_id)
        for t in step.http:
            if _skip(t) or t.status >= 500:
                continue
            body = excerpt_json(t.response_excerpt)
            out += _check_detail(step, t, body, factory)
            out += _check_personal_keys(step, t, body, factory)
            out += _check_generate(step, t, body, factory)
            if body is None:
                out += _check_excerpt_contract(step, t, factory)
                continue
            for d in _walk_dicts(body):  # 先に覚える（同じ応答の中の program も照合できるように）
                if isinstance(d.get("parts_catalog"), list) or isinstance(d.get("parts"), list) and "/parts" in t.path:
                    for p in d.get("parts_catalog") or d.get("parts") or []:
                        if isinstance(p, dict) and p.get("entry_id"):
                            seen_catalog.add(str(p["entry_id"]))
                if _is_module(d):
                    seen_recon[str(d.get("module_key") or "")] = bool(d.get("reconstructed_inputs"))
            for d in _walk_dicts(body):
                if _is_run(d):
                    out += _check_run_contract(step, d, factory)
                    out += _check_run_principles(step, d, factory)
            out += _check_closed_world(step, t, body, factory)
            out += _check_scoring(step, body, factory)
            out += _check_reconstructed(step, body, seen_recon, factory)
            if "/propose-revision" in (t.path or ""):
                out += _check_revision_catalog(step, body, seen_catalog, factory)
            out += _check_language(step, body, language, factory)
            out += _check_internal_ids(step, body, factory)
            out += _check_counts(step, body, factory)
    return out


# ---------------------------------------------------------------------------
# E 観測（砂場 DB の読みだけ）
# ---------------------------------------------------------------------------
def _window(meta: RunMeta) -> dict[str, str]:
    return {"start": meta.started_at, "end": meta.finished_at or meta.started_at}


def collect(database_url: str, meta: RunMeta, engine: Any = None, *,
            usernames: Optional[list[str]] = None) -> dict[str, Any]:
    """砂場 DB から SB の観測を読む（書き込まない）。

    ``usernames`` を渡すと LLM 呼び出しの件数をそのペルソナ（``users.display_name``）の行に限る。
    レーンを並列に走らせると時間窓が重なり、他レーンの生成を自分の予算超過と読む（第 21 周の偽所見）。
    """
    from sqlalchemy import bindparam, create_engine, text  # 遅延 import

    eng = engine or create_engine(database_url)
    w = dict(_window(meta), kind=sb.TRACE_KIND_VERIFICATION_ATTEMPT,
             f_codegen=sb.FEATURE_LEARNER_CODEGEN, f_revision=sb.FEATURE_LEARNER_REVISION,
             succeeded=sb.RUN_STATUS_SUCCEEDED)
    rows: dict[str, Any] = {}
    llm_sql = ("SELECT feature, count(*) AS n FROM llm_usage_events WHERE feature IN (:f_codegen, :f_revision) "
               "AND occurred_at BETWEEN CAST(:start AS timestamptz) AND CAST(:end AS timestamptz)")
    llm_stmt = text(llm_sql + " GROUP BY feature")
    if usernames:
        llm_stmt = text(llm_sql + " AND user_id IN (SELECT id FROM users WHERE display_name IN :names) GROUP BY feature"
                        ).bindparams(bindparam("names", expanding=True))
        w = dict(w, names=list(usernames))
    with eng.connect() as conn:
        rows["verification_traces"] = [dict(r) for r in conn.execute(text(
            "SELECT id::text AS id, payload FROM interest_traces WHERE kind = :kind "
            "AND created_at BETWEEN CAST(:start AS timestamptz) AND CAST(:end AS timestamptz) ORDER BY created_at"),
            w).mappings().all()]
        rows["learner_runs_succeeded"] = int(conn.execute(text(
            "SELECT count(*) FROM simulation_runs WHERE course_id IS NOT NULL AND status = :succeeded "
            "AND created_at BETWEEN CAST(:start AS timestamptz) AND CAST(:end AS timestamptz)"), w).scalar() or 0)
        rows["llm_learner_features"] = [dict(r) for r in conn.execute(llm_stmt, w).mappings().all()]
        rows["runs_without_owner"] = [dict(r) for r in conn.execute(text(
            "SELECT id::text AS id, status, created_at::text AS created_at FROM simulation_runs "
            "WHERE owner_user_id IS NULL "
            "AND created_at BETWEEN CAST(:start AS timestamptz) AND CAST(:end AS timestamptz)"), w).mappings().all()]
    return rows


def _numeric_payload_keys(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    hits = set()
    for d in _walk_dicts(payload):
        for k, v in d.items():
            if k in NUMERIC_TRACE_KEYS or (isinstance(v, (int, float)) and not isinstance(v, bool)):
                hits.add(str(k))
    return sorted(hits)


def check_observation(meta: Optional[RunMeta], factory: FindingFactory, database_url: str = "", engine: Any = None,
                      *, steps: Optional[list[TranscriptStep]] = None) -> list[Finding]:
    """E の規則。DB の URL も engine も無ければ空（未実施）。``steps`` は学生ペルソナ数の導出に使う。"""
    if meta is None or not (database_url or engine is not None):
        return []
    students = {s.persona_id for s in (steps or []) if s.screen == "learning" and s.persona_id}
    try:
        from uxsim.runner.api import persona_username  # 遅延 import（runner → oracles の向きを増やさない）

        names = sorted(persona_username(pid) for pid in students)
    except Exception:  # noqa: BLE001
        names = []
    try:
        rows = collect(database_url, meta, engine, usernames=names or None)
    except Exception:  # noqa: BLE001 — 読めなければ未実施（observation.py が読めなかった事実を残す）
        return []
    out: list[Finding] = []
    traces = rows["verification_traces"]

    def make(hypothesis: str, server_rows: dict, key: str) -> Finding:
        return factory.make(oracle="E", severity="inconsistent", screen="", affordance="", hypothesis=hypothesis,
                            server_rows=server_rows, layers=["simulation_bridge_sb", "trace_registry"],
                            fingerprint_key=f"sb:e:{key}")

    if rows["learner_runs_succeeded"] and not traces:
        out.append(make("学習者の実行が成功しているのに、確かめた痕跡（verification_attempt）が記録されていない",
                        {"learner_runs_succeeded": rows["learner_runs_succeeded"]}, "trace_missing"))
    numeric = [{"id": r["id"], "keys": _numeric_payload_keys(r.get("payload"))} for r in traces
               if _numeric_payload_keys(r.get("payload"))]
    if numeric:
        out.append(make("確かめた痕跡の payload に数値の欄が残っている", {"interest_traces": numeric}, "trace_numeric"))
    bad_level = [{"id": r["id"], "agreement_level": (r.get("payload") or {}).get("agreement_level")} for r in traces
                 if isinstance(r.get("payload"), dict)
                 and (r["payload"].get("agreement_level") not in sb.AGREEMENT_LEVELS)]
    if bad_level:
        out.append(make("確かめた痕跡の一致の段階が製品の語彙の外の値になっている", {"interest_traces": bad_level},
                        "trace_level"))
    if steps is not None and students:  # 学生ペルソナの居ない run（教員だけ）では予算の照合をしない
        over = [r for r in rows["llm_learner_features"] if int(r.get("n") or 0) > len(students)]
        if over:
            out.append(factory.make(
                oracle="E", severity="inconsistent", screen="", affordance="",
                hypothesis="学習者のコード生成・改訂案の LLM 呼び出しが、宣言した予算（ペルソナごとに一度）を超えている",
                server_rows={"llm_usage_events": over, "student_personas": sorted(students)},
                layers=["simulation_bridge_sb", "usage_metering_u"], fingerprint_key="sb:e:llm_budget"))
    if rows["runs_without_owner"]:
        out.append(make("検証シミュレーションの実行記録に所有者の無い新しい行がある",
                        {"simulation_runs": rows["runs_without_owner"]}, "run_owner"))
    return out
