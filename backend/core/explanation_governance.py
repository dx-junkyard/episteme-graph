"""C層（承認・共有）の説明の「誰を数え、誰に届けるか」の正本（第 25 周 面 A 第 3 周）。

課題: IK-0926（著者の自己支持が「N名が支持」に数えられ、自己査読で別のコースの学生に
届いた）・IK-0923（標準の説明が部品の要約の写しのまま残る）・IK-0924 / IK-0925（説明
カードの 2 軸と操作の可否）。オーナー判断は指揮者が推奨どおりに決定済み:

1. **支持の集計から著者本人を除く** — 著者の支持の行は書く（記録は残す — P4）が、
   段階ラベルの人数には数えない。取り消した支持（``revoked``）も数えない。
   集計ビュー ``component_explanation_endorsement_summary``（migration 021）は残し、
   読み手は :data:`COUNTED_ENDORSEMENT_SUMMARY_SQL` を ``LEFT JOIN`` する（ビューを
   書き換えると migration が要り、他の層の読み手の意味も黙って変わるため）。
2. **コースをまたぐ配信は著者以外の査読承認を要件にする** — 共有 ON の説明が書いた
   コース以外の学生に届くのは、最新の「査読承認」の記帳（``theory_review_events``）の
   記帳者が著者でないときだけ。書いたコースの学生には著者の査読のままで届く。
   査読者は新しい列を足さず、既存の監査行（``changed_by``）から読む。
3. 学生向けの段階ラベルの定義は制度指標カタログ
   （``core/indicator_catalog.py`` の ``explanation-support-label``）に宣言する（IG4）。

SQL 断片は説明の行の別名 ``e``（``component_explanations e``）を前提にする。
本モジュールは純宣言（FastAPI / LLM 非依存）。
"""

from __future__ import annotations

from core.schema import AUDIT_ENTITY_EXPLANATION

__all__ = [
    "AUTHOR_ENDORSED_SQL",
    "COUNTED_ENDORSEMENT_SUMMARY_SQL",
    "EXPLANATION_AUTHOR_ENDORSER_ROLE_LABEL",
    "EXPLANATION_AUTHOR_ONLY_NOTE",
    "EXPLANATION_AUTHOR_SUPPORT_NOT_COUNTED_NOTE",
    "EXPLANATION_BODY_EDIT_ACTION",
    "EXPLANATION_DELIVERY_CROSS_COURSE",
    "EXPLANATION_DELIVERY_NOT_APPROVED",
    "EXPLANATION_DELIVERY_NO_SOURCE_COURSE",
    "EXPLANATION_DELIVERY_OWN_COURSE_ONLY",
    "EXPLANATION_DELIVERY_SELF_REVIEWED",
    "EXPLANATION_DELIVERY_STANDARD",
    "LEARNER_EXPLANATION_SCOPE_SQL",
    "NON_AUTHOR_REVIEWED_SQL",
    "STANDARD_BODY_PRIOR_APPROVAL_NOTE",
    "STANDARD_BODY_REFRESHED_ACTION",
    "explanation_delivery_note",
]


#: 著者を除き、取り消しを除いた支持の集計（説明ごと）。列名はビューと同じ。
COUNTED_ENDORSEMENT_SUMMARY_SQL = """(
        SELECT en.explanation_id,
               COUNT(*)                                                  AS endorser_count,
               COUNT(*) FILTER (WHERE en.level = 'strong')               AS strong_count,
               COUNT(*) FILTER (WHERE en.level = 'provisional')          AS provisional_count,
               COUNT(DISTINCT en.expertise_tag) FILTER (WHERE en.expertise_tag <> '')
                                                                         AS expertise_breadth
          FROM component_endorsements en
          JOIN component_explanations ex ON ex.id = en.explanation_id
         WHERE NOT en.revoked
           AND en.endorser_id IS DISTINCT FROM ex.author_id
         GROUP BY en.explanation_id
    )"""

#: 著者自身の支持（取り消していないもの）が記録されているか。数えないことを事実文で言うため。
AUTHOR_ENDORSED_SQL = (
    "EXISTS (SELECT 1 FROM component_endorsements ae"
    " WHERE ae.explanation_id = e.id AND ae.endorser_id = e.author_id AND NOT ae.revoked)"
)

