---
id: IK-0403
title: "予約疑似トピックの内部 id「_discussion」が構造帰属・違和感抽出の worker の LLM 入力と保存ラベルにそのまま出て、候補ブロックの本文には [[eq_eqcand_inline_blk_…]] 等の内部参照プレースホルダーが残っていた"
status: open
recorded_at: 2026-09-28
resolved_at: null
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: discuss の問い・違和感を、非同期 worker が構造に帰属させ、本人のダイジェストや進捗に表示する
  layers: [discuss, learner_experience_b]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 meaning: 「_discussion」は識別子であって表示名ではないが、表示名への変換は routes/learning.py の topic_title 決定の
    1 か所にしか無く、core の worker（structure_anchor / tension）は course_data を引いて一致しなければ id をそのまま表示名に
    していた（core は api を import できないので変換を参照できなかった）。同じく、チャンク本文の [[FORMULA_N]] / [[eq_…]] は
    描画器が解決する内部参照で、描画器の無い worker の LLM 入力では中身の無い記号になる。処理軸 wording（medium）: 表示に
    出た語の問題として現れた。確認: 第 8 周で構造帰属 worker のプロンプトに topic_id/topic_title=_discussion、候補ブロックの
    head に [[eq_eqcand_inline_blk_…]] が載り、tension ダイジェストの context_label が「_discussion」だった。
generalization:
  level: repo_pattern
  general_form: 識別子から表示名への変換が 1 つの層にしか無く、別の層（バックグラウンド処理）が同じ識別子を扱うと識別子のまま外へ出る
pattern: last-mile-missing
discovery:
  perspective: [data_inspection]
  note: worker の実プロンプトと、学習者 DTO の context_label / 進捗タブの sessions を読んだ。
resolution:
  perspective: [canonical_source, pending]
  note: >-
    worker 側は是正した: 予約 id と表示名の正本を core/discuss/context.py に置き（DISCUSSION_TOPIC_ID / DISCUSSION_TOPIC_LABEL /
    DOCUMENT_DISCUSSION_TOPIC_LABEL / discussion_topic_label。routes/learning.py の同名定数との一致はテストで固定）、worker は
    core/topic_labels.py の薄い入口から表示名を引く（structure_anchor / tension の _topic_labels。帰属プロンプトでは予約 id の
    topic_id 行を省く）。候補ブロック・context blocks の本文は text_hygiene.scrub_internal_placeholders で「（数式）」「（図）」に
    置き換えてから LLM に渡す（id 列には触れない）。新しい tension 候補の context_label は「論文との議論」で保存される。
    残り（別担当の範囲）: ①backend/api/services.py の calculate_progress が sessions の topic に topic_id をそのまま入れる
    （course_data の topics に一致しないと topic_name = topic_id_val のまま。予約 id は core.topic_labels.reserved_topic_label で
    表示名に変える）。②backend/api/services.py の get_tension_digest が payload.context_label を素通しする（是正前に保存された
    行は「_discussion」のまま。読み時に予約 id を表示名へ変える）。worker 側の変更はテスト（guardrail）で確かめ、砂場での再演はまだ。
  landed_in:
    - backend/core/discuss/context.py
    - backend/core/topic_labels.py
    - backend/core/text_hygiene.py
    - backend/core/structure_anchor/worker.py
    - backend/core/structure_anchor/input_builder.py
    - backend/core/tension/worker.py
    - backend/core/tension/input_builder.py
    - backend/tests/test_wave6_tension_worker_inputs.py
related: [IK-0402, IK-0400]
view_of: []
history: []
---

## 課題

内部 id と内部参照が、バックグラウンド処理の LLM 入力と表示に出る。

## 発見の観点

worker の実プロンプトと学習者 DTO を読んだ。

## 解決の観点

識別子と表示名の正本を core に置き、どの層からも同じ変換を引く。LLM 入力の本文は内部参照を事実語に置き換える。

## 一般化

識別子を表示名に変える場所は、その識別子を扱うすべての層から届く位置に置く。
