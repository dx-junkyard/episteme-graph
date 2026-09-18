---
id: IK-0307
title: サーバが立てた縮退の事実文が画面に描かれず、0 件が「近い論文が無い」と読める
status: resolved
recorded_at: 2026-09-13
resolved_at: 2026-09-15
sources:
  - docs/features/paper_radar_design.md §13
  - docs/features/paper_radar_design.md §14.7
  - docs/features/paper_radar_design.md §14.8
feature_context:
  realizing: 教材を起点に近い論文を探し、探せなかったときはその理由を正直に示す
  layers: [paper_radar, paper_discovery, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [information]
    governance: [none]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    原因は「縮退の理由という情報が、サーバの応答には載っているのに最後の描画段で
    読まれないこと」。各段は単体では正しく（core は事実文を返し、画面は候補を描く）、
    段をまたいだところで情報が落ちているため接続。設計書 §13.1 が
    「閉世界の正直さがサーバ側でだけ守られ、最後の1段で落ちていた」と確認している。
    §14.7 は同じ誤読が別経路で再発する構造（状態が文章の中にしか無い）を扱う。
generalization:
  level: repo_pattern
  general_form: 縮退や失敗の理由が応答には在るのに表示側と繋がっておらず、空の結果が「該当なし」と読める
pattern: available-but-unwired
discovery:
  perspective: [symptom_report, trace_walk]
  note: >-
    「検索しても 0 件」という報告から、検索条件が空 → 外部 API を呼んでいない →
    メタデータ取得が失敗していた → 失敗の事実文は応答に在った、と原因の連鎖を
    末端から遡った。外部 API のレート制限という制約が起点だった。
resolution:
  perspective: [carry_through, first_class_state]
  note: >-
    ①画面が応答の事実文をそのまま描く（文言をフロントで発明しない）②理由ごとに
    別の事実文を立てる（繋がらない／制限されている／該当が無い、で次の一手が違う）
    ③状態を機械可読の目印にし、制限中と分かっている操作は呼ばずに 200 と事実文で返す。
    サーバの事実文が画面に出ることをテストで固定した。
  landed_in:
    - backend/core/paper_discovery/radar.py
    - frontend/public/js/admin-paper-radar.js
    - docs/features/paper_radar_design.md §13.2
    - docs/features/paper_radar_design.md §14.8
related: [IK-0308]
view_of: []
history:
  - date: '2026-09-19'
    field: classification
    from: primary=connection facets=[connection.information, structure.representation]
    to: axes=processing=[none]; structure=[representation]; connection=[information];
      governance=[none]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

症状は「レーダーを開くと検索条件が空のまま、検索しても『この検索条件では候補が
見つかりませんでした』しか出ない」。教員には「近い論文が無い」と読める。

実際には検索そのものが実行されていなかった。条件が空だったのは起点論文のメタデータ取得が
外部 API のレート制限で失敗したためで、その事実はサーバの応答に事実文として載っていた。
落ちていたのは**最後の描画段**で、画面はその項目を読んでいなかった。加えて「いま制限
されている」という状態は文章の中にしか無く、空の結果と区別する目印が無かった。

## 発見の観点

症状報告から、条件が空 → 外部 API を呼んでいない → 取得が失敗していた → 失敗の理由は
応答に在った、と原因の連鎖を遡った（`symptom_report` + `trace_walk`）。外部 API の
レート制限という制約から逆算したことで、理由を分ける必要（待てば直るのか、条件を疑うのか）
も見えた。

## 解決の観点

応答に在った情報を表示まで運び（`carry_through`）、理由ごとに別の事実文を立てる契約に
した。さらに「いま制限されている」を文章の中ではなく機械可読の目印という一級の状態にして
（`first_class_state`）、呼ぶ前から分かっている操作は外部を呼ばずに事実文で返す。空一覧に
は必ず理由を伴わせる、という規律もテストで固定した。

## 一般化

「正直に縮退する」を core に実装しても、表示側が読まなければ利用者にとっては嘘のままで
ある。空の結果は常に二義的（該当が無い／探せていない）なので、理由の担体を画面に持つ。
辞書の型 `available-but-unwired` に対応する。
