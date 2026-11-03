---
id: IK-0935
title: "複数の論文を束ねたコースの議論の開幕で、「この論文が確かめていないこと」区画がコース全体の台帳由来の前提を、すべての論文を並べ終えた後の 1 か所にまとめて出すため、見出しは単数の「この論文」なのにどの論文の前提か分からず、学生は 1 本目の論文の範囲で区画を見つけられない"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.3"
  - "docs/features/discussion_mode_design.md"
  - "docs/features/discuss_opening_authoring_design.md"
feature_context:
  realizing: "学生が議論の開幕で、開いた論文が確かめていないことを最初に読む"
  layers: [discuss, doubt_d, frontend_learning_ui]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [target]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 target: discuss.js は論文ごとのループの外で renderPaperUnverifiedSection(fragile, …) を呼び（945 行・コメント「D層台帳由来（コース全体の前提）は
    document に紐づかないので docs ループの外に出す」）、見出しは「この論文が確かめていないこと」（797 行）。台帳の行は IK-0899 以降 document_id を持つので、論文ごとに
    振り分ける材料は存在する。処理軸 wording: 単数の「この論文」がコース全体の一覧に付く。構造軸 none。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "親ごとに並べた画面の末尾に、親を問わない集約を親の単数形の見出しで置くため、利用者はどの親の項目か・どこにあるかを読み違える"
pattern: wording-mismatch
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    st-01 と st-04 が開幕で「この論文が確かめていないこと」を 1 本目（Cepheus）の範囲に見つけられず、2 本目以降の論文へ読み進んだ。区画の描画位置を discuss.js まで
    辿った（観察はハーネスの上限で区画まで届いていないので、区画の中身は読めていない）。run: uxsim/runs/c-r25-doubt-r3/20261023T033451Z-40732（seq13）/
    uxsim/runs/c-r25-doubt-r3/20261023T070217Z-13811（seq13）。
resolution:
  perspective: [carry_through]
  note: >-
    discuss 開幕の fragile_points の前提（kind=assumption）に台帳行の document_id を載せ（core/discuss/opening.project_fragile_points）、discuss.js が各論文の区画の中に「この論文が確かめていないこと」を置き、どの論文の区画にも入らない前提だけを末尾に、複数論文なら「このコースの教材全体で確かめていないこと」の見出しで出す。前提が 1 つも無いときの閉世界の一文は末尾に 1 回。
  landed_in:
    - "backend/core/discuss/opening.py"
    - "frontend/public/js/discuss.js"
    - "frontend/public/index.html"
    - "backend/tests/test_doubt_round25_wave3.py"
  verification:
    methods: [guardrail]
    unverified:
      - "4 本束ねたコースの開幕の実画面で各論文の範囲に前提が出ること（第 3 周は観察の上限で区画まで届いていない）"
related: [IK-0853, IK-0899]
view_of: []
history: []
---

## 課題

症状: 4 本の論文を束ねたコースの開幕で、学生は 1 本目の論文の問い・骨格・主張を読んだ後に「この論文が確かめていないこと」を探したが、
その範囲に区画が無く、次の論文へ進んだ。区画はすべての論文の後ろにまとめて置かれ、見出しは単数の「この論文」。

原因: 区画を論文ごとのループの外に置き、コース全体の一覧に単数の見出しを付けている。

## 発見の観点

学生 2 人に開幕の区画を探させ（再現）、描画の位置を JS まで辿った。

## 解決の観点

台帳の行が持つ document_id で論文ごとに振り分けた（carry_through）。（第 25 周 第 3 波・2026-10-23）

## 一般化

複数の親を並べる画面で、親に紐づく材料を親の外にまとめると、見出しの単数・複数と位置の両方で読み違える。確かめ方は、親が 2 つ以上
ある状態で画面を開き、各親の範囲に自分の項目が出ることを見ること。
