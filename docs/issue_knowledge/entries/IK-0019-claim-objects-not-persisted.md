---
id: IK-0019
title: 分解した主張と式由来の主張が行にならず、グラフの根拠が「未解決」になる
status: resolved
recorded_at: 2026-09-10
resolved_at: 2026-09-13
sources:
  - docs/architecture/six_lenses_2026-09-10/known_issues_features.md A-22
  - docs/architecture/six_lenses_2026-09-10/03_knowledge.md
  - docs/features/knowledge_objects_design.md
feature_context:
  realizing: 論文から取り出した主張を、承認や参照の対象になる一級の記録として扱う
  layers: [pipeline_a, knowledge_objects, graph_review]
classification:
  primary: structure
  facets: [structure.representation, connection.information]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    原因は「生成された主張のうち一部だけが行として表現され、残りは生成ログの中にしか
    居場所が無い」こと。永続化処理は対象としたものを正しく書いており、権限も版も落ちて
    いない。表現の対象を広げないかぎり、読み側でいくら補っても参照先が行として存在しない。
    確認手段は A-22 行が引く永続化の範囲と、分解の粒度に対応する列が無いこと。
generalization:
  level: general
  general_form: 生成物の一部だけが一級の記録になり、残りは後から参照できない場所に留まる
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [symptom_report, trace_walk]
  note: >-
    グラフの画面で根拠の主張が「未解決」と出る症状から遡った。参照されている識別子が
    どこから来るかを辿ると、生成ログには存在するのに行としては書かれていない種類の主張が
    あることが分かった。読み側の補完でしのいでいたが、承認の対象になれない点は残っていた。
resolution:
  perspective: [representation_change, carry_through]
  note: >-
    読み側の補完を厚くする方向ではなく、生成された主張を全て行にする方向で解いた。親の
    主張・分解した子・式から合成したものを、それぞれ出自の区別と親への参照つきで書く。
    これにより、根拠として参照される識別子はすべて行として存在し、承認の対象にもなる。
  landed_in:
    - backend/core/document_pipeline/persistence.py
    - backend/core/schema.py
    - docs/features/knowledge_objects_design.md §12
related: [IK-0002, IK-0020]
view_of: [IK-0105, IK-0304]
history: []
---

## 課題

症状は、グラフの画面でノードの根拠として挙がっている主張が「未解決」と表示されることである。
遡ると、生成された主張のうち一部の種類（分解した子の主張と、式から合成した主張）が行として
書かれず、生成ログの中にしか存在しなかった。

原因は、行として表現する対象が生成物の一部に限られていたことにある。永続化処理は対象として
いたものを正しく書いている。参照される側が行として存在しないので、参照する側からは解決
できない。承認の対象にもなれないため、教員の判断が届く母集団からも外れていた。

## 発見の観点

画面の症状から遡り、参照されている識別子の出どころを一本辿った。生成ログには存在するのに
行としては無い、という非対称が見えた。読み側で生成ログを引いて補う暫定対応が入っていたが、
それは表示を直すだけで、承認の対象にはならない。

## 解決の観点

読み側の補完を厚くする案は、承認の母集団が変わらないので採らなかった。生成された主張を
全て行にし、出自の区別と親への参照を持たせた。参照される識別子がすべて行として存在すれば、
表示も承認も同じ土台に乗る。

## 一般化

生成物の一部を「派生だから」と行にしない設計すべてで再発する。後から参照や承認の対象に
なったときに顕在化する。辞書の `information-dropped-as-unrepresentable` に対応し、
処方は「参照される可能性のある単位は、生成の時点で一級の記録にする」。
