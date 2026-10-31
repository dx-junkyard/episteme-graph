---
id: IK-0941
title: "論文の海で、領域そのものに置かれた論文の円が「宇宙論・大規模構造（霧の領域）」と名乗り、同じ画面の領域の塗り「少しある」と矛盾する（分野の地図の API が霧の状態を領域ノードの名前に焼き込み、海がその名前を借りる）"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/corpus_roaming_design.md"
feature_context:
  realizing: "学習者が論文の海で、領域そのものに置かれた論文の円を選んで論文の一覧を絞り込む"
  layers: [corpus_roaming, field_atlas_s, frontend_learning_ui]
classification:
  axes:
    processing: [wording]
    structure: [representation]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸 representation: atlas_view.py（433 付近）は霧の領域を詳細パネルで選べるよう nodes[region_id] に
    label = 「{領域名}（霧の領域）」を入れる — 状態を名前の欄に書いている。corpus-sea.js anchorLabel は円の名前を
    「概念ノード → 骨格ノード辞書（nodes）→ 領域 → id」の順で引くので、領域アンカーは状態入りの名前を拾う。海は
    IK-0847 で霧を自分の配置から決めるので、塗りは「少しある」なのに名前は骨格側の霧を言う。接続軸 meaning: 同じ
    領域を同じ画面で 2 つの状態で呼ぶ。処理軸 wording（表示名の組み立て）。統制軸 none。IK-0940 が直れば霧は消えるが、
    状態を名前に焼く表現は残る（海で霧の領域に配置が付いた場合など、判定が食い違うたびに再発する）。
    確認: run 20261023T070912Z-18387 seq 4（「宇宙論・大規模構造: 少しある」と「宇宙論・大規模構造（霧の領域）（円の大きさ: 小）」）。
generalization:
  level: repo_pattern
  general_form: "ある画面の状態表示を項目の名前の欄に焼き込み、別の判定を持つ画面がその名前を借りて、状態の違う所で古い状態を名乗る"
pattern: wording-mismatch
discovery:
  perspective: [reproduction, trace_walk]
  note: "データサイエンス出身の学生ペルソナが海の塗りと円の名前の食い違いを記した。corpus-sea.js anchorLabel と atlas_view の nodes の label を辿った。 run: uxsim/runs/c-astro-r25-atlas-r3/20261023T070912Z-18387。"
resolution:
  perspective: [representation_change]
  note: >-
    状態を名前の欄に書かない。atlas_view.py の霧の領域ノードの label は領域名のまま（霧であることは pill が言う）。corpus-sea.js の anchorLabel は領域アンカーなら
    regionById の名前を最初に引く（骨格の詳細パネル用の辞書を借りない）。atlas-fixture.js の霧ノード名も同様に直した。
  landed_in:
    - "backend/api/routes/atlas_view.py"
    - "frontend/public/js/corpus-sea.js"
    - "frontend/public/js/atlas-fixture.js"
    - "backend/tests/test_atlas_review_round25_r3.py"
  verification:
    methods: [guardrail]
    unverified:
      - "論文の海の画面で円の名前が「（霧の領域）」を名乗らないことは再演で未確認"
related: [IK-0847, IK-0940]
view_of: []
history: []
---

## 課題

症状: 論文の海の地図で、領域の塗りは「宇宙論・大規模構造: 少しある」、同じ領域に置かれた論文の円は「宇宙論・大規模構造
（霧の領域）」。星間物質・高エネルギー・観測の領域も同じ。学生は同じ領域が霧なのか論文があるのか分からない。

原因: 分野の地図の API が霧の状態を領域ノードの名前に焼き、海の円の名前がそれを借りる。

## 発見の観点

通し受講（reproduction）で学生が塗りと名前の食い違いを記し、名前の引き方を辿った（trace_walk）。

## 解決の観点

第 3 波（2026-10-23）で是正。状態を名前の欄に書かない。

## 一般化

状態を名前の文字列に混ぜると、名前を再利用する別画面で状態がずれて残る。確かめ方: 骨格側で霧・海で配置ありの領域に
論文を置き、海の円の名前に状態語が付かないことを見る。
