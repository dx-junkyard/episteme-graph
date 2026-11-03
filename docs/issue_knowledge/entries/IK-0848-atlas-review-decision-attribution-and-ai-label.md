---
id: IK-0848
title: "分野の地図の候補（カテゴリギャップ・関係の辺）は、教員が採用・見送りした後も「AIによる検出／推定（未確認）」が並んで残り、誰がいつ判断したかが API にあるのに画面に出ない（辺は採用後も[採用]が押せる表示のまま）"
status: resolved
recorded_at: 2026-10-22
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/category_gap_candidates_design.md"
  - "docs/features/atlas_relation_edges_design.md"
  - "docs/features/decision_context_design.md"
  - "docs/features/account_lifecycle_management_design.md §16"
feature_context:
  realizing: "教員が分野の地図の候補を採用・見送りし、その判断を後から別の教員が読んで再構成できるようにする"
  layers: [category_gaps, relation_edges_re, frontend_admin_ui]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [information]
    governance: [review]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 information: gap-candidates / edge-candidates の decision は decided_by・decided_at を返す
    （run 20261022T123418Z-31802 seq4 / seq10 / seq11 の応答）が、admin.js の _gapCandidateCardHtml（7322〜）と
    _edgeCandidateCardHtml（7760〜）は status_label と review_note だけを描き、帰属と日時を描かない。統制軸 review:
    原則1 改訂（確定は再構成可能な手続にのみ）に対して、判断の主体が画面から再構成できない。処理軸 wording（medium）:
    出所ラベル（7338 GAP_ORIGIN_LABEL / 7772 EDGE_ORIGIN_NOTE）が判断の有無にかかわらず常に描かれ、「教員が採用した」と
    「AI の未確認」が同じ行に並ぶ。辺の[採用]は採用後も disabled にならない（7790。gap は 7355 で disabled）。
    gap の却下の説明（7374 GAP_DISMISS_NOTE）が採用の説明と取り込みの案内の間に置かれ、どの操作の結果かを読み違える。
    構造軸 none（medium: 状態は decision 行として既に一級）。
generalization:
  level: repo_pattern
  general_form: "AI の候補を人が判断した後も、候補の出所ラベルだけが残って判断の主体・日時が表示されず、判断済みと未判断が同じ見た目になる"
pattern: available-but-unwired
discovery:
  perspective: [reproduction, data_inspection]
  note: "教員ペルソナが採用後の行に「AIによる検出（未確認）」と[採用]が並ぶのを紛らわしいと記し、判断の帰属が出ないことを指摘した。応答に decided_by / decided_at があることと、admin.js のカード描画を突き合わせた。 run: uxsim/runs/c-astro-r25-atlas-r1/20261022T123418Z-31802（頭脳の所見 uxsim/runs/round25/BRAIN_NOTES_atlas.md）。"
resolution:
  perspective: [canonical_source]
  note: >-
    判断の帰属は route 層で core/atlas_review_display.py の project_decisions / project_decision_for_response を通して表示名に写し（UUID は外す・削除済みアカウントの墓標名も出さない）、decided_on / decision_line / provenance_line（「AIによる検出 → 〇〇が 日付 に採用しました」）を返す。admin.js は判断のある候補で出所ラベルをこの 1 行に置き換え、見送りから戻した候補は未確認のラベルのまま判断の行を添える。採用済みの辺は[採用]と種類の選択を disabled にし、却下・見送りの説明は理由を尋ねる prompt に移した。
  landed_in:
    - "backend/core/atlas_review_display.py"
    - "backend/api/routes/atlas_gaps.py"
    - "backend/api/routes/atlas_edges.py"
    - "frontend/public/js/admin.js"
    - "backend/tests/test_atlas_review_round25.py"
    - "backend/core/account_display.py"
    - "backend/db/094_users_profile_name.sql"
    - "backend/tests/test_account_display_guardrails.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "gap と辺の 2 つのカードに帰属の写しを書き分けず、route 層の 1 つの投影を通す。第 5 波では名前の読みも core/account_display.py 1 箇所に寄せた（面を跨ぐ）。"
  verification:
    methods: [guardrail]
    unverified:
      - "第 5 波の profile_name 化はブラウザ・砂場での再演が未実施（既存アカウントは名前の登録が無いので「名前を登録していない利用者」と出る）"
      - "採用済みの辺の種類を変える操作は作っていない（必要なら別の操作として足す）"
      - "ブラウザでの表示は未確認"
related: [IK-0660]
view_of: []
history: []
---

## 課題

症状: 星間磁場を採用した後も同じ行に「AIによる検出（未確認）」が残り、[採用]の印と並ぶ。辺も同様で、採用後も
[採用]が押せる表示。見送り済みの表示では判断のメモは読めるが、誰がいつ判断したかは採用・見送りとも出ない。
原因: API は判断の帰属と日時を返しているが、カード描画が使わず、出所ラベルを判断の有無で切り替えない。

## 発見の観点

ペルソナの通し受講（reproduction）と応答の読み取り（data_inspection）。

## 解決の観点

判断の帰属を route 層で表示名と判断の 1 行に写し、出所ラベルを判断の行に置き換える。

## 一般化

候補→確定の画面で、確定後も候補時の表示が残ると、確定の主体と未確定の区別が画面から再構成できない。
確かめ方は、採用・見送りの後に別の教員で一覧を開き、判断した人と日付と判断の種類が 1 行で読めることを見ること。

## 第 2 周の再演（2026-10-23・campaign c-astro-r25-atlas-r2）

