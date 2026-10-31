---
id: IK-1019
title: "凍結の影響で［止めている候補を見る］を押すと、止めているのが関係（辺）でも候補の区画の先頭（論文由来の候補・0 件）へ移るだけで、止めているカードには着地しない"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/atlas_relation_edges_design.md"
  - "docs/features/category_gap_candidates_design.md"
feature_context:
  realizing: "教員が凍結の影響で止める理由を読み、止めている採用済みの判断へ移って反映する"
  layers: [field_atlas_s, relation_edges_re, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [target]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 target: admin.js の #atlas-freeze-impact-goto は止める理由の種類（gap の採用済み・未反映 / 関係の採用済み・未反映）を見ずに
    scrollToAtlasSection('atlas-reports-section') へ移る。区画の先頭は論文由来の候補のグループで、関係の候補はその下。凍結の影響の応答は
    止めている組の名前を持っているので、着地先は決められる。処理・構造・統制軸 none。
generalization:
  level: repo_pattern
  general_form: "止める理由を名前で言う確認画面の『そこへ行く』導線が、理由の対象ではなく複数の対象を含む区画の先頭へ移る"
pattern: last-mile-missing
discovery:
  perspective: [reproduction, trace_walk]
  note: "新任の教員 te-01 が凍結の影響（止める理由: 惑星形成 — 星形成）から［止めている候補を見る］を押し、移った先の先頭が『論文の解析から見つかった候補 … いまレビューする候補はありません』で、止めているカードは次の『関係（辺）の候補』の 1 枚目だった。admin.js の click handler で区画単位のスクロールであることを確かめた。run: uxsim/runs/c-astro-r25-atlas-r6/20261027T122716Z-6544 seq 13 / 14。"
resolution:
  perspective: [carry_through, single_point_fix]
  note: >-
    凍結の可否の pending_items[] に表示しない番地 target_key（関係 = edge_key / 論文由来の候補 = cluster_key）を足し（core/atlas_review_display.py
    pending_freeze_items）、admin.js の［止めている候補を見る］を scrollToFreezeBlocker に替えた。先頭の理由のカード（[data-edge-key] /
    [data-cluster-key] の一致）へ scrollIntoView(center) で着地し枠を一時強調する。カードが無ければその種類のグループ（atlas-edges-group /
    atlas-gaps-group）、それも無ければ区画。凍結の 409 経路（押してから止まる場合）は従来どおり区画へ移る。
  landed_in:
    - "backend/core/atlas_review_display.py"
    - "frontend/public/js/admin.js"
    - "frontend/public/admin.html"
    - "docs/manual/teacher/17-admin-atlas.md"
    - "backend/tests/test_atlas_review_round25_r6.py"
  verification:
    methods: [guardrail]
    unverified:
      - "管理画面での着地（スクロール位置と枠の強調）はブラウザで確かめていない"
      - "見送り済みフィルタなどで止めているカードが一覧に無いときの縮退（グループへ移る）"
related: [IK-0885, IK-0937]
view_of: []
history: []
---

## 課題

症状: 凍結の影響が「惑星形成 — 星形成」が止めていると名前で言うのに、［止めている候補を見る］の行き先は候補の区画の先頭で、0 件の論文由来の候補の
説明が先に来る。止めているカードはスクロールして探す。

原因: 導線が区画（atlas-reports-section）単位で、止める理由の種類と対象を見ていない。

## 発見の観点

通し受講（reproduction）と click handler の読み（trace_walk）。第 6 周は候補 0 件で、区画の先頭が空の説明だったため、行き先のずれが目立った。

## 解決の観点

第 6 波（2026-10-27）で解決。止める理由の番地をサーバが添え、導線はそのカードへ着地する。

## 一般化

型 last-mile-missing。確認画面の理由と「そこへ行く」導線は同じ対象を指す。確かめ方: 関係だけが止めている状態で凍結の影響から移り、最初に見える
カードが止めている組であること。

## 第 7 周の再演（第 25 周・面 C 第 7 周、2026-10-28・campaign c-astro-r25-atlas-r7）

未観測。te-05 の凍結の影響（2026.4）は止める条件なしで、［止めている候補を見る］は出なかった（採用済み・未反映は te-05 自身が先に反映した）。run: uxsim/runs/c-astro-r25-atlas-r7/20261027T171933Z-54352 seq 10〜11。

## 第 8 周の再演（2026-10-28・judge_atlas_r8）

**未観測（入口は出た）**。te-01 の 1 回目（run uxsim/runs/c-astro-r25-atlas-r8/20261027T223540Z-94369 seq 9）で凍結の影響が「採用済みでまだ次版の下書きに入っていない関係があるため…暗黒エネルギー — 暗黒成分の物理」と［止めている候補を見る］・［公開できません］（押せない）を出した。止める条件を名前で言うことは確認。［止めている候補を見る］は押されず着地は未観測。

## 第 25 周 面 C 第 9 周（2026-10-31・judge_atlas_r9）

**未観測**。te-03 の凍結の影響（run uxsim/runs/c-astro-r25-atlas-r9/20261031T015006Z-36651 seq 8〜9）は止める条件なし（反映済み）で、［止めている候補を見る］は出なかった。
