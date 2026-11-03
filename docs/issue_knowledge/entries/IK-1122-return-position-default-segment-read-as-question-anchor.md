---
id: IK-1122
title: "問いの痕跡の position_anchor.segment_id（寄り道からの復帰位置・未報告なら 0 — レクチャー外の app.js は常に 0）を、構造帰属の worker が「学習者が見ていた教材の区画」として読むため、分野の地図の場所（暗黒エネルギー・中性子星）について尋ねた問いが、トピックの先頭区画 seg_0（DCF 法・PINN の章）への帰属候補（connection / scope）として記帳される — 地図の場所は帰属の候補の型にも無い"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/features/structure-anchored-questions.md"
  - "docs/architecture/persona_enactment_testing_design.md"
feature_context:
  realizing: "学習者の問いを、提示された構造のどこに引っかかったかとして記録し、本人が帰属カードで確定・訂正する"
  layers: [learner_experience_b, frontend_learning_ui, field_atlas_s]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [meaning, target]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: low
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（コード + 製品頭脳の要求）: frontend/public/js/app.js の Session.currentAnchor() は lectureState.active でなければ segment_id: 0 を返し、
    routes/learning.py は `_seg = int(_anchor.get("segment_id") or 0)` で既定 0 にし、core/learning_experience.build_position_anchor も未指定を 0 にする
    （docstring「復帰位置アンカー…未指定なら先頭(0)」）。core/structure_anchor/worker.py はこの payload.position_anchor.segment_id を QuestionRecord.segment_id に
    そのまま渡し、input_builder が「segment_id=seg_0」と「segments: seg_0 = トピックの先頭章」を候補に入れる。帰属の候補（stages / claims / equations /
    derivation_steps / concepts / chunks / segments）に地図の場所の型は無い。mailbox req-04304（st-05・t11）: 地図の中性子星について尋ねた 2 問が seg_0 付きで渡り、
    製品頭脳 A は seg_0（connection / scope）に縮退。req-04309（st-08・t2）: 地図の暗黒エネルギーの英語の問いが seg_0 付き・候補は segments だけ。
    接続軸 meaning = 復帰位置の既定値（意味は「戻る先」）が「見ていた区画」と読まれる。接続軸 target = 画面で選んでいた対象（地図の場所）と帰属の対象がずれる。
    構造軸は low で none — 型の追加（地図の場所）は SA層の element と同じ語彙の判断を伴うので、まず意味のずれだけを確定とした。
generalization:
  level: repo_pattern
  general_form: "ある目的の既定値（復帰位置の先頭）を別の目的（関心の所在）の入力に流用すると、報告の無い場合が既定値への帰属として記録され、記録の上では区別できない"
pattern: fallback-fabricates-missing-link
discovery:
  perspective: [trace_walk, reproduction]
  note: >-
    製品頭脳 A 4304・B 4309 の所見（「地図で問うた問いは segment に縮退するしかない」「地図の場所の問いがトピック t2 の seg_0 付きで来ている」）から、
    要求の候補ブロックと position_anchor の出所を app.js → routes/learning.py → structure_anchor/worker.py と辿った。
    run: uxsim/runs/c-r25-doubt-r10/20261031T053655Z-69809（seq13・14・16・17）/ uxsim/runs/c-r25-doubt-r10/20261031T054204Z-72077（seq8〜10）。
    B 4309 の「同一の英語の問いが 3 件（重複記録）」は、場面が同じ問いを 2 回送ったのと前周の未解析分で、製品の重複記録ではない（審判の読み）。
resolution:
  perspective: [explicit_contract, fail_closed]
  note: >-
    第 25 周 第 10 波: app.js の Session.currentAnchor はレクチャー中だけ segment_id を送る（0 を既定にしない — P2-7 の
    selection_segment_id と同じ規律）。route は _seg_reported を求め、build_position_anchor(..., segment_reported=) が痕跡の
    position_anchor に segment_reported を残す（復帰位置の segment_id 0 は戻る先として不変）。構造帰属の worker は
    reported_segment_id で読み、報告の無い区画・キーの無い旧い痕跡の 0 は区画に結ばない（縮退順 claim → concept → chunk →
    segment の最後の段を位置が分かるときに限る）。地図の場所を帰属の候補に足すかは構造帰属の語彙の判断として据え置き、
    いまは区画に結ばない（出典のチャンクか unattributed に倒れる）。
  landed_in:
    - "frontend/public/js/app.js"
    - "backend/api/routes/learning.py"
    - "backend/core/learning_experience.py"
    - "backend/core/structure_anchor/worker.py"
    - "backend/tests/test_doubt_round25_wave10.py"
  verification:
    methods: [guardrail]
    unverified:
      - "地図の場所について尋ねた問いの帰属候補に seg_0 が出ないことを製品頭脳の要求で確かめていない（第 11 周 st-08）"
      - "地図の場所を帰属の候補の型に足す判断は未着手（構造帰属の設計書への追補が要る）"
related: [IK-1112, IK-1113, IK-1049]
view_of: []
history: []
---

## 課題

症状: 分野の地図で場所を開いて尋ねた問いが、構造帰属の候補として「トピックの先頭の区画」に結ばれる（scope / connection）。学習者が帰属カードで確定すると、
地図の場所への問いが教材の章への引っかかりとして記録に残る。

原因: 復帰位置の既定値（区画 0）を、構造帰属が「見ていた区画」と読む。地図の場所は帰属の候補に無い。

## 発見の観点

製品頭脳の要求の候補ブロックから、position_anchor の出所を画面 → route → worker と辿った。

## 解決の観点

未着手。報告の無い区画を既定値で埋めない（帰属に使う側で「報告あり」を確かめる）。

## 一般化

型 fallback-fabricates-missing-link。別目的の既定値を関心の所在の入力に流用しない。

第 10 波（2026-10-31）: 区画を報告したときだけ区画に結ぶ（上の resolution）。ladder の「表示している区画に注目」も報告のある送信だけに効くようになった。

## 第 11 周の再演（第 25 周・面 B 第 11 周、2026-10-31・judge_doubt_r11 — 固定コース 1bad7d9f・3 周連続の同じ対象）

GONE（製品頭脳の要求で確認）。st-08 の地図の場所（暗黒エネルギー）の英語の問い 2 回の構造帰属の要求 req-04325 に segment_id も segments の候補も無く（候補は stages だけ）、3 件とも unattributed（理由: 地図の注記と詳細の場所を問う・どの候補にも当たらない）。痕跡 payload の position_anchor に segment_reported:false（復帰位置の segment_id 0 は戻る先として残る）。3 件のうち 1 件は第 10 周の未解析の痕跡（segment_reported キーなし）で、旧い痕跡の 0 も区画に結ばれなかった。地図の場所を帰属先に選べないのは IK-1146（deferred）。run: 20261031T102036Z-2368（seq9・10）・mailbox req-04325 / res-04325。
