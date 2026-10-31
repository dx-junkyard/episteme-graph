---
id: IK-0958
title: "Copilot がライブラリの問いに KB の手順節を写して答え、節内リンク「[…](#what-students-see)」を生のまま出し、問われた「凍結していなくても学生に見えるか」「確定の取り消しはどこから」には答えない"
status: resolved
recorded_at: 2026-10-25
resolved_at: 2026-10-25
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "教員が Copilot に、確定・凍結と学生の画面の関係、取り消しの場所を尋ねる"
  layers: [admin_copilot, help_kb, concept_registry]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸 wording: frontend/public/js/admin-assistant.js:62-69 の renderMarkdown は ** と ` しか変換せず、KB 本文の相対リンク
    [見出し](#anchor) が生の文字列で画面に出る。接続軸 information: 回答の citations は library.md#identity-candidates が先頭で、問いに直接答える
    library.md#what-students-see（凍結は学生に直接は出ない）を引いていない。取り消しの手順は KB に無い（IK-0953 で製品にも無い）。IK-0921
    の是正（語表を登録簿へ・タブで加点）で出所はライブラリになった（GONE）が、問いの別の節を引けない。
generalization:
  level: repo_pattern
  general_form: "知識ベースの節を写す回答が、節内の参照記法を変換せずに出し、問いに直接答える別の節を引かない"
pattern: last-mile-missing
discovery:
  perspective: [reproduction]
  note: >-
    te-05 が「凍結していなくても学生に見えるか・取り消しはどこから」を問い、回答の中心が候補の確認手順で、問いの 2
    点に答えが無く、リンクの先も分からなかった（uxsim/runs/c-astro-r25-share-r4/20261024T232812Z-98848 seq14）。回答の citations と KB
    の節を突き合わせた。
resolution:
  perspective: [single_point_fix]
  note: >-
    Copilot の最後の一手を 2 点直した。admin-assistant.js の renderMarkdown が [見出し](#anchor) を「見出し」の文字に落とす。登録簿に library.what_students_see（#what-students-see）と library.revoke_identity_link（#revoke-identity-link）を足し、学生に見えるか・取り消しの問いがそれぞれの節に当たるようにした。#what-students-see は凍結版だけが学生に出ること（IK-0954）と取り消しの場所（IK-0953）の両方を書く。
  landed_in:
    - "frontend/public/js/admin-assistant.js"
    - "backend/core/admin_assistant/capabilities.py"
    - "docs/admin_operations/library.md"
    - "backend/tests/test_share_round25_r4_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "実 Copilot（LLM の意図分類経由）での回答"
related: [IK-0921, IK-0865, IK-0953, IK-0954]
view_of: []
history: []
---

## 課題

症状: Copilot の回答に生の Markdown リンクが出て、問いの 2 点が答えられない。原因: 描画が相対リンクを変換しない／検索が問いの別の節を引かない／取り消しの手順が KB にも製品にも無い。

## 発見の観点

再現（te-05 の Copilot の 1 手）。

## 解決の観点

是正済み（第 25 周・面 A 第 4 波・2026-10-25）。

旧案: 是正案: ① renderMarkdown が [text](#anchor) を本文の文字だけにするか、citations のリンクに置き換える。② KB の検索で、問いが「学生」「見える」を含むときは #what-students-see の節に加点（登録簿の keywords）。③ IK-0953 の是正と同時に「確定を取り消す」の節を KB に足し、IK-0954 の判断に合わせて「凍結していないエントリが学生に届くか」を 1 文で書く。

## 一般化

最後の一手が欠ける型。確かめ方は、同じ問いを送り、回答に生のリンクが無く、2 点への答えと出所の節が並ぶことを見ること。

## 第 5 周の再演（第 25 周・面 A 第 5 周、2026-10-27）

第 5 周の再演: 未評価（Copilot を使う手が無かった）。

## 第 6 周の再演（第 25 周・面 A 第 6 周、2026-10-27）

第 6 周の再演: GONE。te-03 の問い（未凍結の確定は学生に出るか・取り消すとどうなるか）に「凍結していないエントリ（凍結版が 1 つも無いもの）は、確定していても学生には出ません」「確定の行の「確定を取り消す」を押し、理由を書きます」と両方に答えた（生の節内リンクなし）。run: uxsim/runs/c-astro-r25-share-r6/20261027T122225Z-3503 seq6
