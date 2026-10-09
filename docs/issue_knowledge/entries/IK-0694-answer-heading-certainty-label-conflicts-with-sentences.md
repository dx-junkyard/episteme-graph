---
id: IK-0694
title: "学習チャットの回答で、節の見出しに付けた確からしさの語（論文に明記）と、その節の中の文に付けた語（論文から推定）が食い違う"
status: open
recorded_at: 2026-10-10
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/dialogue_response_shape_design.md"
feature_context:
  realizing: "学習者が回答のどこが論文に書いてあり、どこが推定かを読み分ける"
  layers: [rag_chat]
classification:
  axes:
    processing: [none]
    structure: [decomposition]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: routes/learning.py の生成プロンプトの規則は「論文に明記 / 論文から推定 / 一般知識の 3 段の言葉で示す」
    とだけ言い、その語をどの単位（文・節・回答全体）に付けるかを定めない。生成は節の見出しにも文にも語を付け、
    見出しの語が節の中の文の語を包まない。構造軸 decomposition = 確からしさの語を付ける単位が定義されていない。
    処理軸 none（3 段の語そのものは守られている）。接続軸 none。統制軸 none。単位を定めれば食い違いが消えるかは
    未確認（生成の振る舞い）。
generalization:
  level: repo_pattern
  general_form: "ラベルを付ける規則が単位を定めず、入れ子の上と下に別々のラベルが付いて、上のラベルが下を包まない"
pattern: unit-of-work-undefined
discovery:
  perspective: [reproduction]
  note: "第 23 周の砂場で、懐疑的な学生が「符号を決めている仮定（論文に明記）」の節の中に「（論文から推定）」の文が混ざっていると書いた。"
resolution:
  perspective: [pending]
  note: "未解決。推奨（core）: 規則に「確からしさの語は文ごとに付け、見出しには付けない」を足すか、応答の骨格（core/dialogue_shape.py）で見出しの語を外す。プロンプトの継ぎ足しで足りるかは再演で確かめる。"
related: []
view_of: []
history: []
---

## 課題

症状: 見出しの確からしさと文の確からしさが食い違う。原因: 語を付ける単位が規則に無い。

## 発見の観点

ペルソナの通し受講（reproduction）。見出しと文の語を突き合わせて見えた。

## 解決の観点

未解決。単位を文に決めるか、見出しの語を外せば足りる見込み。

## 一般化

単位を決めないラベルは入れ子のどの段にも付き、段どうしで食い違う。確かめ方は、確からしさの語が混ざる回答で、見出しと文の語が矛盾しないことを見ること。
