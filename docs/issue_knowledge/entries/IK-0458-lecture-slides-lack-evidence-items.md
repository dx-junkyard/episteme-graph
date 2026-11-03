---
id: IK-0458
title: "レクチャー（スライド）表示は evidence_items を持たない疑似チャンクで教材を描くため、引ける ![[claim|component|source:…]] まで「未解決」カードに内部 ID を出す"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: レクチャー再生が、受講画面と同じレンダラでトピック教材をスライド1枚ずつ描く
  layers: [lecture_player, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸: app.js renderLectureStage は {text, formulas, figures} の疑似チャンクを renderMaterialChunk に渡し、evidence_items を
    渡さない。LectureSegment / LectureSlide にも evidence_items が無い（schemas.py）。受講画面では引ける根拠の埋め込みが、
    レクチャーでは kind:id の「未解決」カードになる（information）。砂場の t15 のスライドには ![[claim:claim_span_…]] が
    複数残っていた。
generalization:
  level: repo_pattern
  general_form: 同じレンダラを使う 2 つの画面の片方にだけ、解決の材料が届いていない
pattern: available-but-unwired
discovery:
  perspective: [trace_walk]
  note: build_topic_slides の display_text に残る埋め込みと、レクチャーの疑似チャンクが持つフィールドを突き合わせた。
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: >-
    LectureSegment に evidence_items を足し、routes/lecture.py の _build_topic_draft_segment が受講画面と同じ
    build_topic_evidence_items の投影を載せる（再実装しない）。引けない ![[component|claim|source:…]] は
    drop_unresolved_evidence_embeds でスライドの表示本文から外す（スライド分割の後に掛けるので、build_topic_slides の
    ページ境界・slide_index・読み上げ原稿は変わらない）。app.js はデッキに evidence_items を引き継ぎ、renderLectureStage が
    疑似チャンクに evidence_items と drop_unresolved_embeds を渡す。renderMaterialChunk は drop 指定のとき、
    renderMaterialMissingEmbed の出力と完全一致する未解決カードを取り除く（受講画面の挙動は不変）。
  landed_in:
    - backend/api/routes/lecture.py
    - backend/api/schemas.py
    - frontend/public/js/app.js
    - frontend/public/index.html
    - backend/tests/test_lecture_evidence_items.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場の受講画面（ブラウザ）でのレクチャー表示
      - drop 指定の実評価（Node で renderMaterialChunk を走らせる検査）はこの環境に node が無く skip した
      - チャンク経路（トピック教材を持たないトピック）のレクチャーは evidence_items を持たないまま
related: [IK-0455]
view_of: []
history: []
---

## 課題

レクチャーの描画に解決の材料が渡らず、引ける根拠まで内部 ID の未解決カードになる。

## 発見の観点

スライドの本文に残る埋め込みと、描画に渡すフィールドを突き合わせた。

## 解決の観点

受講画面と同じ解決の材料（evidence_items）をレクチャーの DTO に同じ関数から載せ、引けない埋め込みは表示から外す。

## 一般化

レンダラを共有する画面には、同じ解決の材料を同じ形で渡す。
