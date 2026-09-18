---
id: IK-0120
title: 規律を守るガードレールが動的な表名と新しい経路を覆わず、静かに破られていた
status: resolved
recorded_at: 2026-09-13
resolved_at: 2026-09-13
sources:
  - docs/architecture/knowledge_structure_review_2026-09-12.md §9.2 P1-R2 / P1-R11
  - docs/features/knowledge_objects_design.md §12.2
  - docs/features/concept_registry_design.md §13.3 P3-R13
feature_context:
  realizing: 読み手は live ビューだけを読む・AI は骨格に書かないといった規律を構造的に守る
  layers: [tests_guardrails, knowledge_objects, field_atlas_s]
classification:
  axes:
    processing: [none]
    structure: [aggregation]
    connection: [none]
    governance: [review]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    規律そのものと実装は正しいが、規律を守るための検査が行単位の正規表現で、表名を変数で受ける
    呼び出しを素通りさせ、除外リストもファイル単位だったため後から足した読み手が検査に入らなかった。
    検査の網の張り方（何を対象に、どの粒度で許すか）という手続を直さなければ、同じ規律違反が
    緑のまま入り続ける点が統制の定義に当たる。実際に 3 箇所の読み漏れが緑のまま残っていた。
generalization:
  level: general
  general_form: >-
    再発防止の検査が特定の書き方だけを見ているため、別の書き方・後から足した経路が検査を
    素通りし、規律が緑のまま破られる
pattern: guardrail-does-not-cover-new-path
discovery:
  perspective: [adversarial_review, inventory]
  note: >-
    「この検査は何を見ていないか」を問い、規律の対象になるはずの呼び出しを全部列挙して検査結果と
    突き合わせた。表名を変数で受ける形・別ディレクトリに増えた層が抜けていた。
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: >-
    検査を全文走査に変え、変数で受ける表名は定数へ解決し、解決できないものは理由付きの明示
    許可を要求する形にした。除外は「ファイル:シンボル」の粒度に細かくし、対象ディレクトリの
    列挙をやめて全走査 + 除外に切り替えた。検査器自身の退行検査も足した。
  landed_in:
    - backend/tests/test_knowledge_objects_guardrails.py
    - docs/features/knowledge_objects_design.md §12.2
related: [IK-0105]
view_of: []
history:
  - date: '2026-09-19'
    field: classification
    from: primary=governance facets=[governance.review, structure.aggregation]
    to: axes=processing=[none]; structure=[aggregation]; connection=[none]; governance=[review]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

**症状**: 「読み手は live ビューを読む」という規律が守られている前提で設計が進む一方、実際には
3 箇所が基表を直接読み、superseded の行を優先して返していた。検査は緑のまま。

**原因**: 検査が行単位の正規表現で、表名を文字列補間で受ける呼び出しを見逃していた。除外リストも
ファイル単位なので、同じファイルに後から足した読み手は自動的に除外に入った。別の規律の検査では、
対象ディレクトリをハードコードしていたため後から増えた層が走査対象外になっていた。

## 発見の観点

実装後の敵対的レビューで「この検査は何を見ていないか」を問い（`adversarial_review`）、規律の
対象になるはずの呼び出しを全部列挙して検査結果と突き合わせた（`inventory`）。

## 解決の観点

規律違反を 1 件ずつ直すのではなく、許可の与え方を「場所」から「理由つきの許可表」という 1 つの
正本に寄せた（`canonical_source`）。そのうえで、対象の列挙をやめて全走査 + 明示除外へ検査の網を
張り直した（`guardrail_fix`）。

## 一般化

ガードレールは書いた時点の書き方しか知らない。動的な名前・新しいディレクトリ・別の呼び方が
増えるたびに穴が開くので、「対象を列挙する」型の検査は「全部走査して理由付きで除外する」型に
置き換えたほうが寿命が長い。
