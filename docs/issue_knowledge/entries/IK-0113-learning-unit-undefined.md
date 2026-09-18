---
id: IK-0113
title: 「学ぶ単位」が定義されておらず、教材と成果を題名の重なりで結んでいた
status: resolved
recorded_at: 2026-09-12
resolved_at: 2026-09-13
sources:
  - docs/architecture/knowledge_structure_review_2026-09-12.md §2 D3
  - docs/architecture/knowledge_structure_review_2026-09-12/C_consumers.md C-3 / C-13
  - docs/architecture/knowledge_structure_review_2026-09-12/A_fidelity.md F-5 / F-14 / F-19
  - docs/features/learning_units_design.md
feature_context:
  realizing: 論文の構造化成果を、教員が章立てしたコースの学習単位に結び付けて教材にする
  layers: [learning_units, course_builder, pipeline_a]
classification:
  primary: structure
  facets: [structure.representation, structure.decomposition, connection.target]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    学習の単位を表す一級の対象が無く、コースの見出しと抽出部品を題名の重なり率で結んでいたため、
    26 見出し中 18 が無接続になり、教員が自分の言葉で章立てするほど接続が切れる逆向きの誘因が
    生まれていた。同じ規則が「1 操作 = 1 単位」と「1 章だけ 14 件」という両極端を生んでいるのは、
    粒度の定義が無いことの帰結。結合の実装を改善しても、単位という表現を持たない限り再発する。
generalization:
  level: general
  general_form: >-
    扱う対象の「単位」が一級の存在として定義されていないため、別の目的で作られた名前の一致で
    対象どうしを結ぶことになり、結合が内容と無関係に切れる
pattern: unit-of-work-undefined
discovery:
  perspective: [trace_walk, data_inspection]
  note: >-
    「学習者に最終的に何が届くか」を入口から出口まで 1 本で辿り、届くのが二次生成の散文だけで
    あること、見出しと成果の結合が文字列一致であることを実データで確かめた。機械生成された
    部品名がそのまま学習画面の見出しになる症状も同じ根から出ていた。
resolution:
  perspective: [first_class_state, explicit_contract]
  note: >-
    文章層と親部品を種別つきの学習単位として永続化し、見出しは単位の並びとして定義した。
    題名一致は救済としてのみ残し、どちらで結ばれたかを区別して記録する。
  landed_in:
    - backend/core/knowledge_objects/learning_units.py
    - backend/core/course_units.py
    - docs/features/learning_units_design.md §12.1
related: [IK-0114, IK-0122]
view_of: []
history: []
---

## 課題

**症状**: コースの見出しの多くが成果と接続できず、接続できたものも「Transform representation: …」
のような機械生成の名前で学習者に出る。同じ規則が、ある論文では細かすぎる単位を、別の論文では
粗すぎる単位を生む。

**原因**: 「学ぶ単位」を表す対象が存在せず、学習側の唯一の単位はコースの見出しだった。
成果との結合は題名の重なり率という、内容と無関係に切れる手段しか無かった。忠実に読めている
文章層（章の骨格・支持構造）は、保存される単位を持たないため学習者に届かない。

## 発見の観点

入口から出口まで 1 本で辿り（`trace_walk`）、学習者に届く実データを読んだ（`data_inspection`）。
「トピック名が機械文字列になる」という症状報告も同じ根に着地した。

## 解決の観点

結合の精度を上げる方向（類似度の改善）ではなく、結ぶべき対象そのものを一級の行にした
（`first_class_state`）。見出しは単位の並びとして定義し、題名一致は救済に格下げして
どちらで結ばれたかを残した（`explicit_contract`）。

## 一般化

「何を 1 つと数えるか」が決まっていない領域では、名前の一致・位置・順番など**別の目的で
作られた手掛かり**で対象を結ぶことになる。単位を定義しないまま結合の精度を上げても、
粒度の両極端は消えない。
