"""第 25 周 面 C「分野の地図の運用」の審判 — A 契約・B 原則（決定論・LLM 0 回・DB を読まない）。

対象は行為 id が ``admin.atlas.`` で始まるもの（``admin.atlas_binding.*`` は含まない）と、学生の地図の行為
（``learning.atlas.*`` / ``learning.landscape.view`` / ``learning.corpus.*``）。応答は ``HttpTrace.response_excerpt``
（先頭 2000 字）と、実行モジュール ``uxsim/runner/atlas_exec.py`` が置く ``digest["atlas"]``（骨格・地図の応答は
抜粋に収まらないため）、client が全 trace に置く ``digest["numeric_keys"]``（値が数の欄の名前）を読む。
製品コードは import しない（``test_isolation`` — 製品の import は principle.py だけ）。固定文の照合は
``frontend/public/js`` の原文をファイルとして読む（PE6 — 文字列を写して比べない）。

A 契約:
- A1 判断の応答・候補一覧に、見送り（dismissed）なのに判断のメモ（review_note）が空の判断がある（理由必須の弁が通らなかった）
- A2 関係の候補の判断に、採用（accepted）なのに関係の種類（edge_kind）が空のものがある
- A3 分野の地図の操作の 4xx に、日本語の事実文の detail が無い / 内部の例外文が出ている

B 原則:
- B1 学生の地図の応答・教員の候補一覧の全体に、確からしさの生の値（cosine / similarity / score / weight /
  confidence）の欄がある（LS5 / RE4 / VA2 — 段階ラベルなら欄名が ``*_label`` になる）
- B2 学生の地図の応答・教員の候補一覧に件数の欄（``count`` / ``*_count``）がある（CR3 / LS5。
  ``source_document_count`` は LS8 のコーパス事実行の材料として設計上の例外）
- B3 推定の糸に、領域を端点にした糸がある / 項目ごとに出る糸・糸の総数が設計の上限を超える（RE7）
- B4 推定の糸が出ているのに骨格の版が付いていない・地図の版と食い違う（RE2 / VA8）
- B5 教員が見送った関係が、その後に学生の推定の糸に出ている（RE8。戻した関係は除く）
- B6 凍結の操作が無いのに、凍結版の中身が変わった / 学生の地図の版が変わった（KN-3 / AB4 / LS7）。
  判断の操作だけの区間で次版の下書きが変わった（RE3 / §5.5 — 下書きを書くのは教員の保存だけ）
- B7 近い既存項目の注記に骨格の版が付いていない（VA8）/ 候補に触れている論文の題名が空（LS5 — 支持は題名の列挙）
- B8 学生の位置づけに出所ラベル（AIによる推定（未確認）/ 教員確認済み）の無い配置がある / 現行版に対応の無い
  位置づけがあるのに版の事実文が無い（NC6）
- B9 推定の糸・関係の候補の出所の固定文（RE2「AIによる推定（未確認）」）が画面の原文から消えている

- B10 候補に触れている論文の並びに、同じ論文（同じ arXiv 番号・正規化した題名が同じ）が別の行として重なっている
  （PDF 版と TeX 版の二重取り込みが「別々の論文 2 本」として反復の閾値を満たす — IK-0846）
- B11 教員向けの地図の画面（投影）に内部 ID（製品の内部 ID 語彙と、骨格の snake_case の node id）が出ている（IK-0850）
- B12 判断済み（採用・見送り）の候補のカードに出所ラベル（AIによる…）だけが残り、誰が判断したかが描かれていない
  （API は判断の帰属を返している — IK-0848）
- B13 論文の海で、配置があるのに地図の全領域が霧で描かれ、厚みが読めない（IK-0847）
- B14 凍結の影響（freeze-impact）が止める条件を言わなかった（readiness が ready / 無い）のに、続く凍結が
  採用済み未反映の候補で 409 になった — 押してから止まる（IK-0885。凍結の可否の判定が 2 つある）
- B15 候補の一覧（ギャップ・関係）が空なのに、候補ゼロの理由（empty_notes・索引の構築中の index_note）も導出から外れた判断の別枠も無い（IK-0851）
- B16 凍結を止めた採用済みの候補（409 の pending_labels / pending_edges）が、直前に読んだ候補の一覧にも
  「候補の条件から外れた採用済みの判断」の別枠にも見えない — 止めている候補を画面で探せない（IK-0884）

限界: 抜粋が切れた応答は digest の要約だけを見る。digest の無い既存の行為（``learning.atlas.view`` /
``learning.atlas.threads``）は B1・B2 だけ（numeric_keys）。同じ run の外（別の周・別の端末）での書き込みは見えない。
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from typing import Any, Iterator, Optional

from uxsim.config import REPO_ROOT
from uxsim.oracles.findings import FindingFactory, excerpt_json
from uxsim.schema import Finding, HttpTrace, TranscriptStep

ADMIN_PREFIX = "admin.atlas."
LEARNER_ACTIONS = ("learning.atlas.", "learning.landscape.view", "learning.corpus.")
RAW_VALUE_KEYS = frozenset({"cosine", "similarity", "score", "weight", "confidence", "raw_confidence", "raw_weight"})
COUNT_KEY_ALLOW = frozenset({"source_document_count"})
THREADS_PER_NODE_MAX = 2  # RE7（atlas_relation_edges_design.md）— 比較にだけ使い、仮説の文に書かない
THREADS_TOTAL_MAX = 30
PROVENANCE_FIXED = "AIによる推定（未確認）"
THREADS_JS = REPO_ROOT / "frontend" / "public" / "js" / "atlas-threads-layer.js"
ADMIN_JS = REPO_ROOT / "frontend" / "public" / "js" / "admin.js"
INTERNAL_ERROR_WORDS = ("Traceback", "KeyError", "NoneType", "TypeError", "AttributeError", "Exception")
_JAPANESE = re.compile(r"[぀-ヿ一-鿿]")
MUTATING_DRAFT = ("admin.atlas.gaps.incorporate_apply", "admin.atlas.edges.incorporate_apply",
                  "admin.atlas.gaps.draft_from_frozen", "admin.atlas.freeze")


def _is_admin(step: TranscriptStep) -> bool:
    return step.action_id.startswith(ADMIN_PREFIX)


def _is_learner(step: TranscriptStep) -> bool:
    return step.action_id.startswith(LEARNER_ACTIONS)


def _layers(step: TranscriptStep, t: Optional[HttpTrace] = None) -> list[str]:
    path = (t.path if t else "") or ""
    if "/gap-candidates" in path or ".gaps." in step.action_id:
        core = "category_gaps"
    elif "/edge-candidates" in path or ".edges." in step.action_id or "threads" in step.action_id:
        core = "relation_edges_re"
    elif "/vectors" in path or "/aliases" in path or ".vectors." in step.action_id or ".aliases." in step.action_id:
        core = "vector_anchoring_va"
    elif "landscape" in path and "corpus" not in path:
        core = "knowledge_landscape"
    elif "/corpus/" in path or step.action_id.startswith("learning.corpus.") or "sea_map" in step.action_id:
        core = "corpus_roaming"
    else:
        core = "field_atlas_s"
    return [core, "frontend_admin_ui" if _is_admin(step) else "frontend_learning_ui"]


def _make(factory: FindingFactory, step: TranscriptStep, t: Optional[HttpTrace], *, oracle: str, severity: str,
          hypothesis: str, key: str, quote: str = "", server_rows: Optional[dict] = None) -> Finding:
    return factory.make(oracle=oracle, severity=severity, step=step, quote=quote, layers=_layers(step, t),
                        hypothesis=hypothesis, server_rows=server_rows,
                        fingerprint_key=f"atlas:{key}:{step.action_id}")


def _traces(step: TranscriptStep) -> Iterator[HttpTrace]:
    for t in step.http:
        if t.status is None or t.error.startswith(("precondition:", "unsupported")):
            continue
        yield t


def _walk_dicts(v: Any) -> Iterator[dict]:
    if isinstance(v, dict):
        yield v
        for x in v.values():
            yield from _walk_dicts(x)
    elif isinstance(v, list):
        for x in v:
            yield from _walk_dicts(x)


def _atlas_digest(t: HttpTrace) -> dict:
    d = (t.digest or {}).get("atlas") if isinstance(t.digest, dict) else None
    return d if isinstance(d, dict) else {}


def _decisions(body: Any) -> Iterator[dict]:
    """応答の中の判断（gap: cluster_key 付き / edge: edge_key 付き）。"""
    for d in _walk_dicts(body):
        if "status" in d and ("cluster_key" in d or "edge_key" in d) and ("review_note" in d or "edge_kind" in d):
            yield d


# ---------------------------------------------------------------------------
# A 契約
# ---------------------------------------------------------------------------
def _check_contract(step: TranscriptStep, t: HttpTrace, factory: FindingFactory) -> list[Finding]:
    out: list[Finding] = []
    body = excerpt_json(t.response_excerpt)
    where = f"{t.method} {t.path}"
    if 400 <= (t.status or 0) < 500 and t.status not in (401, 403, 429):
        detail = body.get("detail") if isinstance(body, dict) else None
        text = detail if isinstance(detail, str) else str((detail or {}).get("message") or "") \
            if isinstance(detail, dict) else ""
        if not text.strip():
            out.append(_make(factory, step, t, oracle="A", severity="confused", key="detail_empty",
                             quote=t.response_excerpt,
                             hypothesis=f"分野の地図の操作の拒否応答に次の手を言う事実文が無い（{step.action_id}）",
                             server_rows={"status": t.status, "where": where}))
        elif any(w in text for w in INTERNAL_ERROR_WORDS) or not _JAPANESE.search(text):
            out.append(_make(factory, step, t, oracle="A", severity="confused", key="detail_not_fact",
                             quote=text[:200],
                             hypothesis=f"分野の地図の操作の拒否応答が日本語の事実文になっていない（{step.action_id}）",
                             server_rows={"status": t.status, "where": where}))
    if body is None or not (200 <= (t.status or 0) < 300):
        return out
    for d in _decisions(body):
        status = str(d.get("status") or "")
        if status == "dismissed" and not str(d.get("review_note") or "").strip():
            out.append(_make(factory, step, t, oracle="A", severity="inconsistent", key="dismiss_without_reason",
                             quote=t.response_excerpt,
                             hypothesis="見送った候補の判断に理由が残っていない（理由必須の弁が通らなかった可能性）",
                             server_rows={"where": where, "key": d.get("cluster_key") or d.get("edge_key")}))
        if "edge_key" in d and status == "accepted" and not str(d.get("edge_kind") or "").strip():
            out.append(_make(factory, step, t, oracle="A", severity="inconsistent", key="accept_without_kind",
                             quote=t.response_excerpt,
                             hypothesis="採用した関係の候補に関係の種類が記録されていない（種類必須の弁が通らなかった可能性）",
                             server_rows={"where": where, "edge_key": d.get("edge_key")}))
    return out


# ---------------------------------------------------------------------------
# B 原則
# ---------------------------------------------------------------------------
def _numeric_keys(t: HttpTrace) -> list[str]:
    keys = (t.digest or {}).get("numeric_keys") if isinstance(t.digest, dict) else None
    return [str(k) for k in keys] if isinstance(keys, list) else []


def _check_numbers(step: TranscriptStep, t: HttpTrace, factory: FindingFactory) -> list[Finding]:
    learner = _is_learner(step)
    candidates_list = "/gap-candidates" in t.path or "/edge-candidates" in t.path
    if not (learner or (candidates_list and t.method == "GET")):
        return []
    keys = _numeric_keys(t)
    out: list[Finding] = []
    raw = sorted(k for k in keys if k in RAW_VALUE_KEYS)
    if raw:
        who = "学生" if learner else "教員の候補一覧"
        out.append(_make(factory, step, t, oracle="B", severity="principle", key="raw_value_keys",
                         quote=", ".join(raw),
                         hypothesis=f"{who}の応答に確からしさの生の値の欄が載っている（段階ラベルではなく数のまま）",
                         server_rows={"where": f"{t.method} {t.path}", "keys": raw}))
    counts = sorted(k for k in keys if (k == "count" or k.endswith("_count")) and k not in COUNT_KEY_ALLOW)
    # 第 25 周 H23: 件数キーの検査は画面の投影（observation）を対象にする。応答の DTO の欄（corpus.*_count 等）は
    # 画面に描かれない（画面は LS8 の事実行だけ）— 投影にキー名が漏れたときだけ所見にする
    obs = str(getattr(step, "observation", "") or "")
    counts = [k for k in counts if k.rsplit(".", 1)[-1] in obs]
    if counts:
        out.append(_make(factory, step, t, oracle="B", severity="principle", key="count_keys",
                         quote=", ".join(counts),
                         hypothesis="分野の地図の応答に件数の欄が載っている（厚みは段階・支持は題名の列挙で示す規律）",
                         server_rows={"where": f"{t.method} {t.path}", "keys": counts}))
    return out


def _check_threads(step: TranscriptStep, t: HttpTrace, dg: dict, factory: FindingFactory) -> list[Finding]:
    out: list[Finding] = []
    if dg.get("kind") != "learner_atlas" or not dg.get("threads_available"):
        return out
    pairs = [p for p in dg.get("thread_pairs") or [] if isinstance(p, list) and len(p) == 2]
    regions = set(dg.get("region_ids") or [])
    bad = [p for p in pairs if p[0] in regions or p[1] in regions]
    if bad:
        out.append(_make(factory, step, t, oracle="B", severity="principle", key="thread_region_endpoint",
                         hypothesis="推定の糸に領域を端点にした糸がある（糸は概念どうしだけを結ぶ）",
                         server_rows={"pairs": bad[:5]}))
    degree = Counter(x for p in pairs for x in p)
    if any(v > THREADS_PER_NODE_MAX for v in degree.values()) or len(pairs) > THREADS_TOTAL_MAX:
        out.append(_make(factory, step, t, oracle="B", severity="principle", key="thread_limit",
                         hypothesis="推定の糸の本数が設計の上限（項目ごと・全体）を超えている",
                         server_rows={"over_nodes": sorted(k for k, v in degree.items() if v > THREADS_PER_NODE_MAX)[:5]}))
    version = str(dg.get("threads_version") or "")
    if not version or (dg.get("skeleton_version") and version != dg.get("skeleton_version")):
        out.append(_make(factory, step, t, oracle="B", severity="principle", key="thread_version",
                         hypothesis="推定の糸に骨格の版が付いていない、または地図の版と食い違う（出所と版の明示）",
                         server_rows={"threads_version": version, "skeleton_version": dg.get("skeleton_version")}))
    return out


def _edge_pair(edge_key: str) -> Optional[frozenset]:
    parts = str(edge_key or "").split("|")
    return frozenset(parts[2:4]) if len(parts) >= 4 and parts[0] == "edge" else None


def _check_candidates_digest(step: TranscriptStep, t: HttpTrace, dg: dict, factory: FindingFactory) -> list[Finding]:
    out: list[Finding] = []
    if dg.get("kind") != "gap_candidates":
        return out
    no_version = [c.get("cluster_key") for c in dg.get("candidates") or []
                  if c.get("near_anchor") and not c.get("near_anchor_version")]
    if no_version:
        out.append(_make(factory, step, t, oracle="B", severity="principle", key="near_anchor_version",
                         hypothesis="近い既存項目の注記に骨格の版が付いていない（近さの言明には版を添える）",
                         server_rows={"cluster_keys": no_version[:5]}))
    untitled = [c.get("cluster_key") for c in dg.get("candidates") or []
                if c.get("document_titles") and not any(x.strip() for x in c["document_titles"])]
    if untitled:
        out.append(_make(factory, step, t, oracle="B", severity="confused", key="gap_untitled_documents",
                         hypothesis="候補に触れている論文が題名なしで並んでいる（支持を題名の列挙で読めない）",
                         server_rows={"cluster_keys": untitled[:5]}))
    return out


def _check_landscape(step: TranscriptStep, t: HttpTrace, dg: dict, factory: FindingFactory) -> list[Finding]:
    out: list[Finding] = []
    if dg.get("kind") != "learner_landscape":
        return out
    if "" in (dg.get("provenance_labels") or []) and dg.get("statuses"):
        out.append(_make(factory, step, t, oracle="B", severity="principle", key="placement_without_provenance",
                         hypothesis="学生の位置づけに出所ラベル（AIの推定か教員確認済みか）の無い配置がある"))
    if dg.get("unmapped") and not dg.get("domain_facts"):
        out.append(_make(factory, step, t, oracle="B", severity="confused", key="unmapped_without_fact",
                         hypothesis="現行版に対応の無い位置づけがあるのに、そのことを言う版の事実文が無い"))
    return out


# ---------------------------------------------------------------------------
# B10〜B13（第 25 周 H9 — 第 1 周で審判が拾わず、頭脳の行動だけが見つけたもの）
# ---------------------------------------------------------------------------
_ARXIV_ID = re.compile(r"(?<![0-9])(\d{4}\.\d{4,5})(?:v\d+)?(?![0-9])")
_TITLE_NOISE = re.compile(r"(?:v\d+)?(?:\.(?:pdf|tex|tar\.gz|tgz|gz|zip))+$", re.IGNORECASE)
_SNAKE_TOKEN = re.compile(r"(?<![A-Za-z0-9_])[a-z][a-z0-9]*(?:_[a-z0-9]+)+(?![A-Za-z0-9_])")
_DRAFT_NODE_ID = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*_c\d+")  # 取り込みで作られる概念の id（例: ism_star_formation_c5）
_ATTRIBUTION_WORDS = ("判断した", "判断者", "判断:", "判断：", "採用した教員", "見送った教員", "教員が採用", "教員が見送",
                     # 製品の判断の 1 行（core/atlas_review_display.decision_line の動詞。IK-0848 の是正後）
                     "採用しました", "見送りました", "統合しました", "見送りから戻しました")
PROVENANCE_PREFIX = "AIによる"


def _norm_title(title: str) -> str:
    t = unicodedata.normalize("NFKC", str(title or "")).casefold().strip()
    t = _TITLE_NOISE.sub("", t)
    return re.sub(r"[^0-9a-z぀-ヿ一-鿿]+", "", t)


def _duplicate_papers(titles: list[str]) -> list[str]:
    """同じ論文の重なり（arXiv 番号か正規化した題名が 2 行以上で同じ）。返すのは重なった題名だけ。"""
    seen_id: dict[str, str] = {}
    seen_title: dict[str, str] = {}
    dup: list[str] = []
    for title in titles:
        m = _ARXIV_ID.search(str(title or ""))
        keys = [("id", m.group(1))] if m else []
        nt = _norm_title(title)
        if nt:
            keys.append(("title", nt))
        hit = False
        for kind, k in keys:
            table = seen_id if kind == "id" else seen_title
            if k in table:
                hit = True
            table.setdefault(k, title)
        if hit:
            dup.append(str(title))
    return dup


def _check_duplicate_papers(step: TranscriptStep, t: HttpTrace, dg: dict, factory: FindingFactory) -> list[Finding]:
    if dg.get("kind") not in ("gap_candidates", "edge_candidates"):
        return []
    hits = []
    for c in dg.get("candidates") or []:
        dup = _duplicate_papers([x for x in c.get("document_titles") or [] if str(x).strip()])
        if dup:
            hits.append({"label": c.get("label") or c.get("cluster_key") or c.get("edge_key"), "titles": dup[:3]})
    if not hits:
        return []
    return [_make(factory, step, t, oracle="B", severity="principle", key=f"duplicate_papers:{dg['kind']}",
                  hypothesis="候補に触れている論文の並びに同じ論文（同じ arXiv 番号・同じ題名）が別の行として重なり、"
                             "別々の論文の反復として数えられている",
                  server_rows={"candidates": hits[:5]})]


def _internal_tokens(text: str, node_ids: set[str]) -> list[str]:
    from uxsim.oracles.principle import _internal_id  # 製品の内部 ID 語彙の参照は principle に限る（PE6）

    out: list[str] = []
    for line in str(text or "").splitlines():
        hit = _internal_id(line)
        if hit:
            out.append(hit)
        for m in _SNAKE_TOKEN.finditer(line):
            tok = m.group(0)
            labelled = re.search(r"\bid\s*[:：]\s*$", line[:m.start()]) is not None
            if tok in node_ids or labelled or _DRAFT_NODE_ID.fullmatch(tok):
                out.append(tok)
    return list(dict.fromkeys(out))


def _card_blocks(observation: str, label: str) -> Iterator[list[str]]:
    """投影のカード（「N. ラベル」で始まる行から、次のカードの頭か空行まで）。"""
    lines = str(observation or "").splitlines()
    head = re.compile(r"^\d+\. ")
    for i, line in enumerate(lines):
        if not head.match(line) or not label or label not in line:
            continue
        block = [line]
        for nxt in lines[i + 1:]:
            if not nxt.strip() or head.match(nxt) or not nxt.startswith(" "):
                break
            block.append(nxt)
        yield block


def _check_decided_attribution(step: TranscriptStep, t: HttpTrace, dg: dict,
                               factory: FindingFactory) -> list[Finding]:
    if dg.get("kind") not in ("gap_candidates", "edge_candidates") or not _is_admin(step):
        return []
    bad = []
    for c in dg.get("candidates") or []:
        if not (c.get("decided") and c.get("decided_by_in_api")):
            continue
        markers = _ATTRIBUTION_WORDS + ((str(c["decider"]),) if c.get("decider") else ())
        for block in _card_blocks(step.observation, str(c.get("label") or "")):
            text = "\n".join(block)
            if PROVENANCE_PREFIX in text and not any(m in text for m in markers):
                bad.append(c.get("label"))
                break
    if not bad:
        return []
    return [_make(factory, step, t, oracle="B", severity="confused", key=f"decided_without_attribution:{dg['kind']}",
                  hypothesis="判断済みの候補のカードに出所ラベル（AIによる…）だけが残り、誰が判断したかが画面に出ていない"
                             "（判断の帰属は応答にある）",
                  server_rows={"labels": bad[:5]})]


def _check_sea_fog(step: TranscriptStep, t: HttpTrace, dg: dict, factory: FindingFactory) -> list[Finding]:
    if dg.get("kind") != "sea_map" or not (dg.get("has_placements") and dg.get("all_regions_fog")):
        return []
    return [_make(factory, step, t, oracle="B", severity="confused", key="sea_all_fog_with_placements",
                  hypothesis="論文の海で、論文の配置があるのに地図の領域がすべて霧で描かれ、厚みが読めない")]


def _js_has_fixed_phrase() -> dict[str, bool]:
    res = {}
    for name, path in (("threads", THREADS_JS), ("admin_edges", ADMIN_JS)):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        res[name] = PROVENANCE_FIXED in text and (name != "threads" or "ATLAS_VERSION_PREFIX" in text or "・骨格 版" in text)
    return res


def _freeze_pending(t: HttpTrace) -> list[str]:
    """凍結の 409 の detail の pending_labels / pending_edges（抜粋から読めた分だけ）。"""
    if t.method != "POST" or "/freeze" not in t.path or "freeze-impact" in t.path or t.status != 409:
        return []
    body = excerpt_json(t.response_excerpt)
    detail = body.get("detail") if isinstance(body, dict) else None
    if not isinstance(detail, dict):
        return []
    return [str(x) for x in list(detail.get("pending_labels") or []) + list(detail.get("pending_edges") or []) if x]


def _check_freeze_readiness(steps: list[TranscriptStep], factory: FindingFactory) -> list[Finding]:
    """B14〜B16（第 25 周 第 2 波）。"""
    out: list[Finding] = []
    last_ready: Optional[bool] = None
    visible: dict[str, set[str]] = {"gap_candidates": set(), "edge_candidates": set()}
    for step in steps:
        if not _is_admin(step):
            continue
        for t in _traces(step):
            dg = _atlas_digest(t)
            kind = dg.get("kind")
            if kind == "freeze_impact":
                last_ready = dg.get("ready")
            if kind in visible:
                labels = {str(c.get("label") or "") for c in dg.get("candidates") or []}
                labels |= {str(x) for x in dg.get("orphaned_labels") or []}
                visible[kind] = {x for x in labels if x}
                # 第 6 周 H37: 索引の構築中の index_note（「…関係の候補はまだ出していません」）も空の理由の文として数える
                if not dg.get("candidates") and not dg.get("empty_notes") and not dg.get("index_note") \
                        and not dg.get("orphaned_labels") \
                        and 200 <= (t.status or 0) < 300:
                    out.append(_make(factory, step, t, oracle="B", severity="confused", key=f"empty_without_reason:{kind}",
                                     hypothesis="候補の一覧が空なのに、候補が無い理由の事実文も、候補の条件から外れた判断の"
                                                "別枠も無い"))
            pending = _freeze_pending(t)
            if not pending:
                continue
            if last_ready is not False:
                out.append(_make(factory, step, t, oracle="B", severity="confused", key="freeze_blocked_after_ready",
                                 hypothesis="凍結の影響の画面が止める条件を言わなかったのに、凍結を押すと採用済み未反映の"
                                            "候補で止まった（押す前に言われない）",
                                 server_rows={"pending": pending[:5], "impact_ready": last_ready}))
            shown = visible["gap_candidates"] | visible["edge_candidates"]
            unseen = [x for x in pending if not any(x in v or v in x for v in shown)]
            if unseen:
                out.append(_make(factory, step, t, oracle="B", severity="confused", key="freeze_pending_not_listed",
                                 hypothesis="凍結を止めた採用済みの候補が、候補の一覧にも外れた判断の別枠にも見えない",
                                 server_rows={"pending": unseen[:5]}))
    return out


def check(steps: list[TranscriptStep], factory: FindingFactory) -> list[Finding]:
    """A・B の規則を分野の地図の行為にかける（steps は seq 順）。"""
    out: list[Finding] = []
    dismissed_edges: dict[frozenset, int] = {}
    frozen_seen: dict[tuple[str, str], tuple[str, int]] = {}
    learner_version: dict[str, tuple[str, int]] = {}
    freeze_since: set[str] = set()
    last_draft: Optional[tuple[Any, str, int]] = None
    draft_writes_since = False
    showed_threads = showed_edges = False
    node_ids: set[str] = set()
    for step in steps:
        if not (_is_admin(step) or _is_learner(step)):
            continue
        if step.action_id in MUTATING_DRAFT and any(200 <= (t.status or 0) < 300 and t.method in ("PUT", "POST")
                                                    for t in _traces(step)):
            draft_writes_since = True
        if step.action_id == "admin.atlas.freeze" and any(t.method == "POST" and "/freeze" in t.path
                                                          and 200 <= (t.status or 0) < 300 for t in _traces(step)):
            freeze_since.update(learner_version)
            frozen_seen.clear()
        for t in _traces(step):
            out += _check_contract(step, t, factory)
            out += _check_numbers(step, t, factory)
            dg = _atlas_digest(t)
            out += _check_threads(step, t, dg, factory)
            out += _check_candidates_digest(step, t, dg, factory)
            out += _check_landscape(step, t, dg, factory)
            out += _check_duplicate_papers(step, t, dg, factory)
            out += _check_decided_attribution(step, t, dg, factory)
            out += _check_sea_fog(step, t, dg, factory)
            if dg.get("node_ids"):
                node_ids.update(str(x) for x in dg["node_ids"] if "_" in str(x))
            if "/edge-candidates" in t.path:
                showed_edges = True
            # 見送り / 戻すの記録（RE8）
            body = excerpt_json(t.response_excerpt) if 200 <= (t.status or 0) < 300 else None
            for d in _decisions(body):
                pair = _edge_pair(d.get("edge_key") or "")
                if pair is None:
                    continue
                if d.get("status") == "dismissed":
                    dismissed_edges[pair] = step.seq
                else:
                    dismissed_edges.pop(pair, None)
            if dg.get("kind") == "edge_candidates":
                for c in dg.get("candidates") or []:
                    pair = _edge_pair(c.get("edge_key") or "")
                    if pair is None:
                        continue
                    if c.get("status") == "dismissed":
                        dismissed_edges.setdefault(pair, step.seq)
                    elif c.get("status") in ("candidate", "accepted"):
                        dismissed_edges.pop(pair, None)
            if dg.get("kind") == "learner_atlas" and dg.get("threads_available"):
                showed_threads = True
                hits = [sorted(p) for p in (frozenset(x) for x in dg.get("thread_pairs") or [] if len(x) == 2)
                        if p in dismissed_edges]
                if hits:
                    out.append(_make(factory, step, t, oracle="B", severity="principle", key="dismissed_edge_thread",
                                     hypothesis="教員が見送った関係が、その後の学生の推定の糸に出ている",
                                     server_rows={"pairs": hits[:5]}))
            # 凍結以外の書き込み（B6）
            if dg.get("kind") == "skeleton_state":
                ver, fp = str(dg.get("frozen_version") or ""), str(dg.get("frozen_fingerprint") or "")
                dom = t.path
                if ver and fp:
                    prev = frozen_seen.get((dom, ver))
                    if prev and prev[0] != fp:
                        out.append(_make(factory, step, t, oracle="B", severity="principle", key="frozen_changed",
                                         hypothesis="凍結の操作が無いのに、同じ版の凍結済みの骨格の中身が変わった",
                                         server_rows={"version": ver, "first_seen_step": prev[1]}))
                    frozen_seen[(dom, ver)] = (fp, step.seq)
                cur = (dg.get("draft_revision"), str(dg.get("draft_fingerprint") or ""), step.seq)
                if last_draft is not None and not draft_writes_since and dg.get("draft_exists") \
                        and last_draft[1] and (cur[0] != last_draft[0] or cur[1] != last_draft[1]):
                    out.append(_make(factory, step, t, oracle="B", severity="principle", key="draft_changed_by_decide",
                                     hypothesis="教員の下書きの保存・複製・凍結が無い区間で、次版の下書きが変わった",
                                     server_rows={"since_step": last_draft[2]}))
                last_draft = cur
                draft_writes_since = False
            if dg.get("kind") == "draft_saved":
                last_draft = (dg.get("draft_revision"), str(dg.get("draft_fingerprint") or ""), step.seq)
                draft_writes_since = False
            if dg.get("kind") == "learner_atlas" and dg.get("skeleton_version") and t.path.endswith("/api/atlas"):
                ver, cart = str(dg["skeleton_version"]), str(dg.get("cartridge") or "")
                prev_v = learner_version.get(cart)
                if prev_v and prev_v[0] != ver and cart not in freeze_since:
                    out.append(_make(factory, step, t, oracle="B", severity="principle", key="learner_version_changed",
                                     hypothesis="この run の記録に凍結の操作が無いのに、学生の地図の骨格の版が変わった",
                                     server_rows={"since_step": prev_v[1]}))
                learner_version[cart] = (ver, step.seq)
                freeze_since.discard(cart)
    # B11: 教員向けの画面の投影（observation）に内部 ID が出ていないか。node id は run の中で骨格を読んだ後の分だけ
    seen_leak: set[str] = set()
    for step in steps:
        if not _is_admin(step) or not step.observation:
            continue
        leaks = [x for x in _internal_tokens(step.observation, node_ids) if x not in seen_leak]
        if leaks:
            seen_leak.update(leaks)
            out.append(_make(factory, step, None, oracle="B", severity="confused", key="admin_internal_id",
                             hypothesis="教員向けの地図の画面に内部 ID（骨格の node id・解析の内部 id）がそのまま出ている",
                             quote=leaks[0], server_rows={"tokens": leaks[:8]}))
    out += _check_freeze_readiness(steps, factory)
    if showed_threads or showed_edges:
        phrases = _js_has_fixed_phrase()
        missing = [k for k, ok in phrases.items() if not ok and ((k == "threads" and showed_threads)
                                                                 or (k == "admin_edges" and showed_edges))]
        if missing:
            anchor = next((s for s in steps if _is_learner(s) or _is_admin(s)), None)
            if anchor is not None:
                out.append(factory.make(oracle="B", severity="principle", step=anchor,
                                        layers=["relation_edges_re", "frontend_learning_ui"],
                                        hypothesis="推定の糸・関係の候補の出所の固定文（AIによる推定（未確認））が画面の原文に見当たらない",
                                        server_rows={"files": missing}, fingerprint_key="atlas:provenance_fixed_text"))
    return out
