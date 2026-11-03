---
id: IK-1081
title: "生成の規則を直して（作業の記録・内部の番地を学生の欄に書かない — IK-0948）も、それより前に保存した教材本文・注意点に残った作業メモ（「紐づく主張（claim）6件は本文が根拠候補に含まれていないため、教材には埋め込んでいません」）と内部 ID（eq_blk_* / claim_span_*）はそのまま学生と教員に届き、付録の古さのような検出も付け直しの道も無い"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
feature_context:
  realizing: "コース内容生成で作った授業用の教材を、学生が読む本文として届ける（教員宛ての作業の記録は学生に届けない）"
  layers: [course_builder, lecture_studio, learner_experience_b]
classification:
  axes:
    processing: [none]
    structure: [responsibility]
    connection: [version]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確定（砂場 DB の版 5 の写しで読んだ）: t2 と t4 の student_material に「> 注: 紐づく主張（claim）6件は本文が根拠候補に含まれていないため…」が
    ある（course_content_status.updated_at 2026-09-28 の生成物）。第 3 波で `TOPIC_DRAFT_FIELD_RULES` に「作業の記録…は注意点にも教材欄にも
    書かない」を入れたが、規則は新しい生成と AI 書き換えにしか効かない。付録には `topic_appendix_is_stale` という保存物の古さの検出と
    ［付録を付け直す］があるのに、本文・注意点の作業メモには `split_cautions_and_handoff` が書き換えの経路で効くだけで、保存済みの物を
    見分ける段が無い。t16 の保存済みの注意点にも eq_blk_003_0065 / claim_span_* が残る（IK-0948 の保存済みの側）。構造軸 responsibility
    （規則の責務が生成の時点にしか無い）。接続軸 version（旧い規則の版で作った保存物が新しい規則の下で配信され、版の差を見分ける印が無い）。
generalization:
  level: repo_pattern
  general_form: "生成の規則を是正しても、是正の前に保存された生成物は旧い規則のまま配信され、旧さを見分ける段も付け直す道も無い"
pattern: stale-derivative-served
discovery:
  perspective: [trace_walk, data_inspection]
  note: >-
    第 25 周 面 D 第 9 周。st-01 が t2（DCF 法）を開き「最後の注に『紐づく主張（claim）6件は…』とあるが、学生の自分には何のことか分からない
    （先生向けのメモに見える）」（C 所見）。te-01 の t16 の注意点に eq_blk_003_0065 / claim_span_* がそのまま（A / C 所見）。
    run: uxsim/runs/c-astro-r25-studio-r9/20261031T015559Z-39886 seq17・18・19、uxsim/runs/c-astro-r25-studio-r9/20261031T015111Z-37232 seq6。
    審判が版 5 の写しの全トピックを走査し、t2・t4 の本文に同型の注を見つけた。
resolution:
  perspective: [canonical_source, single_point_fix]
  note: >-
    第 25 周 第 9 波（2026-10-31・保存物の自動書き換えはオーナー判断で禁止）: 作業メモの判定を `course_content_builder.is_work_note_text`
    1 つにし（内部 ID・材料の欄名に加えて「根拠候補」「（claim）」「埋め込ん/み/ま」「本文が供給/読め/載って」「教員が」）、生成時の
    `split_cautions_and_handoff` も同じ表を通す。本文は「注」の段落（`> 注:` / `注：` / `※`）で作業メモと判定したものだけを
    `split_work_note_paragraphs` が外し、受講者向け本文の正本 `core.lecture.topic_student_material`（受講画面・講義・音声・readiness・
    プレビューが通る。routes/learning.py の重複実装は委譲）で読み時に遮断する。教員には topic の `work_note_state.message` と
    コース見出しの `work_note_course_notice` + 題名の一覧で「古い生成規則の作業メモが残っています（受講者には表示していません）」と言う。
    書き換えのプロンプトに渡す下書きも `_rewrite_prompt_draft` で注の段落・注意点の作業メモを外し、付録のある下書きはいまの規則で
    付け直した形を渡す（製品頭脳 A の所見: 内部 ID・定型文「中心的な関係式です」の写し）。
    第 10 波（2026-10-31・第 10 周の残り）: ①判定の語に生成の過程を語る語（「誤抽出」「誤って…取り込」「取り込まれたもの」
    「教材/本文/下書きに…載せ/含め/入れていません」）を足し、t1 の「座標目盛りが誤って式として取り込まれた…教材には載せていません」も
    読み時に外す（地の文「電子が取り込まれた後」「高温の領域には載せられません」は作業メモにしない — 単体テスト）。②全 20 トピックに
    注意点の作業メモが実在する（砂場の HEAD を読み取りで確認）ので一覧の母集合は変えず、残っている場所で 2 つに分けた
    （「本文の「注」の段落に作業メモが残るトピック」/「注意点だけに作業メモが残るトピック」・件数なし・`work_note_topics[].places`）。
    見出しの文は「注意点は受講者の画面に出ない欄です」「システムは保存済みの本文・注意点を書き直さない」を言う。まとめて外す手は
    オーナー判断（保存済み本文の自動書き換え禁止）に触れるので作らない。③注意点の「…埋め込んでいません」が言う図・式（UUID の先頭
    8 桁・`eq_…`）がいまの本文に埋め込まれていれば、`work_note_state.embed_conflict_note`「このメモは本文より前の生成時の記録で、
    いまの本文とは食い違っています」をトピックの区画に描く（`work_note_embed_conflicts`・読み時・決定論。砂場の写しで t7 / t12 / t13 /
    t14 / t17 が該当）。
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "判定を 1 関数に置き、生成時の後始末・受講者の読み時・教員の事実文・書き換えの材料の 4 経路が通す。"
    - principle: sync-by-stable-key-and-protect-human-decisions
      use: consulted
      note: "保存物（教員の確定）を書き換えず、読み時の投影と教員への事実で扱う。"
  landed_in:
    - "backend/core/course_content_builder.py"
    - "backend/core/lecture.py"
    - "backend/core/lecture_studio_state.py"
    - "backend/api/routes/lecture_studio/topics.py"
    - "backend/api/routes/learning.py"
    - "frontend/public/js/admin-lecture-studio.js"
    - "backend/tests/test_lecture_studio_round25_r9.py"
    - "backend/tests/test_lecture_studio_round25_r10.py"
  verification:
    methods: [guardrail, scratch_db]
    unverified:
      - "地の文の中の「根拠候補」（t9 / t13 / t19 の本文）は注の段落ではないので外していない（教員の一覧にも出ない）"
      - "学習者向けコース DTO（GET /api/learning/courses/{id}）の topics[].student_material / cautions は生のまま（画面は描かない・GET→PUT の往復で保存物を変えないため射影しない）"
      - "砂場の固定コースで学生の t2 / t4 の本文から注が消えることは実機で見ていない（版の写しに読み時投影がかかる経路は審判の計算のみ）"
      - "第 10 波: t1 の注が学生の本文・講義から消えることは砂場の HEAD の写しを判定関数に通して確かめただけ（実機の再演は第 11 周）"
      - "第 10 波: 地の文に残る「根拠候補」（t7 / t9 / t13 / t19）は注の段落ではないので受講者に届く（地の文を読み時に削るかは別の判断）"
