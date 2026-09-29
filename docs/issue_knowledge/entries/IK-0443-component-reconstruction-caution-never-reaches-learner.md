---
id: IK-0443
title: "部品の注意書き「Residual equation is reconstructed.」（式が AI の復元）や前提「15 seeds aggregated」が、授業用ドラフトの注意点に届かず学習者に見えない"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: コース内容の生成が、論文の部品の注意書き・前提を学習者向けの注意点へ運ぶ
  layers: [course_builder]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [none]
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
    接続軸: 部品の cautions は content_blocks の components に入るが、予算で間引かれやすい奥にあり（req 00240 /
    00241 は _omitted 付き）、部品の assumptions は投影に入っていなかった。式が AI の復元であること（A層
    reconstruction.status）も content_blocks の式に運ばれず、注意書きの equation_ids と突き合わせられなかった
    （information）。第 9 周の req 00240（t11）の comp_005 に「Residual equation is reconstructed.」、00241（t12）の
    comp_004 に「Weighted-sum equation is reconstructed.」があるが、生成された下書きの cautions に復元の事実が無い。
    構造軸は none（medium: 復元の目印を持つ列が投影に無かったとも読める）。統制軸は none（medium: 注意点の採否を
    生成モデルに任せきりにした割り当てとも読める）。
generalization:
  level: general
  general_form: 上流が付けた注意書きを下流の生成に任せ、落とされても後段で補わない
pattern: available-but-unwired
discovery:
  perspective: [data_inspection]
  note: 第 9 周のコース内容の生成プロンプト（req 00240 / 00241）の部品の注意書きと、下書きの注意点を並べた。
resolution:
  perspective: [carry_through, guardrail_fix]
  note: >-
    content_blocks の式に reconstructed（A層 reconstruction.status != none の写し）、部品に assumptions を載せ、
    根拠候補の先頭寄りに component_cautions（部品名・注意書き / 前提・復元した式を指すか）を並べた。後処理
    _ensure_reconstruction_caution は、注意書きの equation_ids が復元した式に当たるのに生成された注意点に復元の
    ことが無ければ固定文 label_vocab.RECONSTRUCTED_TOPIC_CAUTION を 1 つ足す（非LLM）。
  landed_in:
    - backend/core/course_content_builder.py
    - backend/core/label_vocab.py
    - backend/tests/test_ik0438_0443_course_draft_context.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場でのコース内容の再生成（第 9 周の t11 / t12 の注意点に復元の事実が出るか）
      - 復元でない注意書き・前提（「15 seeds aggregated」）は材料として渡すだけで、注意点に載るかは生成モデル次第
      - 学習画面の数式に reconstructed が載ることで「AI復元」の印が付く経路は、この変更では確かめていない
related: [IK-0409]
view_of: []
history: []
---

## 課題

部品の注意書き（式が AI の復元であること）が学習者向けの注意点まで届いていなかった。

## 発見の観点

生成モデルが受け取った部品の注意書きと、生成された注意点を並べて比べた。

## 解決の観点

注意書きと復元の目印を根拠候補の前の方へ運び、復元の事実が落とされたら固定文で補う。

## 一般化

上流の注意書きを生成に任せきりにすると、落とされても誰も補わない。
