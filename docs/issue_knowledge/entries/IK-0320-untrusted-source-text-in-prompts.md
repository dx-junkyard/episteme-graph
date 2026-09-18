---
id: IK-0320
title: 第三者が書いた資料本文が、指示と区別されないままプロンプトへ流れ込む
status: open
recorded_at: 2026-09-10
resolved_at: null
sources:
  - docs/architecture/trust_boundary_pdf_input.md §3
  - docs/architecture/trust_boundary_pdf_input.md §4
feature_context:
  realizing: 取り込んだ論文の本文を材料に、構造化と対話を行う
  layers: [pipeline_a, rag_chat, deliberation_w]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [none]
    governance: [review]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    原因は「指示側とデータ側を区別する表現（区切りと、資料本文中の指示に従わないという
    明示）が規約として存在しなかったこと」。個々のプロンプトを直しても、新しい経路が
    増えるたびに同じ状態へ戻るため構造。調査表が、資料本文を渡す経路と区切りの強さを
    全件で確認しており、明示は着手前に 0 本だった。
generalization:
  level: general
  general_form: 第三者由来のテキストが指示と同じ文脈へ混ざり、データと命令の境界が溶ける
pattern: untrusted-input-reaches-instruction
discovery:
  perspective: [inventory, invariant_audit]
  note: >-
    資料本文が LLM へ渡る経路を全件棚卸しし、区切りの有無・指示非実行の明示の有無を
    表にした。実際に最初に効いたのは悪意ではなく事故由来の混入（着色用の制御文字が
    成果物に紛れ込み、応答へ転写された）の報告だった。
resolution:
  perspective: [explicit_contract, pending]
  note: >-
    境界の規約を 4 条として明文化し、資料本文を渡す主要経路へ区切りと明示を入れ、
    表示・読み上げの前に制御文字を落とす層を置いた。残っているのは、出力分布に影響が
    及ぶため別便とした構造化パイプライン側の全 agent と、見出しだけの弱い区切り 2 経路。
    弱い区切りは資料本文が同じ文字列を再現できるため、区切りの形自体の設計が要る。
  landed_in:
    - backend/core/text_hygiene.py
    - backend/tests/test_pdf_trust_boundary_guardrails.py
    - docs/architecture/trust_boundary_pdf_input.md §2
related: [IK-0305, IK-0015]
view_of: [IK-0015]
history:
  - date: '2026-09-19'
    field: classification
    from: primary=structure facets=[structure.representation, governance.review]
    to: axes=processing=[none]; structure=[representation]; connection=[none]; governance=[review]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

論文本文・要旨・図の説明・図中ラベル、そしてそこから導いた主張や逐語引用は、教員でも
学習者でもない第三者が書いた文字列である。これらが、指示と同じ文脈にそのまま連結されて
LLM へ渡っていた。区切りの弱い経路では、資料本文が区切りそのもの（見出しや水平線）を
再現できるため、指示側とデータ側の境界が溶ける。

原因は、その境界を表す規約が存在しなかったこと。個々のプロンプトは目的に対して妥当で、
欠けていたのは「どこからがデータか」「データ中の指示には従わない」という共通の表現である。

## 発見の観点

資料本文が LLM へ渡る経路を全件棚卸しし（`inventory`）、区切りの有無と明示の有無を表にした。
上位の原則（資料は untrusted）と実装を突き合わせる形の点検である（`invariant_audit`）。
最初に実害として現れたのは悪意ではなく事故由来の混入で、着色用の制御文字が成果物に紛れ込み
応答へ転写された、という利用者報告だった。

## 解決の観点

境界の規約を条文として明文化し（`explicit_contract`）、主要経路へ区切りと明示を入れ、
表示・読み上げの前に制御文字を落とす層を置いた。新しい経路を足すときに規約を満たすことを
検査でも固定した。残りは未解決（`pending`）で、構造化パイプライン側の
agent 群はプロンプト変更が出力分布を変えるため期待値と合わせて別便、見出しだけの弱い区切りは
区切りの形自体の設計が要る。

## 一般化

外部から来た文字列を材料にする機能では、入口が増えるたびに同じ境界が問われる。区切りと
「データ中の指示に従わない」の明示を、新経路の受け入れ条件にする。
辞書の型 `untrusted-input-reaches-instruction` に対応する。
