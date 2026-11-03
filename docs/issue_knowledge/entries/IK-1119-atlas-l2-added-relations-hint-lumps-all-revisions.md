---
id: IK-1119
title: "学生の地図 L2 の事実文の「地図の版の改訂で加わった関係」は最初の版以降の全改訂（2026.2〜2026.7）を指すが、学生は「今回の版で何が加わったか」と読み、どれが直近の版で加わったかを地図からもパネルからも特定できない"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/architecture/field_map_display_principles_2026-08-29.md"
  - "docs/features/category_gap_candidates_design.md §5.6"
feature_context:
  realizing: "学生が分野の地図で、教員が確定して版を上げた関係を、どの版で加わったものかとして読む"
  layers: [field_atlas_s, frontend_learning_ui]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: low
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 meaning: routes/atlas_view.py の added_relation_pairs は凍結の履歴を遡り、系列の最初の版から無かった関係を全部
    （直近 → 古い順）返し、手がかりの語 ATLAS_L2_HINT_ADDED_RELATIONS「地図の版の改訂で加わった関係」もこの全改訂を指す（IK-1045 の第 7 波で
    末尾 2 版の差から全版の差へ広げた）。一方で学生は見出しの「版 2026.7」と並べて「この改訂で加わった関係」と読む。同じ語が 2 つの範囲を
    指しうる。版の情報は関係の行にもパネルの「地図の関係:」にも無い（IK-1072 の是正は「この版で加わった」印を意図的に作らなかった）。
    処理軸 wording（範囲を言わない語）。統制軸 none / low: 版ごとに言うかは「版更新の告知・NEW バッジを作らない」との兼ね合いで、
    そこを変えるなら不変条項の読みの判断になる（オーナー判断の候補）。構造軸 none。
generalization:
  level: repo_pattern
  general_form: "履歴の差を集計する範囲を広げたとき、その集合を呼ぶ語を据え置くと、読み手は直近の差を指す語として読む"
pattern: same-name-different-referents
discovery:
  perspective: [reproduction, invariant_audit]
  note: "第 10 周、凍結 2026.7 の直後の学生 st-03（run uxsim/runs/c-astro-r25-atlas-r10/20261031T053524Z-69161 seq 6・審判 C）が「「地図の版の改訂で加わった関係」として過去の版で加わったものとまとめて書かれていて、今回の 2026.7 で何が加わったのかは、どれか 1 本だとは言われていません」、seq 8 のパネルでも「この関係がこの版で加わったものかどうかは書いてありません」と記した。第 9 周の st-03 も同じ要望（judge_atlas_r9 §3 の起こさなかったもの）。added_relation_pairs と ATLAS_L2_HINT_ADDED_RELATIONS を読み、版の告知を作らない原則（category_gap_candidates_design §5.6）と突き合わせた。"
resolution:
  perspective: [explicit_contract, deferred_decision]
  note: >-
    オーナー判断（judge_atlas_r10 §5）は推奨どおり: 地図の上には印・NEW バッジ・告知を付けず、右パネルの「地図の関係:」の行にだけ
    「（版 2026.7 で加わった）」の事実を添える（label_vocab.ATLAS_PANEL_RELATION_ADDED_IN。判定は added_relation_pairs と同じ内部関数の
    routes/atlas_view.py added_relation_versions・最初の版から在る関係には添えない）。学生が選んだ概念について尋ねたときだけ見える属性は
    「版更新の告知」に当たらないと読む（category_gap_candidates_design §5.6 の規律は維持）。L2 の語は範囲の分かる
    「これまでの版の改訂で加わった関係」（ATLAS_L2_HINT_ADDED_RELATIONS / ATLAS_L2_UNDRAWN_RELATIONS_PREFIX）。
    設計書 field_atlas_overlay_spec §21・学生マニュアル 02-student の地図の詳細パネル節に追記。
  landed_in:
    - "backend/core/label_vocab.py"
    - "backend/api/routes/atlas_view.py"
    - "docs/features/field_atlas_overlay_spec.md"
    - "backend/tests/test_atlas_review_round25_r10.py"
  verification:
    methods: [guardrail]
    unverified:
      - "版上げを挟んだ後の学生が『これまでの版の改訂』を直近の版の差と誤読しないか（次の周の再演）"
      - "パネルの版の添え書きのブラウザでの見え方"
related: [IK-1045, IK-1072, IK-1118]
view_of: []
history: []
---

## 課題

症状: 凍結 2026.7 の直後、学生の L2 の事実文は「（…地図の版の改訂で加わった関係と、そのつながりを優先しています）」「地図の版の改訂で加わった
関係のうち、ここには描いていないもの: 惑星形成 — 星形成」と言う。学生は見出しの「版 2026.7」と結んで「今回の改訂で加わった関係」と読み、
惑星形成 — 星形成（2026.3）を今回のものと取り違えるか、どれが今回の 1 本かを推測する。右パネルの「地図の関係:」も版を言わない。

原因: 「改訂で加わった」の集合は第 7 波で全改訂に広げられたが、語はそのまま。版の情報はどの面にも無い（印を作らない方針）。

## 発見の観点

版上げの直後の学生で地図を再演し（reproduction）、版の告知を作らない原則と、学生の場面の目標（版が上がって何がつながったか）を突き合わせた
（invariant_audit）。

## 解決の観点

未解決。語の是正は原則の範囲。版ごとの事実を出すかはオーナー判断の候補（frontmatter の resolution.note）。

## 一般化

型 same-name-different-referents: 集計範囲を変えた集合の呼び名を据え置く。確かめ方: 版上げを 2 回以上続けた後、学生の事実文の語がどの範囲を
指すかを、直近の版の差だけを読む人が誤らないこと。

## 第 11 周の再演（2026-10-31・judge_atlas_r11）

**GONE**。右パネル「地図の関係: 「分子雲」が「惑星形成」に依存する（版 2026.8 で加わった）。」（st-03 / st-01、run uxsim/runs/c-astro-r25-atlas-r11/20261031T102103Z-2572 seq 8・uxsim/runs/c-astro-r25-atlas-r11/20261031T102321Z-3474 seq 8）— 版は凍結の履歴（te-04 の 2026.8）と一致、地図の上に印・バッジは無い。L2 の語は「これまでの版の改訂で加わった関係」。st-01 は L2 の文だけでは範囲を特定できず問い返したが誤読はせず、右パネル 1 回で「今回の版で加わった」と解けた（版上げを 1 回挟んだ後の誤読は起きなかった）。
