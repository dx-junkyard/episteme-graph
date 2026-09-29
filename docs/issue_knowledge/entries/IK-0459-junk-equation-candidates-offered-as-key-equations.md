---
id: IK-0459
title: "授業用ドラフトの材料に arXiv の見出し行・図の軸ラベル・天体名・番号だけの空の式・本文の文・文字化けの断片・1つの式が割れた断片が「式」として載り、参照一覧・「重要な数式」・付録に内部 ID で出ていた"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: コース内容の生成が、トピックに結びついた式を授業用ドラフトの材料・教材の付録・確認問題の要件として差し出す
  layers: [course_builder, pipeline_a]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸: 解析は PDF の文字層から式らしい区画を式の候補として取り込み、授業用ドラフト側の判定は IK-0429 の軸目盛り・短い代入だけだった（content_blocks の式にしか掛けず、evidence_links の式は素通り）。式かどうかを区画の出自（式の候補として取り込まれた）で決め、中身の形で確かめていなかった（meaning）。接続軸 medium: A層の semantic_kind は「式ではない」と自由文で書いていたが、自由文は判定に使わない方針なので構造では受け渡されていない。
generalization:
  level: repo_pattern
  general_form: 取り込み段の候補を「その種類の実体」として後段が差し出し、中身の形で確かめない
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [data_inspection, trace_walk]
  note: 再生成プロンプト 20 本の available_references と content_blocks の式の本文を1件ずつ読み、式でないものの出現を数えた。
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: >-
    is_junk_equation_candidate（見出し行・軸ラベル・空の式・文字化け・関係記号の無い複数語・本文の文）と junk_equation_candidates（同じ頁で区画番号が近い復元なしの断片が3件以上続く並び）を1つの判定にし、_unpresentable_equation_ids が content_blocks と evidence_links の両方の式に掛ける。参照一覧・linked_equation_ids・付録・要件・「重要な数式」の行（内部 ID だけで本体の無い行と空になった見出しを含む）から外し、プロンプトに excluded_equations_note（理由の語だけ）、course_content_status.extra に excluded_equation_candidates（topic_id / equation_id / reason）を残す（P4）。本文中の短い式候補（J=3–2 / w0）は IK-0454 の経路が本文へ書くので落とさない。
  landed_in:
    - backend/core/course_content_builder.py
    - backend/core/label_vocab.py
    - backend/tests/test_ik0459_0465_course_draft_regen11.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場でのコース内容の再生成（新しい材料で生成モデルが外した式を発明しないか）
      - 保存済みの content_blocks と学習者向けの教材本文に既に書かれた埋め込みは再生成まで残る
      - 本文中の短い式候補（z < 2 / wa = 0）は式として差し出され続ける（IK-0454 と両立させるため）
related: [IK-0429, IK-0438, IK-0440, IK-0443, IK-0454]
view_of: []
history: []
---

## 課題

式でない区画が式として授業用ドラフトの材料・教材の付録に出ていた。

## 発見の観点

再生成プロンプトの式の本文を読み、見出し行・軸ラベル・空の式が埋め込み可能な式として並ぶのを見た。

## 解決の観点

式の候補を中身の形で確かめる判定を1つにし、式を差し出す全ての場所で同じ判定を使う。外した事実は理由付きで残す。

## 一般化

候補を差し出す前に、その種類の実体であることを中身の形で確かめる判定を1箇所に置く。
