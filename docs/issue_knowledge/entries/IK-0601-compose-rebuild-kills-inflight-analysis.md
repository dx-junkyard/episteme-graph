---
id: IK-0601
title: "実機検証で静的資産だけのつもりの compose 再ビルドが依存先の API サーバも作り直し、走行中の解析 run を止めて running のまま残す"
status: resolved
recorded_at: 2026-10-03
resolved_at: 2026-10-03
sources:
  - docs/features/simulation_bridge_design.md §12.2
  - docs/architecture/improvement_cycle.md
feature_context:
  realizing: "検証シミュレーション層の画面を実機の dev スタックで確かめるため、フロントエンドの静的資産を差し替える"
  layers: [cycle_verification, pipeline_a]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [condition]
    governance: [ordering, resume]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: `docker compose -f docker-compose.yml -f docs/architecture/improvement_cycle.md §2.5 up -d --build frontend` は、設定が
    変わった依存先のコンテナも作り直す。sim-runner のネットワーク・環境変数の追加で api-server の設定が変わって
    いたため api-server も再作成され、走行中の解析 run が equation_semantics の段で止まり、状態は running のまま
    残った。api-server のログに migration の起動表示が出ていたことから再起動に気づいた。接続軸 condition =
    「解析 run が走行中」という条件が、再ビルドの操作を選ぶ段に伝わっていなかった。統制軸 ordering = 再ビルドは
    run の完了後に置くべき操作だった / resume = 止まった run は自動では再開・失敗化されず、再解析が要った。
    処理軸・構造軸はコードの誤りではなく運転手順の問題なので none。
generalization:
  level: general
  general_form: "一部だけを差し替えるつもりの再配置操作が依存関係を通じて長時間の処理を担う実行体まで作り直し、走行中の処理を止める"
pattern: condition-not-propagated
discovery:
  perspective: [data_inspection, trace_walk]
  note: "api-server のログを読むと migration の起動表示が出ており、フロントエンドだけを再ビルドしたはずの操作で API サーバが再起動していたこと、そこから解析 run が running のまま止まっていることを辿った。"
resolution:
  perspective: [order_and_budget]
  note: >-
    サイクルの検証段の手順として順序を決めた。解析 run が走行中の間はどのサービスにも `compose up` を実行しない。
    静的資産の差し替えは `docker cp` でフロントエンドのコンテナへ直接写し、再ビルドは run の完了後に行う。
    止まった run は再解析でやり直した。破壊的操作の確認部品を作る案（`destructive-action-without-confirmation`）は、
    これがアプリの操作ではなく検証者の運転手順の問題なので採らなかった。
  landed_in:
    - docker-compose.local.yml
    - docs/features/simulation_bridge_design.md §12.2
  verification:
    methods: [docker_e2e]
    unverified:
      - "起動時に running のまま残った run を失敗として記帳すべきか（パイプラインの担当への後続の提案。今回は実装しない）"
      - "依存先の作り直しを伴わない再ビルドの指定（compose の依存再作成を抑える指定）の検討"
related: [IK-0599, IK-0600]
view_of: []
history: []
---

## 課題

実機検証の途中で、フロントエンドの静的資産を差し替えるつもりで `compose up --build frontend` を実行したところ、設定が変わって
いた依存先の api-server も作り直された。走行中だった解析 run はそこで止まり、状態は running のまま残ったため、再解析を
やり直す必要があった。

## 発見の観点

api-server のログに migration の起動表示が出ていたことから再起動に気づき、run の状態が running のまま進んでいないことまで辿った。

## 解決の観点

順序の統制として、解析 run が走行中は compose の再配置操作を行わない規律にした。静的資産は `docker cp` でコンテナへ直接写して
配信されたファイルを確かめ、再ビルドは run の完了後に回す。止まった run を起動時に失敗として記帳するかはパイプライン側の判断で、
後続の提案に留めた。

## 一般化

再配置・再起動の操作は、指定したもの以外の実行体まで巻き込むことがある。長時間の処理が走っている間に配置を変えるときは、
その処理を担う実行体が巻き込まれないかを先に問う。辞書の型は `condition-not-propagated`（「処理が走行中」という条件が操作の
選択に渡っていない）。止まった処理が状態を running のまま残す点は再開の統制の問題として残っている。
