---
id: IK-0017
title: 「Phase 3」のような段階名が設計書ごとに独立し、同じ名前が別物を指す
status: open
recorded_at: 2026-09-10
resolved_at: null
sources:
  - docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md §10
feature_context:
  realizing: 複数の設計書にまたがる実装の順序を、会話と文書で共有する
  layers: [docs]
classification:
  primary: structure
  facets: [structure.representation, connection.meaning]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    原因は「段階の名前が設計書という文脈の中でしか一意でないのに、文脈を外して引用される」
    こと。各設計書の中では番号は正しく一意であり、情報も条件も落ちていない。名前の付け方
    そのものを変えないかぎり、注意を促しても取り違えは起き続ける。確認手段は §10 が挙げる
    同名の段階が四つの別物を指す事実。
generalization:
  level: general
  general_form: 文脈の中でだけ一意な名前が、文脈を外して引用され、別のものを指す
pattern: same-name-different-referents
discovery:
  perspective: [doc_code_diff, inventory]
  note: >-
    複数の設計書の残課題を横断で棚卸ししたときに見えた。一件ずつ読むかぎり番号は正しく、
    横に並べて初めて同名が別物を指していることが分かる。実装の層の名前でも同種の衝突が
    起きており、その時は接頭辞を分けて回避していた。
resolution:
  perspective: [pending]
  note: >-
    未解決。運用としては「会話では設計書名を添える」が置かれているが、名前の付け方自体は
    変わっていない。段階名を設計書の名前と組にした形で表記する規約を置けば解ける。既存の
    文書をどこまで遡って書き換えるかが残る判断。
  landed_in: []
related: [IK-0016]
view_of: []
history: []
---

## 課題

症状は、「Phase 3」と言ったときに相手と別のものを指していることである。設計書ごとに段階の
番号が独立しているので、同じ名前が四つの別物を指していた。会話でも文書でも取り違えが起きる。

原因は、名前が設計書という文脈の中でだけ一意なのに、文脈を外して引用されることにある。個々の
設計書の中では番号は正しい。名前が持ち出されたときに文脈が一緒に来ないだけである。同種の衝突は
実装側の層の名前でも起きており、そのときは接頭辞を分けて回避していた。

## 発見の観点

複数の設計書の残課題を横断で棚卸しした。一件ずつ読むかぎりは正しく、横に並べて初めて衝突が
見える。棚卸しという観点でなければ出てこない種類の課題である。

## 解決の観点

未解決。運用として「会話では設計書名を添える」が置かれているが、名前の付け方は変わって
いないので、書かれたものを読む側には効かない。段階名を設計書の名前と組にする表記規約を
置けば解ける。既存の文書をどこまで遡るかが残る判断である。

## 一般化

名前空間の無い識別子を複数の文書やモジュールで独立に採番する場所すべてで再発する。番号・
フェーズ名・層名・コード名がその形を取りやすい。辞書の `same-name-different-referents` に
対応し、処方は「持ち出される名前は、文脈を含んだ形で付ける」。
