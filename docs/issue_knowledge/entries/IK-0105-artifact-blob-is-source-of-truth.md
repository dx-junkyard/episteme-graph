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
  axes:
    processing: [none]
    structure: [representation, decomposition]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理: 個々の読み書きは仕様どおりで、単一処理を直しても正本の位置は変わらない。構造: 知識の実体が解析実行ごとの 1 行の入れ物に入り索引も制約も無い（表現）、粒度が「実行ごとに 1
    行」で部分更新も差分監査もできない（分割）。責務の置き場所（どの層が正本を持つか）も同じ根にあるが、軸あたり 2 値までのためここに記す。接続: 生成 132 件に対し保存 9
    件で、生成の段から永続化の段へ渡る途中で大半の知識が落ちている。正本の位置という表現の問題と同じ事実の別面という読み方もでき、そこが確信を下げている。統制: 順序・予算・再開・レビュー・完了の扱いに崩れは無い。
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
history:
  - date: '2026-09-19'
    field: classification
    from: primary=structure facets=[structure.representation, structure.decomposition,
      structure.responsibility]
    to: axes=processing=[none]; structure=[representation, decomposition]; connection=[none];
      governance=[none]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持。軸あたり最大 2 のため structure.responsibility
      は座標から外し、ここに残す
  - date: '2026-09-19'
    field: classification.axes
    from: connection=[none]
    to: connection=[information]
    reason: >-
      軸ごとの再判定で、生成から永続化へ渡る途中の情報の落ちを接続の要素として置いた
---

## 課題

**症状**: 承認・疑義・説明・痕跡が、実体のない ID 文字列に紐づく。説明の 95% が対応する行を
持たず、再解析のたびに静かに別物を指す。式や記号を横断して引くことができない。

**原因**: 知識の正本が解析実行の生成ログ（1 行の JSONB）で、関係テーブルは採択された一部の
投影だった。正本の側には索引も外部キーも制約も無く、粒度も「実行ごとに 1 行」なので部分更新も
差分監査もできない。読み手はそれぞれ生成ログを開き直す運用になっていた。

**軸ごとの判断（2026-09-19）**: 構造は正本の位置（表現）と実行ごと 1 行という粒度（分割）の 2 点。
接続は、生成された知識の大半が永続化の段へ渡らない点を要素として置いた（正本の位置と同じ事実の別面という読み方も残る）。
処理と統制は要素なし。

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
