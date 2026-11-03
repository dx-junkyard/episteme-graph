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
3. **著者のいない標準の説明（AI が作った部品の要約の写し）の査読者**（IK-0984・第 5 波・
   オーナー判断の推奨どおり）: その部品の論文を編集できる教員が査読できる（管理者に限らない）。
   査読者は既存の監査行（``changed_by``）に残る。標準の説明は著者がいないので、査読した教員を
   「著者に準じる者」とし、1 人の査読では**その教員のコース**（``learning_courses`` の所有者）の
   学生にだけ届く。この部品を使うほかのコースへ広げるには、いまの承認の期間に**別の教員の
   査読**がもう 1 件要る（AI の要約を 1 人の判断で全コースへ配らない）。いまの承認の期間に
   説明カードからの査読の記帳が 1 件も無い承認（部品の承認から引き継いだ・この規則より前の
   承認）は、従来どおり部品を使うコースへ届く（記帳の無い承認者を推測で決めない — 判断材料は
   設計書の追補に残す）。
4. **教員の説明の「著者以外の査読者」**（IK-1023・第 6 波・オーナー判断の推奨どおり）: 標準の説明と
   同じく、**その部品の論文を編集できる教員**（著者以外）が、著者が査読して共有 ON にした説明に
   「もう一人の査読者として査読する」を記帳できる。記帳は状態を変えない監査行
   （``action =`` :data:`STANDARD_ADDITIONAL_REVIEW_ACTION`・``changed_by`` が査読者）で、
   :data:`NON_AUTHOR_REVIEWED_SQL` がそのまま「著者以外の査読承認」として読む（新しい列を足さない）。
   本文・題名・共有の設定は著者だけのまま。引用はコースへ届く理由にしない（引用の応答は、引用先の
   学生に届くかを :data:`LEARNER_EXPLANATION_SCOPE_SQL` と同じ条件で言うだけ）。
5. 学生向けの段階ラベルの定義は制度指標カタログ
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
    "EXPLANATION_ALREADY_REVIEWED_BY_YOU",
    "EXPLANATION_APPROVED_FACT",
    "EXPLANATION_DELIVERY_STANDARD_ONE_REVIEWER",
    "EXPLANATION_SECOND_REVIEW_FACT",
    "EXPLANATION_SHARED_OFF_FACT",
    "EXPLANATION_SHARED_ON_FACT",
    "EXPLANATION_STANDARD_BODY_ADMIN_ONLY",
    "EXPLANATION_STANDARD_REVIEWER_NOTE",
    "STANDARD_ADDITIONAL_REVIEW_ACTION",
    "STANDARD_DELIVERY_SQL",
    "STANDARD_REVIEWERS_SQL",
    "EXPLANATION_BODY_EDIT_ACTION",
    "EXPLANATION_DELIVERY_CROSS_COURSE",
    "EXPLANATION_DELIVERY_NOT_APPROVED",
    "EXPLANATION_DELIVERY_NO_SOURCE_COURSE",
    "EXPLANATION_DELIVERY_OWN_COURSE_ONLY",
    "EXPLANATION_DELIVERY_SELF_REVIEWED",
    "EXPLANATION_PERSONAL_SECOND_REVIEW_ONLY",
    "CITATION_DELIVERED_FACT",
    "CITATION_NOT_SHARED_PERSONAL_NOTE",
    "CITATION_NOT_SHARED_STANDARD_NOTE",
    "citation_blocked_note",
    "CITATION_NOT_APPROVED_FACT",
    "CITATION_NOT_IN_COURSE_FACT",
    "CITATION_COURSE_UNCHECKED_FACT",
    "CITATION_NO_ANCHOR_IN_COURSE_FACT",
    "CITATION_ANCHOR_UNCHECKED_FACT",
    "citation_course_scope_fact",
    "CITATION_NOT_YET_DELIVERED_FACT",
    "CITATION_RECORDED_FACT",
    "CITATION_ALREADY_RECORDED_FACT",
    "ENDORSEMENT_REAPPLIED_FACT",
    "ENDORSEMENT_RESTORED_FACT",
    "citation_delivery_fact",
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

# ── 標準の説明の査読者（IK-0984）────────────────────────────────────────────────
#: 監査行の action（承認済みの標準の説明に、別の教員がもう 1 人の査読者として査読した）。
STANDARD_ADDITIONAL_REVIEW_ACTION = "additional_review"

