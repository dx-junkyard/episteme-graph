---
id: IK-0988
title: "わたしの地図の「問いからの旅」が、テキスト選択（区画）に帰属した問いから始まると「あなたの別の問いもこの近くにあります」の 1 行だけで終わり、論文の構造・別の論文へ渡る道を辿らなかったこと（辿れない理由）を言わない"
status: open
recorded_at: 2026-10-27
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "学生が自分の問いから旅を開き、部品から別の論文へ渡れるかを確かめる"
  layers: [personal_network, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [completion]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 統制軸 completion — 旅の縮退の事実文（NOTICE_JOURNEY_EMPTY・§17）は steps が空のときだけ出る。区画（segment）帰属の問いは最も粗いアンカーで [1]〜[4]（論文のグラフ・同一性リンク・L層・骨格）を辿らず、[5] の本人の別ノードが 1 件あれば steps が空でないので、辿らなかったことを言わない（応答 /api/me/personal-network/journey の steps 1 件・frontier_note null）。接続軸 information: 区画の選択に ⚓ の部品が含まれていても、帰属は区画で止まり部品へ渡らない。確定には core/personal_graph/journey.py で segment アンカーの経路を読むことが要る。
generalization:
  level: repo_pattern
  general_form: "経路の一部だけが成立したとき、成立しなかった経路について黙り、無いのか辿っていないのかを利用者が区別できない"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    st-07 seq13（uxsim/runs/c-astro-r25-share-r5/20261027T071522Z-64789）の旅の応答（steps 1 件）と、seq10・11 の問いの帰属（anchor_type segment）を突き合わせた。部品の文脈では「ほかの論文の部品と同じものとして…記録がありません」と言えているのに旅では無言。
resolution:
  perspective: [pending]
  note: >-
    未解決（第 25 周・面 A 第 5 周の審判で起票）。是正案は本文「解決の観点」。
  landed_in: []
related: [IK-0920, IK-0983]
view_of: []
history: []
---

## 課題

症状: 旅で別の論文へ渡る道が無いことが読めず、学生は「探し方が悪いのか本当に無いのか」を区別できない。旅の起点がどの部品・どの問いかも出ない。原因（仮説）: 縮退の事実文が「steps が空」のときだけで、粗いアンカーから構造の経路を辿らなかった事実を落とす。

## 発見の観点

再現（st-07 (d) seq13）と応答の点検。

## 解決の観点

未解決。確かめた後の是正案: 旅の起点が区画・トピックの粗いアンカーのときは、steps の有無によらず「この問いは教材の区画に結び付いているため、論文の部品・ほかの論文への道は辿っていません。部品の ⚓ を開いて質問すると、部品から辿れます。」（精密化の出口・§17 の事実文の再利用）を添える。区画の中に ⚓ の部品が 1 つだけあるときは帰属の候補にその部品を出す（本人確定 = P1）。

## 一般化

部分的な成功が失敗の説明を消す型。確かめ方は、区画帰属の問いから旅を開いたとき、辿らなかった経路の事実文と精密化の出口が出ることを見ること。

## 第 6 周の再演（第 25 周・面 A 第 6 周、2026-10-27）

第 6 周の再演: GONE（縮退の言明）。旅が「この記録からたどれる道は、まだ記録されていません。」+ 帰属の種類に応じた出口を言った。ただし案内された出口（帰属カードで確定）がこの記録の出所では空で入れない → IK-1025。run: uxsim/runs/c-astro-r25-share-r6/20261027T122930Z-7994 seq13
