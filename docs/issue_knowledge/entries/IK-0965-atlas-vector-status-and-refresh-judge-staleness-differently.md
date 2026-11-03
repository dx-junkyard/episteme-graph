---
id: IK-0965
title: "ベクトル索引の状態が「作り直す必要はありません」と言った直後に再構築すると「変わった項目だけ新しく索引しました」になる — 状態は行の本数だけで要否を判定し、再構築は項目の材料（別名・確認済み配置の引用）の変化で判定する"
status: resolved
recorded_at: 2026-10-25
resolved_at: 2026-10-25
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/atlas_vector_anchoring_design.md"
feature_context:
  realizing: "教員が凍結の前にベクトル索引の状態を読み、作り直しが要るかを判断する"
  layers: [vector_anchoring_va, field_atlas_s, frontend_admin_ui]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [condition]
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
    接続軸 condition: routes/atlas_vectors.py の状態は coverage_status が数える索引の行（total_nodes と embedded_nodes）と
    「現行版の行が無く別版だけある」で要否を決め、vector_status_facts が「作り直す必要はありません」と言う。
    atlas_vectors/builder.py の再構築は項目ごとの source_hash（label + 確認済み別名 + 確認済み配置の引用）の変化で
    埋め込み直しを決める。同じ『要るか』を 2 つの条件で判定し、状態は材料の変化を見ない。処理軸 logic（要否の判定式）。
    実測: 状態は全項目を索引済みと言い、直後の再構築の応答は embedded が 1 件（変化した項目がある）。どの項目の材料が
    変わったかは run からは確定していない（応答に項目名が無い）が、条件の食い違いはコードで確定。
generalization:
  level: repo_pattern
  general_form: "同じ『作り直しが要るか』を表示側は件数で、実行側は内容の指紋で判定し、表示が要らないと言った直後に実行が変化を見つける"
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [reproduction, data_inspection]
  note: "教員が『作り直す必要はありません』を読んだ後に再構築し、結果文が何かが変わったように読めると記した。再構築の応答（embedded / reused）と状態の判定式を突き合わせた。run: uxsim/runs/c-astro-r25-atlas-r4/20261024T233234Z-25818 seq 3 / 4。"
resolution:
  perspective: [canonical_source]
  note: >-
    要否の判定を 1 つにした。atlas_vectors/builder._attach_source_hashes を再構築と索引の状態（material_changed_node_ids・embedding を呼ばない）が共有し、状態は材料が変わった項目があれば「作り直す必要はありません」と言わず VECTOR_INDEX_MATERIAL_CHANGED で項目名を列挙する。再構築の結果文は embedded_node_ids を名前に写し「新しく索引した項目: …」と言う（件数なし）。
  landed_in:
    - "backend/core/atlas_vectors/builder.py"
    - "backend/api/routes/atlas_vectors.py"
    - "backend/core/atlas_review_display.py"
    - "backend/tests/test_atlas_review_round25_r4.py"
  verification:
    methods: [guardrail]
    unverified:
      - "実 DB で配置を確認した直後の状態表示は確かめていない"
related: [IK-0889]
view_of: []
history: []
---

## 課題

症状: 「現在の公開版の全項目を索引しています。作り直す必要はありません」の直後に［索引を再構築］を押すと
「索引を作り直しました（変わった項目だけ新しく索引しました）」。教員は何かが変わったのか、作り直しは要ったのかが読めない。

原因: 状態は行の本数で、再構築は材料の指紋で「要るか」を判定する。

## 発見の観点

通し受講（reproduction）で教員が記し、再構築の応答と状態の判定を突き合わせた（data_inspection）。

## 解決の観点

第 4 波（2026-10-25）で解決。要否の判定を 1 つにした。

## 一般化

「要る/要らない」を言う画面と、それを実行する処理が同じ条件を見ているかを確かめる。確かめ方: 確認済み配置を 1 件足した
後に状態を開き、作り直しが要ると言うことを見る。

第 5 周の再演（2026-10-27・campaign c-astro-r25-atlas-r5）: 解消を確認。状態が「説明の材料…が変わった項目があります: ダスト」と名前で言い、再構築の結果文「新しく索引した項目: ダスト」の後は「作り直す必要はありません」に変わり、前後の判定が一致した。凍結の直後の索引の空白期間の言い方は別課題 IK-0993。run: uxsim/runs/c-astro-r25-atlas-r5/20261027T070623Z-23509 seq 2 / 3 / 4。