#: 「いまの承認の期間」の起点（最後に承認から外れた記帳の時刻。無ければ最初から）。
_APPROVAL_WINDOW_START_SQL = (
    "COALESCE((SELECT max(lv.created_at) FROM theory_review_events lv"
    f" WHERE lv.entity_type = '{AUDIT_ENTITY_EXPLANATION}' AND lv.entity_id = e.id::text"
    " AND lv.old_status = 'teacher_approved' AND lv.new_status <> 'teacher_approved'),"
    " '-infinity'::timestamptz)"
)

#: いまの承認の期間に、説明カードから査読承認を記帳した人（遷移の記帳 + もう 1 人の査読）。
STANDARD_REVIEWERS_SQL = (
    "SELECT rv.changed_by FROM theory_review_events rv"
    f" WHERE rv.entity_type = '{AUDIT_ENTITY_EXPLANATION}' AND rv.entity_id = e.id::text"
    " AND rv.new_status = 'teacher_approved' AND rv.changed_by IS NOT NULL"
    " AND (rv.old_status <> 'teacher_approved'"
    f" OR rv.metadata->>'action' = '{STANDARD_ADDITIONAL_REVIEW_ACTION}')"
    f" AND rv.created_at >= {_APPROVAL_WINDOW_START_SQL}"
)

#: 標準の説明がどのコースの学生に届くか（``:course_id`` を使う）。①説明カードからの査読の
#: 記帳が無い承認（引き継ぎ・旧規則）②別々の教員が 2 人以上査読した ③査読した教員が
#: このコースの所有者、のいずれか。
STANDARD_DELIVERY_SQL = (
    f"(NOT EXISTS ({STANDARD_REVIEWERS_SQL})"
    f" OR (SELECT count(DISTINCT sr.changed_by) FROM ({STANDARD_REVIEWERS_SQL}) sr) >= 2"
    " OR EXISTS (SELECT 1 FROM learning_courses lc WHERE lc.id = :course_id"
    f" AND COALESCE(lc.owner_id, lc.user_id) IN ({STANDARD_REVIEWERS_SQL})))"
)

#: 学生へ届く説明の条件（IK-0861 + IK-0926 + IK-0984）。承認済みに限り、標準の説明は
#: :data:`STANDARD_DELIVERY_SQL` の範囲で届く。教員の説明は書いたコース（出所 ``course_id``）の
#: 学生に届き、共有 ON の説明がほかのコースの学生に届くのは著者以外の査読承認を受けたときだけ。
LEARNER_EXPLANATION_SCOPE_SQL = f"""
                  AND e.review_status = 'teacher_approved'
                  AND ((e.kind = 'standard' AND {STANDARD_DELIVERY_SQL}) OR e.course_id = :course_id
                       OR (e.shared = TRUE AND {NON_AUTHOR_REVIEWED_SQL}))
"""

#: 監査行の action（標準の説明の本文を部品の要約へ追随させた — 旧本文は metadata に残す）。
STANDARD_BODY_REFRESHED_ACTION = "standard_body_refreshed"
#: 監査行の action（人が説明の本文・題名を書き換えた — 以後、標準の説明を自動で追随させない）。
EXPLANATION_BODY_EDIT_ACTION = "body_edit"

# --- 説明カードの事実文（サーバ固定文。JS は素通しする） -----------------------

EXPLANATION_AUTHOR_ONLY_NOTE = "この説明の公開・共有の設定は著者だけが変えられます。"
#: IK-0984: 標準の説明（著者なし）を査読できない人に見せる事実文。
EXPLANATION_STANDARD_REVIEWER_NOTE = (
    "AI が作った標準の説明です。学生への公開は、この論文を編集できる教員が査読します。"
)
#: IK-0984: 標準の説明の本文は査読者でも書き換えない（管理者だけ）の 403 の文。
EXPLANATION_STANDARD_BODY_ADMIN_ONLY = (
    "標準の説明の本文・題名・根拠の主張は、管理者だけが書き換えられます。査読と共有の設定はできます。"
)
#: IK-0984: 既に自分が査読した標準の説明に、もう一度査読を記帳しようとしたときの文。
EXPLANATION_ALREADY_REVIEWED_BY_YOU = "あなたはこの説明を既に査読しています。もう一人は別の教員が査読します。"
#: IK-0985: 操作の後の知らせ（方向を言う。届く先の文を後ろに続ける）。
EXPLANATION_SHARED_ON_FACT = "共有を ON にしました。"
EXPLANATION_SHARED_OFF_FACT = "共有を OFF にしました。"
EXPLANATION_APPROVED_FACT = "学生に公開してよいと査読しました。"
EXPLANATION_SECOND_REVIEW_FACT = "もう一人の査読者として査読しました。"
EXPLANATION_AUTHOR_SUPPORT_NOT_COUNTED_NOTE = "著者自身の支持は記録していますが、支持の人数には数えません。"
EXPLANATION_AUTHOR_ENDORSER_ROLE_LABEL = "著者（人数に数えません）"
STANDARD_BODY_PRIOR_APPROVAL_NOTE = (
    "この標準の説明は、部品の要約の更新に合わせて本文が書き換わりました。"
    "既にある査読・支持は前の本文に対するものです。"
)