- 応答では消えた: edge-candidates の各判断に `decision_line`（「〇〇が 日付 に採用しました」）と `provenance_line`
  （「AIによる推定 → 〇〇が …」）が載っている（run 20261022T220051Z-74929 seq 4〜7 の応答）。
- 画面では未確認: ハーネスの投影（`atlas_exec.gap_card_lines` / `edge_card_lines`）がこの 2 行を描かず、採用済みの
  [採用]を押せない状態も写していないため、ペルソナは「誰が採用したか出ない」と書き、審判 B12 が同じ投影を製品の課題として
  拾った（偽所見 — 「harness_fixes_atlas.md」 第 2 周 H11）。
- 是正者向けの差分（小）: 帰属の表示名が利用者名のまま助詞に続く（「uxsim_te_02_veteran_introが」）。表示名に姓名が無い
  アカウントでは読みにくいので、区切り（「〇〇 が」か「〇〇（教員）が」）を `atlas_review_display.decision_line` で入れる。

- 第 2 波で是正（2026-10-23）: `atlas_review_display.decision_line` が利用者の表示名を鉤括弧で区切る（「「uxsim_te_02」が 日付 に採用しました」。固定の表示語は区切らない）。

## 第 3 周の再演（2026-10-23・campaign c-astro-r25-atlas-r3）

- GONE（本体）: 採用・見送りとも「AIによる推定 → 『…』が 日付 に採用しました／見送りました」が出て、採用済みは［採用］と
  種類が押せない。見送りは判断のメモ付きで［見送りから戻す］まで 1 操作で読めた（run 20261023T032742Z-99530 seq 11 / 14）。
- 第 3 周の再演: 残存（帰属の名前がログイン ID に見える）。製品は users.display_name を出しているが、この製品では
  display_name がログインの利用者名を兼ねる（routes/auth.py のログイン照合）ため、砂場のペルソナ（uxsim_te_03_…）は
  機械的な ID に見える。分野の地図の面の欠陥ではなく、アカウントに「人に見せる名前」と「ログイン名」を分けて持つかの
  判断（judge_atlas_r3.md のオーナー判断候補）。

## 第 3 波の扱い（2026-10-23）

- 名前がログイン ID に見える残り（users.display_name がログイン名を兼ねる）は、アカウント層の別課題として
  **現状維持**とした（指揮者の判断・推奨どおり）。分野の地図の面は表示名を `atlas_review_display.fetch_actor_labels`
  から受け取るだけなので、アカウント層が表示名を別に持てば変えずに済む。
- 第 3 波で、帰属の一行は凍結の止め理由（`readiness.pending_items[].decision_line`）と反映の確認
  （incorporate-preview の `decision_line`）にも運ばれるようになった（IK-0939）。判断者の UUID は出さない。

第 4 周の再演（2026-10-25・campaign c-astro-r25-atlas-r4）: 残存（名前）。帰属は一覧・反映の確認・凍結の影響のすべてに出るが、名前がログイン ID の形（uxsim_te_0N_…）。display_name がログイン名を兼ねる構造の事情で、オーナー判断待ちのまま。run: uxsim/runs/c-astro-r25-atlas-r4/20261024T233234Z-25818 seq 2 / 10 / 13。

第 5 周の再演（2026-10-27・campaign c-astro-r25-atlas-r5）: STILL（名前）。帰属は外れた採用・反映の確認・凍結後の確認者に出るが、名前はログイン ID の形（uxsim_te_03_meticulous_seminar / uxsim_te_05_delegator_lab）のまま。表示名の層はオーナー判断待ち。run: uxsim/runs/c-astro-r25-atlas-r5/20261027T070623Z-23509 seq 2 / 7 / 10 / 15。

## 第 5 波の是正（2026-10-27・面を跨ぐ「表示名」— IK-1007 と同時）

- 残っていた「名前がログイン ID の形」を是正した。オーナー判断（推奨どおり）: ログイン ID は見せない。
  migration 094 の `users.profile_name`（本人が登録する、人に見せる名前）を足し、`atlas_review_display.fetch_actor_labels`
  は `core/account_display.py` の `display_label` を通して `profile_name` だけを読む。登録が無ければ
  「名前を登録していない利用者」（鉤括弧で区切らない固定の主語）で、ログイン ID へフォールバックしない。
  `project_decision_for_response` はトークンの利用者名（ログイン ID）を表示に使う分岐を外し、常に `users` から引く。
- 分野の地図の面は表示名を `fetch_actor_labels` から受け取るだけなので、面の側の描画は変えていない（第 3 波の見立てどおり）。


## 第 6 周の再演（第 25 周・面 C 第 6 周、2026-10-27・campaign c-astro-r25-atlas-r6）

GONE（第 6 周の観察）。帰属は「江口 大地」「堀 正和」「水野 早紀」と人の名前で出た（判断のカード・反映の確認・凍結の影響・確認者）。砂場で名前を登録したうえでの観察で、未登録の利用者の表示は第 6 周では見ていない。run: uxsim/runs/c-astro-r25-atlas-r6/20261027T122507Z-5200 seq 4 / 6、uxsim/runs/c-astro-r25-atlas-r6/20261027T122716Z-6544 seq 13 / 19。

## 第 7 周の再演（第 25 周・面 C 第 7 周、2026-10-28・campaign c-astro-r25-atlas-r7）

GONE（観察）。帰属は「堀 正和」（採用・見送り）・「江口 大地」（見送り）・確認者「西川 智也」で、ログイン ID は出ない。run: uxsim/runs/c-astro-r25-atlas-r7/20261027T171656Z-52459 seq 4 / 7、uxsim/runs/c-astro-r25-atlas-r7/20261027T171933Z-54352 seq 12。
