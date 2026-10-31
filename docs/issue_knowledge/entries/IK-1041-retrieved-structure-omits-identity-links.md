---
id: IK-1041
title: "学習チャットの構造ブロックが主張と理論の骨格の段階だけを渡し、確定済みの同一性リンク（ほかの論文の同じ部品）を渡さないため、「別の論文の同じ部品」の問いに答えられない"
status: open
recorded_at: 2026-10-27
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22"
  - "docs/features/focus_document_design.md"
  - "docs/features/assistant_screen_adapter_design.md §11"
feature_context:
  realizing: "学生が部品について、ほかの論文の同じ部品との関係を学習チャットで尋ねる"
  layers: [rag_chat, screen_adapter_sa, concept_registry]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 接続軸 information — 同一性リンクは確定済みでも、学習チャットの文脈（retrieved_structure / element 解決器）に運ぶ経路が無い。
    部品の文脈 API（component_context の shared_part）は持っているが、チャットの解決器は主張と main ノードだけを読む。確定には、
    該当部品に confirmed の同一性リンクがあることを砂場で見ることが要る。
generalization:
  level: repo_pattern
  general_form: "別の画面で確定済みの結び付きを、対話の材料を組む経路が読まず、対話はその結び付きを知らない"
pattern: available-but-unwired
discovery:
  perspective: [data_inspection]
  note: >-
    製品頭脳 A 4240（選択箇所付きの学習チャット）で、別の論文の同じ部品を問う質問に対し、grounding に共通部品の事実が無く「この教材の範囲では確認できない」と答えた。
resolution:
  perspective: [pending]
  note: >-
    第 6 波では記帳のみ。解決器に同一性リンクの事実を足すには、部品の解決（主張 → 部品）・相手の論文の可視性（閉世界・スコープの積）・
    事実文の語彙（共通部品の名前と相手の題名だけ）の 3 点の設計が要り、SA層設計書 §11 の解決器の種類を 1 つ増やす変更になるため、
    設計書の追補を切ってから扱う。
  landed_in: []
related: [IK-0919]
view_of: []
history: []
---

## 課題

症状: 部品の同一性が確定していても、学習チャットは「ほかの論文の同じ部品」を答えられない。

## 発見の観点

製品頭脳に渡ったプロンプトの点検。

## 解決の観点

未解決（原因は仮説のまま — 同一性リンクの確定の有無を砂場で見ていない）。component_context の shared_part を解決器から呼ぶ形（同じ可視性ゲート）が候補。

## 一般化

確定済みの結び付きが対話へ配線されていない型。確かめ方: 確定済みのリンクを持つ部品について問い、文脈に共通部品の事実が載ることを見る。

## 第 7 周の再演（第 25 周・面 A 第 7 周、2026-10-28）

第 7 周の再演: STILL（open のまま）。st-05 の「ほかの論文でも同じものが使われてる？」に、構造ブロックに同一性リンクの事実が無く、回答は「出典はすべてこの1本の論文からの抜粋なので、教材の中では確かめられません」と閉世界で答えた（製品頭脳 B 4257・uxsim/runs/c-astro-r25-share-r7/20261027T171757Z-53326 seq10）。この周は確定リンクのある部品を学生が選べなかった（IK-1051）ので、構造ブロックに載るべき事実があったかは未確定。

## 第 8 周の再演（第 25 周・面 A 第 8 周、2026-10-28・judge_share_r8）

未評価。st-07 の選択質問は音声のペルソナとして casual で送られ（campaign はテキストの予定・H50）、casual では構造の手がかりも画面文脈も渡らない規則。回答は閉世界を守った（uxsim/runs/c-astro-r25-share-r8/20261027T224047Z-97606 seq10）。

## 第 9 周の再演（第 25 周・面 A 第 9 周、2026-10-31・judge_share_r9）

未評価。st-07 の選択質問「ここの EOS のネットワークってさ、ほかの論文でも同じのが使われてるの？」は DOMAIN_RAG で「手元の教材からは確認できません」と閉世界で答えたが、問うた部品（EOS network）に確定の同一性リンクが無いので、構造ブロックが同一性リンクを渡すかは判定できない。run: uxsim/runs/c-astro-r25-share-r9/20261031T015336Z-38765（seq10）。

## 第 10 周の再演（第 25 周・面 A 第 10 周、2026-10-31・judge_share_r10）

未評価。選択質問「ほかの論文でも同じ部品として使われてる？」は DOMAIN_RAG で「別の論文で同じ部品として結び付けられているかの記録も届いていません」と閉世界で答えた。問うた箇所の部品は置き換わり・テキスト選択は部品に解決されない（製品頭脳 4314）。run: uxsim/runs/c-astro-r25-share-r10/20261031T054227Z-72202（seq13）。

## 第 11 周の再演（第 25 周・面 A 第 11 周、2026-10-31・judge_share_r11）

未評価。選択質問の部品（磁場形態遷移診断）に確定リンクが無く、選択「ここで使っている見方は」は部品名の前で切れて部品に解決されない（製品頭脳 4331）（uxsim/runs/c-astro-r25-share-r11/20261031T102623Z-4737 seq13）。
