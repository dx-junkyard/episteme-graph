---
id: IK-0590
title: "前提ゲートの直後に「はい、そうですね」で始めた新しい質問が、ゲートへの返事として扱われて前提トピックの説明が足され、いま開いているトピックの教材を「前提トピックの資料」と呼ぶ"
status: open
recorded_at: 2026-10-01
resolved_at: null
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.11
feature_context:
  realizing: "前提ゲートを出したあとの次の往復で、学習者の発話がゲートへの返事か新しい質問かを判定する"
  layers: [rag_chat, learner_experience_b]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [meaning]
    governance: [ordering]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: low
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: ゲート後の往復で、発話の冒頭の同意の前置き（「はい、そうですね」）という近くの手がかりでゲートへの返事と判定し、
    後ろに続く新しい質問という中身で確かめていない（処理軸 logic・接続軸 meaning）。判定がゲート返事の経路を先に通すため
    （統制軸 ordering）、新しい質問への回答のあとに前提トピックの説明（【ひとことで】【しくみ】）が付き、前提説明のプロンプトの
    語（「前提トピックの資料」「次のトピックで扱う論文」）が表示中トピックの内容に貼られる。前提ゲート後の否定の判定（IK-0560）の
    判定関数が肯定側の前置きをどう扱うかを読めば確定する。構造軸は単一の判定順の問題として none。
generalization:
  level: repo_pattern
  general_form: "直前の問いへの返事かどうかを発話の前置きという手がかりで決め、続く本文が別の要求であることを確かめない"
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [reproduction, symptom_report]
  note: "第 18 周 st-06（s-misconception-mirror）で、前置き付きの新しい質問への回答が途中から前提トピックの説明に切り替わり、画面の位置関係（表示中のトピックと前提トピック）が逆転した。同じ前置きでも別の往復ではゲート扱いされず、判定が揺れる。"
resolution:
  perspective: [pending]
  note: "未解決。発話の本文が問い（「か。」「？」・依頼）を含むときはゲート返事ではなく新しい質問として先に答え、ゲートは次の往復へ繰り延べる（詰まりの区画と同じ骨格）案が候補。"
  landed_in: []
related: [IK-0560, IK-0586, IK-0509]
view_of: []
history: []
---

## 課題

前提ゲート（「この前提知識を理解していますか？」）が出たあとの往復で、学習者が「はい、そうですね、分かりました。」と前置きしてから新しい質問をすると、
回答が質問に答えたあと前提トピックの説明（【ひとことで】【しくみ】）に切り替わる。いま開いているトピックの内容を「次のトピックで扱う論文の結果」
「上の抜粋（前提トピックの資料）」と呼び、出典が 1 件も付かないのに「出所: 教材に基づく」と出る。同じ前置きでも別の往復ではゲート扱いされない。

証拠: run 20261001T054334Z-73002 seq=8（st-06）、20261001T053920Z-70419 seq=22（st-02・テキスト選択の質問）。

## 発見の観点

再演（reproduction）。頭脳メモの「前置き付きの質問が前提ゲートへの『はい』として扱われた」を transcript の回答本文で確かめた。

## 解決の観点

未解決。ゲート返事の判定を本文の中身（問い・依頼の有無）で確かめ、問いがあれば先に答えてゲートを次の往復へ送る。前提説明の経路のプロンプト語が
通常の回答に混ざらないよう、経路ごとの材料の呼び名を分ける。
原因は仮説で、確かめる点は classification.basis に書いた。

## 一般化

直前の問いへの返事判定を前置きの語で済ませる型。確認ダイアログ・選択肢付きの往復を持つ対話全般で再発しうる。