EXPLANATION_DELIVERY_STANDARD = "査読済みのため、この部品を使うコースの学生に届きます。"
#: IK-0984: 標準の説明を 1 人だけが査読したとき。
EXPLANATION_DELIVERY_STANDARD_ONE_REVIEWER = (
    "査読した教員のコースの学生に届きます。この部品を使うほかのコースの学生に届くのは、"
    "別の教員がもう一人の査読者として査読した後です。"
)
EXPLANATION_DELIVERY_NOT_APPROVED = "査読待ちのため、学生にはまだ届いていません。"
EXPLANATION_DELIVERY_OWN_COURSE_ONLY = "書いたコースの学生にだけ届きます（共有 OFF）。"
EXPLANATION_DELIVERY_CROSS_COURSE = (
    "書いたコースと、この部品を使うほかのコースの学生に届きます（著者以外の査読承認あり）。"
)
EXPLANATION_DELIVERY_SELF_REVIEWED = (
    "書いたコースの学生に届きます。ほかのコースの学生に届くのは、著者以外の査読承認を受けた後です"
    "（この部品の論文を編集できる別の教員が、もう一人の査読者として査読できます）。"
)
#: IK-1023: 教員の説明に著者以外ができる操作は「もう一人の査読者として査読する」だけ（403 の文）。
EXPLANATION_PERSONAL_SECOND_REVIEW_ONLY = (
    "教員の説明に著者以外ができるのは、著者が査読して共有 ON にした説明への"
    "「もう一人の査読者として査読する」だけです（この部品の論文を編集できる教員に限ります）。"
)
#: IK-1023: 引用の応答 — 引用先のコースの学生に届くか（学習者向けの配信条件と同じ判定）。
CITATION_DELIVERED_FACT = "引用先のコースの学生にも、この説明が届きます。"
CITATION_NOT_YET_DELIVERED_FACT = (
    "引用先のコースの学生にはまだ届きません。届くのは、この部品の論文を編集できる著者以外の教員が"
    "もう一人の査読者として査読した後です（引用しただけでは届きません）。"
)
CITATION_NOT_APPROVED_FACT = "引用先のコースの学生にはまだ届きません（この説明は査読待ちです）。"
CITATION_RECORDED_FACT = "引用しました。"
#: IK-1154: 同じ説明を同じコースへ同じ意図で既に引用しているとき（新しい行を作らない）。
CITATION_ALREADY_RECORDED_FACT = (
    "この説明は、このコースへ同じ意図で既に引用しています。新しい引用の記録は作っていません（変更はありません）。"
)
#: IK-1154: 既に支持している説明に、もう一度支持したとき（行は 1 つのまま・人数は変わらない）。
ENDORSEMENT_REAPPLIED_FACT = (
    "この説明は既に支持しています。支持を付け直しました（段階: {level}）。支持の記録は 1 つのままで、人数は変わりません。"
)
#: IK-1154: 取り消していた支持を付け直したとき。
ENDORSEMENT_RESTORED_FACT = "取り消していた支持を付け直しました（段階: {level}）。"
#: IK-1061: 引用先のコースの教材（sources）がこの部品の論文を含まない。学生は部品の ⚓ から説明を
#: 読むので、公開条件を満たしていても届かない。
CITATION_NOT_IN_COURSE_FACT = (
    "ただし、引用先のコースの教材にはこの部品の論文が含まれていないため、"
    "そのコースの学生の画面にはこの説明は出ません。"
)
#: IK-1061: 引用先のコースの教材を確かめられなかった（届くとは言わない — fail-closed）。
CITATION_COURSE_UNCHECKED_FACT = (
    "引用先のコースの教材にこの部品の論文が含まれるかを確かめられなかったため、"
    "そのコースの学生に届くかはまだ言えません。"
)
#: IK-1126: 引用先のコースの教材に論文はあるが、どのトピックにもこの部品の ⚓ が置かれていない。
#: 学生は部品の ⚓ から説明を読むので、画面の道が無い（引用の記録は残る）。
CITATION_NO_ANCHOR_IN_COURSE_FACT = (
    "ただし、引用先のコースのトピックにはこの部品の ⚓ が置かれていないため、"
    "そのコースの学生の画面からはこの説明を開けません（引用の記録は残ります）。"
)
#: IK-1126: トピックの ⚓ を確かめられなかった（届くとは言わない — fail-closed）。
CITATION_ANCHOR_UNCHECKED_FACT = (
    "引用先のコースのトピックにこの部品の ⚓ があるかを確かめられなかったため、"
    "そのコースの学生に届くかはまだ言えません。"
)
#: IK-1126: 引用先のコースが公開されていないときの公開範囲の事実（受講者の有無は言わない）。
CITATION_COURSE_SCOPE_FACT = "引用先のコースの公開範囲は「{label}」です。"
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
    standard_reviewers: int | None = None,
) -> str:
    """説明が学生のどこに届くかの事実文（:data:`LEARNER_EXPLANATION_SCOPE_SQL` と同じ条件）。

    ``standard_reviewers`` は標準の説明のいまの承認の期間の査読者の人数（``None`` / 0 =
    説明カードからの査読の記帳なし）。
    """
    if review_status != "teacher_approved":
        return EXPLANATION_DELIVERY_NOT_APPROVED
    if kind == "standard":
        if standard_reviewers == 1:
            return EXPLANATION_DELIVERY_STANDARD_ONE_REVIEWER
        return EXPLANATION_DELIVERY_STANDARD
    cross = bool(shared) and bool(non_author_reviewed)
    if cross:
        return EXPLANATION_DELIVERY_CROSS_COURSE
    if not has_source_course:
        return EXPLANATION_DELIVERY_NO_SOURCE_COURSE
    if shared:
        return EXPLANATION_DELIVERY_SELF_REVIEWED
    return EXPLANATION_DELIVERY_OWN_COURSE_ONLY


