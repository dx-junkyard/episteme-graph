---
id: IK-0700
title: "論文レーダーで取り込んだ後、その論文の解析結果（位置づけ・グラフレビュー・理論モジュール）へ辿る導線も、取り込んだ論文を次の起点にする導線もモーダル内に無い"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/features/paper_radar_design.md"
  - "docs/manual/teacher/11-admin-materials.md"
feature_context:
  realizing: "教員が近い論文を探して取り込み、その論文の解析結果を確かめ、さらにその論文を起点に探索を続ける"
  layers: [paper_radar, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [responsibility]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: high
    structure: low
    connection: medium
    governance: medium
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 取り込みの受理応答は material_id を返し（backend/api/routes/admin.py の受理応答）、教材一覧の行には解析結果へ
    向かう入口（グラフレビュー・位置づけ・📡）が既にあるが、レーダーのモーダル（frontend/public/js/admin-paper-radar.js の
    受理後処理）は material_id を表示にもリンクにも使わない。接続軸 information = 受理応答が運んだ対象の手掛かりが画面で
    捨てられ、利用者の操作列が確定（取り込み）で途切れる。構造軸 responsibility（low）= 取り込み後の続きを担うのが教材一覧
    だけという責務の置き方が、探索の連続を妨げている可能性。統制軸 none（medium）= 解析完了の判定は教材一覧側で正しく
    動いており、未完了のまま導線を出すかは設計判断の範囲。確定には、教員の操作列（開く → 検索 → 取り込む → 解析完了 →
    結果を見る → 次の起点にする）を実画面で歩き、どこで教材一覧へ戻る必要があるかを数える。
generalization:
  level: repo_pattern
  general_form: "探索の画面が取り込み（確定）で終わり、確定した対象の結果と次の探索の起点へ戻る導線が別画面にしか無い"
pattern: last-mile-missing
discovery:
  perspective: [trace_walk, inventory]
  note: "論文レーダー UX の棚卸し（uxsim/runs/radar_loop/UX_BASELINE.md §1 の 12・13）で、教員の操作列を開くところから受理後まで辿り、受理応答の material_id が画面で使われていないこと・openModal が教材行からしか呼ばれないことを確かめた。"
resolution:
  perspective: [carry_through, responsibility_move]
  note: "第 2 波（paper_radar_design.md §19 C）。受理応答の material_id と、検索で見える取り込み済み教材の document_id / material_id / analysis_status を候補カードまで運び、既存の入口（教材一覧の行・グラフレビュー・位置づけ・PaperRadar.openModal）につないだ。「この論文を起点に探す」は同じモーダルを新しい起点で開き直す（連鎖）。解析の完了は教材一覧の再読込（renderMaterials → PaperRadar.onMaterialsUpdated）で引き直し、レーダーからはポーリングしない（PR5）。未完了・失敗は理由付きで無効。新しい API・テーブルなし。"
  landed_in:
    - frontend/public/js/admin-paper-radar.js
    - frontend/public/js/admin.js
    - backend/core/paper_discovery/radar.py
    - backend/core/help_kb/admin_ui_anchors.py
    - docs/manual/teacher/11-admin-materials.md
    - "docs/features/paper_radar_design.md §19"
  verification:
    methods: [guardrail]
    unverified:
      - "実画面で 開く → 検索 → 取り込む → 解析完了 → グラフレビュー／連鎖 を歩いていない（docker・ブラウザを使わない波。静的検査と core / API のテストのみ）"
      - "受理直後のカードが、タスク追跡の完了で教材一覧が読み直されたときに連鎖のボタンが押せるようになることは、モーダルを開いたままの実機で見ていない"
related: [IK-0701]
view_of: []
history: []
---

## 課題

症状: レーダーで論文を取り込んだ教員は、その論文がどう解析されたか（分野の地図のどこに置かれたか・部品・理論モジュール）を
レーダーの画面から確かめられず、取り込んだ論文を起点に続けて探すこともできない。教材一覧に戻って行を探し、📡 を押し直す必要がある。
原因（仮説）: 受理応答の material_id を画面が使わず、続きの責務が教材一覧だけにある。

## 発見の観点

操作列の棚卸し（UX_BASELINE §1）で入口から確定の先まで歩いた（trace_walk / inventory）。

## 解決の観点

未解決。受理応答が既に運んでいる対象（material_id）を画面まで運び、既存の入口（教材一覧の行の操作・レーダーの openModal）へ
つなぐだけで足りる見込み（新しい API は要らない）。

## 一般化

確定で画面を閉じる設計は、探索のような連続する作業で利用者を毎回入口まで戻す。確かめ方は、利用者の操作列を取り込み → 解析完了
→ 結果の確認 → 次の探索まで実画面で歩き、別画面へ戻らずに到達できることを見る。
