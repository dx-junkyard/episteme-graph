---
id: IK-0677
title: "配置の「根拠」に、論文原文の逐語と解析が作った日本語の文（中心の問い）が区別なく並び、教員は AI の言い換えを根拠にしていると読む"
status: resolved
recorded_at: 2026-10-09
resolved_at: 2026-10-25
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/knowledge_landscape_design.md"
  - "docs/features/release_review_flow_design.md"
feature_context:
  realizing: "教員がリリース前の確認で配置の根拠を開き、論文に支えられているかを判断する"
  layers: [knowledge_landscape, release_review, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [meaning]
    governance: [review]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（指揮者の DB 読み取り・te-03 レーン）: 「問い」「主題」観点の配置の evidence.quote は thesis の central_question 由来の日本語の生成文で claim_id が null。verbatim 検査は配置ステージに渡した素材（解析の生成文を含む）に対して行うので通る。画面は原文の逐語と同じ「根拠」の枠で出す。構造軸 representation = 根拠の出所（原文 / 解析の再構成）を表す印が無い。接続軸 meaning = 「根拠」が原文の意味で読まれる。
generalization:
  level: repo_pattern
  general_form: "根拠の照合先に生成された文が含まれるのに、照合を通った引用をすべて原文の引用と同じ顔で出す"
pattern: projection-mistaken-for-source
discovery:
  perspective: [reproduction, data_inspection]
  note: "第 23 周の砂場で、几帳面な教員が「英語の論文なのに根拠が日本語の一文。原文の引用ではなく AI が言い換えた問いに見える」と書いて却下した。"
resolution:
  perspective: [representation_change]
  note: >-
    推奨案で解決（LS4 の意味は原文の逐語に限定しない — 検査は緩めも狭めもしない）。配置の保存時に引用の出所を
    evidence[].source（claim = 主張の本文から / paper_summary = 解析が論文全体を再構成した文から）として記録し
    （core/landscape/schema.evidence_source_for・builder）、表示は source_label（label_vocab.LANDSCAPE_EVIDENCE_SOURCE_LABELS）を
    教員の位置づけモーダル・リリース前の確認・学習者の地図・出典タブがサーバの文を素通しで描く。出所の記録が無い旧行で
    claim_id も無い引用は「論文全体の記述からの引用（原文の逐語とは限りません）」とし、再構成文と断定しない。
    理由文の範囲の問題は IK-0967 で同時に解いた。
  landed_in:
    - "backend/core/landscape/schema.py"
    - "backend/core/landscape/builder.py"
    - "backend/core/landscape/projection.py"
    - "backend/core/label_vocab.py"
    - "frontend/public/js/admin.js"
    - "frontend/public/js/admin-release-review.js"
    - "frontend/public/js/landscape-layer.js"
    - "frontend/public/js/app.js"
    - "backend/tests/test_atlas_review_round25_r4.py"
  verification:
    methods: [guardrail]
    unverified:
      - "既存の配置行は再解析まで出所が未記録（旧行の印で表示）"
related: [IK-0571]
view_of: []
history: []
---

## 課題

症状: 根拠の枠に生成文と原文が混ざる。原因: 根拠の出所を表す印が無い。

## 発見の観点

ペルソナの通し受講（reproduction）と DB の読み取り（data_inspection）。

## 解決の観点

未解決。出所の印を足すか、根拠を原文に限るかはオーナー判断（推奨は印）。

## 一般化

照合先に生成物が混ざると、照合を通った引用が原文の権威を借りる。確かめ方は、根拠の照合先の構成を列挙し、生成物由来の根拠が画面で区別されることを見ること。

## 第 25 周 第 3 周の再観測（2026-10-23・campaign c-astro-r25-atlas-r3）

- 学習者の地図 L2 と出典タブで、英語論文（Cepheus B）の「多波長観測」の配置の根拠が日本語の文「偏光観測と分子線観測を
  用いて明らかにすること」として逐語の顔（かぎ括弧）で出た（run 20261023T070701Z-16885 seq 6 / 7）。教員側と同じ材料が
  学習者にも届いている。

## 第 25 周 第 4 周の再観測（2026-10-25・campaign c-astro-r25-atlas-r4）

- 学習者の L2 と出典タブで、多波長観測の根拠「偏光観測と分子線観測を用いて明らかにすること」が逐語の顔で出た（変化なし）。
  加えて、理由文が逐語より広いことを言う例（宇宙膨張: 理由は BAO と超新星・逐語は BAO だけ）を IK-0967 に分けた。
  run: uxsim/runs/c-astro-r25-atlas-r4/20261024T233738Z-85320 seq 6 / 7。

第 4 波（2026-10-25）: 推奨案（出所の印）で解決。引用ごとに出所の印、理由には「AIの要約」の印（IK-0967）。

第 5 周の再演（2026-10-27・campaign c-astro-r25-atlas-r5）: 言い方は解消。多波長観測の日本語の引用に「論文全体の記述からの引用（原文の逐語とは限りません）」と出所の印が付き、学生（st-09）は逐語と読み違えなかった。保存行が逐語でない事実そのもの（旧行の引用の作り直し）は残る — 再解析で置き換わる。run: uxsim/runs/c-astro-r25-atlas-r5/20261027T071720Z-78305 seq 6 / 7。
