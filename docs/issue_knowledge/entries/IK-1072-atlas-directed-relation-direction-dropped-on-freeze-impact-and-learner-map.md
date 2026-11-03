---
id: IK-1072
title: "有向の関係（依存）の向きが、反映の確認と下書きの一覧では一文で読めるのに、凍結の影響（「次版で加わる関係: 宇宙膨張 — 一般相対論（依存）」）と学生の地図 L2 の実線（無向の線・向きの語なし）では落ちる"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/atlas_relation_edges_design.md"
feature_context:
  realizing: "教員が確定した有向の関係（依存）を、凍結の前の確認と学生の分野の地図で、どちらがどちらに依存するかとして読む"
  layers: [field_atlas_s, frontend_admin_ui, frontend_learning_ui]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 information: 関係の向き（from → to）は骨格の edges に保存されており、反映の確認（incorporate-preview の direction_line）と
    下書きのプレビュー（「「A」が「B」に依存する」）は向きを運ぶ。凍結の影響は core/atlas_lifecycle.py の edge_change_label が
    「A — B（依存）」で種別の語だけを付け、学生の L2 は routes/atlas_view.py が levels["2"].edges を [a, b] の組だけで送り
    atlas-overlay.js が矢印なしの線を引く（kind も方向も落ちる）。同じ関係が面によって向きを失う。処理軸 wording: 種別の語「依存」は
    向きの一文の代わりにならない（「A — B」の並びが向きと読める保証が無い）。構造軸 none（向きの正本は 1 つで、各面がそれを読む構造は
    ある）、統制軸 none。
generalization:
  level: repo_pattern
  general_form: "保存された関係の属性（向き）を、ある面では文にして運ぶが、同じ関係を列挙する別の面では種別の語や無向の記号に縮めて落とす"
pattern: rule-enforced-per-surface
discovery:
  perspective: [reproduction, trace_walk]
  note: "te-03 の凍結の影響（run uxsim/runs/c-astro-r25-atlas-r9/20261031T015006Z-36651 seq 8）で頭脳が「依存とだけあって向きの一文が無い」と記し、同じ関係が te-04 の反映の確認（20261031T003210Z-30687 seq 7）と下書きの一覧（te-03 seq 2）では向きの一文で出ていた。学生 st-03（20261031T015252Z-38248 seq 6）の L2 の実線も「宇宙膨張 — 一般相対論」で、edge_change_label・levels[\"2\"].edges・atlas-overlay.js の L2 の描画を突き合わせた。"
resolution:
  perspective: [canonical_source, carry_through]
  note: >-
    向きの句の正本を label_vocab.edge_direction_phrase（依存だけ・「「A」が「B」に依存する」）と edge_kind_is_directed に置き、
    反映の確認・カード（atlas_review_display.edge_direction_line — 下書きに入った後は「記載されています」の現在形）、
    凍結の影響（atlas_lifecycle.edge_change_label →「A — B（依存: 「A」が「B」に依存する）」）、学生の L2
    （routes/atlas_view.py が levels["2"].directed_edges（from → to）と relations_note を返し、atlas-overlay.js は
    サーバの組にだけ矢じりを付けて事実文を同じ行に出す — 種別をフロントで解釈しない）、右パネル（nodes[*].relations /
    nodes_list[*].panel.relations = label_vocab.atlas_panel_relations_line の名前の列挙・件数なし）が同じ句を通る。
    「この版で加わった」印（NEW）は作らない。
  landed_in:
    - "backend/core/label_vocab.py"
    - "backend/core/atlas_review_display.py"
    - "backend/core/atlas_lifecycle.py"
    - "backend/api/routes/atlas_edges.py"
    - "backend/api/routes/atlas_view.py"
    - "frontend/public/js/atlas-overlay.js"
    - "frontend/public/js/atlas-panel.js"
    - "backend/tests/test_atlas_review_round25_r9.py"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場で有向の関係を採用 → 反映 → 凍結し、凍結の影響・学生の L2 の矢じりと事実文・右パネルで向きが読めること（第 10 周の再演）"
      - "矢じりの見え方（線の短さ・ノード半径との重なり）はブラウザで未確認（node / ブラウザ未実行）"
related: [IK-1047, IK-1045]
view_of: []
history: []
---

## 課題

