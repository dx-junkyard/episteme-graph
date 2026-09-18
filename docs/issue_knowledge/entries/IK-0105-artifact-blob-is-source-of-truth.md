---
id: IK-0105
title: 生成ログの JSONB blob が知識の正本で、関係テーブルがその劣化投影になっている
status: resolved
recorded_at: 2026-09-12
resolved_at: 2026-09-13
sources:
  - docs/architecture/knowledge_structure_review_2026-09-12.md §2 D2
  - docs/architecture/knowledge_structure_review_2026-09-12.md §4 Phase 1 実装記録
  - docs/architecture/knowledge_structure_review_2026-09-12/B_storage.md S-1 / S-2 / S-9 / S-12
  - docs/features/knowledge_objects_design.md
feature_context:
  realizing: 論文から抽出した主張・式・根拠・導出・記号を、検索・承認・参照できる知識として保つ
  layers: [knowledge_objects, pipeline_a]
classification:
  primary: structure
  facets: [structure.representation, structure.decomposition, structure.responsibility]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    知識の実体が解析実行ごとの 1 行の JSONB に入り、関係テーブルは一部の投影にすぎないため、
    検索・結合・制約・部分更新が知識本体に効かない。主張は生成 132 件に対し保存 9 件、式・根拠・
    導出・記号には表そのものが無い。処理を直しても「何が正本でどの粒度で持つか」という表現と
    責務を変えなければ再発する点が構造の定義に当たる。書き出し API 自身が正本を生成ログ側と
    宣言していたこと、1 行が最大 10MB まで単調増加していたことを実測で確認。
generalization:
  level: general
  general_form: >-
    生成過程の記録を正本として読み書きし、正規化された行を投影として扱うため、正本に対して
    検索・制約・部分更新・参照整合が効かない
pattern: projection-mistaken-for-source
discovery:
  perspective: [data_inspection, inventory]
  note: >-
    92 の表を棚卸しして式・根拠・導出・記号に行が無いことを確かめ、知識のバイト数が正規化 2 表に
    1〜2% しか入っていないことを実測した。永続グラフの参照 6,379 件のうち関係テーブルに着地
    するのが 2 件という数字が、正本の位置を示した。
resolution:
  perspective: [canonical_source, representation_change]
  note: >-
    生成ログを「1 実行 × 1 段階 = 1 行の不変ログ」に降格し、知識オブジェクトを内容由来の安定キーを
    持つ一級の行にした。読み手は live ビューを読む規律にし、基表を直接読んでよい場所を理由付きの
    allowlist に限定した。
  landed_in:
    - backend/core/knowledge_objects/
    - backend/core/document_pipeline/persistence.py
    - backend/db/079_analysis_artifacts.sql
    - docs/features/knowledge_objects_design.md §12.1
related: [IK-0106, IK-0107, IK-0122]
view_of: [IK-0019, IK-0304]
history: []
---

## 課題

**症状**: 承認・疑義・説明・痕跡が、実体のない ID 文字列に紐づく。説明の 95% が対応する行を
持たず、再解析のたびに静かに別物を指す。式や記号を横断して引くことができない。

**原因**: 知識の正本が解析実行の生成ログ（1 行の JSONB）で、関係テーブルは採択された一部の
投影だった。正本の側には索引も外部キーも制約も無く、粒度も「実行ごとに 1 行」なので部分更新も
差分監査もできない。読み手はそれぞれ生成ログを開き直す運用になっていた。

## 発見の観点

表の棚卸し（`inventory`）と実データのバイト数・件数の実測（`data_inspection`）を突き合わせ、
グラフの参照が関係テーブルにほとんど着地しないことを 1 本の経路として辿った。

## 解決の観点

「どちらを正本とするか」を先に決めないと直せない型なので、正本の位置を明示的に反転させた
（`canonical_source` + `representation_change`）。生成ログは 1 実行 1 段階の追記専用に分割し、
読み手の側には「live ビューを読む」という契約を置いた。

## 一般化

同じ型は「ログ・スナップショット・キャッシュを読み書きの主経路にする」あらゆる場所で起きる。
投影のつもりの表が実際は劣化コピーで、正本には制約も検索も効かないという倒錯は、規模が
大きくなるほど巻き戻しにくくなる。
