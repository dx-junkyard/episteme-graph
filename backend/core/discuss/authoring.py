"""discuss 開幕素材のオーサリング側プリミティブ（Phase 1・非LLM・決定論的）。

正本: ``docs/features/discuss_opening_authoring_design.md`` §4.1（入力の組み立て）と
§7.1（鮮度 = ``evidence.source_fingerprint``）。

このモジュールが持つのは**LLM に渡す素材の解決**と**鮮度指紋の計算**だけで、
LLM 呼び出しそのものは持たない（呼ぶのは ``src/episteme_graph/agents/discuss_opening/``
の agent で、パイプラインのステージから 1 document = 1 コールだけ実行される）。
``core/discuss/`` パッケージの規約どおり FastAPI は import しない。

設計上の要点:

- **不透明 ID を渡さない**（設計書 §4.1・二層説明 §5.1 の規約）: claim id や
  equation id ではなく、解決済みのテキスト（未検証前提の statement / operation と
  その前後の式ラベル / thesis の合成文）だけを組み立てる。
- **DB 読み出しは1関数に閉じる**（``collect_untested_assumptions``）。他は artifact と
  in-memory のパイプライン成果を読む純粋関数で、fake dict だけで単体テストできる
  （``core/discuss/opening.py`` / ``core/personal_graph/derive.py`` と同じ流儀）。
- **Wave 2（レビューキュー・配信）から単体で import できる**こと。
  :func:`compute_source_fingerprint` は生成時と再解析後の突合の両方で使う。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable, Mapping

from sqlalchemy import text as sa_text

# 鮮度指紋の版（計算式を変えるときはここを上げる。旧版の指紋とは比較しない）。
FINGERPRINT_VERSION = "v1"

# 素材の上限（プロンプト膨張の防波堤。agent 側 input_builder でも二重に切る）。
MAX_UNTESTED_ASSUMPTIONS = 8
MAX_AUTHOR_CHOICES = 8
MAX_TEXT_CHARS = 400
MAX_EQUATION_LABEL_CHARS = 120

# D層台帳のうち「この論文が確かめていないこと」に当たる状態（設計書 §2）。
UNTESTED_VERIFICATION_STATUSES = ("untested", "unknown")

# 台帳から引く行数の倍率（[D-1]）。statement が不透明 ID の行は素材にしないため、
# ``limit`` 件ちょうど引くと equation 行ばかりの document で素材が0件になる。
# 多めに引いて、解決できた行だけを ``limit`` 件まで採る（SQL 側の LIMIT は残す）。
_LEDGER_FETCH_MULTIPLIER = 4

# 汎用すぎて「著者が選んだ手」として提示しにくい operation（derivation_chain の
# 語彙のうち抽象動詞。TheoryOperationGraph の generic 扱いと同じ考え方）。
# 落とすのではなく**後ろに回す**（P4: 情報を落とさない）。
_GENERIC_OPERATIONS = frozenset({"transform", "relate", "apply_equation", ""})

# ---------------------------------------------------------------------------
# 不透明 ID の検出（[D-1] 2026-08-01）
#
# ``core.doubt.open_assumptions.target_label`` は引き当てに失敗すると **target_id を
# そのまま返す**（equation は分岐すら持たない）。その戻り値を statement にすると
#
#   (a) `eq_2_7` / UUID だけの素材で ``has_material()`` が True になり、skip すべき
#       document に LLM を1コール消費する
#   (b) `MIN_EVIDENCE_QUOTE_CHARS=6` を満たすため LLM が ID を evidence_quote に
#       コピーすると verbatim 検査を通過し、承認されると学習者に「引用」として出る
#   (c) 設計書 §4.1「不透明 ID を渡さず解決済みテキストに展開する」に正面から反する
#
# ので、素材にする前にここで落とす。D層（``core/doubt/``）は他にも読み手がいるため
# 非改変（``target_label`` の戻り値契約は変えない）。
# ---------------------------------------------------------------------------

_UUID_RE = re.compile(
    r"\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z", re.IGNORECASE
)
# 命題ではない単一トークン（`eq_2_7` / `claim-12` / `c1` / `fig:2` / `n_3`）。
# 空白を含む文（=複数語）と CJK を含む文は対象外にして誤検出を避ける。
_ID_TOKEN_RE = re.compile(r"\A[A-Za-z0-9]+(?:[_.:\-#/][A-Za-z0-9]+)*\Z")
_ID_TOKEN_HINT_RE = re.compile(r"[0-9_.:\-#/]")
_CJK_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿ｦ-ﾟ]")


def is_opaque_statement(statement: Any, target_id: Any = "") -> bool:
    """statement が「解決済みテキスト」ではなく不透明な ID か。

    判定（保守的・決定論的）:

    1. 空文字 → 不透明（引き当て失敗と同じ扱い）
    2. ``target_id`` と一致 → 不透明（``target_label`` の引き当て空振りの主シグナル）
    3. UUID 形 → 不透明
    4. CJK を含む / 空白を含む（=複数語の文）→ **テキスト扱い**（誤検出させない）
    5. 残った単一トークンのうち ID 形（英数 + ``_ . : - # /`` の区切り、かつ数字か
       区切り文字を含む）→ 不透明
    """
    text = str(statement or "").strip()
    if not text:
        return True
    tid = str(target_id or "").strip()
    if tid and text == tid:
        return True
    if _UUID_RE.match(text):
        return True
    if _CJK_RE.search(text):
        return False
    if re.search(r"\s", text):
        return False
    if _ID_TOKEN_RE.match(text) and _ID_TOKEN_HINT_RE.search(text):
        return True
    return False


# ---------------------------------------------------------------------------
# 汎用アクセサ（artifact は dict、in-run のパイプライン成果は dataclass で来る）
# ---------------------------------------------------------------------------


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _text(value: Any, limit: int = MAX_TEXT_CHARS) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _artifact(artifacts: Any, stage: str) -> Any:
    value = _get(artifacts, stage)
    return value if value is not None else {}


def _skeleton_entry_text(skeleton: Any, key: str) -> str:
    """``paper_skeleton`` artifact の1エントリから本文だけを取り出す。

    ``core/discuss/opening.py::_skeleton_entry_text`` と同型（``paper_goal`` は
    thesis artifact に無く skeleton が正本）。投影側とオーサリング側で同じ縮退規則を
    使うため、規則そのものは両方に小さく持つ（相互 import はしない）。
    """
    entry = _get(skeleton, key)
    if isinstance(entry, Mapping):
        return str(entry.get("text") or "").strip()
    return str(entry or "").strip()


# ---------------------------------------------------------------------------
# 鮮度指紋（設計書 §7.1）
# ---------------------------------------------------------------------------


def collect_thesis_claim_ids(artifacts: Any) -> list[str]:
    """thesis artifact が参照している claim id の集合（重複除去・ソート）。

    ``central_thesis.claim_ids`` / ``support_structure`` 配下の全 ``claim_ids`` /
    ``supporting_subclaim_ids`` を再帰的に集める。thesis artifact の
    ``support_structure`` の形（dict/list のネスト）は版により違うため、キー名で
    再帰収集して形に依存しない。
    """
    thesis = _artifact(artifacts, "thesis_reconstruction")
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            for key, value in node.items():
                if key in ("claim_ids", "supporting_subclaim_ids") and isinstance(value, (list, tuple)):
                    for raw in value:
                        cid = str(raw or "").strip()
                        if cid:
                            found.add(cid)
                else:
                    walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)

    walk(thesis if isinstance(thesis, Mapping) else {})
    return sorted(found)


def compute_source_fingerprint(artifacts: Any) -> str:
    """開幕素材の元になった解析結果の決定論指紋（設計書 §7.1）。

    入力は ``document_analysis_runs.stage_outputs["_artifacts"]`` と同じ
    「ステージ名 → artifact」のマッピング（``core.deliberation.refs.document_run_artifacts``
    の戻り値、またはパイプライン実行中の ``ctx.all_artifacts()``）。

    計算対象（この3つだけ。増やすときは :data:`FINGERPRINT_VERSION` を上げる）:

    1. ``thesis_reconstruction.central_question``
    2. ``thesis_reconstruction.central_thesis.text``
    3. thesis artifact が参照する claim id 集合（:func:`collect_thesis_claim_ids`）

    正規化: 各文字列は strip のみ（大文字小文字・記号は保存する）。JSON は
    ``sort_keys=True`` / ``separators`` 固定 / ``ensure_ascii=False`` で直列化し、
    UTF-8 の sha256 hexdigest を返す。artifact が空でも例外にせず、空入力の
    指紋（安定値）を返す — 「指紋が取れない」ことを鮮度不明と区別しない。
    """
    thesis = _artifact(artifacts, "thesis_reconstruction")
    central = _get(thesis, "central_thesis") or {}
    payload = {
        "fingerprint_version": FINGERPRINT_VERSION,
        "central_question": str(_get(thesis, "central_question") or "").strip(),
        "central_thesis_text": str(_get(central, "text") or "").strip(),
        "claim_ids": collect_thesis_claim_ids(artifacts),
    }
    serialized = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def has_fingerprint_source(artifacts: Any) -> bool:
    """:func:`compute_source_fingerprint` が読む素材が1つでも入っているか（[D-3]）。

    ``compute_source_fingerprint`` は空入力でも安定ハッシュを返す（「指紋が取れない」
    ことをエラーにしない）。その性質は生成側では正しいが、**鮮度の突合側**では
    「artifact が無い」と「artifact が変わった」を同じ形にしてしまう:
    run が無い document や ``stage_outputs._artifacts`` が欠けた run では
    ``document_run_artifacts`` が例外ではなく ``{}`` を返すため、空入力の指紋と
    保存済み指紋が必ず食い違い「元の解析結果が変わっています」を誤表示する。

    そのため突合側（``routes/element_explanations.py``）はこの述語で素材の有無を
    先に確かめ、素材が無ければ鮮度判定そのものをスキップする。指紋の計算対象を
    増やすときは :func:`compute_source_fingerprint` と**この関数を同時に**直す
    （どちらも同じ3つを見るのが不変条件）。
    """
    thesis = _artifact(artifacts, "thesis_reconstruction")
    central = _get(thesis, "central_thesis") or {}
    if str(_get(thesis, "central_question") or "").strip():
        return True
    if str(_get(central, "text") or "").strip():
        return True
    return bool(collect_thesis_claim_ids(artifacts))


# ---------------------------------------------------------------------------
# 素材の解決（すべて解決済みテキストにする）
# ---------------------------------------------------------------------------


def collect_thesis_facts(artifacts: Any) -> dict:
    """thesis / skeleton artifact → ``{central_question, paper_goal, central_thesis_text}``。

    投影（Phase 0）と同じ縮退: ``central_question`` は thesis artifact 優先で、
    無ければ ``paper_skeleton`` を見る。``paper_goal`` は skeleton が正本。
    """
    thesis = _artifact(artifacts, "thesis_reconstruction")
    skeleton = _artifact(artifacts, "paper_skeleton")
    central = _get(thesis, "central_thesis") or {}
    central_question = str(_get(thesis, "central_question") or "").strip()
    if not central_question:
        central_question = _skeleton_entry_text(skeleton, "central_question")
    return {
        "central_question": _text(central_question),
        "paper_goal": _text(_skeleton_entry_text(skeleton, "paper_goal")),
        "central_thesis_text": _text(
            _get(central, "text") or _get(thesis, "headline_claim") or ""
        ),
    }


def build_equation_label_index(equations: Any) -> dict[str, str]:
    """equation_id → 表示ラベル（``core/discuss/opening.py::_equation_label`` と同型）。"""
    index: dict[str, str] = {}
    for record in _get(equations, "equations") or []:
        equation_id = str(_get(record, "equation_id") or "").strip()
        if not equation_id:
            continue
        src = _get(record, "source_extraction") or {}
        rec = _get(record, "reconstruction") or {}
        label = (
            _get(rec, "plain_text")
            or _get(rec, "latex")
            or _get(src, "plain_text")
            or _get(src, "latex")
            or _get(record, "label")
            or equation_id
        )
        index[equation_id] = _text(label, MAX_EQUATION_LABEL_CHARS)
    return index


def collect_author_choices(
    derivations: Any,
    equations: Any = None,
    *,
    limit: int = MAX_AUTHOR_CHOICES,
) -> list[dict]:
    """derivation の operation 列 → 「著者が選んだ手」（解決済みテキスト）。

    設計書 §4.1 の ``linearize_*`` / ``eliminate_*`` 等がここに入る。抽象的すぎる
    operation（``transform`` / ``relate`` 等）は**捨てずに後ろへ回す**だけにして、
    具体的な手が足りない document でも素材ゼロに落とさない（P4）。

    各要素は ``{operation, operation_subtype, reason, from_label, to_label}``。
    式 id は表示ラベルへ解決してから渡す（不透明 ID を agent に見せない）。
    """
    equation_labels = build_equation_label_index(equations)

    def label_for(ids: Iterable[Any]) -> str:
        labels = []
        for raw in ids or []:
            equation_id = str(raw or "").strip()
            if not equation_id:
                continue
            labels.append(equation_labels.get(equation_id) or "")
        labels = [label for label in labels if label]
        return _text(" ; ".join(labels), MAX_EQUATION_LABEL_CHARS)

    specific: list[dict] = []
    generic: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    for chain in _get(derivations, "chains") or []:
        for step in _get(chain, "steps") or []:
            operation = str(_get(step, "operation") or "").strip()
            subtype = str(_get(step, "operation_subtype") or "").strip()
            reason = _text(_get(step, "reason"))
            from_label = label_for(_get(step, "input_equation_ids") or [])
            to_label = label_for(_get(step, "output_equation_ids") or [])
            if not operation:
                continue
            # 同じ手が何度も出てくる論文で素材が単調になるのを避ける（決定論的な重複除去）。
            key = (operation, subtype, reason)
            if key in seen:
                continue
            seen.add(key)
            item = {
                "operation": operation,
                "operation_subtype": subtype,
                "reason": reason,
                "from_label": from_label,
                "to_label": to_label,
            }
            if operation in _GENERIC_OPERATIONS and not subtype:
                generic.append(item)
            else:
                specific.append(item)

    ordered = specific + generic
    return ordered[: max(0, int(limit))]


def collect_untested_assumptions(
    session: Any,
    document_id: str,
    *,
    limit: int = MAX_UNTESTED_ASSUMPTIONS,
    stats: dict | None = None,
) -> list[dict]:
    """D層台帳（``epistemic_ledger``）の未検証行 → ``{statement, target_type,
    verification_status}``（statement は解決済みテキスト）。

    対象は当該 document の ``verification_status IN ('untested','unknown')``。
    ``directly_verified`` / ``indirectly_supported`` / ``refuted`` は「確かめていないこと」
    ではないので入れない。statement の解決は
    ``core.doubt.open_assumptions.target_label`` に委譲する（claim / component /
    assumption の引き当て規則をここに再実装しない）。

    ただし ``target_label`` は引き当てに失敗すると target_id を返し、``equation`` の
    分岐すら持たないため、**戻り値が不透明 ID の行は素材にしない**
    （:func:`is_opaque_statement`。[D-1]）。落とした件数は ``stats`` に正直に記録する
    （P4: 黙って消さない）。``stats`` は与えられたときだけ
    ``{"rows": int, "opaque_skipped": int, "kept": int}`` で埋める。

    セッションは呼び出し側が管理する（本モジュールは commit/close しない）。
    """
    from core.doubt.open_assumptions import target_label

    rows = session.execute(
        sa_text(
            """
            SELECT target_id, target_type, verification_status
            FROM epistemic_ledger
            WHERE document_id = :document_id
              AND verification_status = ANY(:statuses)
            ORDER BY updated_at DESC, target_type, target_id
            LIMIT :limit
            """
        ),
        {
            "document_id": str(document_id),
            "statuses": list(UNTESTED_VERIFICATION_STATUSES),
            # 不透明 ID の行（equation 等）で枠を食い潰さないよう多めに引き、
            # 解決できたものだけを limit 件まで採る（[D-1]）。
            "limit": max(0, int(limit)) * _LEDGER_FETCH_MULTIPLIER,
        },
    ).fetchall()

    items: list[dict] = []
    opaque_skipped = 0
    kept_limit = max(0, int(limit))
    for row in rows:
        target_id = str(row[0] or "")
        target_type = str(row[1] or "")
        statement = _text(target_label(session, target_type, target_id))
        if is_opaque_statement(statement, target_id):
            # 引き当てできない行（空 / 生 ID / UUID / `eq_2_7` 等）は素材にしない。
            opaque_skipped += 1
            continue
        if len(items) >= kept_limit:
            continue
        items.append(
            {
                "statement": statement,
                "target_type": target_type,
                "verification_status": str(row[2] or ""),
            }
        )
    if stats is not None:
        stats["rows"] = len(rows)
        stats["opaque_skipped"] = opaque_skipped
        stats["kept"] = len(items)
    return items


# ---------------------------------------------------------------------------
# 台帳が空のときのフォールバック（[D-4] 初回解析、2026-08-01）
# ---------------------------------------------------------------------------

# ``core.doubt.ledger_builder._claim_status`` の写像。evidence 付きの source_backed
# claim は台帳では ``indirectly_supported`` になる = 「確かめていないこと」ではないので
# 素材にしない。それ以外は ``unknown`` = 素材にする。
_LEDGER_SOURCE_BACKED = "source_backed"
_FALLBACK_VERIFICATION_STATUS = "unknown"


def derive_untested_assumptions_from_artifacts(
    artifacts: Any,
    *,
    limit: int = MAX_UNTESTED_ASSUMPTIONS,
    stats: dict | None = None,
) -> list[dict]:
    """``epistemic_ledger`` が空のときの素材フォールバック（非LLM・決定論的）。

    **なぜ必要か（[D-4]）**: D層台帳を書くのは ①パイプライン**完了後**の
    ``core.doubt.ledger_builder.backfill_document_ledger`` ②教員の D層 API だけで、
    ``discuss_opening`` ステージは ``course_mapping`` / ``persist_claims_components_graph``
    の**前**に走る。したがって初回解析では台帳が構造的に空で、
    :func:`collect_untested_assumptions` は必ず0件を返す。式の無い論文
    （derivation 由来の author_choices も空）では ``has_material()`` が False になり
    ``skipped_reason='no_source_material'`` が恒久化し、再解析を1回回すまで
    「議論のきっかけ」が出ない — 設計書 §4.1 の主素材が初回で使えない状態だった。

    **同じ判定を写像する（D層には書き込まない）**: backfill が読む DB 行
    （``theory_claims``）はこの時点でまだ永続化されていない（persist ステージが後）。
    そこで同じ内容の in-run artifact（``claim_object_builder``）から、backfill と同じ
    保守的マッピング（``support_status='source_backed'`` かつ evidence 本文あり →
    ``indirectly_supported`` = 素材にしない / それ以外 → ``unknown`` = 素材にする）で
    未検証前提相当を導出する。**台帳への記帳は一切行わない**（D層非改変・記帳の正本は
    パイプライン完了後の backfill のまま）。

    claim 以外の台帳対象は写像しない:

    - ``equation``: ``target_label`` に分岐が無く、台帳経路でも
      :func:`is_opaque_statement` で落ちる（式ラベルは命題ではない）。
    - ``component``: graph node は「解析がまだ裏付けを取れていないところ」= 主語が
      システム側で、開幕画面では ``fragile_points`` の別区画（subject=system）が担う。
      「この論文が確かめていないこと」に混ぜない（設計書 §0 欠陥1）。
    - ``assumption``（D層 assumption_nodes）: 初回解析時点では存在しない。

    ``atomic`` claim を先に並べる（CLAUDE.md: 非 atomic / split_pending は強い backing に
    使わない）が、捨てはしない（P4）。
    """
    result = _artifact(artifacts, "claim_object_builder")
    claims = _get(result, "claims") or []
    atomic: list[dict] = []
    non_atomic: list[dict] = []
    seen: set[str] = set()
    skipped_supported = 0
    opaque_skipped = 0

    for record in claims:
        support_status = str(_get(record, "support_status") or "").strip()
        evidence_text = str(_get(record, "evidence_text") or "").strip()
        if support_status == _LEDGER_SOURCE_BACKED and evidence_text:
            # backfill 側で indirectly_supported になる行（= 確かめていないこと ではない）。
            skipped_supported += 1
            continue
        raw_statement = _get(record, "normalized_text") or _get(record, "text") or ""
        statement = _text(raw_statement)
        claim_id = str(_get(record, "claim_id") or "")
        if is_opaque_statement(statement, claim_id):
            opaque_skipped += 1
            continue
        if statement in seen:
            continue
        seen.add(statement)
        item = {
            "statement": statement,
            "target_type": "claim",
            "verification_status": _FALLBACK_VERIFICATION_STATUS,
        }
        if bool(_get(record, "is_atomic", True)):
            atomic.append(item)
        else:
            non_atomic.append(item)

    ordered = (atomic + non_atomic)[: max(0, int(limit))]
    if stats is not None:
        stats["claims"] = len(claims)
        stats["opaque_skipped"] = opaque_skipped
        stats["skipped_supported"] = skipped_supported
        stats["kept"] = len(ordered)
    return ordered


def build_discuss_opening_input(
    *,
    document_id: str,
    artifacts: Any,
    derivations: Any = None,
    equations: Any = None,
    untested_assumptions: list[dict] | None = None,
    language: str = "ja",
    max_seeds: int = 4,
) -> dict:
    """agent（``DiscussOpeningAgent.run``）に渡す入力ペイロードを組み立てる。

    戻り値は ``DiscussOpeningInput.from_dict`` がそのまま受け取れる plain dict。
    ``has_material`` 相当の判定（未検証前提も operation も無ければ生成しない）は
    agent 側が行うが、呼び出し側が stage_outputs に正直な件数を記録できるよう、
    件数は戻り値から数えられる形にしている。
    """
    return {
        "document_id": str(document_id),
        "thesis": collect_thesis_facts(artifacts),
        "untested_assumptions": list(untested_assumptions or [])[:MAX_UNTESTED_ASSUMPTIONS],
        "author_choices": collect_author_choices(derivations, equations),
        "language": str(language or "ja"),
        "max_seeds": int(max_seeds),
    }
