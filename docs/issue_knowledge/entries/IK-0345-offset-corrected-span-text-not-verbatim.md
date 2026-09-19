---
id: IK-0345
title: 役割判定のオフセット訂正 3 戦略のうち 2 つが、位置を当てても span.text を LLM の写しのまま残し、validator の逐語比較で修復失敗に数えられる
status: resolved
recorded_at: 2026-09-19
resolved_at: 2026-09-19
sources:
  - docs/architecture/knowledge_reproduction_review_2026-09-19.md §7.1
feature_context:
  realizing: LLM が返した span の位置をサーバ側で決定論的に解決し、修復失敗を減らす
  layers: [pipeline_a]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [contract]
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
    接続軸: validator の契約は「block_text[start:end] == span.text」で、訂正は (start, end) だけを書き換え text 側を触らなかった（contract-changed-one-side）。処理軸: 空白正規化・頭尾アンカーは本文と写しが文字列として一致しない前提の戦略なので、戻り値の片側だけ直すのは論理の欠落。確認手段: repair.py の _parse_block_annotation が correction を受けても text を据え置いていたこと。
generalization:
  level: general
  general_form: 対になる 2 つの値の片方を訂正し、もう片方を据え置いたため、両者の一致を前提にする検証が訂正を無効にする
pattern: contract-changed-one-side
discovery:
  perspective: [adversarial_review, trace_walk]
  note: >-
    resolve_span_offsets の 3 戦略それぞれについて、返した offsets を validator に通したときの結果を追った。exact 以外の 2 戦略は位置が正しくても span_text_mismatch になる。
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: >-
    訂正したときは本文のスライスを text にする（本文が正本・検証基準は不変）。戦略ごとに validator を通すテストを置く。
  landed_in:
    - src/episteme_graph/agents/rhetorical_role/repair.py
    - src/tests/agents/rhetorical_role/test_offset_correction.py
related: [IK-0331]
view_of: []
history: []
---

## 課題

**症状**: 空白違い・中間の脱字を含む写しは位置が当たっても修復失敗（役割 unknown・confidence 0.0）のままだった。

**原因**: 訂正がオフセットだけを書き換え、validator が比較する span.text を本文に揃えていなかった。

## 発見の観点

敵対的レビュー（`adversarial_review`）で戦略ごとに validator まで経路を辿った（`trace_walk`）。

## 解決の観点

一点修正（`single_point_fix`）で text を逐語スライスにし、再発防止のテストを置く（`guardrail_fix`）。

## 一般化

「A と B が一致すること」を検証する経路で A だけを直すと、検証が直しを打ち消す。訂正は検証と同じ単位（対）で行う。
