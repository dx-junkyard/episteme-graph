---
id: IK-1080
title: "［付録をまとめて付け直す］の知らせは、トピックごとの結果（付録を外した 10・置き換えた 5）を応答に持ちながら「付録を付け直して保存しました: 題名…」の 1 文で全部を言い、付録が消えたトピックと置き換わったトピックを教員が見分けられない（1 トピックの［付録を付け直す］で是正した言い分けがまとめての面に無い）"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
feature_context:
  realizing: "所有者が、付録が古いトピックをまとめて付け直し、何が変わったかを知らせで確かめてから発行する"
  layers: [lecture_studio, frontend_admin_ui]
classification:
  axes:
    processing: [wording]
    structure: [responsibility]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確定（生応答とコードで読んだ）: POST …/lecture-studio/appendix/reattach-all の応答は `outcomes`（t0 / t1 / t3 / t4 / t5 / t6 / t8 / t10 /
    t15 / t19 = removed、t7 / t11 / t12 / t13 / t14 = replaced）を返すが、知らせは `lecture_studio_state.APPENDIX_REATTACH_ALL_DONE_NOTICE`
    「付録を付け直して保存しました: {titles}」の固定文 1 本で、outcome を見ない。1 トピックの経路は IK-1032 で
    `appendix_reattach_notice(outcome)` に分岐を持たせたが、まとめての経路はその 1 箇所を通らない。処理軸 wording（結果の語を文に出さない）、
    構造軸 responsibility（言い分けの規則が面ごとに置かれ、新しい面が規則を持たない）。
generalization:
  level: repo_pattern
  general_form: "1 件の操作の知らせで結果の分岐を言い分けるよう直したが、同じ操作をまとめて行う面はその分岐の規則を通らず、全件を同じ 1 文で言う"
pattern: rule-enforced-per-surface
discovery:
  perspective: [trace_walk, data_inspection]
  note: >-
    第 25 周 面 D 第 9 周（判断 1 の初観察）。te-01 が［付録をまとめて付け直す］を押した。知らせは 15 題名を列挙したが、学生 API で見ると
    t0（J=3 の項）・t1（座標目盛り）は付録「この節で使う数式」が見出しごと消え、t11 は英語の項目名から「$N$ を定める式」1 項目に置き換わった
    （砂場 DB の版 4 ⇄ 版 5 の写しの比較と st-01 / st-08 の教材）。頭脳は「15 題名を列挙・一覧と一致を数えた」と読み、消えたのか
    置き換わったのかは知らせからは区別していない。run: uxsim/runs/c-astro-r25-studio-r9/20261031T012109Z-34801 seq5。
resolution:
  perspective: [canonical_source]
  note: >-
    第 25 周 第 9 波（2026-10-31）: `lecture_studio_state.appendix_reattach_all_outcome_groups` / `appendix_reattach_all_notice` が
    結果を外した → 作り直した → 新しく付けたの段に分け、題名を列挙する（件数なし・unchanged は段にしない・語彙外は「作り直した」）。
    一括の応答に `outcome_groups` を足し、`notice` はこの関数の文。1 件の経路の語彙（`APPENDIX_REATTACH_OUTCOMES`）と同じ分岐。
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "分岐の語彙を単発の付け直しの経路と共有し、まとめての面も同じ分岐で言う。"
  landed_in:
    - "backend/core/lecture_studio_state.py"
    - "backend/api/routes/lecture_studio/topics.py"
    - "backend/tests/test_lecture_studio_round25_r9.py"
  verification:
    methods: [guardrail]
    unverified:
      - "所有者の画面で段が読めるか（action status の 1 行に収まるか）は実機で見ていない"
related: [IK-1032, IK-1054, IK-0872]
view_of: []
history: []
---

## 課題

症状: まとめて付け直した後の知らせが全トピックに同じ「付け直して保存しました」を言い、付録が消えたトピックが分からないまま発行に進む。

原因: 結果の語はサーバの応答にあるが、まとめての知らせは 1 件の経路で直した言い分けの規則を通らない固定文。

## 発見の観点

まとめての付け直しを所有者の画面で歩き（trace_walk）、応答の outcomes と知らせの文・版の写しを突き合わせた（data_inspection）。

## 解決の観点

第 25 周 第 9 波で是正（resolution 参照）。言い分けを 1 箇所の正本に置き、1 件とまとめての両方がそれを通す。

## 一般化

1 件の面で直した言い分けは、まとめての面にも同じ正本で効かせる。確かめ方: 結果の分岐が混在する状態でまとめて実行し、知らせが分岐ごとに分かれるかを見る。

## 第 10 周（第 25 周・面 D 第 10 周、2026-10-31・judge_studio_r10）

**GONE（実機）**。一括付け直しの知らせが「付録を外したトピック（いまの根拠からは付録に載せる式・図がありません）: 二重ネットワーク構成と… 付録を作り直したトピック: 学習損失と…」と段を分けて題名を列挙した。砂場 DB の版 5 ⇄ 版 6 の写しで、外した側（t11）は付録の節ごと消え、作り直した側（t12）は  = 15$ が消えて図 1 枚が残ることを確かめた（知らせの段と実際の差が一致）。小: 同じ知らせの中で「受講者は…版 5 を見ています」が 2 度出る。run: uxsim/runs/c-astro-r25-studio-r10/20261031T052922Z-65584 seq5。