症状: 第 9 周、te-04 が「宇宙膨張 — 一般相対論」を有向の「依存」で採用し、画面だけで下書きを作って反映した。反映の確認と下書きの一覧には
「「宇宙膨張」が「一般相対論」に依存する」が出る。ところが te-03 が凍結する前の確認には「次版で加わる関係: 宇宙膨張 — 一般相対論（依存）」と
種別の語だけが出て、どちらがどちらに依存するかは確認画面だけでは読めない（頭脳: 「確認画面だけを後から見る人には向きが分からない」）。
学生の地図 L2 では、凍結 2026.6 で加わったこの関係も他の確定済み 5 本と同じ無向の実線で、種別も向きも出ない。

原因: 向きの正本（骨格の edges の from / to）は 1 つあるのに、凍結の影響は `edge_change_label`（種別の語だけ）、学生の L2 は
`levels["2"].edges` の [a, b] 組と矢印なしの線、と面ごとに縮めて写している。IK-1047 は反映の確認の 1 面だけを直した。

## 発見の観点

前周の是正（IK-1047 = 反映の確認の向き）の続きとして、同じ関係を他の面で再演し（reproduction）、各面の組み立てを読んだ（trace_walk）。

## 解決の観点

第 25 周 第 9 波で解消（frontmatter の resolution）。是正前の案: 向きの一文を作る関数を 1 つにし、凍結の影響・学生の L2（事実文の行か矢印）・これまでの変更 の全てがそれを使う。学生の L2 で矢印を描くかは
原則①′（関係は離散の辺）の範囲の表示の工夫で、不変条項の解釈変更には当たらない。

## 一般化

型 rule-enforced-per-surface: 「向きを言う」を見えた面にだけ足すと、同じ関係を列挙する別の面で落ちる。確かめ方: 有向の関係を 1 本確定し、
採用 → 反映の確認 → 下書きの一覧 → 凍結の影響 → 学生の L2 の全てで「どちらがどちらに依存するか」が読めること。

## 第 10 周の再演（2026-10-31・judge_atlas_r10）

**GONE（是正した面）／ 残りは別エントリ IK-1118**。te-02 が有向の「依存」で「初期宇宙・インフレーション — 初期宇宙の物理」を採用し画面で下書き → 反映（run uxsim/runs/c-astro-r25-atlas-r10/20261031T052922Z-65581 seq 4〜8）、te-01 が凍結 2026.7（uxsim/runs/c-astro-r25-atlas-r10/20261031T053235Z-67481 seq 8〜10）。前後比較: 第 9 周の凍結の影響「次版で加わる関係: 宇宙膨張 — 一般相対論（依存）」→ 第 10 周「次版で加わる関係: 初期宇宙・インフレーション — 初期宇宙の物理（依存: 「初期宇宙・インフレーション」が「初期宇宙の物理」に依存する）」。学生の L2（同じ固定コース 1bad7d9f・同じ st-03 / st-01、uxsim/runs/c-astro-r25-atlas-r10/20261031T053524Z-69161 seq 6・uxsim/runs/c-astro-r25-atlas-r10/20261031T053658Z-69836 seq 6）は第 9 周の無向の実線 6 本 → 描いた有向 3 本が「A → B（依存）」（directed_edges）+「矢印のある線は向きのある関係です（矢印は依存する側から依存される側へ）: …」の名前の列挙。右パネル（st-03 seq 8）に「地図の関係: 「初期宇宙・インフレーション」が「初期宇宙の物理」に依存する。」。カードは下書きに入ると「…記載されています」の現在形。**有向 4 本のうち L2 の上限で描けなかった 1 本（惑星形成 → 星形成）は「ここには描いていないもの: 惑星形成 — 星形成」で向きが無く、反映の知らせも「A — B」** → IK-1118（同じ型の 4 回目）。矢じりの見え方は API runner のためブラウザ未確認のまま。

## 第 11 周の再演（2026-10-31・judge_atlas_r11）

**GONE（維持）**。凍結の影響「次版で加わる関係: 分子雲 — 惑星形成（依存: 「分子雲」が「惑星形成」に依存する）」（run uxsim/runs/c-astro-r25-atlas-r11/20261031T101612Z-580 seq 8）、学生の L2 の有向 5 本と右パネル。向きの言い方は全面で揃ったが、向きの決め方は IK-1149。
