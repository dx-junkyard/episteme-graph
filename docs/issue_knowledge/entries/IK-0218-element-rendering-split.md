---
id: IK-0218
title: 同じ要素を描く実装が系統ごとに分かれ、種別の呼び名と辿れるかどうかが画面で食い違う
status: resolved
recorded_at: 2026-08-01
resolved_at: 2026-08-01
sources:
  - docs/architecture/admin_ux_issues_2026-08-01.md §3
  - docs/architecture/feature_consolidation_proposals_2026-08-13.md §2-6
feature_context:
  realizing: 論文から取り出した要素を、どの画面でも同じ形で見せ、近傍へ辿れるようにする
  layers: [deliberation_w, lecture_studio, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [aggregation, responsibility]
    connection: [none]
    governance: [none]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    取得する側は単一の正規化された応答へ既に共通化されており、各画面の描画もそれ自体は動く。
    原因は、要素の呼び名と組み立ての正本が描画側に無く系統ごとに持たれていること。画面を
    個別に直しても次の画面で分裂が再発するため構造とする。劣化した側の実装が表に出ている
    のは責務の置き場所の問題。
generalization:
  level: general
  general_form: >-
    同じ対象を描く実装が系統ごとに分かれ、呼び名と操作の可否が画面によって食い違う
discovery:
  perspective: [symptom_report, inventory]
  note: >-
    「選んだノードの説明が何を説明しているのか分からない」という指摘を起点に、同じ要素を
    描く箇所を全て列挙した。呼び名の表が六箇所に分かれ、同じ種別が画面ごとに別の訳語に
    なっていた。近傍へ辿れるかどうかも画面ごとに異なり、正規化された応答を使っていない
    画面だけが、その場しのぎの解決で生の識別子を出していた。
resolution:
  perspective: [canonical_source, responsibility_move]
  note: >-
    呼び名の正本を一箇所に置き、六つの表をそこへ委譲した（表示文字列を変えない入れ替えなので
    単独で先行できた）。そのうえで共通のカードを作り、既にある正規化された応答を入力の契約に
    固定して各画面を寄せた。画面ごとに個別最適する案は、次の画面で同じ分裂が起きるため退けた。
  landed_in:
    - frontend/public/js/element-vocab.js
    - frontend/public/js/element-card.js
    - docs/architecture/admin_ux_issues_2026-08-01.md §3.3
related: [IK-0216, IK-0217]
view_of: []
pattern: duplicate-canonical-sources
history:
  - date: '2026-09-19'
    field: classification
    from: primary=structure facets=[structure.aggregation, structure.responsibility]
    to: axes=processing=[none]; structure=[aggregation, responsibility]; connection=[none];
      governance=[none]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

**症状**: 同じ種別の要素が画面によって別の名前で呼ばれる（同じものが「論理要素」と
「コンポーネント」、「主張」と「claim」、「図」と「図・画像」）。近傍へ辿れるかも画面ごとに
違う。理論グラフの詳細では見出しが英語の抽象語のまま、説明欄に見出しと同じ文字列が再掲され、
根拠の行ラベルは内部語彙のまま出ていた。

**原因**: 要素を取得する側は既に単一の正規化された応答へ共通化されているのに、描画する側が
系統ごとに呼び名の表と組み立てを持っている。とくに詳細の画面だけが正規化された応答を使わず、
その時読み込まれている情報から名前を引く場当たりの解決に依存しており、引けなければ生の
識別子がそのまま出ていた。同じノードについて別の画面はサーバ側で整えた情報を持っており、
**二重実装のうち劣化した側が表に出ていた**。

## 発見の観点

`symptom_report` を起点に `inventory`。呼び名の定義箇所を全て列挙して訳語を突き合わせると、
食い違いが一覧で見える。「取得は共通化済みで、分裂しているのは描画だけ」という切り分けが、
作業の性質を「統一」ではなく「既にある正本へ寄せる」に変えた。

## 解決の観点

`canonical_source`（呼び名の正本を一箇所に）+ `responsibility_move`（描画を共通のカードへ寄せ、
入力の契約を既存の応答形に固定する）。表示文字列を変えない入れ替えから始めたので、挙動不変の
まま先行できた点が実務上の要点。

## 一般化

同じ対象を複数の画面で描くあらゆるシステムで再発する。取得が共通化されていても描画が
分かれていれば、利用者から見た一貫性は保てない。辞書の型は `duplicate-canonical-sources`。
