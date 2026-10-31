---
id: IK-0761
title: "論文レーダーで取り込んだ論文を次の起点にする連鎖の系譜（元の起点 → トライスペクトル → FoG …）が画面に出ず、第 4 波の「前の起点」1 行も保存されず 1 段だけ"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-22
sources:
  - "docs/features/paper_radar_design.md §24"
  - "docs/features/paper_radar_design.md §21"
  - "docs/features/paper_radar_autonomous_loop_design.md"
feature_context:
  realizing: "教員が近い論文を取り込んでは次の起点にして探索を続けるとき、いまの起点がどの起点から来たかの系譜を読む"
  layers: [paper_radar, paper_discovery]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [condition]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: low
    connection: medium
    governance: medium
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 「この論文を起点に探す」で開き直したときの前の起点は JS 内の呼び出し履歴だけで保存も送信もしない（§21 D）ため、系譜は 1 段で
    途切れ、モーダルを閉じる・教材行から開き直すと消える。取り込みの事実（どの起点のレーダーから取り込んだか）がサーバ側の起点解決
    （resolve_seed）に渡らない。接続軸 condition = 取り込み時の文脈（起点）が次の起点の画面へ伝わらない。構造軸 none（確信度 low: 系譜を
    持つ列が無いと読むなら representation にも当たる）。処理・統制は none。確定には、取り込みの監査（AUDIT_ENTITY_PAPER_DISCOVERY）の
    metadata に起点の document が記帳されているか、documents.source_url と突き合わせて系譜を辿れるかを確かめる。
generalization:
  level: general
  general_form: "ある操作の結果を次の操作の入力にして連鎖させるとき、前段の文脈をクライアントの一時状態にしか持たないため、連鎖の系譜が 1 段で途切れ、画面を開き直すと消える"
pattern: condition-not-propagated
discovery:
  perspective: [data_inspection]
  note: "浅い段の審判が scan-07 の seed ヘッダと取り込み済みカードを読み、元の起点（尖度の整合関係）から来たこと・前段で取り込んだ運動論の論文がこの起点から取り込まれたことが読めないと記録した（scan-06 仮説 4 の継続。出所: uxsim/runs/radar_loop/judge-scan-07.md §④-5 / judge-deep-06.md）。"
resolution:
  perspective: [carry_through, required_argument]
  note: "取り込みの監査（AUDIT_ENTITY_PAPER_DISCOVERY の ingest / ingest_batch）には起点が記録されていなかったので、レーダーの取り込みが seed_document_id を送り、サーバが view を確かめて documents.id に正規化したときだけ監査 metadata に残すようにした（閲覧できない値は記録しない・取り込みは止めない）。GET /radar/seed は radar.seed_lineage で source_url の arXiv ID → それを取り込んだ監査 → 起点 → … を遡り、seed.lineage（古い → 新しい）/ lineage_line / lineage_note を返す。段数の上限・循環の停止・閲覧できない論文は題名も id も出さずに止める・失敗は fail-soft。可視集合は route からの必須の注入で、空なら SQL を発行しない。新テーブル・保存なし。画面は seed ヘッダに 1 行で、JS 内の「前の起点」行に優先する。"
  landed_in:
    - "backend/core/paper_discovery/radar.py"
    - "backend/api/routes/paper_discovery.py"
    - "frontend/public/js/admin-paper-radar.js"
    - "backend/tests/test_paper_radar_core.py"
    - "backend/tests/test_paper_radar_api.py"
    - "backend/tests/test_paper_radar_ui_static.py"
    - "docs/features/paper_radar_design.md §24"
  principles:
    - principle: enforce-condition-in-query-with-required-argument
      use: consulted
      note: >-
        採った部品は可視集合の必須キーワード引数と、空集合での SQL 非発行。固有の差分: 閲覧できない論文は名前を伏せた語に置き換え、そこで遡るのを止める（存在は 1 語で残し、先の系譜は辿らない）。取り込み時の起点の記録もクライアントの申告を信用せず view で確かめてから正規化する。
  verification:
    methods: [guardrail]
    unverified:
      - "実 DB の theory_review_events に対する JSONB 包含（metadata->'arxiv_ids' @> ...）の問い合わせ（フェイクセッションで検証・実 DB は触っていない）"
      - "この変更より前の取り込みには起点の記録が無いので系譜に出ない"
      - "取り込み → 次の起点で開き直す → 閉じて教材行から開き直す操作列での画面の確認（ブラウザは開いていない）"
related: [IK-0700]
view_of: []
history: []
---

## 課題

症状: 連鎖 2 回目（起点 = トライスペクトルの full-shape 解析）の seed ヘッダは起点だけを示し、元の起点から来たこと、前段で取り込んだ
論文がこの起点から取り込まれたことが読めない。第 4 波の「前の起点」1 行は JS 内の一時状態で 1 段だけ。
原因（仮説）: 前段の起点を保存も送信もせず、サーバ側の起点解決に取り込みの文脈が渡らない。

出所: uxsim/runs/radar_loop/judge-scan-07.md §④-5。

## 発見の観点

連鎖の周（scan-06 → scan-07）の seed ヘッダと取り込み済みカードを並べ、系譜が辿れないことを確かめた（data_inspection）。

## 解決の観点

未解決。既存の出所（source_url）と監査から読み時導出する案を第一候補とする。

## 一般化

連鎖する操作では、前段の文脈をどこに持つか（一時状態か、既存の記録から導出できるか）を決めておく。確かめ方は、取り込み → 次の起点で
開き直す → モーダルを閉じて教材行から開き直す、の操作列で系譜が保たれるかを見ること。
