---
id: IK-0372
title: 課題ナレッジの型から解決への対応が解決観点のタグの束で、「何を守るためにどの部品を併用し、どの条件でどの派生形を選ぶか」を表す場所が無い
status: resolved
recorded_at: 2026-09-27
resolved_at: 2026-09-27
sources:
  - docs/architecture/improvement_cycle.md §7
  - docs/issue_knowledge/taxonomy.md §9
feature_context:
  realizing: 同じ型の課題を直すとき、過去の解決から必要な部品の組み合わせと選択条件を引く
  layers: [cycle_design, cycle_recording, docs]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [none]
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
    構造軸: 辞書の型が持つ「典型的な解決観点」と索引 §14.4 の解決観点列は、事例をまたいだ語彙タグの
    和集合で、補完関係（どれか 1 つでは残る）と選択関係（状況で選ぶ）を区別して表す表現が無かった
    （representation）。接続軸は「型の見分け方 → 解決」の受け渡しで条件が落ちるとも読めるが、渡す
    側に情報が無いのだから表現の欠落が先で、条件の伝達不良ではないと判断した（medium）。処理軸・統制軸
    は該当なし（索引生成器も選別の手続も正しく動いていた）。確認: 索引 §14.4 の
    `contract-changed-one-side` 行に別事例由来の解決観点が平らに並ぶこと、probe-agent の同型の検討
    （2026-09-27）との突き合わせ。
generalization:
  level: general
  general_form: 事例から抽出した解決の知識を語彙タグの集合でしか持たず、部品の併用関係と状況による選択条件を表す場所が無い
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [doc_code_diff, inventory]
  note: >-
    別プロジェクトの抽象化層の検討文書と、こちらの辞書・索引・正本モジュール・設計書を層ごとに突き合わせ、
    どの層が対応し何が欠けるかを表にした。索引 §14.4 の行を読んで、解決観点が事例をまたいだ和集合に
    なっていることを確かめた。
resolution:
  perspective: [representation_change, explicit_contract]
  note: >-
    辞書と同じ作法の 1 ファイル（原理 = `####` 見出し）に、守る条件（不変条項 ID）・破られる条件・
    当たる型・併用する部品・派生形・非適用・反例・正本の固定行を置き、エントリは `resolution.principles`
    （原理・use・固有の差分）で参照する。抽象課題側の独立カタログと自動推薦は作らず、前向きに使われたか
    は use の `consulted` で読む。
  landed_in:
    - docs/issue_knowledge/principles.md
    - backend/scripts/issue_knowledge_index.py
    - backend/tests/test_issue_knowledge_guardrails.py
  verification:
    methods: [guardrail]
    unverified:
      - 前向きの適用（設計前に原理を読んで選ぶ `consulted`）が実際の改善案件で起きるか
      - 敵対的レビュー（未実施）
related: [IK-0325]
view_of: []
history: []
---

## 課題

型を当てても、その型を過去にどう解いたかは解決観点のタグ（`carry_through` など）の束としてしか引けない。
複数の部品を併用しなければ残る破られ方（例: 安定キー・同期・保護列・再係留）と、状況で選ぶ派生形
（例: 列単位の保護か取り込み側の合流か）を区別して表す場所が無く、事例の `note` を個別に読むしかなかった。

## 発見の観点

別プロジェクト（probe-agent）の同型の検討文書を、こちらの層（4 軸・辞書の族/型・観点・§14.4・
pre-mortem・横断基盤の正本モジュール・設計書）に写像し、対応の無い行を特定した。

## 解決の観点

表現を足す（`representation_change`）。既存の辞書の `####` 見出しと索引生成器の解析をそのまま使い、
原理の定義と参照の欄を機械検査付きで宣言した（`explicit_contract`）。抽象課題側は型と不変条項の ID で
足りるため新設せず、1 原理 1 ファイル・機械可読化の後回し・効率の比較指標・自動推薦は取り入れなかった。

## 一般化

事例から知識を抽出する仕組み全般で、タグの集計は「何をしたか」を残すが「何と併せて・どの条件で」を落とす。
辞書の型 `information-dropped-as-unrepresentable`。
