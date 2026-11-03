---
id: IK-0858
title: "ゼミ前ブリーフの「一点吊りの支持線」が対象ごとに 1 行を作るため、同じ切断点（同じ支持元の主張）に吊られた別々の対象が同じ事実文を繰り返して並び、どこが一点なのかが読み取れない — 対象の文面は式の詳細層の「操作ラベル: 英語の理論対象」の途中切れになる"
status: resolved
recorded_at: 2026-10-22
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
  - "docs/features/seminar_brief_mirroring_design.md"
  - "docs/features/stakes_ledger_design.md"
feature_context:
  realizing: "教員が輪講の前にゼミ前ブリーフで、どこが一点で支えられているかを確かめる"
  layers: [seminar_brief, stakes_ledger_sl, doubt_d]
classification:
  axes:
    processing: [none]
    structure: [decomposition]
    connection: [none]
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
    構造軸 decomposition: 区画の 1 行の単位が「対象」で、利用者が知りたい単位（一点 = 切断点）と合わない。
    `core/doubt/seminar_brief.py::_single_support_lines`（501-）は level=single の対象を 1 件ずつ行にし、`_disambiguate_same_text`
    （300-）は statement と fact_line の両方が同じ群だけを畳むので、statement が違い fact_line（「『結論を導出: Unlike the case
    without cosmic shear…』が同時に崩れると、観測からの支持が途切れます」）が同じ 5 件はそのまま並ぶ（IK-0593 の是正は逆向きの
    「同じ文面・別の対象」だけを扱う）。対象の文面は依存グラフのノード表示名（detail 層の display_label = 操作ラベル + theory_object の
    英語）で、`_ReadableLabels` を通っても英語の切り詰めのまま。処理軸・接続軸 none。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "共通の原因（同じ切断点・同じ支持元）を持つ複数の対象を、原因ではなく対象を単位に列挙するため、同じ原因の説明が対象の数だけ繰り返され、要点（どこが一点か）が埋もれる"
pattern: unit-of-work-undefined
discovery:
  perspective: [reproduction, data_inspection]
  note: "教員 te-02 がゼミ前ブリーフを開くと、英語の文 5 つ全部に同じ「単一の支持線に立っています」の一文が付き、「ゼミでどこから入ればいいのか決められない」と記した。応答の single_support_lines と _disambiguate_same_text の群のキーを突き合わせた。 run: uxsim/runs/c-r25-doubt-r1/20261022T124129Z-35838（seq20）。頭脳の所見 BRAIN_NOTES_doubt.md 00034。"
resolution:
  perspective: [representation_change]
  note: >-
    seminar_brief._single_support_lines は対象単位の行に切断点のキー（cut_key・表示しない）と読める切断点名を付け、_group_single_support_by_cut が同じ切断点の対象を 1 行に束ねる（事実文 SINGLE_SUPPORT_GROUP_FACT は切断点を主語に 1 回だけ・対象は名前を最大 3 件列挙し超過は「ほか」＝件数なし）。束ねてから IK-0593 の同文面の畳みを掛け、上限は束ねた行に掛ける。対象名は既存の _ReadableLabels（式の印字番号・操作の日本語名）で読める名前にしてから束ねる。
  landed_in:
    - "backend/core/doubt/seminar_brief.py"
    - "backend/tests/test_doubt_course_scope_round25.py"
  verification:
    methods: [guardrail]
    unverified:
      - "実データのブリーフで束ねた行の読みやすさは画面で未確認"
      - "対象名が英語の主張本文のままになる場合（_ReadableLabels で引けないもの）は残る"
related: [IK-0593]
view_of: []
history: []
---

## 課題

症状: ゼミ前ブリーフの「一点吊りの支持線」に、英語の途中切れの対象が並び、すべてに同じ事実文が付く。原因: 行の単位が
対象で、同じ切断点に吊られた対象を束ねない。

## 発見の観点

ペルソナの通し受講（reproduction）と応答の読み取り（data_inspection）。

## 解決の観点

解決済み（2026-10-23）。切断点を行の単位にする。

## 一般化

原因を共有する項目を対象ごとに並べる一覧は、原因の数でなく対象の数だけ同じ説明を繰り返す。確かめ方は、同じ支持元に
吊られた対象が複数ある論文で、一覧の行数が切断点の数と一致することを見ること。

第 2 周の再演: 残存（一部）。一点吊りの行は切断点で束ねられたが、対象名が引けない主張は「結論を導出: In the MG model with $K_DE$ …」のように操作の機械ラベル＋英語本文の途中切れのまま（unverified に書いた範囲が実データで出た）。脆い前提の式名は TeX ラベル（式 (eq:mod_dcf)）。表示名の解決の分裂は IK-0901。run: uxsim/runs/c-r25-doubt-r2/20261022T215750Z-73306（seq37）・uxsim/runs/c-r25-doubt-r2/20261022T220507Z-77990（seq33）。

第 2 波で是正（2026-10-23）: 対象名の解決を core/doubt/target_labels.py に寄せ（IK-0901）、TeX の label キー（eq:mod_dcf）を印字番号として「式 (eq:mod_dcf)」にしない（display_projection.is_tex_label_key・equation_display_label / has_printed_label）。英語の論文の主張本文が英語のまま出るのは論文の言語で、語の途中で切らない丸め（text_excerpt）は従来どおり。
