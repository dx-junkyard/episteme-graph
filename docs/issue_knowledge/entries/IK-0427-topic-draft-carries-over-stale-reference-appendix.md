---
id: IK-0427
title: "コース内容の再生成で、下書き末尾の「この節で使う数式 / この節で参照する図」が前の生成のまま持ち越され、いまの根拠候補では別の式を指す eq_3 等に別論文の式の説明が付き、中性子星のトピックに連星ブラックホール論文の図が付いたまま生成モデルへ渡る"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 教員が「コース内容を生成」でトピックの授業用ドラフトを作り直す
  layers: [course_builder]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [version]
    governance: [resume]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸: 付録は生成のたびに、そのときのトピックの content_blocks から決定論的に付け直す派生物だが、
    前の生成（別の解析結果・IK-0377 以前の実装）で付いた付録が保存済みの student_material に残り、
    _topic_existing_draft がそのまま「現在の下書き」としてプロンプトへ渡していた。第 9 周の req 00154 /
    00158 / 00160 / 00162 では、根拠候補の eq_blk_003_0055 / eq_3 / eq_5 はいまの論文の式なのに、下書きの
    付録は同じ ID に偏光角の誤差式・暗黒エネルギーの圧力摂動・Cep B の磁場比を付け、図は連星ブラック
    ホール論文の Figure 2 / 6 を付けていた（version）。構造軸: 付録はモデルが書いた本文と同じ文字列に
    混ざり、どこからが付け直す部分かを表現が区別しない（representation。medium: 見出しと行の形で
    区別はできる）。統制軸: _ensure_required_equations_in_material は埋め込み記法が本文にあれば「付いて
    いる」と見なすため、モデルが古い付録を写すと再生成でも直らず、再実行が冪等でない（resume。
    medium）。処理軸は none: 現行の付録生成自体は document 単位に正しく解決する（確認: 両論文が eq_3 を
    持つ手組みの bundle で、B のトピックの付録は B の式を付けた）。
generalization:
  level: general
  general_form: 毎回付け直す派生物を、利用者の本文と同じ入れ物に保存し、次の生成の入力として持ち越す
pattern: stale-derivative-served
discovery:
  perspective: [data_inspection]
  note: 第 9 周のコース内容の再生成プロンプト（mailbox req 00154 / 00158 / 00160 / 00162）の「現在の下書き」を読んだ。
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: >-
    付録の正本を「いまのトピック」に一本化した。strip_generated_reference_appendix が本文末尾の付録の
    形（見出し・「- 」行・埋め込み行・空行だけの塊）を外し、_topic_existing_draft はプロンプト用の写しで
    付録と旧形式の自動要件を外して渡す（保存しているトピックは書き換えない）。
    _ensure_required_equations_in_material / _ensure_required_figures_in_material は付け直す前に古い付録を
    外すので、モデルが写しても再生成で直る（2 回通しても同じ結果）。本文の中で同じ見出しを使い散文が
    続く節は外さない。
  landed_in:
    - backend/core/course_content_builder.py
    - backend/tests/test_ik0427_0429_course_draft_hygiene.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場でのコース内容の再生成（第 9 周のコースで、次のプロンプトの「現在の下書き」から古い付録が消えるか）
      - 1 つのトピックが 2 本の論文の同じ ID（eq_3）を同時に束ねる場合。埋め込み記法が素の ID なので、付録も学習画面の解決も曖昧なまま残る
related: [IK-0377, IK-0389]
view_of: []
history: []
---

## 課題

再生成のたびに付け直すはずの付録が、前の生成のまま次のプロンプトへ渡っていた。

## 発見の観点

実際に生成モデルが受け取ったプロンプトを読み、根拠候補と下書きの付録で同じ ID の中身が食い違うことを見た。

## 解決の観点

付録の正本はいまのトピックだけにし、保存済みの本文からは付け直す前に外す。

## 一般化

毎回作り直す派生物を利用者の本文と同じ入れ物に置くと、作り直しの入力に古い派生物が混ざる。
