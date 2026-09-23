---
id: IK-0351
title: 起動時 stable_key バックフィルのセッションスコープ advisory lock が、ORM Session の commit で接続がプールへ返った後に別接続で unlock され、警告とロック残留を起こしていた
status: resolved
recorded_at: 2026-09-21
resolved_at: 2026-09-21
sources:
  - docs/features/knowledge_objects_design.md §5.6
  - docs/features/knowledge_objects_design.md §12.2
feature_context:
  realizing: 複数レプリカの同時起動でも stable_key バックフィルを直列化し、部分一意索引の衝突を防ぐ
  layers: [knowledge_objects, migrations_db, deployment, shared_infra]
classification:
  axes:
    processing: [resource]
    structure: [responsibility]
    connection: [condition]
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
    処理軸: 取ったロックの後始末（unlock）が、それを握る接続の外で行われる resource の不良。
    構造軸: ロックの寿命を「接続」に置きながら、接続の寿命を管理するのは ORM Session
    （commit で返却）という別の層で、責務が割れていた。接続軸: ロックを握った接続という
    条件が commit の境界を越えて unlock 側へ伝わらない（condition）。統制軸: 順序・予算・
    再開の統制自体は正しく、ロックの取り方の問題なので none。確認手段: 起動ログの
    postgres WARNING の位置が schema_registry シード直後・llm_policy シード直前で
    バックフィル区画と一致すること、SQLAlchemy 2.0 の Session が commit で接続を返す仕様、
    旧コードに対してプール挙動を模した fake が unlock を別接続で捕まえること。
generalization:
  level: general
  general_form: 接続に寿命が結び付く資源（ロック・一時テーブル・SET）を、接続をプールへ返す操作をまたいで取得・解放する
pattern: context-lost-across-execution-boundary
discovery:
  perspective: [symptom_report, data_inspection]
  note: >-
    オーナーが持ち込んだ起動ログの postgres WARNING（you don't own a lock of type
    ExclusiveLock）を、api-server 側のログの前後関係と main.py の lifespan の順序に
    突き合わせ、advisory lock を使う 2 箇所（migrations.py / main.py）の接続の扱いの差を読んだ。
resolution:
  perspective: [order_and_budget, guardrail_fix]
  note: >-
    ロックの寿命を接続ではなくトランザクションに置き換える（pg_advisory_xact_lock）。
    バックフィルは末尾で 1 回しか commit しないので、その commit / rollback で必ず外れ、
    unlock の呼び出し自体が不要になる（atlas_store / help_kb と同じ作法）。代替案の
    「1 本の Connection に Session を束ねる」は、解放の呼び出しと接続管理が残るため採らなかった。
    ガードレールは main.py の区画をそのまま実行し、プール返却を模した fake session で
    「ロックとバックフィルが 1 接続・1 トランザクション」「unlock 不在」を固定する。
  landed_in:
    - backend/api/main.py
    - backend/core/knowledge_objects/backfill.py
    - backend/tests/test_knowledge_objects_backfill.py
    - docs/features/knowledge_objects_design.md §5.6
  verification:
    methods: [guardrail]
    unverified:
      - docker で組み上げた実機の起動ログで WARNING が消えること
      - 2 レプリカ同時起動での直列化（scratch DB での同時実行）
related: [IK-0310]
view_of: []
history: []
---

## 課題

**症状**: 起動のたびに postgres が `WARNING: you don't own a lock of type ExclusiveLock` を出す。
api-server 側のログには何も出ない（unlock の失敗は例外にならず debug ログにも残らない）。

**原因**: main.py の起動時バックフィルが ORM Session で `pg_advisory_lock`（セッションスコープ）を
取り、バックフィル後に `commit()` してから finally で `pg_advisory_unlock` を呼んでいた。SQLAlchemy 2.0 の
Session は commit のたびに接続をプールへ返し、次の execute は新しいトランザクションで接続を取り直す。
プールは FIFO で、起動時には atlas シードや schema_registry のセッションが先に返した接続が先頭に
あるため、unlock は別の接続に乗る。警告は無害だが、ロック本体は元の接続に握られたまま残り、
このプロセスが生きている間に別レプリカが起動すると `pg_advisory_lock` で待ち続ける。
migrations.py のランナーは 1 本の `Connection` を通しで使うため同じ問題が無い。

## 発見の観点

起動ログの症状報告（`symptom_report`）から、警告の時刻・前後の api-server ログと lifespan の
順序を突き合わせ（`data_inspection`）、advisory lock を使う 2 箇所の接続の扱いを比較した。

## 解決の観点

ロックの寿命をトランザクションに置く `pg_advisory_xact_lock` に置き換え、解放をトランザクションの
終了に委ねる（`order_and_budget`）。旧コードに対して新テストが落ちること（unlock が 2 本目の接続に
乗る）を確認してから修正した（`guardrail_fix`）。

## 一般化

接続に寿命が結び付く資源（セッションスコープのロック・`SET`・一時テーブル）を、接続をプールへ
返す操作（ORM Session の commit / rollback）をまたいで扱うと、取得側と解放側が別の接続になる。
同じ Session でロックが要るときは xact スコープにするか、1 本の Connection に束ねる。辞書の
`context-lost-across-execution-boundary`（境界 = 接続プールの返却）に当たる。
