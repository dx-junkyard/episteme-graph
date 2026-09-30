---
id: IK-0582
title: "応答の形（答えの位置・確認の数・問い返しの数・鏡の範囲）が LLM の自制と継ぎ足しのプロンプト規則に任され、規則同士が衝突していた"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/root_cause_consolidation_2026-09-30.md §5
  - docs/features/dialogue_response_shape_design.md
feature_context:
  realizing: "学習チャットで、答えを先に置き、確認と問い返しを 1 つに絞り、前の往復の訂正を保ったまま応答する"
  layers: [rag_chat, discuss, frontend_learning_ui, tests_guardrails]
classification:
  axes:
    processing: [none]
    structure: [responsibility]
    connection: [contract]
    governance: [assignment]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造=responsibility: 応答の区画の順序と数を決める判断を生成器（LLM）とプロンプトの規則に置き、サーバ側に
    組み立ての置き場所が無かった（応答の骨格 §0 で確認）。接続=contract: 継ぎ足したプロンプト規則（鏡の確認で終える /
    末尾に問いを必ず添える）が互いに両立せず、保存形に依存する履歴の判定器との契約も暗黙だった。
    統制=assignment: 形の判断を誰が持つか（サーバか生成器か）の割り当てが原因で、同じ判断を生成器に委ねる限り
    再発する（確信度 medium は responsibility との境界で迷ったため）。処理=none: 個々の規則文・固定文は単体では
    意図どおり。
generalization:
  level: general
  general_form: "出力の形を生成器の自制と継ぎ足しの指示に任せ、指示同士が衝突して区画の順序と数が崩れる"
pattern: rule-enforced-per-surface
discovery:
  perspective: [inventory, reproduction]
  note: "逆質問のターンで質問を落とす・毎回問い返す・確認プロンプトの再掲・鏡の前置き復唱・訂正の覆りを並べ、再演で 1 往復に確認の区画が最大 4 つ並ぶことを見た。"
resolution:
  perspective: [responsibility_move, canonical_source]
  note: "生成後・保存前の 1 点でサーバが応答の区画を組み立てる純関数を置き、先に答える / 確認は後ろに 1 つまで / 問い返しは 1 つまで / 鏡は核心語 / 直前の訂正の持ち越し、をサーバが決める。プロンプトは区画の中身を埋めるだけにした。"
  landed_in:
    - backend/core/dialogue_shape.py
    - backend/api/routes/learning.py
    - backend/api/schemas.py
    - backend/tests/test_dialogue_shape_guardrails.py
    - docs/features/dialogue_response_shape_design.md §8
  principles:
    - principle: route-all-surfaces-through-one-point
      use: extracted
      note: "生成物の形の派生形（生成後・保存前の 1 点でサーバが区画を組み立て、保存形は不変）。早期 return の経路は対象外で残した。"
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再演（ペルソナ通し受講の次の周）
      - ブラウザでの画面確認
      - 早期 return の経路（HELP / 学習相談 / 地図 / 要素説明）
      - 実 LLM 出力での問いの切り分けの取りこぼし
related: [IK-0509, IK-0548, IK-0562, IK-0575, IK-0576]
view_of: []
history: []
---

## 課題

前提の逆質問のターンで質問を落とす、毎回問い返す、帰属の確認プロンプトが再掲される、鏡が同意の前置きを
復唱する、前の往復の訂正が次の往復で覆る — の欠陥が往復ごとに出た。原因は、応答の形を決める判断が生成器と
継ぎ足しのプロンプト規則に任され、規則同士（ルール 2 と 6）が衝突していたこと。

## 発見の観点

個別是正の棚卸しと、再演で 1 往復の区画の数を数えたこと。

## 解決の観点

形の判断をサーバへ移す（責務の移動）を主に、組み立ての 1 点を正本にすることを従にした。プロンプトに規則を
足し続ける案は、規則同士の衝突が増えるため採らなかった。

## 一般化

出力の形を生成器の自制に任せる構造では、規則を足すほど衝突が増える（`rule-enforced-per-surface`）。
