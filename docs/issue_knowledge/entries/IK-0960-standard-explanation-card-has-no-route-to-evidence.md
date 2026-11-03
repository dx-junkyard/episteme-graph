---
id: IK-0960
title: "標準の説明のカードが本文（部品の要約）だけを持ち、根拠の主張・原文へ渡る導線が無く、精読する教員が支持の判断をできない"
status: resolved
recorded_at: 2026-10-25
resolved_at: 2026-10-25
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "教員が他の教員・標準の説明を原文と照らして支持・引用する"
  layers: [endorsement_c, lecture_studio, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [review]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 information: backend/api/routes/theory_components.py:4919-4934 の _ensure_standard_explanation は
    title・body・review_status だけを入れ、部品が持つ evidence_claims（根拠の主張）を backing_claims に運ばない。一覧の応答の backing_claims
    は空で、admin-lecture-studio.js の説明カードは根拠を描けない。統制軸 review: 支持（承認の厚み）の判断材料が弁の位置に無い。
generalization:
  level: repo_pattern
  general_form: "派生物を作るときに、元が持つ根拠の参照を運ばず、派生物を判断する人が原典に戻れない"
pattern: upstream-loss-masked-downstream
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    几帳面型の te-03 が標準の説明を開き「原文のどこから来たのかを開く導線がこのカードに無い。根拠を原文と照らせないので支持はしない」と記した（uxsim/runs/c-astro-r25-share-
    r4/20261024T233620Z-65949 seq3）。応答の backing_claims と部品の evidence_claims を突き合わせた。
resolution:
  perspective: [carry_through]
  note: >-
    標準の説明に根拠への導線を置いた。説明一覧の応答（routes/theory_components.py list_component_explanations）で、標準の説明に部品の evidence_claims を theory_claims_live に読み時解決した主張（claim_id と本文の抜粋）と document を載せ、解決できなければ事実文 evidence_note。admin-lecture-studio.js の標準の説明のカードに根拠の主張の抜粋と［根拠を開く］（Deliberation.openElement の theory_claim・アンカー lecture-studio.explanation-evidence-open）。backing_claims（説明への AI 候補の紐づけ）とは別のキーで、確定の意味は持たせない。
  landed_in:
    - "backend/api/routes/theory_components.py"
    - "frontend/public/js/admin-lecture-studio.js"
    - "docs/manual/teacher/14-admin-lecture-studio.md"
    - "backend/tests/test_share_round25_r4_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "evidence_claims が agent ID で保存された部品での解決（legacy_ids 照合）"
      - "実画面での［根拠を開く］から要素文脈"
related: [IK-0923, IK-0924]
view_of: []
history: []
---

## 課題

症状: 標準の説明のカードから根拠へ渡れず、支持が保留される。原因: 標準の説明の生成時に根拠の参照を運んでいない。

## 発見の観点

再現（te-03 の (b2)）と経路の追跡。

## 解決の観点

是正済み（第 25 周・面 A 第 4 波・2026-10-25）。

旧案: 是正案: 標準の説明の一覧の応答に、部品の evidence_claims を読み時に解決した根拠（主張の本文・原文の位置）を載せ（保存列を増やさず読み時投影）、カードに［根拠を開く］（既存の要素文脈・深く検討へ）を足す。独自解釈は backing_claims の候補を教員が確定する既存の経路のまま。

## 一般化

上流で落ちた参照を下流が隠す型。確かめ方は、標準の説明のカードから根拠の主張を開き、原文の箇所に着けることを画面で見ること。

## 第 5 周の再演（第 25 周・面 A 第 5 周、2026-10-27）

第 5 周の再演: 消滅（表示）。標準の説明に「この説明（部品の要約）の根拠の主張:」と各行［根拠を開く］。押した先（深く検討）は未評価。根拠の主張の本文が記号の抜けた英語なのは IK-0665 / IK-0571。run: uxsim/runs/c-astro-r25-share-r5/20261027T070623Z-23499 seq2
