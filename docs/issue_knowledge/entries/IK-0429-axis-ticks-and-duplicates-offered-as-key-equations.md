---
id: IK-0429
title: "コース内容の生成プロンプトの「重要な数式」に、図の座標軸の目盛り（22h58m00s・62°42'00\"）や式の断片（13CO J=3–2 の J=3）、同じ式の重複（同じ Horndeski 作用・N = 15 が別 ID で 2 件）が並ぶ"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: コース内容の生成が、トピックの根拠候補として式を生成モデルへ渡す
  layers: [course_builder]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [contract]
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
    処理軸: 根拠候補の組み立て（_topic_evidence_for_prompt）と付録（_required_equation_items）は、
    content_blocks の equations を中身を見ずにそのまま渡し、重複も畳まなかった（input_handling）。
    第 9 周の req 00152 では 6 件の「式」が図の軸の目盛り（raw_text だけ）で、生成モデル自身が
    assumptions に「軸ラベルの誤分類」と書いた。00144 は J=3、00162 は N = 15 が 2 件、00164 は同じ
    作用が \left( と \bigg( の違いだけで 2 件。接続軸: 上流（A層の式候補抽出）の「式」という出力の
    中身が、下流の「重要な数式」の入力契約（読んで意味のある式）を満たすかが境界で検査されない
    （contract。medium: 上流の抽出を直すべきとも読める — A層の artifact には触れない方針で下流に置いた）。
generalization:
  level: general
  general_form: 上流が付けた種別を信じて、中身を見ないまま下流の利用者に「その種別の代表」として渡す
pattern: unchecked-type-contract-at-boundary
discovery:
  perspective: [data_inspection]
  note: 第 9 周のコース内容の生成プロンプトの根拠候補（content_blocks の equations）を読んだ。
resolution:
  perspective: [fail_closed, guardrail_fix]
  note: >-
    プロンプトの材料（available_references の式・content_blocks の equations・linked_equation_ids・
    content の「重要な数式」の行）と付録・確認問題の要件から、座標軸の目盛り（時・分・秒・度・分・秒と
    裸の数値だけの本体）・4 文字未満の「X=N」断片・正規化した本体（空白と \left / \right / \bigg / \, 等の
    見た目だけの TeX を落とす）が先に出た式と一致するものを外した。保存する content_blocks（学習画面の
    [[FORMULA_N]] の位置引き）と A層の artifact は変えない。
  landed_in:
    - backend/core/course_content_builder.py
    - backend/tests/test_ik0427_0429_course_draft_hygiene.py
    - backend/tests/test_ik0379_course_context_prompt.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場でのコース内容の再生成（第 9 周の Cep B のトピックで目盛りが根拠候補から消えるか）
      - z < 2・wa = 0 のような短い条件式や、arXiv の柱（arXiv:2606.00411v1 …）を式と誤った候補は外していない
      - 学習画面の教材区画（content_blocks）には目盛りの「式」が残る
related: [IK-0379]
view_of: []
history: []
---

## 課題

図の軸の目盛りや式の断片、同じ式の重複が「重要な数式」として生成モデルに渡っていた。

## 発見の観点

生成モデルが受け取った根拠候補の式を 1 件ずつ読んだ。

## 解決の観点

中身が式でないものと重複は、プロンプトと付録に出さない（保存物は変えない）。

## 一般化

上流が付けた種別だけを信じると、種別の代表として中身の無いものが下流の利用者に届く。
