"""Stage 1 — 出力検証（設計書 §6）。ハードエラーは repair へ回す。

| ルール | 種別 |
|---|---|
| JSONスキーマ適合（必須キー・型） | hard |
| evidence_quote が入力中の learner 発話の部分文字列として逐語一致 | hard |
| turn_ids ⊆ 入力窓の turn id 集合 | hard |
| target_refs の全 id が context blocks に存在 | hard |
| tension_type ∈ タクソノミー | hard |
| paraphrase に断定形を含まず、推量末尾を持つ（英語の学習者は推量の助動詞・副詞） | hard |
| paraphrase ≤ 120字 | hard |
| is_tension_not_gap == false のエントリが candidates に混入していない | hard |
| candidates 件数 ≤ max_candidates | hard |
| 単一マーカーのみ言及の reason で confidence > 0.5 | warning（0.5 にクランプ） |
| reason が空 | hard |
"""

from __future__ import annotations

import re

from core.tension.schema import (
    TENSION_TYPES,
    ConversationWindow,
    RejectedGap,
    TensionCandidate,
    TensionMiningResult,
)

PARAPHRASE_MAX_LEN = 120

# 断定形の禁止パターン（P2: 学習者の心的状態を断定しない）
_ASSERTIVE_PATTERNS = (
    re.compile(r"と感じています"),
    re.compile(r"と思っています"),
    re.compile(r"あなたは.{0,30}(です|だ|でしょう)"),
    re.compile(r"に違いありません"),
    re.compile(r"はずです。?$"),
)
# 推量末尾（末尾の句点は許容）
_TENTATIVE_ENDINGS = ("かもしれません", "ように見えます", "のかも")

# 英語の学習者向け（プロンプト規則4「学習者の言語で」、IK-0401）: 英語は推量が文末でなく
# 文中の助動詞・副詞に出るので、末尾一致ではなく語境界付きの含有で判定する。
_ENGLISH_TENTATIVE_RE = re.compile(
    r"\b(?:may|might|could|perhaps|possibly|seems?|seem to|appears?|appear to)\b",
    re.IGNORECASE,
)
# 英語の断定形（P2: 学習者の心的状態・結論を言い切らない）。両言語で常に検査する。
_ENGLISH_ASSERTIVE_PATTERNS = (
    re.compile(r"\byou (?:feel|think|believe)\b", re.IGNORECASE),
    re.compile(r"\b(?:definitely|certainly|undoubtedly|surely)\b", re.IGNORECASE),
    re.compile(r"\bmust (?:be|feel|have)\b", re.IGNORECASE),
)
# 学習者発話の言語判定: かな・漢字と英字の比。英字がかな漢字の4倍を超えれば英語とみなす
# （日本語の発話にも LaTeX・英語の術語は混じるので、単純な英字の有無では決めない）。
_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_ENGLISH_DOMINANCE_RATIO = 4


def learner_language(learner_texts: list[str]) -> str:
    """学習者発話が主に英語なら ``"en"``、それ以外は ``"ja"``（空なら ``"ja"``）。"""
    joined = "\n".join(str(t or "") for t in learner_texts or [])
    cjk = len(_CJK_RE.findall(joined))
    latin = len(_LATIN_RE.findall(joined))
    if latin and latin > cjk * _ENGLISH_DOMINANCE_RATIO:
        return "en"
    return "ja"

# confidence キャリブレーション警告用: reason が言及するシグナル種別の素朴カウント
_SIGNAL_KEYWORD_GROUPS = (
    ("hedge", ("hedge", "なんとなく", "腑に落ち", "気持ち悪", "しっくり", "違和感", "unease")),
    ("revisit", ("revisit", "再訪", "repeated", "return", "戻")),
    ("counterexample", ("counterexample", "反例", "edge case", "場合はどう")),
    ("assumption", ("assumption", "前提", "premise", "そもそも")),
    ("analogy", ("analogy", "類似", "resemblance", "似て")),
    ("source_doubt", ("doubt", "疑", "本当に", "source", "論文")),
    ("no_ack", ("acknowledgement", "納得表明", "no なるほど", "without acknowledgement", "逆接")),
)


def _paraphrase_errors(paraphrase: str, idx: int, language: str = "ja") -> list[str]:
    """paraphrase の文体検査（P2）。``language`` は :func:`learner_language` の結果。

    日本語の学習者: 推量末尾（かもしれません / ように見えます / のかも）必須（従来どおり）。
    英語の学習者: 推量の助動詞・副詞（may / might / seems 等）の含有を必須にし、日本語の
    推量末尾も受ける（学習者の言語で書けと指示している以上、英語の推量形を不合格にしない）。
    断定形の検査は両言語で常に行う。
    """
    errors: list[str] = []
    p = (paraphrase or "").strip()
    if not p:
        errors.append(f"candidates[{idx}].paraphrase is empty")
        return errors
    if len(p) > PARAPHRASE_MAX_LEN:
        errors.append(f"candidates[{idx}].paraphrase exceeds {PARAPHRASE_MAX_LEN} characters")
    for pat in _ASSERTIVE_PATTERNS + _ENGLISH_ASSERTIVE_PATTERNS:
        if pat.search(p):
            errors.append(
                f"candidates[{idx}].paraphrase uses an assertive form (pattern: {pat.pattern}); "
                "use a tentative register such as 「〜かもしれません」 or 'may / might / seems'"
            )
            break
    stripped = p.rstrip("。 　.")
    japanese_ok = any(stripped.endswith(e) for e in _TENTATIVE_ENDINGS)
    if language == "en":
        if not (japanese_ok or _ENGLISH_TENTATIVE_RE.search(p)):
            errors.append(
                f"candidates[{idx}].paraphrase must use a tentative form "
                "(may / might / seems / perhaps / possibly / could / appears)"
            )
    elif not japanese_ok:
        errors.append(
            f"candidates[{idx}].paraphrase must end with a tentative form "
            "(かもしれません / ように見えます / のかも)"
        )
    return errors


