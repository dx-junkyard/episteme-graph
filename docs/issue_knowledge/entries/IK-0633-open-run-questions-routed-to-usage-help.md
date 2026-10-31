---
id: IK-0633
title: "開いている自分の実行についての問い（前の実行・配線・段階・共有の帰結）が使い方 HELP に回り、実行の事実ではなくマニュアルの一般論が返った"
status: resolved
recorded_at: 2026-10-04
resolved_at: 2026-10-04
sources:
  - "docs/architecture/persona_enactment_testing_design.md §19.6"
  - "docs/features/simulation_bridge_design.md §12.5"
  - "docs/features/manual_help_kb_design.md §9"
feature_context:
  realizing: "学習者が開いている自分の実行の結果について、学習チャットで日本語・英語で尋ねる"
  layers: [help_kb, screen_adapter_sa, rag_chat, simulation_bridge_sb]
classification:
  axes:
    processing: [none]
    structure: [responsibility]
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
    確認: 結果の語（段階ラベル等）だけを RAG へ流し、共有・配線・前の実行は方針の問いとして HELP に残していたため、開いている run についての問いがマニュアル検索に回った。実行の事実文は SA層の解決器が作るが RAG 経路でしか届かない。接続軸 information = 事実は作られているのに問いの経路から届かない。構造軸 responsibility = 「開いている run の問い」をどの経路が担うかの線引きが HELP 側に寄っていた。処理軸 none。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "利用者が手元の対象について尋ねた問いを一般論の窓口へ回し、対象の事実を持つ経路に届かず一般論が返る"
pattern: available-but-unwired
discovery:
  perspective: [reproduction, trace_walk]
  note: "第 22 周の学生場面 (j)(l) で、前の実行や共有の帰結を尋ねて目次や別節の本文が返った。分類から HELP と RAG の線引き、画面文脈の解決器まで辿った。"
resolution:
  perspective: [responsibility_move, carry_through]
  note: "開いている実行の問いを RAG 側に移し（結果の語に段階・配線・実行の記録・前の実行・共有と英語の run / wiring / share を追加）、解決器に共有の意味と教員の判断の行を足し、事実の上限を広げた。正本は manual_help_kb_design.md §9 の改訂。"
  landed_in:
    - "backend/core/simulation_bridge/schema.py"
    - "backend/core/assistant_context/resolvers/learning.py"
    - "backend/core/assistant_context/schema.py"
    - "backend/api/routes/learning.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: extracted
      note: "開いている実行の事実は画面文脈の解決器 1 箇所で作り、問いの語の線引きでそこへ寄せた。全経路の横断検査は置いていない。"
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified:
      - "分類 LLM が開いている実行の問いを HELP に分類したときの経路"
      - "実行を開いていないときの共有の方針の問い"
related: [IK-0611, IK-0619]
view_of: []
history:
  - date: 2026-10-04
    field: resolution.verification
    from: 記録時の範囲
    to: 第 22 周の再演で更新
    reason: "第 22 周 (j) で GONE"
---

## 課題

症状: 「前の実行はいつ・どの配線か」「この実行を共有すると誰に見えるか」に、使い方マニュアルの節や目次が返る。原因: 開いている run についての問いを方針の問いとして HELP に残していたが、マニュアルの一般論では個別の実行に答えられず、実行の事実は RAG 経路の画面文脈でしか届かなかった。

## 発見の観点

ペルソナの通し受講（reproduction）で日本語・英語の両方の問いが HELP に回るのを観測し、分類・HELP 検索・画面文脈の解決器を辿った（trace_walk）。

## 解決の観点

線引きを「開いている実行についての問いは RAG」に改め、事実を作る解決器を拡張した。HELP 側で実行の事実を引く案は、HELP を非 LLM のマニュアル素通しに保つため採らなかった。

第 22 周の再演: (j) で開いている実行の問いが実行の事実で答えられた（GONE）。

## 一般化

手元の対象への問いと一般の使い方の問いが同じ語で現れると、窓口の線引きが対象の事実を遠ざける。型は available-but-unwired。確かめ方は、対象を開いた状態と開いていない状態で同じ問いを送り、前者だけが対象の事実で答えられることを見ること。