related: [IK-0948, IK-0461, IK-1054, IK-0873]
view_of: []
history: []
---

## 課題

症状: 学生の教材本文に教員宛ての作業メモ（内部語 claim・件数）が出る。教員の注意点にも内部 ID が残る。

原因: 規則の是正は生成の時点にしか効かず、是正の前に保存された本文・注意点を見分ける段が無い。

## 発見の観点

学生の受講を歩き（trace_walk）、保存済みの版の写しを全トピック走査した（data_inspection）。

## 解決の観点

第 25 周 第 9 波で是正（resolution 参照）。付録の古さの検出と同型の「旧い規則の名残」の検出と付け直しの道。

## 一般化

生成規則を変えたら、変える前に保存された物の扱い（検出・付け直しの道）を同じ波で決める。確かめ方: 是正の前に作った保存物を持つコースで、規則が禁じる文が学生に届かない・届くなら所有者に見えることを見る。

## 第 10 周（第 25 周・面 D 第 10 周、2026-10-31・judge_studio_r10）

**一部 GONE・一部 STILL**。①GONE: t2 の「紐づく主張（claim）6件は本文が根拠候補に…」は学生の本文・講義から外れた（版 7 の写しの raw には残り `topic_student_material` に無いことを計算で確認）。教員側はコースの見出しに作業メモの一覧と事実文、トピックの区画に `work_note_state` の文が出る。②STILL: t1 の本文の注「> 注: このトピックに紐づく『式』は図の座標目盛り（赤経・赤緯）が誤って式として取り込まれたもので、数式として使えないため教材には載せていません。」は判定語（根拠候補 / （claim） / 埋め込 / 本文が / 教員が）に当たらず、学生の本文と講義の最後の枚に届く（st-01 が「作った人向けのメモ」と読んだ）。③外す手がトピックごとの手編集・保存だけで、砂場の固定コースでは**全 20 トピック**が一覧に載る（見出しに常設）。注意点を変えずに保存しても一覧から外れない（t17 の保存の後も残る）。④付け直した付録・本文に図が埋め込まれているのに、注意点が「図 d687f713-… は…埋め込んでいません」「figure 97fbef7c-… は…埋め込んでいません」と逆を言う（t17・t12。作業メモの事実文は区画に出る）。run: uxsim/runs/c-astro-r25-studio-r10/20261031T053843Z-70580 seq13〜19、uxsim/runs/c-astro-r25-studio-r10/20261031T052922Z-65584 seq4、uxsim/runs/c-astro-r25-studio-r10/20261031T053217Z-67272 seq6・9・13。

## 第 11 周（第 25 周・面 D 第 11 周、2026-10-31・judge_studio_r11）

**t1 GONE（実機）・t4 GONE（計算のみ）・教員の 2 区分と食い違いの事実文 GONE／STILL（小）**。①st-01 の t1（版 8）の本文と講義 2 / 2 の最後の枚に「座標目盛り…取り込まれたもの…載せていません」の注が無い（第 10 周は残っていた）。②t4 は経路で開かれず、版 8 の写しを `topic_student_material` に通して注が学習者に出ないことだけ確かめた。③原稿スタジオの見出しの一覧が「本文の『注』の段落」3 件（1.2 / 1.3 / 1.5）と「注意点だけ」17 件の 2 区分になり、t17 の区画の `embed_conflict_note` で te-01 が作業メモの行（図 d687f713-…）を特定した。④STILL: t18 の区画は「このトピックの注意点に…作業メモが残っています」と言うが 4 行のどれかを示さず、te-01 は「ふつうの注意書きに見え、どれが作業メモか見分けがつかない」（判定に当たるのは 4 行目「根拠候補に数式・主張・図の参照が無いため…」）。run: uxsim/runs/c-astro-r25-studio-r11/20261031T102539Z-4317 seq13〜16、uxsim/runs/c-astro-r25-studio-r11/20261031T101202Z-98537 seq4・6・13。
