---
id: IK-0934
title: "台帳の行が論文ごとに分かれた（IK-0899）後も、台帳詳細の見出しは主グラフの段の英語名（「Theory basis」）だけで、どの論文の台帳を見ているかが画面に出ないため、同じ番号のノードを持つ 2 本の論文の台帳を開いた教員が取り違えを見分けられない"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.3"
  - "docs/features/doubt_layer_issues.md"
feature_context:
  realizing: "教員が論文ごとに分かれた台帳を開き、いまどの論文の対象に記帳しているかを確かめる"
  layers: [doubt_d, frontend_admin_ui, lecture_studio]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [target]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 target: IK-0899 の是正は行キーに document_id を入れ、画面は data-doubt-document 属性と ledgerPath で論文を添えるが、利用者の見える文には論文が出ない。
    台帳 GET の応答は document_id を持つが題名を持たず、doubt-atlas.js の renderLedgerInto も題名を描かない。処理軸 wording: 見出しは target_label で、主グラフの
    ノードは seminar_brief._ReadableLabels._readable_node_label が段の英語名（graph の label）をそのまま返す（generic_head と判定されない）ので「Theory basis」になる
    （IK-0901 の残り）。構造軸 none。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "同じ名前の対象を別の親ごとに分けて保存したのに、画面の見出しに親を出さないため、利用者はどちらの親の対象を操作しているか見分けられない"
pattern: same-name-different-referents
discovery:
  perspective: [reproduction, boundary_walk]
  note: >-
    te-02 が Cepheus の Theory basis に記帳した直後に中性子星の論文の Theory basis を開き、空であることは読めたが「どちらも見出しが同じで、いまどの論文の台帳かが
    画面から分からない」と述べた。run: uxsim/runs/c-r25-doubt-r3/20261023T033238Z-24957（seq9・10）。
resolution:
  perspective: [carry_through]
  note: >-
    台帳 GET（行あり・行なしの両方）に document_title を足し（core/library/structural_matches.load_display_titles の表示題名。ファイル名様のままなら「題名を引けない論文」）、doubt-atlas.js の台帳の見出しを「題名 › 対象の名前」にした。主グラフのノードの名前は段の英語名（Theory basis）を element_vocab の段の日本語名（理論の土台）+ 根拠の主張にする（seminar_brief._ReadableLabels._readable_node_label。台帳の表示名・ブリーフ・地図が通る）。
  landed_in:
    - "backend/api/routes/doubt.py"
    - "backend/core/doubt/seminar_brief.py"
    - "frontend/public/js/doubt-atlas.js"
    - "backend/tests/test_doubt_round25_wave3.py"
  verification:
    methods: [guardrail]
    unverified:
      - "2 本の論文の同じ番号のノードを続けて開いたときの実画面の見出し"
related: [IK-0899, IK-0901, IK-0407]
view_of: []
history: []
---

## 課題

症状: 2 本の論文の同じ番号のノードを続けて開くと、台帳の見出しはどちらも「Theory basis」で、記帳の有無の違いしか手がかりが無い。

原因: 行は論文ごとに分かれたが、論文の題名が応答にも画面にも無い。見出しの名前は主グラフの段の英語名のまま。

## 発見の観点

同じ番号の対象を持つ 2 本の論文の台帳を続けて開かせた（境界の歩き）。

## 解決の観点

分けた親（論文）を見出しの一部として運んだ（carry_through）。段の名前は日本語の正本に寄せた。（第 25 周 第 3 波・2026-10-23）

## 一般化

キーに親を足して衝突を直したとき、利用者の見る名前にも親を足さないと、データは分かれても人が取り違える。確かめ方は、同名の対象を
2 つ並べて開き、見出しだけでどちらか言えることを見ること。
