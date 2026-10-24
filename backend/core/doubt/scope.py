"""D層の範囲（document 集合）の正本（IK-0852, 2026-10-23）。

D層のコース単位の読み出し（前提の地図・未検証合意リスト・観測系の主張・コーパス監査・
負荷の再計算・反実仮想・開幕の「この論文が確かめていないこと」）は、かつて成果の行に
焼かれた ``course_id`` 列との一致で範囲を決めていた。その列は解析 run に course_id が
渡ったときだけ値を持つので、教材を解析してからコースを作る現行の標準経路では常に空で、
どのコースからも成果が届かなかった。

現行の規約:

- **範囲の正本はコースの教材から求めた document 集合**。route 層が
  ``services.list_course_source_document_ids(course_data)`` で解決し、core の関数は
  ``document_ids`` を**必須キーワード引数**で受けて ``document_id = ANY(:doc_ids)`` で絞る。
- **空集合は SQL を出さずに空を返す**（fail-closed。範囲を黙って広げない）。
- 行の ``course_id`` 列は「どのコースの画面で記帳したか」の出所の意味に退く（絞り込みに使わない）。

このモジュールは FastAPI / LLM を import しない純関数だけを置く。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def normalize_document_ids(document_ids: Iterable[Any] | None) -> list[str]:
    """document id の列を、空白・空文字・重複を除いた昇順のリストに揃える。

    ``None`` も空リストとして扱う（範囲が決まらない = 何も読まない）。
    """
    if document_ids is None:
        return []
    if isinstance(document_ids, (str, bytes)):
        document_ids = [document_ids]
    out = {str(d or "").strip() for d in document_ids}
    out.discard("")
    return sorted(out)


def document_scope_clause(
    document_ids: Iterable[Any] | None,
    *,
    column: str = "document_id",
    param: str = "doc_ids",
) -> tuple[str, dict[str, Any]] | None:
    """``{column}::text = ANY(:{param})`` の WHERE 断片とパラメータを返す。

    範囲が空なら ``None``（呼び出し側は SQL を出さずに空を返す — fail-closed）。
    ``column`` / ``param`` は呼び出し側の定数のみを渡す（利用者の入力を補間しない）。
    """
    ids = normalize_document_ids(document_ids)
    if not ids:
        return None
    return f"{column}::text = ANY(:{param})", {param: ids}


# ---------------------------------------------------------------------------
# 空・未実行の理由の事実文（IK-0853）。画面はこれをそのまま描く（JS に日本語表を持たない）。
# 件数は書かない（P7 / SL4）。閉世界（SL1）: 不在は「このコーパス（このコースの教材）の中では」。
# ---------------------------------------------------------------------------

#: 範囲（コースの教材）に解析済みの論文が無い。
SCOPE_NO_DOCUMENTS_NOTE = "このコースの教材に、解析済みの論文が見つかりませんでした。"

#: 台帳を開く別の入口（地図・一覧が空でも記帳はできる）。
STUDIO_LEDGER_HINT = "台帳の対象は、原稿スタジオの理論操作グラフのノード詳細からも開けます。"

#: 前提の地図が空（範囲はあるが、地図に載る対象が無い）。
ATLAS_EMPTY_NOTE = (
    "このコースの教材の範囲では、地図に載る前提・主張がまだありません"
    "（前提は確定すると載り、主張は依存の広がりが大きいものだけが載ります）。"
)

#: 未検証合意リストが空（範囲はあるが、高負荷 × 低検証の対象が無い）。
OPEN_ASSUMPTIONS_EMPTY_NOTE = "このコースの教材の範囲では、依存の広がりが大きく検証の記録がない対象はありません。"

#: 学習者向けの未検証の前提一覧が空。
LEARNER_OPEN_ASSUMPTIONS_EMPTY_NOTE = (
    "このコースでは、まだ一覧に載っている前提がありません（教員が記帳すると載ります）。"
)

#: 区画（ゼミ前ブリーフの脆い前提・晴れ間など）に該当が無い（閉世界）。
SECTION_EMPTY_NOTE = "このコーパスの中では該当がありません。"

#: 非同期のバッチを受け付けた。
BATCH_SCHEDULED_NOTE = "受け付けました。結果は少し待ってから再読込すると反映されます。"

#: 負荷の段階を再計算した。
LOAD_RECOMPUTED_NOTE = "依存の広がり（負荷の段階）を再計算しました。"

#: コーパス監査の結果・実行しなかった理由（``run_corpus_audit`` の ``reason`` 語彙ごと）。
CORPUS_AUDIT_NOTES = {
    "no_documents": SCOPE_NO_DOCUMENTS_NOTE,
    "no_analysis": "このコースの教材には、まだ解析結果（理論操作グラフ）がありません。",
    "corpus_too_small": (
        "コーパス監査は複数の論文を突き合わせる処理のため、"
        "このコースの教材の範囲では論文が少なく実行しませんでした。"
    ),
    "registered": "コーパス監査を実行し、新しい前提の候補を登録しました。",
    "none_found": "コーパス監査を実行しました。新しい前提の候補はありませんでした。",
    "failed": "コーパス監査を実行できませんでした。",
}


# ---------------------------------------------------------------------------
# 台帳詳細・記帳面の事実文（第 25 周 面 B 第 2 周 — IK-0900 / IK-0904 / IK-0905 / IK-0853）。
# 画面はそのまま描く（JS に日本語表を持たない）。件数・数値は書かない（P7 / SL4）。
# ---------------------------------------------------------------------------

#: 台帳を閲覧できるが記帳できない教員に添える（IK-0900。記帳ボタンは無効になる）。
LEDGER_READ_ONLY_NOTE = (
    "この論文の編集権限が無いため、台帳への記帳はできません（閲覧と疑義の記帳はできます）。"
)

#: 記帳者の名前を引けないとき（アカウントの墓標化など。IK-0905）。
RECORDER_UNKNOWN_LABEL = "記帳者を引けません"

#: 同じ対象に同じ内容のスコープが既にある（受けたうえで添える。IK-0905）。
SCOPE_DUPLICATE_NOTE = (
    "同じ内容の検証スコープが既に記帳されています。別の記帳として並べて残しました。"
)

#: 検証状態の記帳（IK-0905。変わったか・同じだったかを言い分ける）。
VERIFICATION_STATUS_CHANGED_NOTE = "検証状態を記帳しました。"
VERIFICATION_STATUS_UNCHANGED_NOTE = "すでにこの検証状態です（記帳は監査に残しました）。"

#: 疑義の入口（IK-0904）。式・部品の台帳には疑義を残せないので、支える主張へ案内する。
CHALLENGE_TARGETS_NOTE = "疑義は前提と主張に残せます。この対象を支える主張は次のとおりです。"
CHALLENGE_TARGETS_NONE_NOTE = (
    "疑義は前提と主張に残せます。この対象を支える主張は、このコーパスの中では見つかりませんでした。"
)

#: 前提マイニングの結果（IK-0853 / IK-0904。受付だけで終わらせない）。マイニングは非同期なので、
#: 前提の一覧を読み直すたびに「範囲にマイニング由来の候補があるか」の事実を添える
#: （実行したかどうかは記録が無いので言わない）。
ASSUMPTION_MINING_NOTES = {
    "found": "前提マイニングで見つかった前提の候補が、この一覧にあります（「候補」で絞り込めます）。",
    "none_in_scope": "前提マイニングで見つかった前提の候補は、いまこのコースの教材の範囲にはありません。",
}
