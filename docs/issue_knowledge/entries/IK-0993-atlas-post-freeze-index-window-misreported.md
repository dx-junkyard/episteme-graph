---
id: IK-0993
title: "凍結の直後、製品は新しい版の索引を裏で作り始めているのに、索引の状態は「索引は前の版のままです。［索引を再構築］で作り直してください」と手作業を促し、関係の候補は理由の一文なしに空になる"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/atlas_vector_anchoring_design.md"
  - "docs/features/atlas_relation_edges_design.md"
feature_context:
  realizing: "教員が凍結した直後に、索引と関係の候補が新しい版に追いついたかを分野の地図タブで確かめる"
  layers: [vector_anchoring_va, relation_edges_re, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [condition]
    governance: [ordering]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 condition: routes/atlas.py の凍結は best-effort の daemon thread で新しい版の索引を作り始めるが、vectors/status は行の版だけを見て VECTOR_INDEX_STALE（再構築を促す文）を返し、構築中という状態を知らない。routes/atlas_edges.py の候補は保存済みアンカーが無いと空タプルに縮退し、empty_notes を付けない。統制軸 ordering: 凍結 → 裏の構築 → 一覧の読み直しの順序の途中の状態に名前が無い。教員がその間に再構築を押すと embedding が二重に走る。
generalization:
  level: repo_pattern
  general_form: "非同期の後処理が走っている間の状態に名前が無く、状態表示は手作業を促し、依存する一覧は理由なしに空になる"
pattern: stale-derivative-served
discovery:
  perspective: [reproduction, trace_walk]
  note: "凍結の直後の再読み込みで審判 B が『候補の一覧が空なのに理由の文が無い』を出した（関係の候補）。同じ手の vectors/status は stale で再構築を促し、次の手（約 1 分後）には索引が 2026.2 で作られ候補が戻っていた。run: uxsim/runs/c-astro-r25-atlas-r5/20261027T070623Z-23509 seq 15 / 16。投影が凍結の手の後の区画を描かず、ペルソナは見ていない（harness_fixes_atlas.md H33）。"
resolution:
  perspective: [first_class_state]
  note: >-
    凍結が裏で始める索引の構築の途中に名前を付けた。core/atlas_vectors/builder.py に構築中の印（プロセス内・thread の finally で必ず外す）を置き、vectors/status は building と VECTOR_INDEX_BUILDING（手作業を促さない）を返し、構築中の refresh は二重に走らせず skipped（building_in_progress・事実文）を返す。関係の候補はアンカーが無いとき index_note（構築中 / 索引なし）を返し、画面は一覧の上に出す。凍結の応答の形は変えない（VA4）。
  landed_in:
    - "backend/core/atlas_vectors/builder.py"
    - "backend/api/routes/atlas.py"
    - "backend/api/routes/atlas_vectors.py"
    - "backend/api/routes/atlas_edges.py"
    - "backend/core/atlas_review_display.py"
    - "frontend/public/js/admin.js"
    - "docs/manual/teacher/17-admin-atlas.md"
    - "backend/tests/test_atlas_vectors_api.py"
    - "backend/tests/test_atlas_edges_api.py"
    - "backend/tests/test_atlas_review_round25_r5.py"
  verification:
    methods: [guardrail]
    unverified:
      - "複数プロセス（複数ワーカー）では他プロセスの構築は見えず、従来の文に縮退する"
      - "凍結直後の実時間（構築中 → 完了）の遷移は実機で見ていない"
related: [IK-0965, IK-0851]
view_of: []
history: []
---

## 課題

症状: 凍結 2026.2 の直後、索引の状態は「地図が新しい版になり、索引は前の版のままです。［索引を再構築］で作り直してください。」、
関係の候補は 0 件で理由の文が無い。約 1 分後に開き直すと、索引は 2026.2 で作られていて「作り直す必要はありません」、候補も戻っている。

原因: 凍結が裏で始める索引の構築を状態表示が知らず、候補の一覧はアンカーの不在を言わない。

## 発見の観点

審判 B（reproduction の審判）が空の一覧を拾い、応答と凍結の後処理を突き合わせた（trace_walk）。

## 解決の観点

第 5 波（2026-10-27）で解決。構築中の状態に名前を付け、状態表示と関係の候補が進行中を言う。

## 一般化

非同期の後処理には「進行中」の状態と、それを待つ一覧の空の理由を持たせる。確かめ方: 凍結の直後と完了後に索引の状態と関係の候補を読み比べる。



## 第 6 周の再演（第 25 周・面 C 第 6 周、2026-10-27・campaign c-astro-r25-atlas-r6）

GONE。凍結直後の索引の区画は「地図が新しい版（地図 版 2026.3）になったので、索引を作っています。作り終わると状態がここに出ます（作り直しの操作は要りません）。」、関係の候補は「新しい版の索引を作っているため、説明文どうしの近さによる関係の候補はまだ出していません。作り終わると、ここに戻ります。」と理由を言い、開き直しで「現在の公開版（地図 版 2026.3）の全項目を索引しています」へ移った（構築中 → 完了の遷移を 2 画面で観察）。審判 B の seq 18 の所見（候補が空なのに理由が無い）は index_note を読み落とした偽陽性（harness_fixes_atlas.md 第 6 周）。残り（軽）: 変更なしの再構築でも「最後に作成」の時刻が進み、作り直したのか確かめただけなのかが時刻だけでは読み分けられない。run: uxsim/runs/c-astro-r25-atlas-r6/20261027T122716Z-6544 seq 4 / 18 / 19。
