---
id: IK-0994
title: "関係の候補を見送ると、カードが既定表示から消えるだけで「見送りました」の一文が出ない — 採用は帰属・メモつきで先頭に残るのに、見送りの結果は［見送り済みも表示］を開かないと確かめられない"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/atlas_relation_edges_design.md"
feature_context:
  realizing: "教員が関係の候補を理由つきで見送り、その判断が記録されたかを確かめる"
  layers: [relation_edges_re, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [review]
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
    接続軸 information: admin.js edgeDecide は成功時に setEdgesStatus('') として一覧を読み直すだけで、結果の事実文を出さない。採用はカードが先頭帯に残るので結果が読めるが、見送りは既定表示（見送り済みオフ）からカードが消える。統制軸 review: 判断の記録（帰属・日付・理由）はサーバに残るが、判断した本人に結果が返らない。構造・処理軸 none。
generalization:
  level: repo_pattern
  general_form: "肯定の判断は結果が画面に残り、否定の判断は対象が消えることでしか結果が分からない"
pattern: negative-decision-second-class
discovery:
  perspective: [reproduction]
  note: "教員が『ブラックホール — 暗黒エネルギー』を見送り、『見送った、という一文は出ず、消えたことでしか分からない』と記した（審判 C が拾った）。［見送り済みも表示］で帰属・日付・理由つきで残っていることは確かめた。run: uxsim/runs/c-astro-r25-atlas-r5/20261027T071308Z-52116 seq 5 / 7。"
resolution:
  perspective: [carry_through]
  note: >-
    decide の応答に result_line（組の名前の結果文・正本 atlas_review_display.edge_decide_result_line。採用 / 見送り / 戻す）を足し、admin.js edgeDecide は結果文を出したまま一覧を読み直す（loadEdgeCandidates(keepStatus)。反映の結果文が読み直しで消える既存の取りこぼしも同時に直した）。
  landed_in:
    - "backend/api/routes/atlas_edges.py"
    - "backend/core/atlas_review_display.py"
    - "frontend/public/js/admin.js"
    - "docs/manual/teacher/17-admin-atlas.md"
    - "backend/tests/test_atlas_edges_admin_ui_static.py"
    - "backend/tests/test_atlas_review_round25_r5.py"
  verification:
    methods: [guardrail]
    unverified:
      - "候補の区画（gap）の却下・戻すの結果文は揃えていない"
      - "画面での見え方はブラウザで確かめていない"
related: [IK-0887, IK-0938]
view_of: []
history: []
---

## 課題

症状: 見送りの直後、一覧から見送った組が消え、結果を言う文が無い。見送り済みを表示すると帰属・日付・理由つきで出る。

原因: 判断の成功時に事実文を出さず、一覧の読み直しに任せている。

## 発見の観点

通し受講で教員が記した（reproduction）。

## 解決の観点

第 5 波（2026-10-27）で解決。判断の結果を組の名前で言う。

## 一般化

判断の操作は種類（採用・見送り・戻す）にかかわらず結果を名前で言う。確かめ方: 見送り直後の画面に結果の一文があることを見る。



## 第 6 周の再演（第 25 周・面 C 第 6 周、2026-10-27・campaign c-astro-r25-atlas-r6）

GONE。te-02 の見送りで「「宇宙背景放射」と「宇宙線」の関係の候補を見送りました（理由を記録しました）。［見送り済みも表示］で確かめられます。」、採用で「「惑星形成」と「星形成」を関係として採用しました。次版の下書きへの反映は別の操作です。」。見送り済みの表示で帰属・日付・理由・［見送りから戻す］が読めた。残り（軽・起票しない）: 見送りの理由の欄名が採用と同じ「判断のメモ」、見送り済みが一覧の途中に混ざって並ぶ。run: uxsim/runs/c-astro-r25-atlas-r6/20261027T122507Z-5200 seq 4 / 5 / 7。
