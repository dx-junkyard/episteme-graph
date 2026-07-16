"""標準化判定の決定論的合成（Phase S・非LLM）。

三角測量の3証拠（①LLM 事前知識 ②L層凍結版類似 ③コーパス内反復）を、知識ネットワーク
ビジョン §3 の判定語彙テーブルへ写像する純粋関数。LLM が「標準だ」と主張しても他の証拠が
伴わない場合に自動昇格させない（幻覚対策。ビジョン §3 修正③が警告する失敗モード）。

決定表（優先順位順、該当した最初の規則を採用。全8通りの (recognized × similar × repeated)
組み合わせを網羅する）:

| # | 条件                                                                  | 判定              |
|---|-----------------------------------------------------------------------|-------------------|
| 1 | LLM が textbook 級で認知 AND 類似あり AND 反復あり                     | standard          |
| 2 | LLM は認知していない AND 反復あり AND 類似なし                         | emerging_common   |
| 3 | 類似あり OR 反復あり（上記に該当しない残り）                           | field_standard    |
| 4 | 類似なし AND 反復なし AND LLM も認知していない                         | novel             |
| 5 | 類似なし AND 反復なし AND LLM は認知している（他証拠の裏付けなし）      | unknown           |

規則5は「LLM だけが標準だと主張し、他のどの証拠も裏付けない」ケース——まさにビジョン
§3 が警告する幻覚パターン。単独の LLM 主張だけで standard/field_standard に昇格させず、
判定不能（unknown）に留める（KN-3: 同一視・標準化の確定は常に人間が最後に行う。LLM の
repair が2回とも失敗した場合も evidence①=非認知として扱われ、この表のどこかへ収束する
——repair 失敗それ自体が単独で "unknown" を生むわけではない）。
"""

from __future__ import annotations

from core.deliberation.standardization.schema import (
    CorpusRepetitionEvidence,
    LibrarySimilarityEvidence,
    LLMPriorKnowledge,
    STANDARDIZATION_STATUS_EMERGING_COMMON,
    STANDARDIZATION_STATUS_FIELD_STANDARD,
    STANDARDIZATION_STATUS_NOVEL,
    STANDARDIZATION_STATUS_STANDARD,
    STANDARDIZATION_STATUS_UNKNOWN,
    StandardizationAssessment,
)


def _evidence_summary(
    llm: LLMPriorKnowledge, library: LibrarySimilarityEvidence, corpus: CorpusRepetitionEvidence,
) -> dict:
    """3証拠それぞれの事実文（body.evidence_summary。W8: 生の confidence 以外は事実として保持）。"""
    return {
        "llm_prior_knowledge": {
            "recognized": llm.recognized,
            "breadth": llm.breadth,
            "claimed_canonical_name": llm.claimed_canonical_name,
            "reason": llm.reason,
            "repair_failed": llm.repair_failed,
        },
        "library_similarity": {
            "hit": library.hit,
            "matched_entry_ids": list(library.matched_entry_ids),
        },
        "corpus_repetition": {
            "repeated": corpus.repeated,
            "document_count": corpus.document_count,
        },
    }


def decide(
    llm: LLMPriorKnowledge,
    library: LibrarySimilarityEvidence,
    corpus: CorpusRepetitionEvidence,
) -> StandardizationAssessment:
    """3証拠を決定論的に合成し :class:`StandardizationAssessment` を返す（純粋関数）。"""
    recognized = bool(llm.recognized)
    textbook = recognized and llm.breadth == "textbook"
    similar = bool(library.hit)
    repeated = bool(corpus.repeated)

    if textbook and similar and repeated:
        status = STANDARDIZATION_STATUS_STANDARD
        reason = "LLM事前知識(教科書級)・L層凍結版類似・コーパス内反復の3証拠が収束した"
    elif (not recognized) and repeated and not similar:
        status = STANDARDIZATION_STATUS_EMERGING_COMMON
        reason = "コーパス内で複数論文に反復するが、外部標準（LLM事前知識・L層類似）は確認できなかった"
    elif similar or repeated:
        status = STANDARDIZATION_STATUS_FIELD_STANDARD
        reason = "L層凍結版類似またはコーパス内反復の少なくとも一方が支持している"
    elif not recognized:
        status = STANDARDIZATION_STATUS_NOVEL
        reason = "単一の出現のみで、外部標準・コーパス内反復のいずれの裏付けも無い"
    else:
        status = STANDARDIZATION_STATUS_UNKNOWN
        reason = "LLMのみが認知を主張し、L層類似・コーパス内反復のいずれの裏付けも無いため判定できない"

    evidence: list[str] = []
    if llm.reason:
        evidence.append(f"LLM事前知識: {llm.reason}")
    if library.hit:
        evidence.append(f"L層凍結版類似ヒット: {len(library.matched_entry_ids)}件")
    if corpus.repeated:
        evidence.append(f"コーパス内 {corpus.document_count} 論文で反復")

    return StandardizationAssessment(
        standardization_status=status,
        claimed_canonical_name=llm.claimed_canonical_name,
        reason=reason,
        confidence=llm.confidence if not llm.repair_failed else None,
        evidence=evidence,
        evidence_summary=_evidence_summary(llm, library, corpus),
    )
