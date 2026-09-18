---
id: IK-0322
title: 前提が揃わないまま一括生成を始められ、飛ばされた分があっても完了として扱われる
status: resolved
recorded_at: 2026-07-14
resolved_at: 2026-09-03
sources:
  - docs/features/lecture_audio_generation_readiness.md §1
  - docs/features/lecture_audio_generation_readiness.md §4
feature_context:
  realizing: 読み上げ原稿が揃っているコースに対して、音声をまとめて生成する
  layers: [lecture_studio, lecture_player, frontend_admin_ui]
classification:
  primary: governance
  facets: [governance.completion, governance.ordering]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    原因は「生成の可否を代理の指標（コース内容が生成済みか・チャンクがあるか）で判定し、
    実際に必要な前提（対象それぞれに読み上げ原稿があるか）を見ていないこと」。処理も
    表現も正しいが、開始条件と完了の定義が崩れているため統制。設計書 §1 が
    「ワーカーがスキップするため、タスクが完了しても一部に音声がない状態を成功として
    扱い得る」と記録している。開始前にモーダルが開く（対象が決まる前に言語を選ばせる）
    順序の乱れも同じ節の症状である。
generalization:
  level: general
  general_form: 開始条件を代理指標で判定し、飛ばされた対象があっても処理全体を成功として扱う
pattern: completion-defined-by-proxy
discovery:
  perspective: [boundary_walk, symptom_report]
  note: >-
    「コースを選ぶ前に言語選択が開く」という症状から操作の状態遷移を書き起こし、
    生成開始の条件・ワーカーのスキップ規則・完了の扱いを並べたときに、完了の定義が
    実態と合っていないことが分かった。
resolution:
  perspective: [canonical_source, fail_closed]
  note: >-
    ①操作を状態起点にする（コースを選ぶ → 読み上げ可能を確認 → 言語を選ぶ → 生成）
    ②開始条件を画面とサーバの両方で強制し、不足があれば理由と次の操作を示して拒否する
    ③準備完了の判定はサーバ側の 1 つの関数を正本にし、案内とボタンの活性が食い違わない
    ようにする。ワーカーのスキップを黙認したまま完了と呼ぶ扱いはやめた。
  landed_in:
    - backend/core/lecture.py
    - backend/api/routes/lecture_studio/scripts.py
    - frontend/public/js/admin-lecture-studio.js
    - docs/features/lecture_audio_generation_readiness.md
related: [IK-0321, IK-0317]
view_of: []
history: []
---

## 課題

症状は二つ。①原稿スタジオを開いた直後、コースを選ぶ前に音声の言語選択が開き得る（対象が
決まっていないので選ぶ意味が定まらない）。②読み上げ原稿が未生成の対象を含んだまま一括生成を
開始でき、ワーカーはそれを飛ばすため、**完了と表示されても一部に音声が無い**。

原因は、生成の可否を代理の指標（コース内容が生成済みか・チャンクがあるか）で判定し、実際に
必要な前提（対象それぞれに読み上げ原稿があるか）を見ていないこと。飛ばした事実も完了の
判定に反映されない。

## 発見の観点

「コースを選ぶ前にモーダルが開く」という症状（`symptom_report`）から操作の状態遷移を書き
起こし、開始条件・スキップ規則・完了の扱いという境界を並べた（`boundary_walk`）。完了の定義が
実態と合っていないことがそこで見えた。

## 解決の観点

準備完了の判定はサーバ側の 1 関数を正本にして、案内・ボタンの活性・実行の可否が同じ根拠を
見るようにした（`canonical_source`）。そのうえで操作を状態起点の流れに直し、開始条件を
画面とサーバの両方で強制して、不足があれば理由を示して拒否する（`fail_closed`）。画面側
だけを直す案は、UI を経由しない呼び出しで同じ状態に戻るため採らなかった。

## 一般化

一括処理は「始められたか」と「全部できたか」を別々に定義しないと、スキップが成功に化ける。
開始条件は対象ごとの実体で確かめ、完了は飛ばした分を含めて定義する。
辞書の型 `completion-defined-by-proxy` に対応する。
