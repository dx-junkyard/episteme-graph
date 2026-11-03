---
id: IK-1148
title: "検証提案の到達可能性の語彙が観測の語だけ（「現在の観測で検証可能」「次世代の装置・観測なら可能」…）で、本文・付録の読み直しで確かめる提案（定義の疑義から作った提案）に当てはまる語が無く、教員は「観測」の語を選ぶしかない"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/features/stakes_ledger_design.md"
  - "docs/architecture/persona_enactment_testing_design.md"
feature_context:
  realizing: "教員が疑義から検証提案を作るとき、その提案がいまの手段で確かめられるかを記帳する"
  layers: [doubt_d, stakes_ledger_sl]
classification:
  axes:
    processing: [wording]
    structure: [representation]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（コード + run）: core/doubt/schema.py の REACHABILITY_LABELS（reachable =「現在の観測で検証可能」/ next_generation =「次世代の装置・観測なら可能」/
    unreachable / unassessed）と doubt-atlas.js の同表は、覆る条件（SL-1）の到達可能性の語彙をそのまま検証提案にも使う（IK-1120 で欄名は
    「検証提案の到達可能性」に分けたが語彙は共有）。te-03 は定義の疑義（W が磁場のエネルギーを含むか）から「本文の式の前後と付録を読み、…確かめる」
    提案を作り、到達可能性に「現在の手段で可能」を選んだ — 画面は「現在の観測で検証可能」と出た（run 99946 seq8）。処理軸 wording = 語が観測に限られる。
    構造軸 representation = 提案の性質（観測で確かめる / 文献・本文で確かめる）を区別する語彙が無い。接続・統制は無し。
generalization:
  level: repo_pattern
  general_form: "ある対象（覆る条件）向けに作った段階語彙を別の対象（検証提案）の欄に流用すると、後者にしか無い場合を表す語が無く、近い語に丸めて記帳される"
pattern: wording-mismatch
discovery:
  perspective: [reproduction]
  note: >-
    te-03 の頭脳ノート seq8「到達可能性の語彙が『観測』中心で、本文の読み直しで確かめる提案に合う語が無い」。
    run: uxsim/runs/c-r25-doubt-r11/20261031T101505Z-99946（seq8・9）。
resolution:
  perspective: [single_point_fix]
  note: >-
    第 25 周 第 11 波（2026-10-31）: REACHABILITY_LEVELS に by_document（「本文・文献の読み直しで確かめられる」）を additive に足し、verification_proposals.reachability の CHECK を migration 097 で同じ語彙の名前付き CHECK に置き換えた（067 は自動名の CHECK・既存行不変）。JS ミラー（doubt-atlas.js）・マニュアル 18・設計書 stakes_ledger §16.11。SL-4 要約の優先は reachable > by_document > next_generation。
  landed_in:
    - "backend/core/doubt/schema.py"
    - "backend/core/doubt/open_assumptions.py"
    - "backend/db/097_verification_proposal_reachability_by_document.sql"
    - "frontend/public/js/doubt-atlas.js"
    - "docs/manual/teacher/18-admin-doubt-atlas.md"
    - "backend/tests/test_round25_wave11_doubt_studio_chat.py"
  verification:
    methods: [guardrail]
    unverified:
      - "migration 097 の実 DB 適用（砂場 DB に触れていない）"
      - "覆る条件の欄にも同じ値が出る（欄ごとの分岐は作っていない）"
related: [IK-1120, IK-0968]
view_of: []
history: []
---

## 課題

症状: 定義の疑義から作った「本文と付録を読み直して確かめる」提案に、到達可能性は「現在の観測で検証可能」としか付けられない。

原因: 検証提案の到達可能性の語彙が覆る条件（観測で覆るか）の語彙の流用で、観測以外の確かめ方を表す語が無い。

## 発見の観点

ペルソナが提案を作るときに選択肢の語と提案の中身を見比べた（reproduction）。

## 解決の観点

第 11 波で是正（frontmatter の resolution）。以前の案: 欄ごとのラベルか語彙の追加。重さは軽。

## 一般化

型 wording-mismatch。別の対象向けの段階語彙を流用すると、流用先にしか無い場合が近い語に丸められる。
