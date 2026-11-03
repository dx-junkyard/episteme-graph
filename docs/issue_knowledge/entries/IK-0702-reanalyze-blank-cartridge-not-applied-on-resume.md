---
id: IK-0702
title: "再解析で分野を「指定しない」に解除しても、resume が走行中の run を同じ id で拾い、run 行の更新が分野を書かず、旧スレッドも前回の分野で並走し続けるため、解除が run 行と LLM 呼び出しに効かない"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/pipeline/cartridges.md §6"
feature_context:
  realizing: "教員が教材の再解析で分野を「指定しない」に戻し、分野中立の語彙で解析パイプラインを走らせ直す"
  layers: [pipeline_a, cartridges, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [contract]
    governance: [resume]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（routes/admin.py::reanalyze_document・orchestrator.run_document_pipeline・persistence.upsert_analysis_run の現物）:
    route と orchestrator は「None = 引き継ぎ / 空文字 = 解除」の契約に揃っていたが、persistence の UPDATE 文は
    cartridge_id 列を一切書かず、新規 INSERT のときだけ記録していた。接続軸 contract = 解除の契約が入口側だけで変わり、
    永続化の側が追随していない。統制軸 resume = resume は走行中（未完了）の run を同じ id で拾うため、再解析を重ねると
    前回の分野で起動した旧スレッドが止まらず並走し、頭脳が受けたプロンプトの分野ヒントはこの旧スレッド由来だった。
    処理軸 none = 各関数の処理自体は単体で正しい。構造軸 none = 受け皿の列は既にあり表現の変更は要らない。
generalization:
  level: repo_pattern
  general_form: "入口で「未指定」と「明示的な解除」を区別する契約を足したが、既存の行を更新する経路は列を書かないままで、再実行が既存行を拾う場合だけ解除が失われる"
pattern: contract-changed-one-side
discovery:
  perspective: [data_inspection, trace_walk]
  note: "自律周回の頭脳メモで、route ログは毎回「指定しない」なのに run 行は前回の分野のまま・同じ run id で、同一ブロックの役割判定が繰り返し届くことに気付き、route → orchestrator → persistence の値の流れと resume の run 選択を辿った。"
resolution:
  perspective: [explicit_contract, state_transition]
  note: "persistence は cartridge_id を「この run の実効の分野」として更新でも必ず書く（空は NULL）。継承の解決は route（前回 run）と orchestrator（env）だけが行い、persistence に「None なら保持」の分岐を置かない。加えて同じ教材の解析がこのプロセスで走っている間の再解析は 409 にし、旧スレッドとの並走を断つ（画面はサーバの事実文を出す）。"
  landed_in:
    - "backend/core/document_pipeline/persistence.py"
    - "backend/api/routes/admin.py"
    - "frontend/public/js/admin.js"
    - "backend/tests/test_pipeline_cartridge_resume.py"
  verification:
    methods: [guardrail]
    unverified:
      - "実 DB・砂場での再解析（run 行が NULL になり、役割判定の入力に分野ヒントが載らないこと）"
      - "複数プロセス構成での走行中判定（判定はプロセス内の状態だけを見る）"
      - "教材管理以外の再実行経路（原稿スタジオのパイプライン実行など）での並走"
related: [IK-0695]
view_of: []
history: []
---

## 課題

症状: 再解析を body `{"cartridge_id": ""}` で 4 回呼んでも、`document_analysis_runs` は同じ run id のまま `cartridge_id='particle_physics'`
で、役割判定などのプロンプトに素粒子物理の語彙ヒント（HQET / SM）と記法パターンが載り続け、同じブロックの判定が繰り返し届いた
（観察の出所は論文レーダー自律周回の頭脳メモ uxsim/runs/radar_loop/BRAIN_NOTES_C3.md 等）。

原因は 2 つ: ①`upsert_analysis_run` の UPDATE が `cartridge_id` を書かず、resume で既存行を更新する経路では解除が記録されない
（次の未指定の再解析も前回 run の分野を引き継いでしまう）。②resume は未完了の run を同じ id で拾うので、最初の解析（前回の分野）が
走っている最中に再解析を重ねると旧スレッドが並走し続け、その LLM 呼び出しに前回の分野が載る。新しいスレッドの agent には
orchestrator が正規化した None が渡っており、run 行から分野を読み戻す経路は無かった。

## 発見の観点

実データ（頭脳が受けた入力と run 行）を読み（data_inspection）、入口から永続化までの値と resume の run 選択を辿った（trace_walk）。

## 解決の観点

契約の明示（explicit_contract）: 継承は route と orchestrator が解決済みにし、persistence は渡された実効値を常に書く。
`options` と同じ「None なら保持」を分野にも入れる案は、resume で解除を表せない二義性を残すので採らなかった。
状態遷移（state_transition）: 走行中の教材の再解析は 409 で受け付けない（旧スレッドを止める仕組みは無いため、重ねないことで断つ）。

## 一般化

「未指定」と「明示的に空」を入口で区別しても、更新経路が列を書かない・None を保持と読む層が 1 つでもあると、既存行を
拾う再実行でだけ解除が失われる。確かめ方は、既存行がある状態（resume）で解除を通し、行の値と下流の入力の両方を読むこと。
