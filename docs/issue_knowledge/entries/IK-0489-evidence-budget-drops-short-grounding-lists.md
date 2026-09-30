---
id: IK-0489
title: "根拠候補の文字数予算の間引きで、数件しかない grounding_facts / reconstructed_equation_ids / linked_* と注記が、長い content_blocks より先に丸ごと落ちていた"
status: resolved
recorded_at: 2026-09-29
resolved_at: 2026-09-29
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: コース内容の生成が、根拠候補を文字数予算に収めつつ、根拠の有無・復元式・トピックに結びついた ID の事実をモデルに渡す
  layers: [course_builder]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [information]
    governance: [budget]
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
    統制軸: 汎用の間引き（_pop_from_longest_list）は「要素数の多いリスト」の末尾から落とす。content_blocks の中身を落とし進めると、5 件の grounding_facts や数件の linked_* が相対的に最長になって先に消え、保護は available_references だけだった。処理軸: 価値（読み方を決める短い事実）ではなく要素数で落とす順を決めていた。
generalization:
  level: repo_pattern
  general_form: 予算の間引きを要素数の多さで決め、短いが読み方を決める事実を先に落とす
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [data_inspection]
  note: 再生成プロンプト 20 本の根拠候補のキーを数え、_omitted の付いた 8 本で落ちたキーを並べた。
resolution:
  perspective: [order_and_budget]
  note: >-
    _prompt_json に protected_keys を足し、根拠候補（_evidence_prompt_json）は EVIDENCE_PROMPT_PROTECTED_KEYS（available_references・grounding_facts・reconstructed_equation_ids・linked_equation_ids / claim_ids / component_ids・3つの注記）を保護する。保護したキーは切り詰めず、ほかを落とし尽くした最終段でだけ落ちる。他のプロンプト（コース全体・前後関係・下書き）の保護は従来どおり。
  landed_in:
    - backend/core/course_content_builder.py
    - backend/tests/test_ik0484_0489_course_draft_regen12.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場でのコース内容の再生成
      - 保護した分だけ content_blocks（式の本体・部品）が先に削られる — 必要な式の本体が落ちないかは IK-0439 の keep_predicate に頼る
related: [IK-0439, IK-0379, IK-0461, IK-0460]
view_of: []
history: []
---

## 課題

予算の間引きで短い事実の一覧が先に消えていた。

## 発見の観点

_omitted の付いたプロンプトで落ちたキーを並べた。

## 解決の観点

読み方を決める短い事実のキーを保護し、最後まで残す。

## 一般化

予算で落とす順は要素数ではなく、読み方への寄与で決める。
