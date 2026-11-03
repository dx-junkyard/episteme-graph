---
id: IK-0864
title: "ナレッジライブラリの空の状態が読めない — 同一性の候補ゼロの事実文をサーバと画面が二重に出し、見送り済みを含めても同じ文のままで、候補がどこで作られるか（解析の最後の段）への道も無い。地図との対応の導出はサーバが返す結果の事実文（索引が無いので意味の近さは照合していない・候補なし）を画面が捨てて一覧に戻るだけ"
status: resolved
recorded_at: 2026-10-22
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
  - "docs/features/concept_registry_design.md"
feature_context:
  realizing: "教員がナレッジライブラリで同一性の候補・地図との対応の候補が無い理由を読み、次の一手を決める"
  layers: [concept_registry, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [aggregation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 information: 地図との対応の導出 POST /api/admin/library/atlas-links/derive は facts（「この地図のベクトル索引が
    まだ作られていないため、意味の近さでは照合していません」「新しい対応の候補はありませんでした」）を返すが、
    frontend/public/js/admin.js:3075 の .then(function () { … }) は応答本文を受け取らず一覧の再読込だけをする。
    構造軸 aggregation: 同一性の候補ゼロの文は backend/api/routes/library.py:1133 の facts と admin.js:2965 の固定文の
    2 箇所にあり、両方が描かれる（:2961-2965）。include_dismissed=true でもサーバの文は「確認をお待ちしている…」のまま
    （:1132-1133 は include_dismissed を見ない）。候補を作るのは解析の最後の段 identity_candidates
    （backend/core/document_pipeline/orchestrator.py:2954）だが、空の状態にその事実も再実行の導線（教材行の
    「パイプラインを実行 ▼」）も無い。
generalization:
  level: repo_pattern
  general_form: "空の結果の理由をサーバが事実文で返しているのに、画面がそれを捨てるか自前の固定文と並べて出すため、空の理由と次の一手が利用者に届かない"
pattern: available-but-unwired
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    教員 te-01 が「同じ文が 2 回」「見送り済みを含めても変わらない」「導出後に結果が出ない」「なぜ候補が無いか書いていない」と
    記した。応答の facts と admin.js の描画を突き合わせた。run: uxsim/runs/c-astro-r25-share-r1/20261022T123625Z-33222 seq2-3・
    20261022T125310Z-41522 seq2-3・seq11-12。
resolution:
  perspective: [canonical_source]
  note: >-
    同一性の候補の空の文言を library.py の facts に一本化（見送り済みを含めたかで文を分け、候補の出所と「パイプラインを実行 ▼」からの作り直しを添える）し、admin.js の固定文を消した。地図との対応の導出結果の facts をボタンの下（#library-atlas-derive-facts）に描く。
  landed_in:
    - "backend/api/routes/library.py"
    - "frontend/public/js/admin.js"
    - "frontend/public/admin.html"
    - "backend/tests/test_concept_registry_ui_static.py"
    - "backend/tests/test_share_round25_fixes.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "空の状態の文言の正本をサーバの facts 1 つにした。"
  verification:
    methods: [guardrail]
    unverified:
      - "画面に見送り済みを含める操作は無い（API の include_dismissed だけが文を分ける）"
related: [IK-0851]
view_of: []
history: []
---

## 課題

症状: 同一性の候補ゼロの文が 2 回出る。見送り済みを含めても文面が変わらず、操作が効いたか分からない。地図との対応を導出
しても、候補ができたか・なぜできないかが出ない。原因: サーバの事実文を画面が捨てる／自前の固定文と並べる。

## 発見の観点

教員ペルソナの通し受講（reproduction）と、応答と描画の突き合わせ（trace_walk）。

## 解決の観点

未解決。文言の正本をサーバの facts に一本化し、画面はそれを描くだけにする。分野の地図タブの空の結果（IK-0851）と同じ族。

## 一般化

空の理由を返す API と描画の間で文が捨てられる型。確かめ方は、候補ゼロ・見送り済みのみ・索引なしの 3 状態で操作し、
サーバの facts がそのまま 1 回ずつ画面に出ることを見ること。
