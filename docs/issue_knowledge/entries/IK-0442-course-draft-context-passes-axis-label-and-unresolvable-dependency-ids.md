---
id: IK-0442
title: "コース内容の生成プロンプトで、式の「掲載節」に図の軸ラベル（B-field strength (mG)）が入り、部品の依存先に参照一覧に無い ID（comp_003__r1）がそのまま並ぶ"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: コース内容の生成が、式の掲載節と部品の依存関係を根拠候補として生成モデルへ渡す
  layers: [course_builder]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [contract]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸: _collect_structured_content は式の section_id を文書構造の節見出しへ引くだけで、見出しとして妥当かを
    見なかった（section_title_rejection_reason は章の題名にしか使っていなかった）。第 9 周の req 00232 の式 eq_15 の
    section_label が「B-field strength (mG)」（図の軸ラベル）。_component_dependency_refs も component_refs を
    そのまま targets に入れ、req 00240 / 00241 は参照一覧に無い「comp_003__r1」（精緻化の子部品の ID）、00236 は
    comp_001 / comp_002 を並べた（input_handling）。接続軸: 上流（文書構造・部品組み立て）の出力の中身が、下流の
    入力契約（見出し・参照可能な ID）を満たすかを境界で検査していない（contract。medium: 上流を直すべきとも
    読める — A層の artifact には触れない方針で下流に置いた）。
generalization:
  level: general
  general_form: 上流の欄の名前（節・依存先）を信じて、中身が下流で参照・表示できるかを見ずに渡す
pattern: unchecked-type-contract-at-boundary
discovery:
  perspective: [data_inspection]
  note: 第 9 周のコース内容の生成プロンプト（req 00232 / 00236 / 00240 / 00241）の content_blocks を読んだ。
resolution:
  perspective: [fail_closed, guardrail_fix]
  note: >-
    section_title_rejection_reason に「末尾の括弧が単位の記号だけ」の軸ラベル判定（axis_label。大小区別の単位表・
    略語「(SM)」は残す）を足し、式の掲載節は見出しとして妥当なものだけ載せる。依存先は組み立て時に同じ論文の中で
    解決できた名前を target_labels として添え（保存形の targets は不変）、プロンプトの写しでは参照一覧に無い ID を
    外して名前（target_names）だけを残す。名前も無い依存先は ID ごと外す。
  landed_in:
    - backend/core/course_content_builder.py
    - backend/tests/test_ik0438_0443_course_draft_context.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場でのコース内容の再生成（第 9 周の t3 / t11 のプロンプトから軸ラベルと comp_003__r1 が消えるか）
      - 単位表に無い独自の単位表記を括弧に持つ軸ラベルは判定しない
      - 学習画面・原稿スタジオの依存関係の表示には target_labels を配線していない（プロンプトの写しだけ）
related: [IK-0370, IK-0379, IK-0429]
view_of: []
history: []
---

## 課題

見出しでない節名と、参照できない依存先 ID が根拠候補として渡っていた。

## 発見の観点

生成モデルが受け取った根拠候補の式と部品を 1 件ずつ読んだ。

## 解決の観点

節名は見出しとして妥当なものだけ、依存先は解決できた名前か参照一覧にある ID だけを渡す。

## 一般化

上流の欄の名前を信じると、表示・参照できない中身が下流に届く。
