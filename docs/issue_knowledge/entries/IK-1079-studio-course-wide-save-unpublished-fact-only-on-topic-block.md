---
id: IK-1079
title: "原稿スタジオでコース全体を変える操作（付録をまとめて付け直す）の後、「版 N の発行の後に保存した変更があります」と［版の管理を開く］がトピックを開くまで出ず、コースの見出し・一括の知らせ・版の管理モーダル（発行を決める画面）にも未発行の変更の事実が無い"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
feature_context:
  realizing: "所有者がコース全体の付録を付け直し、変えた内容を発行して受講者に届ける（発行が要るかを、操作した画面と発行の画面で判断する）"
  layers: [lecture_studio, versioning_v, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [completion]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確定（コードと生応答で読んだ）: サーバは未発行の変更の事実を 3 経路に載せている — course-structure のトップレベル
    `unpublished_changes_note` と `delivery`、付録の一括付け直しの応答 `delivery`（unpublished_changes: true・note・versions_entry）。
    しかし画面は `admin-lecture-studio.js::lsTopicDeliveryInnerHtml`（トピックの区画）だけが note と［版の管理を開く］を描き、
    `lsBindReattachAllAppendicesButton` の知らせは `delivery.message` だけを足して note と入口を捨てる。コースの見出しには描く場所が無く、
    V層の版の管理モーダル（`versioning.js`）は「現在の最新版: v4」と履歴だけで HEAD と版の差を言わない。接続軸 information
    （値はあるのに描く面へ配線されていない）。統制軸 completion（「保存した」と「受講者に届いた」の間の発行という手続の未完了が、
    コース単位の操作の直後と発行の画面で読めない）。IK-1053 の是正はトピックの保存を入口に組んだため、コース単位の操作は経路から外れた。
generalization:
  level: repo_pattern
  general_form: "サーバは状態の事実を全経路の応答に載せるが、画面はその事実を一つの面（個別項目の区画）でしか描かず、まとめての操作の直後と、その事実で判断する画面に届かない"
pattern: available-but-unwired
discovery:
  perspective: [trace_walk, symptom_report]
  note: >-
    第 25 周 面 D 第 9 周。te-01 が 6be229e1（版 4 = HEAD）で［付録をまとめて付け直す］→ 15 トピックを保存。知らせは
    「…新しい版を発行してください。」まで（「版 4 の発行の後に…」と［版の管理を開く］は無い）、コースの見出しにも出ず、
    ［版の管理］を開くと v4 と履歴だけ。頭脳は「発行すると何が新しく届くのかがここでは読めない。迷うので今は発行しない」と
    発行を見送った（C 所見 a19e5556…）。続く split-save でも、コースを開いた時点では見出しに何も無く、t16 を開いて初めて
    「版 4 の発行の後に保存した変更があります」+［版の管理を開く］が見えた。
    run: uxsim/runs/c-astro-r25-studio-r9/20261031T012109Z-34801 seq4・5・7、uxsim/runs/c-astro-r25-studio-r9/20261031T015111Z-37232 seq4・6・11。
resolution:
  perspective: [canonical_source, single_point_fix]
  note: >-
    第 25 周 第 9 波（2026-10-31）: 受講者の版と HEAD の比較を core の 1 本 `lecture_studio_state.course_release_comparison` に移し、
    事実文は `unpublished_changes_facts`（文の正本は従来の `TOPIC_UNPUBLISHED_CHANGES_NOTES`）。原稿スタジオの保存の届き方と
    V層 `GET /shared/course/{id}/version-state`（所有者だけ `unpublished_changes` / `unpublished_changes_note` を追加）が同じ比較を通す。
    画面は描く部品を `admin-lecture-studio.js::lsUnpublishedChangesHtml` 1 つにし、トピックの区画・コースの見出し（所有者・変更ありのとき・
    ［版の管理を開く］付き）が通す。一括付け直しの知らせにも note を足し、版の管理モーダル（versioning.js）は所有者に同じ文を描く。
    モーダルを閉じたとき、トピック未選択ならコース構造を取り直す（見出しの事実が更新される）。自動発行・督促はしない。
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "比較と事実文を 1 箇所に置き、トピック区画・コース見出し・一括の知らせ・版の管理モーダルの 4 面が同じ値を描く。"
  landed_in:
    - "backend/core/lecture_studio_state.py"
    - "backend/api/routes/lecture_studio/topics.py"
    - "backend/api/routes/versioning.py"
    - "frontend/public/js/admin-lecture-studio.js"
    - "frontend/public/js/versioning.js"
    - "backend/tests/test_lecture_studio_round25_r9.py"
    - "backend/tests/test_lecture_studio_round25_r7.py"
  verification:
    methods: [guardrail]
    unverified:
      - "所有者が一括付け直し → 見出し → 版の管理モーダルで発行に至るかは実機（第 10 周 te-01）で見ていない"
      - "JS の構文検査（node）はこの環境に node が無く未実施"
related: [IK-1053, IK-1070, IK-0915]
view_of: []
history: []
---

## 課題

症状: コース全体の付録を付け直して保存した所有者が、発行が要るか・発行すると何が届くかをその場と版の管理で読めず、発行を見送る。未発行の事実はトピックを 1 つ開いたときにしか見えない。

原因: サーバは事実を全経路に載せているが、画面はトピックの区画にしか描いていない（一括の知らせは文だけ拾い、見出しとモーダルには描く場所が無い）。

## 発見の観点

まとめての付け直しから発行までを所有者の画面で歩き（trace_walk）、ペルソナが発行を見送った（symptom_report）。生応答に事実があり画面に無いことをフロントのコードで確かめた。

## 解決の観点

第 25 周 第 9 波で是正（resolution 参照）。事実を描く部品を 1 つにし、見出し・一括の知らせ・版の管理モーダルの 3 面がそれを通す。

## 一般化

状態の事実をサーバが全経路に載せたら、その事実で判断する画面（発行の画面）と、事実を変える操作の直後の画面の両方に描く。確かめ方: 個別の区画を開かずに、まとめての操作 → 発行の画面だけで発行が要るか読めるかを歩いて見る。

## 第 10 周（第 25 周・面 D 第 10 周、2026-10-31・judge_studio_r10）

**GONE（実機）**。te-01 が付け直しだけの段階（3.2 / 3.3）で［付録をまとめて付け直す］を押すと、知らせの末尾・コースの見出し（［版の管理を開く］付き）・版の管理モーダル（「現在の最新版: v5」の下）の 3 か所に「版 5 の発行の後に保存した変更があります。受講者はまだ版 5 を見ています。」が揃い、見出しからモーダルを開いて自分のメモで v6 を発行した（第 9 周は同じ段階で「何が届くか読めない」と見送り）。閉じると見出しの文が消えた。同条件の前後比較（固定コース・同じ操作の段階）。残る迷いは受講者と共有先の宛先の言い分け（IK-1127）。run: uxsim/runs/c-astro-r25-studio-r10/20261031T052922Z-65584 seq5・7・8。