def _count_signal_groups(reason: str) -> int:
    r = reason or ""
    low = r.lower()
    hits = 0
    for _, keywords in _SIGNAL_KEYWORD_GROUPS:
        if any((k in low) if k.isascii() else (k in r) for k in keywords):
            hits += 1
    return hits


def validate_output(
    data: dict,
    window: ConversationWindow,
    max_candidates: int,
) -> tuple[TensionMiningResult | None, list[str], list[str]]:
    """LLM 出力 dict を検証する。

    Returns
    -------
    (result, errors, warnings)
        errors が空なら result に正規化済み TensionMiningResult（warning クランプ適用済み）。
        errors が非空なら result は None（repair 対象）。
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(data, dict):
        return None, ["output must be a JSON object"], warnings
    raw_candidates = data.get("candidates")
    raw_rejected = data.get("rejected_as_gap", [])
    if not isinstance(raw_candidates, list):
        errors.append("'candidates' must be a JSON array")
        raw_candidates = []
    if not isinstance(raw_rejected, list):
        errors.append("'rejected_as_gap' must be a JSON array")
        raw_rejected = []

    if len(raw_candidates) > max_candidates:
        errors.append(f"candidates length {len(raw_candidates)} exceeds max_candidates={max_candidates}")

    turn_ids = window.turn_ids()
    learner_texts = window.learner_texts()
    language = learner_language(learner_texts)
    allowed_components = window.allowed_component_ids()
    allowed_chunks = window.allowed_chunk_ids()

    candidates: list[TensionCandidate] = []
    for idx, raw in enumerate(raw_candidates):
        if not isinstance(raw, dict):
            errors.append(f"candidates[{idx}] must be a JSON object")
            continue
        for key in ("tension_type", "evidence_quote", "turn_ids", "paraphrase", "confidence", "reason"):
            if key not in raw:
                errors.append(f"candidates[{idx}] is missing required key '{key}'")
        cand = TensionCandidate.from_dict(raw)

        if cand.tension_type not in TENSION_TYPES:
            errors.append(f"candidates[{idx}].tension_type '{cand.tension_type}' is not in the taxonomy")

        quote = (cand.evidence_quote or "").strip()
        if not quote:
            errors.append(f"candidates[{idx}].evidence_quote is empty")
        elif not any(quote in t for t in learner_texts):
            errors.append(
                f"candidates[{idx}].evidence_quote is not a verbatim substring of any learner utterance"
            )
        cand.evidence_quote = quote

        if not cand.turn_ids:
            errors.append(f"candidates[{idx}].turn_ids is empty")
        else:
            unknown = [t for t in cand.turn_ids if t not in turn_ids]
            if unknown:
                errors.append(f"candidates[{idx}].turn_ids contains unknown ids: {unknown}")

        bad_components = [c for c in cand.target_refs.component_ids if c not in allowed_components]
        bad_chunks = [c for c in cand.target_refs.chunk_ids if c not in allowed_chunks]
        if bad_components:
            errors.append(f"candidates[{idx}].target_refs.component_ids contains invented ids: {bad_components}")
        if bad_chunks:
            errors.append(f"candidates[{idx}].target_refs.chunk_ids contains invented ids: {bad_chunks}")

        errors.extend(_paraphrase_errors(cand.paraphrase, idx, language))

        if raw.get("is_tension_not_gap") is False:
            errors.append(
                f"candidates[{idx}] has is_tension_not_gap=false; gaps must go to rejected_as_gap, not candidates"
            )

        if not (cand.reason or "").strip():
            errors.append(f"candidates[{idx}].reason is empty")

        if not (0.0 <= cand.confidence <= 1.0):
            errors.append(f"candidates[{idx}].confidence must be within [0.0, 1.0]")
        elif cand.confidence > 0.5 and _count_signal_groups(cand.reason) <= 1:
            # warning: 単一シグナルの言及で confidence > 0.5 → 0.5 にクランプ
            warnings.append(
                f"candidates[{idx}]: confidence {cand.confidence} clamped to 0.5 "
                "(reason cites only a single signal)"
            )
            cand.confidence = 0.5

        candidates.append(cand)

    rejected: list[RejectedGap] = []
    for idx, raw in enumerate(raw_rejected):
        if not isinstance(raw, dict):
            errors.append(f"rejected_as_gap[{idx}] must be a JSON object")
            continue
        rejected.append(RejectedGap(
            turn_ids=[str(t) for t in (raw.get("turn_ids") or [])],
            reason=str(raw.get("reason") or ""),
        ))

    if errors:
        return None, errors, warnings
    candidates.sort(key=lambda c: c.confidence, reverse=True)
    return TensionMiningResult(candidates=candidates, rejected_as_gap=rejected, warnings=warnings), [], warnings
