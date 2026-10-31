---
id: IK-0695
title: "ディスカバリー／レーダーからの取り込みが解析の分野（cartridge_id）を受け取らず、env 既定の分野が黙って適用されて、別分野の語彙ヒントが全プロンプトに混入する"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/features/paper_discovery_design.md §12"
  - "docs/features/paper_radar_design.md §16"
feature_context:
  realizing: "教員が近い論文・購読の候補を選んで取り込み、その論文の分野の語彙で解析パイプラインを走らせる"
  layers: [paper_discovery, paper_radar, cartridges, pipeline_a]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [condition]
    governance: [assignment]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（論文レーダー自律周回 1 周目の役割判定の入力と、取り込み経路のコード）: 手動アップロードは cartridge_id を受けて
    document_analysis_runs へ通すが、/ingest・/ingest-batch の要求モデルとキュー行は分野を持たず、_accept_material_source
    へ cartridge_id を渡していなかった。このため orchestrator の「引数 > env > None」の解決で env 既定が採られ、宇宙物理の論文に
    素粒子物理の正規化ヒントが渡った。接続軸 condition = 入口（教材起点・購読起点）で決まるはずの分野という条件が受理段まで
    届かない。統制軸 assignment = 分野を選ぶのは教員のはずが、選ぶ場所が無く env が代わりに決めていた。構造軸 none = 受け皿
    （document_analysis_runs.cartridge_id と _accept_material_source の引数）は既に存在し、表現を変える必要は無かった
    （キュー行の列追加は運搬のため）。処理軸 none = 各段の処理は単体では正しい。
generalization:
  level: repo_pattern
  general_form: "既存の入口が受ける条件を、後から足した別の入口が受けずに同じ受理処理へ合流し、条件の既定値が黙って適用される"
pattern: condition-not-propagated
discovery:
  perspective: [data_inspection, trace_walk]
  note: "自律周回で役割判定に渡る入力を読んでいて、分野未指定のはずの宇宙物理の論文に素粒子物理の語彙ヒント（HQET・SM）が入っていることに気付き、取り込み経路を遡って分野が運ばれていないことを確かめた。"
resolution:
  perspective: [carry_through, required_argument]
  note: "取り込みの要求モデルに cartridge_id を足し、手動アップロードと同じ検証（_validate_cartridge_option・不明は取得前に 422）を通して、同期受理とキュー行（migration 090）から worker の受理まで運ぶ。NULL（未指定）と空文字（明示的に指定しない）を区別して保持し、画面は seed 教材の採用 run の分野を既定に出す。"
  landed_in:
    - "backend/api/routes/paper_discovery.py"
    - "backend/core/paper_discovery/ingest_queue.py"
    - "backend/api/ingest_worker.py"
    - "backend/db/090_paper_discovery_ingest_cartridge.sql"
    - "frontend/public/js/admin-paper-radar.js"
    - "frontend/public/js/admin-paper-discovery.js"
  principles:
    - principle: enforce-condition-in-query-with-required-argument
      use: extracted
      note: "条件を受理の入口で検証し、キュー行に保存して非同期の受理まで運ぶ部品を採った。未指定と明示的な指定なしを別の値で保持する点が固有の差分。設計前に原理は読んでいない。"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場・実機での取り込み（取り込んだ論文の解析 run に選んだ分野が記録され、役割判定の入力に別分野のヒントが入らないこと）"
      - "migration 090 を適用した実 DB でのキュー行の往復"
related: [IK-0011, IK-0010]
view_of: []
history: []
---

## 課題

症状: 論文レーダーから取り込んだ宇宙物理の論文で、役割判定などのプロンプトに素粒子物理の語彙ヒント（HQET / SM・記法パターン）が入る。
原因: 取り込みの入口（/ingest・/ingest-batch・キュー worker）が分野を受けず、受理処理に cartridge_id を渡していなかったため、
パイプラインの分野解決が env 既定に落ちた。観察の出所は論文レーダー自律周回 1 周目の頭脳メモ（uxsim/runs/radar_loop/BRAIN_NOTES_A.md / BRAIN_NOTES_B.md の初回応答）。

## 発見の観点

実データの入力を読む（data_inspection）中で分野違いに気付き、取り込みの入口から受理・解析 run の作成まで経路を辿った（trace_walk）。

## 解決の観点

条件の運搬（carry_through）と、入口での検証を手動アップロードと同じ関数に揃えること（required_argument 相当の固定）。
キュー経由は非同期なので、キュー行に列を足して worker まで運んだ。

## 一般化

既存入口に揃えずに新しい入口を足すと、同じ受理処理へ合流した時点で条件が既定値に置き換わる。確かめ方は、条件を不利な側
（既定と異なる分野）にして新しい入口から通し、解析 run に記録された分野と下流のプロンプトを読むこと。
