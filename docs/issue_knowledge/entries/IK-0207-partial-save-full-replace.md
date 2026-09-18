---
id: IK-0207
title: 一部だけ編集した保存が設定全体を書き戻し、触っていない項目を既定値で上書きする
status: resolved
recorded_at: 2026-07-17
resolved_at: 2026-07-17
sources:
  - docs/architecture/vision_ux_gap_survey_2026-07-17.md §2 P2
  - docs/architecture/vision_ux_gap_survey_2026-07-17.md §4
  - docs/development_checklist.md §3
feature_context:
  realizing: 原稿の口調や読み上げ言語などコース単位の設定を教員が部分的に編集して保存する
  layers: [lecture_studio, frontend_admin_ui]
classification:
  primary: structure
  facets: [structure.responsibility, connection.contract]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    保存の処理も既定値の補完も、書かれたとおりに正しく動く。原因は、保存の入口が全体の
    書き戻ししか持たず「この項目は変更しない」という意図を表せないこと。送り手が知らない項目が
    設定に増えるたびに同じ事故が起きるため、入口の責務と表現を変えない限り再発する。
    送り手の「省略＝変更しない」と受け手の「省略＝既定値」が両立しない点は副次の契約の問題。
generalization:
  level: general
  general_form: >-
    一部の状態だけ変えたいのに全体を書き戻す入口しかなく、送り手が知らない項目が既定値で上書きされる
discovery:
  perspective: [boundary_walk]
  note: >-
    設定の保存と、その設定を前提に走る生成の連鎖という二つの機能の境界を意図的に歩いた。
    どちらの単体テストも通るが、片方の保存がもう片方の前提を黙って書き換えることが分かった。
resolution:
  perspective: [representation_change, explicit_contract]
  note: >-
    省略できる型にして「省略＝変更しない」を表現に持たせ、送り手側でも現在値を明示送信する
    二重の防御を入れた。加えて、この事故の型（設定の保存と生成の連鎖の境界）を新機能の
    完了条件として回帰テストに含める規律にした。
  landed_in:
    - backend/api/schemas.py
    - frontend/public/js/admin-lecture-studio.js
    - docs/development_checklist.md §3
related: [IK-0208, IK-0209]
view_of: []
pattern: full-update-clobbers-unrelated-fields
history: []
---

## 課題

**症状**: 原稿の口調だけを編集して保存すると、読み上げ言語が英語から日本語へ警告なしに戻る。
その状態で音声を作ろうとすると、言語が変わったことを理由に**全原稿の作り直しの連鎖**が
無警告で始まる。教員は口調しか触っていない。

**原因**: 保存の入口が設定全体の書き戻ししか持たず、送り手が送らなかった項目は受け手が
既定値で埋める。読み上げ言語は送り手の画面に無いため、毎回の保存が黙って既定値へ戻していた。
被害が大きいのは、この設定が後段の生成の前提として使われ、変化が連鎖を起動するため。

## 発見の観点

`boundary_walk`。「設定の保存」と「設定を前提とする生成の連鎖」という、それぞれ正しい二機能の
境界を意図的に歩いた。単体テストは両側に存在したが、境界をまたぐ回帰テストが無かった。

## 解決の観点

`representation_change`（省略という意図に表現上の居場所を作る）を主にし、`explicit_contract`
として「省略は変更しない」の意味をコメントと型で固定した。送り手側の明示送信は二重防御で、
片側だけの対策にはしていない。さらに `guardrail_fix` — この境界の型を完了条件に含めた。

## 一般化

設定・プロフィール・ポリシーなど「一部だけ編集する画面」と「全体を受け取る保存」の組み合わせ
すべてで再発する。項目が増えるほど危険が増し、増やした人は古い画面の存在を知らないのが
典型。辞書の型は `full-update-clobbers-unrelated-fields`。
