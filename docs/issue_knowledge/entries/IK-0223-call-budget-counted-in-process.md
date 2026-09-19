---
id: IK-0223
title: 呼び出し回数の上限がプロセス内の数え上げで、多重化すると統制が効かなくなる
status: deferred
recorded_at: 2026-08-13
resolved_at: null
sources:
  - docs/architecture/feature_consolidation_proposals_2026-08-13.md §2-12
feature_context:
  realizing: 外部モデルの呼び出し回数を機能ごとの上限で抑え、費用と負荷を統制する
  layers: [shared_infra]
classification:
  axes:
    processing: [none]
    structure: [responsibility]
    connection: [none]
    governance: [budget]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 統制は、上限の数え上げが実行プロセスの中に閉じているため、同じ上限が並走するプロセスの
    数だけ重複して許される点に当たる。構造は、利用者あたり・一日あたりという意味を持つ予算を
    実行単位が抱えており、数える主体の置き場所が意味の単位と合っていない点に当たる（どこが持つか
    ではなく何で表すかの問題と読む余地は残る）。処理は、数え上げそのものが書かれたとおりに動く
    ため要素は無いと判断した。接続は、段階の間で情報・条件・対象・版が失われる場面が無いため
    要素は無いと判断した。いずれも、多重の実行単位で同一利用者の日次上限を超える呼び出しが通る
    ことを再現していないため確信は中に留める。
generalization:
  level: general
  general_form: >-
    共有すべき予算の数え上げが実行単位の内側に閉じており、実行単位が増えると上限が実質的に倍加する
discovery:
  perspective: [inventory, external_constraint]
  note: >-
    複数の設計書が同じ制約を「対象外」として繰り返し言及していることに気づき、言及箇所を
    棚卸しした。個々の設計書では正しく非対象と書かれているが、判断そのものは一度も下されて
    いない。外部呼び出しの費用という制約から逆算すると、多重化した時点で一斉に破綻する型の
    負債である。
resolution:
  perspective: [deferred_decision]
  note: >-
    許容し続けるか、共有の記録へ移すかを一度だけ判断する必要がある。判断材料は、多重化の
    予定の有無と、上限を超えたときの実害（費用と外部からの制限）の見積り。散在する言及を
    一箇所へ集約してから判断するのが先で、個別の設計書で繰り返し非対象と書き続けても
    判断は進まない。
  landed_in: []
related: [IK-0222]
view_of: []
pattern: external-budget-exceeded
history:
  - date: '2026-09-19'
    field: classification
    from: primary=governance facets=[governance.budget, structure.responsibility]
    to: axes=processing=[none]; structure=[responsibility]; connection=[none]; governance=[budget]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

**症状**: 現時点では現れていない。複数の機能が「一日あたりの呼び出し回数」の上限を持つが、
その数え上げは実行プロセスの中に閉じている。単一のプロセスで動かしている限り上限は意図
どおりに効く。

**原因（仮説）**: 数え上げが実行単位の内側にあるため、実行単位を増やした時点で、同じ上限が
プロセスの数だけ重複して許される。上限の意味は「利用者あたり・一日あたり」なのに、実装の
単位は「プロセスあたり」であり、意味と単位がずれている。仮説である理由は、多重化した構成で
実際に上限を超えた呼び出しが通るところを再現で確かめていないため。

## 発見の観点

`inventory` と `external_constraint`。複数の設計書が同じ制約を「対象外」として繰り返し
言及していることを棚卸しで拾った。個々の記述はどれも正しく非対象と書いているが、判断そのものは
一度も下されていない — 「何度も先送りされている」こと自体が信号になる。

## 解決の観点

未解決（保留）。分かれば解けるのは二つ — 多重化の予定があるかと、上限を超えたときの実害
（費用と外部からの制限）の大きさ。まず散在する言及を一箇所に集め、許容し続けるか共有の記録へ
移すかを一度だけ決める。個別の設計書で非対象と書き続けても判断は進まない。

## 一般化

レート制限・同時実行数・在庫・ロックなど、共有すべき量を実行単位の内側で数えるあらゆる実装で
再発する。単一構成では完全に正しく動くため、水平に増やした瞬間に一斉に破綻するのが特徴。
辞書の型は `external-budget-exceeded`。
