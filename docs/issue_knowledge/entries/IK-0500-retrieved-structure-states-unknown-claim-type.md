---
id: IK-0500
title: "学習チャットに渡す「検索で当たった箇所の構造」の事実文が、種類の決まっていない主張にも「（主張の種類: 不明）」を付け、情報の無い行を LLM へ渡していた"
status: resolved
recorded_at: 2026-09-29
resolved_at: 2026-09-29
sources:
  - docs/architecture/persona_enactment_testing_design.md §17.10
  - docs/features/knowledge_transfer_design.md §5
feature_context:
  realizing: "学習者の質問に答えるとき、採用した出典の箇所に結ばれた主張と理論の骨格上の位置を事実として添える"
  layers: [screen_adapter_sa]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [none]
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
    処理軸: 事実文の組み立てが claim_type を訳語表でそのまま引き、訳語表に unknown →「不明」があるため、
    種類が決まっていない主張に「（主張の種類: 不明）」を付けていた（wording。挙動は正しく、情報の無い
    ラベルを出す表示だけの誤り）。同じ規則（判定できない種類は出さない）は図の文脈表示では既に採られていた
    （IK-0359）が、この表示面には運ばれていなかった。構造軸 medium: 訳語表の unknown 行自体は他の面が
    使うので残し、表示面ごとに省く判断を置いた — 語彙表の側に「表示しない値」の宣言を持たせる案も
    ありうる。接続軸 medium: 前段は claim_type を正しく渡しており、unknown になる主張の多さ（atomic 子
    主張の種類候補が語彙外で丸められる）は上流の事実で、この表示の不具合ではないと判断した。
    統制軸: 順序・予算・完了判定は関係しない。
generalization:
  level: repo_pattern
  general_form: "値が決まっていないことを表す語彙値を、そのまま訳語で表示し、情報の無い行を利用者や下流の生成へ渡す"
pattern: wording-mismatch
discovery:
  perspective: [data_inspection, symptom_report]
  note: "ペルソナ通し受講の対話記録で、学習チャットのプロンプトの約 3 分の 1 に「主張の種類: 不明」が入っていたことを読み、事実文の組み立てを辿った。"
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: >-
    claim_type が空または unknown のときは種類の行ごと出さない（resolvers/learning.py の
    _retrieved_claim_fact）。訳語表と他の表示面は変えていない。
  landed_in:
    - backend/core/assistant_context/resolvers/learning.py
    - backend/tests/test_retrieved_structure_core.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場での再演（修正後に学習チャットを流し直していない）"
      - "unknown が多い上流の原因（atomic 子主張の種類候補が語彙外で丸められる）は調べていない。route は claim_type_text を渡していない"
related: [IK-0359, IK-0501]
view_of: []
history: []
---

## 課題

学習チャットに渡す「検索で当たった箇所の構造」の事実文が、種類の決まっていない主張にも「（主張の種類: 不明）」を付け、情報の無い行を LLM へ渡していた。

## 発見の観点

ペルソナ通し受講の対話記録でプロンプトの約 3 分の 1 に「主張の種類: 不明」が入っていたことを読み、事実文の組み立てを辿った。

## 解決の観点

claim_type が空または unknown のときは種類の行ごと出さない。図の文脈表示（IK-0359）と同じ規則。

## 一般化

値が決まっていないことを表す語彙値を、そのまま訳語で表示し、情報の無い行を利用者や下流の生成へ渡す。
