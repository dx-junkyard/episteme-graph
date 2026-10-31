---
id: IK-1123
title: "ナレッジライブラリの詳細「結びついた教材の部品」で、見送ったリンクの行が「部品 〔見送り〕 〔見送り〕 見送り: 西川 智也・2026-10-31」と札が二重に並ぶ — 状態の札（status_label）と判断の行（decision_line の印「〔見送り〕」）が同じ事実を別々に描く"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
feature_context:
  realizing: "教員が共通部品に結び付いた教材の部品の行を読み、誰がどう判断したかを一目で読む"
  layers: [concept_registry, frontend_admin_ui]
classification:
  axes:
    processing: [wording]
    structure: [aggregation]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸 wording: core/deliberation/identity_links.py decision_line は「〔{mark}〕 {verb}: {who}・{date}」と印を前に付けて返し、admin.js
    _libraryBoundLinksHtml は status_label の札（見送り）を描いた後に actors として decision_line をそのまま続ける。構造軸 aggregation: 状態の表示が
    札と判断の行の 2 か所で作られ、どちらも同じ状態を言う。同一性の候補の一覧では札と判断の行が別の行に分かれるので目立たないが、詳細は 1 行に並ぶ。
generalization:
  level: repo_pattern
  general_form: "状態の札と、判断の記録の行の両方が状態の印を持ち、同じ行に並べたときに同じ語が重なる"
pattern: duplicate-canonical-sources
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    第 25 周 面 A 第 10 周 te-05 (a) が詳細「EOS feature vector」で戻した行を見送った後の行
    （uxsim/runs/c-astro-r25-share-r10/20261031T053605Z-69501 seq6）。頭脳ノート「札が『〔見送り〕 〔見送り〕 見送り: …』と二重」。
    admin.js の行の組み立てと decision_line を突き合わせた。
resolution:
  perspective: [canonical_source]
  note: >-
    decision_line に with_mark を足し、describe_actors が印の無い形 decision_actor_line（「見送り: 名前・日付」）も返す。詳細の結びついた部品・記号の行（札 status_label と同じ行）は decision_actor_line を使い、印付きの decision_line は札の無い一覧の判断の行に残す（語の正本は decision_line の 1 関数のまま）。 第 11 波（取り消しの行の残り）: revoke_line に with_mark を足し、describe_actors が取り消しの行にも印の無い decision_actor_line（「取り消し: 名前・日付（元の確定: …）」）を返す。詳細の行は見送りと同じく decision_actor_line を先に使う（revoke_line は印付きのまま一覧に残す）。runner の投影（uxsim/runner/share_exec.py）も同じ欄に揃えた。
  landed_in:
    - "backend/core/deliberation/identity_links.py"
    - "frontend/public/js/admin.js"
    - "backend/tests/test_share_round25_r10_fixes.py"
    - "backend/tests/test_share_round25_r11_fixes.py"
    - "uxsim/runner/share_exec.py"
  verification:
    methods: [guardrail]
    unverified:
      - "詳細で見送った行の表示を砂場の画面で"
      - "詳細で取り消し済みの行の表示を砂場の画面で（第 11 波の残りの是正）"
related: [IK-1050, IK-1062]
view_of: []
history:
  - date: 2026-10-31
    field: resolution.note
    from: 見送りの行だけ印の無い形
    to: 取り消しの行も印の無い形（第 11 波）
    reason: "judge_share_r11 で取り消しの行の二重の印が STILL"
---

## 課題

症状: 詳細の「結びついた教材の部品」で見送ったリンクの行が「〔見送り〕 〔見送り〕 見送り: 西川 智也・2026-10-31」と読める。重大ではないが、同じ語が 3 回並ぶ。

原因: 札（status_label）と判断の行（decision_line の印）が同じ状態を描く。

## 発見の観点

再現: te-05 の (a) で戻した行を見送った。経路の追跡: admin.js `_libraryBoundLinksHtml` の行の組み立てと `identity_links.decision_line`。

## 解決の観点

第 25 周 第 10 波で是正（resolution.note）。

## 一般化

状態を描く部品を足すときは、同じ行に既に状態の札があるかを数える。型 duplicate-canonical-sources。確かめ方は、見送り・確定・取り消しの各行を詳細と一覧の両方で読むこと。

## 第 11 周の再演（第 25 周・面 A 第 11 周、2026-10-31・judge_share_r11）

見送りの行の形は今周は出ていない（未評価）。**取り消しの行で同じ二重の印が STILL**: 詳細の結びついた記号の取り消し済みの行が札「確定を取り消し」+ 文「〔取り消し済み〕 取り消し: 名前・日付（元の確定: …）」と並ぶ（admin.js _libraryBoundLinksHtml は revoke_line を印付きのまま actors に足す — 第 10 波は rejected だけを decision_actor_line にした）（uxsim/runs/c-astro-r25-share-r11/20261031T101202Z-98535 seq7・9・10・11）。

## 第 11 波の是正（2026-10-31）

取り消しの行も見送りと同じ decision_actor_line（印の無い形）にした（resolution.note）。
