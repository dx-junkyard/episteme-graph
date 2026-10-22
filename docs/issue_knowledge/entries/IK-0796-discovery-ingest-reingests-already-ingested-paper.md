---
id: IK-0796
title: "論文ディスカバリーの取り込みの弁（/ingest・/ingest-batch・worker）に「取り込み済み」の判定が無く、既に取り込んだ arXiv 論文を再度受理して解析パイプラインを丸ごと二重に走らせた"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/features/paper_discovery_design.md §12.5"
  - "docs/features/paper_radar_autonomous_loop_design.md"
feature_context:
  realizing: "教員が arXiv 候補（分野購読・論文レーダー）から選んだ論文を取得し、既存のアップロードパイプラインへ流す"
  layers: [paper_discovery, paper_radar]
classification:
  axes:
    processing: [none]
    structure: [responsibility]
    connection: [condition]
    governance: [review]
  axis_confidence:
    processing: medium
    structure: high
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸 responsibility = 「取り込み済み」の判定（search.visible_ingested_documents）が候補一覧（表示面）にだけあり、
    取り込みを受理する弁（同期 /ingest と worker の取得直前）が判定を持っていなかった。キュー投入（/ingest-batch）は
    全 documents から作る別の判定（_ingested_arxiv_ids）で、可視性の条件も違った。接続軸 condition = 一覧で効いて
    いる「取り込み済み」という条件が弁へ伝わらず、API を直接呼ぶ経路（自律周回のドライバ）では素通しになった。
    統制軸 review = 二重取り込みを止める人の確認点が UI の表示（チェックボックスを出さない）だけに依存していた。
    処理軸 none（各関数単体の処理は正しい）。
generalization:
  level: general
  general_form: "重複・資格の判定を一覧の表示側にだけ置き、受理する入口に同じ判定を持たないため、表示を経由しない呼び出しで同じ対象が二重に受理され、高価な後段が二重に走る"
pattern: rule-enforced-per-surface
discovery:
  perspective: [symptom_report, data_inspection]
  note: "2026-10-16 23:02 の実機で、documents.source_url = https://arxiv.org/src/2609.35757 の教材がある状態で POST /api/admin/discovery/ingest を再度呼ぶと拒否も警告もなく新しい document（2a24b9e4）が作られ、解析パイプラインが二重に走った（LLM コールの無駄）。候補一覧の判定（source_url）と取り込みの弁を突き合わせて弁に判定が無いことを確かめた。きっかけは IK-0795（ハーネスの再開不良）。"
resolution:
  perspective: [canonical_source, fail_closed]
  note: "可視性つきの判定 visible_ingested_documents を ingest_queue に移して正本とし（search は再エクスポート・worker は arxiv_client を読まずに使える）、同期 /ingest は arXiv を呼ぶ前に判定して全件取り込み済みなら 409（detail は事実文・existing[] に document_id / material_id / title）、一部なら failed に reason=already_ingested + existing、/ingest-batch は投入者の可視集合で判定して skipped に同じ形、worker は取得直前に再判定して failed に残す。force は作らない（重複は資産にならない・やり直しは再解析）。汎用 URL 取得には付けない（UF5 の同一経路・事後の通知では費用が先に出る）。"
  landed_in:
    - backend/api/routes/paper_discovery.py
    - backend/api/ingest_worker.py
    - backend/core/paper_discovery/ingest_queue.py
    - backend/core/paper_discovery/search.py
    - backend/tests/test_paper_discovery_api.py
    - backend/tests/test_paper_discovery_worker.py
    - docs/features/paper_discovery_design.md §12.5
  verification:
    methods: [guardrail]
    unverified:
      - "実機（docker）での 409 と画面表示（レーダーの「解析結果へ」導線は未配線・detail の事実文表示のみ）"
      - "手動アップロード分（source_url 空）の同一論文は判定できない（既知の非スコープ）"
      - "他の教員が非公開で取り込んだ同じ論文は投入者に見えないので止めない（IK-0701 の可視性方針）"
related: [IK-0795, IK-0701]
view_of: []
history: []
---

## 課題

症状: 既に取り込み済みの arXiv 論文（2609.35757）に対して `POST /api/admin/discovery/ingest` を再度呼ぶと、拒否も警告も
なく新しい document が作られ、解析パイプラインが丸ごと二重に走った。原因: 候補一覧は「取り込み済み」を
`documents.source_url` の正規化 arXiv ID で判定しているが（`search.visible_ingested_documents`）、取り込みを受理する
同期の `/ingest` と worker の取得直前に同じ判定が無く、`/ingest-batch` は可視性を見ない別の判定を使っていた。

追加の出所（頭脳 T2）: uxsim/runs/radar_loop/BRAIN_NOTES_T2.md（seq 2975 — 同じ 2609.35757 が document c795b2cf と 2a24b9e4 の 2 件で解析されている）。

## 発見の観点

実機の取り込み結果（新しい document）と既存教材の `source_url` を突き合わせ（data_inspection）、一覧の判定と弁の
判定の有無を比べた（symptom_report）。

## 解決の観点

判定の正本を 1 つ（`ingest_queue.visible_ingested_documents`）にして、表示・同期受理・キュー投入・worker の 4 箇所が同じ
関数・同じ可視集合を通るようにした（canonical_source）。弁は arXiv を呼ぶ前に閉じる（fail_closed）。再取り込みの
`force` は作らない — 重複した教材は資産にならず、やり直しは再解析 API がある。

## 一般化

重複・資格の判定が表示側にだけあると、表示を経由しない入口（API 直叩き・自動化・非同期 worker）から同じ対象が通る。
辞書の型 `rule-enforced-per-surface`。
