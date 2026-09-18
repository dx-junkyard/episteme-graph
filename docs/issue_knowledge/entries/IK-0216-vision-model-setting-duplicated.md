---
id: IK-0216
title: 同じひとつのステージの設定を二つの入口が指し、優先関係も保存の寿命も画面に現れない
status: resolved
recorded_at: 2026-08-01
resolved_at: 2026-08-01
sources:
  - docs/architecture/admin_ux_issues_2026-08-01.md §1
feature_context:
  realizing: 解析の各段で使うモデルを教員が選び、その選択を次の解析にも効かせる
  layers: [model_selection_m, pipeline_a, frontend_admin_ui]
classification:
  primary: structure
  facets: [structure.aggregation, structure.representation]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    どちらの入口も単体では正しく保存・適用され、解決の順序も実装どおりに動く。原因は、
    同じひとつの設定に対する入口が二つ存在し、どちらが勝つか・どちらが永続かを表せない
    こと。説明を足しても入口を一つに畳まない限り誤解は残るため構造とする。
generalization:
  level: general
  general_form: >-
    同じ設定を指す入口が複数あり、優先関係と保存の寿命が利用者に見えないまま片方が黙って勝つ
discovery:
  perspective: [symptom_report, doc_code_diff]
  note: >-
    「設定が二重に見えるが同期するのか」というオーナーの問いから、両方の入口が書き込む
    キーと保存の範囲を実装で突き合わせた。一方は利用者の既定として永続し、もう一方はその
    実行限りで、後者が勝つ。この優先関係は画面のどこにも書かれていなかった。
resolution:
  perspective: [canonical_source, representation_change]
  note: >-
    ひとつの設定にはひとつの入口、という方針で、段階別の表から重複する行を外した。
    優先関係を文章で説明する案は、二つ残る限り誤解も残るため採らなかった。あわせて、
    その解析を行わない設定のときは選べないようにし、効かない設定を置けなくした。
  landed_in:
    - frontend/public/js/admin-llm-models.js
    - docs/architecture/admin_ux_issues_2026-08-01.md §1.5
related: [IK-0218]
view_of: []
pattern: duplicate-canonical-sources
history: []
---

## 課題

**症状**: 画像を解析する段のモデル指定が、同じ画面の上下に二つ並ぶ。名前も違う
（片方は「図の解析」、もう片方は「図の装置同定」）。一方は利用者の既定として保存され、
もう一方はその実行限りで、両方指定すると後者が勝つ。この優先関係も寿命の違いも画面に
書かれていない。加えて、その解析を行わない設定のときでも後者は選べてしまい、選んだモデルは
黙って無視される。

**原因**: 実体はひとつのステージなのに、設定の入口が二系統に分かれて実装された。それぞれの
保存範囲と解決順は実装として正しいが、「どちらが正本か」が利用者にも画面にも表せていない。

## 発見の観点

`symptom_report` を起点に `doc_code_diff`。「二つあるが同期するのか」という問いに答えるには、
両方が書き込むキーと保存の範囲を実装で突き合わせるしかない。片方の画面だけでは分からない。

## 解決の観点

`canonical_source` — 入口を一つに畳んだ。優先関係を注記で説明する案は、二つ残る限り誤解も
残るため退けた。失うのは「解析を行わない設定のまま、その実行限りでモデルだけ変えたい」という
稀な使い方だけ、と明示して決めている。

## 一般化

同じ設定に複数の入口を作ると、優先関係と保存の寿命という二つの見えない軸が生まれる。
設定画面・環境変数と UI・グローバル設定と個別設定の重ね合わせで再発する。辞書の型は
`duplicate-canonical-sources`。