#: 最新の「査読承認」の記帳者が著者でない（かつ記帳者が分かる）か。
#: 記帳者が著者のまま、または記帳が無ければ偽（fail-closed）。
NON_AUTHOR_REVIEWED_SQL = (
    "(COALESCE((SELECT ev.changed_by::text FROM theory_review_events ev"
    f" WHERE ev.entity_type = '{AUDIT_ENTITY_EXPLANATION}' AND ev.entity_id = e.id::text"
    " AND ev.new_status = 'teacher_approved'"
    " ORDER BY ev.created_at DESC LIMIT 1), '')"
    " NOT IN ('', COALESCE(e.author_id::text, '')))"
)

#: 学生へ届く説明の条件（IK-0861 + IK-0926）。承認済みに限り、標準の説明（document 単位）は
#: コースをまたいで届く。教員の説明は書いたコース（出所 ``course_id``）の学生に届き、
#: 共有 ON の説明がほかのコースの学生に届くのは著者以外の査読承認を受けたときだけ。
LEARNER_EXPLANATION_SCOPE_SQL = f"""
                  AND e.review_status = 'teacher_approved'
                  AND (e.kind = 'standard' OR e.course_id = :course_id
                       OR (e.shared = TRUE AND {NON_AUTHOR_REVIEWED_SQL}))
"""

#: 監査行の action（標準の説明の本文を部品の要約へ追随させた — 旧本文は metadata に残す）。
STANDARD_BODY_REFRESHED_ACTION = "standard_body_refreshed"
#: 監査行の action（人が説明の本文・題名を書き換えた — 以後、標準の説明を自動で追随させない）。
EXPLANATION_BODY_EDIT_ACTION = "body_edit"

# --- 説明カードの事実文（サーバ固定文。JS は素通しする） -----------------------

EXPLANATION_AUTHOR_ONLY_NOTE = "この説明の公開・共有の設定は著者だけが変えられます。"
EXPLANATION_AUTHOR_SUPPORT_NOT_COUNTED_NOTE = "著者自身の支持は記録していますが、支持の人数には数えません。"
EXPLANATION_AUTHOR_ENDORSER_ROLE_LABEL = "著者（人数に数えません）"
STANDARD_BODY_PRIOR_APPROVAL_NOTE = (
    "この標準の説明は、部品の要約の更新に合わせて本文が書き換わりました。"
    "既にある査読・支持は前の本文に対するものです。"
)

EXPLANATION_DELIVERY_STANDARD = "査読済みのため、この部品を使うコースの学生に届きます。"
EXPLANATION_DELIVERY_NOT_APPROVED = "査読待ちのため、学生にはまだ届いていません。"
EXPLANATION_DELIVERY_OWN_COURSE_ONLY = "書いたコースの学生にだけ届きます（共有 OFF）。"
EXPLANATION_DELIVERY_CROSS_COURSE = (
    "書いたコースと、この部品を使うほかのコースの学生に届きます（著者以外の査読承認あり）。"
)
EXPLANATION_DELIVERY_SELF_REVIEWED = (
    "書いたコースの学生に届きます。ほかのコースの学生に届くのは、著者以外の査読承認を受けた後です。"
)
EXPLANATION_DELIVERY_NO_SOURCE_COURSE = (
    "書いたコースの記録が無いため、学生にはまだ届いていません。"
    "共有 ON で著者以外の査読承認を受けると、この部品を使うコースの学生に届きます。"
)


def explanation_delivery_note(
    *,
    kind: str,
    review_status: str,
    shared: bool,
    has_source_course: bool,
    non_author_reviewed: bool,
) -> str:
    """説明が学生のどこに届くかの事実文（:data:`LEARNER_EXPLANATION_SCOPE_SQL` と同じ条件）。"""
    if review_status != "teacher_approved":
        return EXPLANATION_DELIVERY_NOT_APPROVED
    if kind == "standard":
        return EXPLANATION_DELIVERY_STANDARD
    cross = bool(shared) and bool(non_author_reviewed)
    if cross:
        return EXPLANATION_DELIVERY_CROSS_COURSE
    if not has_source_course:
        return EXPLANATION_DELIVERY_NO_SOURCE_COURSE
    if shared:
        return EXPLANATION_DELIVERY_SELF_REVIEWED
    return EXPLANATION_DELIVERY_OWN_COURSE_ONLY