#: IK-1077（第 9 波）: 共有されていない説明を別のコースで引用できないことの事実文（カードと 403 の両方）。
CITATION_NOT_SHARED_PERSONAL_NOTE = (
    "この説明は共有されていないため、別のコースで引用できません。共有を ON にできるのは著者だけです。"
)
CITATION_NOT_SHARED_STANDARD_NOTE = (
    "この説明は共有されていないため、別のコースで引用できません。"
    "共有を ON にできるのは、この部品の論文を編集できる教員です。"
)


def citation_blocked_note(*, is_standard: bool) -> str:
    """引用できない説明に添える 1 文（判定は呼び出し側 — ``cite_explanation`` の 403 と同じ条件）。"""
    return CITATION_NOT_SHARED_STANDARD_NOTE if is_standard else CITATION_NOT_SHARED_PERSONAL_NOTE


def citation_delivery_fact(
    *, review_status: str, delivered: bool, course_has_part: bool | None = True,
    part_anchored: bool | None = True,
) -> str:
    """引用の応答の事実文（IK-1023）。``delivered`` は :data:`LEARNER_EXPLANATION_SCOPE_SQL` を
    引用先のコースで評価した結果（判定はその SQL だけ — ここで条件を作り直さない）。

    ``course_has_part``（IK-1061）は引用先のコースの sources がその部品の論文を含むか。学生は
    部品の ⚓（コースの教材の論文の部品）からしか説明を読めないので、公開条件は必要条件にすぎない。
    ``False`` = 含まない（届かない）・``None`` = 確かめられなかった（届くとは言わない）。

    ``part_anchored``（IK-1126）は引用先のコースのトピックにこの部品の ⚓ があるか。論文が
    sources にあっても ⚓ が無ければ学生の画面に道が無い。``None`` = 確かめられなかった。
    """
    if course_has_part is False:
        return CITATION_NOT_IN_COURSE_FACT
    if course_has_part is True and part_anchored is False:
        return CITATION_NO_ANCHOR_IN_COURSE_FACT
    if review_status != "teacher_approved":
        return CITATION_NOT_APPROVED_FACT
    if course_has_part is None:
        return CITATION_COURSE_UNCHECKED_FACT
    if part_anchored is None:
        return CITATION_ANCHOR_UNCHECKED_FACT
    return CITATION_DELIVERED_FACT if delivered else CITATION_NOT_YET_DELIVERED_FACT


def citation_course_scope_fact(visibility: str | None) -> str:
    """引用先のコースが公開（全体）でないときの公開範囲の 1 文（IK-1126）。公開・不明は空文字。

    語は ``label_vocab.SHARED_SCOPE_LABELS``（コース管理の公開範囲の選択肢と同じ語）。受講者が
    いる・いないは言わない（受講の記録は公開範囲と別に残りうる）。
    """
    from core.label_vocab import SHARED_SCOPE_LABELS

    value = str(visibility or "").strip()
    if not value or value == "public":
        return ""
    label = SHARED_SCOPE_LABELS.get(value)
    return CITATION_COURSE_SCOPE_FACT.format(label=label) if label else ""
