---
id: IK-0664
title: "コースの「登録済み教材」が論文の題名ではなく arXiv 番号のまま出る（作成時の仮の題名の写しを、読み時の差し替えが仮の値と見なさない）"
status: resolved
recorded_at: 2026-10-09
resolved_at: 2026-10-28
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/backend/rag-chat.md"
feature_context:
  realizing: "学習者がコースを開き、登録済み教材の一覧から目当ての論文を見つける"
  layers: [learner_experience_b, frontend_learning_ui, course_builder]
classification:
  axes:
    processing: [logic]
    structure: [aggregation]
    connection: [version]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（砂場 DB の読み取り）: learning_courses.data.sources[].title は作成時の写しで「2605.26810v1」、material_id は「c6e9d0bc-3f8」、documents.title は題名の書き戻し（IK-0366/0419）後の正式題名。読み時の差し替え _with_resolved_source_titles は「保存値が空か material_id と同じ」ときだけ差し替えるので、arXiv 番号という別の仮の値を仮と見なさない。仮の題名の判定が documents 側（document_title_is_placeholder）とコース側で別々に書かれている。処理軸 logic = 差し替えの条件式。構造軸 aggregation = 「仮の題名」の判定が 2 箇所。接続軸 version = コースの写しが題名の書き戻しより古い世代のまま読まれる。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "元の値を更新しても、それを写した側が「仮の値」を別の規則で判定しているため、写しが古いまま表示され続ける"
pattern: stale-derivative-served
discovery:
  perspective: [reproduction, data_inspection]
  note: "第 23 周の砂場の再演で学生 3 人がそろって「資料の名前が番号だけ」と書き、砂場 DB の写しと documents.title を読み比べて差し替えの条件が効いていないことを確かめた。"
resolution:
  perspective: [canonical_source, carry_through]
  note: >-
    論文の呼び方の規則を core/document_refs に 1 つ置いた: choose_paper_titles（純関数）/ resolve_paper_titles（SQL）が ①その取り込みの読める題名 →
    ②閲覧できる同じ論文の別の取り込みの読める題名 → ③arXiv メタデータの写し（paper_discovery_arxiv_metadata_cache の読みだけ・外部 API なし）→
    ④保存値（番号）の順に決める（読める判定 title_is_readable は論文の海の代表選出と同じ title_is_arxiv_number）。学習者の「論文の位置」・出典タブの
    位置づけ（routes/landscape.py learner_landscape_for_documents(..., viewer_id=)・兄弟は list_visible_document_ids）と「登録済み教材」
    （services.resolve_course_source_titles は写しまで・routes/learning.py _with_resolved_source_titles は arXiv 番号の写しを仮の値と見なす。
    教員の読める題名は上書きしない）が同じ規則を通る。document_title_is_placeholder への委譲はしていない（コースの写しの仮の値は material_id と
    arXiv 番号で足りるため）。画面文脈アダプターの経路（viewer なし）は兄弟を引かない。
  landed_in:
    - "backend/core/document_refs.py"
    - "backend/api/routes/landscape.py"
    - "backend/api/services.py"
    - "backend/api/routes/learning.py"
    - "docs/features/field_atlas_overlay_spec.md"
    - "backend/tests/test_atlas_review_round25_r7.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "読める題名の判定を論文の海の代表選出と同じ関数に寄せ、地図・出典タブ・登録済み教材を同じ規則に通した。固有の差分: 兄弟から借りるのは閲覧できる取り込みだけ。"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場で学生の地図の「論文の位置」と出典タブが 4 本とも題名で出ること（次周の再演）"
      - "arXiv メタデータの写しが無く兄弟も見えない取り込み（番号のまま出る）"
      - "画面文脈アダプター（viewer なし）の位置づけの事実文は兄弟から題名を引かない"
related: [IK-0366, IK-0419, IK-0381]
view_of: []
history: []
---

## 課題

症状: コース画面の「登録済み教材」に 4 本中 3 本が arXiv 番号で出て、議論の開幕では同じ論文が正式題名で出る（同じ論文が 2 つの名前に見える）。原因: コースの写しの題名が作成時の仮の値のまま残り、読み時の差し替えがその値を仮と見なさない。

## 発見の観点

ペルソナの通し受講（reproduction）と砂場 DB の読み取り（data_inspection）。

## 解決の観点

未解決。仮の題名の判定を 1 箇所に寄せれば、写しの側も同じ規則で差し替わる。

## 一般化

写しを持つ側が元の側と別の規則で「仮の値」を判定すると、元を直しても写しが直らない。確かめ方は、元の題名を更新したあと写しを読む全画面で新しい題名が出ることを見ること。

## 第 7 周の再演（第 25 周・面 C 第 7 周、2026-10-28・campaign c-astro-r25-atlas-r7）

面 C 第 7 周でも観察（2026-10-28）: 学生の地図の「論文の位置」と出典タブ「分野の中の位置づけ」で、4 本のうち 3 本が arXiv 番号（2605.26810v1 / 2606.02318v1 / 2606.00411v1）、1 本が題名で出る。同じ学生が論文の海では 4 本とも題名で読む（面で呼び方が揃わない）。run: uxsim/runs/c-astro-r25-atlas-r7/20261027T172454Z-57525 seq 6 / 7、uxsim/runs/c-astro-r25-atlas-r7/20261027T173037Z-59717 seq 4。

## 第 7 波の是正（2026-10-28）

論文の呼び方の規則を core/document_refs に 1 つ置いた: choose_paper_titles（純関数）/ resolve_paper_titles（SQL）が ①その取り込みの読める題名 → ②閲覧できる同じ論文の別の取り込みの読める題名 → ③arXiv メタデータの写し（paper_discovery_arxiv_metadata_cache の読みだけ・外部 API なし）→ ④保存値（番号）の順に決める（読める判定 title_is_readable は論文の海の代表選出と同じ title_is_arxiv_number）。学習者の「論文の位置」・出典タブの 位置づけ（routes/landscape.py learner_landscape_for_documents(..., viewer_id=)・兄弟は list_visible_document_ids）と「登録済み教材」 （services.resolve_course_source_titles は写しまで・routes/learning.py _with_resolved_source_titles は arXiv 番号の写しを仮の値と見なす。 教員の読める題名は上書きしない）が同じ規則を通る。document_title_is_placeholder への委譲はしていない（コースの写しの仮の値は material_id と arXiv 番号で足りるため）。画面文脈アダプターの経路（viewer なし）は兄弟を引かない。

## 第 8 周の再演（2026-10-28・judge_atlas_r8）

**GONE（観察の範囲）**。出典タブ「分野の中の位置づけ」・地図の論文の位置・海の論文一覧の論文 4 本はすべて題名で、arXiv 番号との混在は出なかった（run uxsim/runs/c-astro-r25-atlas-r8/20261027T224229Z-98423 seq 7・20261027T224529Z-99914 seq 5）。
経路の残り: 同じ周の面 B のレーンで、学習チャットの出典名が『2605.26810v1』（arXiv 番号）のまま届いた（製品頭脳 A 4278・B 4277）。第 7 波の差し替えは地図の位置づけとコースの登録済み教材の題名で、チャットの出典（RAG の出典メタ）の題名は対象外だった可能性 — 面 B の審判が経路を確かめる（別エントリにせずここを参照）。
