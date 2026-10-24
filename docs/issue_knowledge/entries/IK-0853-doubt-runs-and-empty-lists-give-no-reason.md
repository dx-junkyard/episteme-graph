---
id: IK-0853
title: "前提の地図・ゼミ前ブリーフ・開幕・学生の未検証の前提一覧で、「実行したが該当なし」「実行されなかった（論文が少ない・材料がコースに届いていない）」「受け付けた（非同期）」「取得に失敗した」が同じ空の見た目になる — コーパス監査の skipped / documents とマイニングの scheduled を画面が捨て、空の区画は区画ごと消えるか同じ一文になる"
status: resolved
recorded_at: 2026-10-22
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
  - "docs/features/doubt_layer_issues.md"
  - "docs/features/seminar_brief_mirroring_design.md"
feature_context:
  realizing: "教員が前提の地図でバッチを起動して結果を確かめる／ゼミ前ブリーフで脆い前提を探す／学生が未検証の前提一覧と開幕の「確かめていないこと」を読む"
  layers: [doubt_d, seminar_brief, discuss, frontend_admin_ui, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 information: API は理由を返しているのに画面が落とす — corpus-audit は `{"skipped": true, "documents": 0}`、
    assumption-mining は `{"scheduled": true}` を返すが、`doubt-atlas.js` のボタン（1240-1251）は `.then(reloadAll)` で応答を読まない。
    構造軸 representation: 空の理由を表す値・文言が無い — 散布図・前提候補（「該当する前提はありません。」）・未検証合意リスト
    （「高負荷 × 低検証の対象は現在ありません。」）は該当なしと未実行・材料なしを同じ文にし、空のときに台帳を開く別の入口
    （原稿スタジオのグラフのノード詳細）を示さない。ゼミ前ブリーフ `admin.js::_sbSection`（3988-）は行ゼロなら区画を返さず、
    「脆い前提」「晴れ間」が消える（無いとは言わない）。開幕 `discuss.js::renderPaperUnverifiedSection`（790-）も空なら ""。
    学生の `app.js::loadLearnerOpenAssumptions`（3321-3342）は 4xx・空・例外を同じ「現在、表示できる項目はありません。」にする。
    処理軸 none（各分岐は宣言どおり）。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "処理の結果や一覧が空のとき、「調べて該当なし」と「調べなかった・調べられなかった・まだ終わっていない」を同じ空で返し、利用者が次の一手を選べない"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction, data_inspection]
  note: "教員 te-03 はコーパス監査・前提マイニングを押しても画面が一字一句同じで、実行されたのか条件で止まったのかを判別できなかった。te-02 はゼミ前ブリーフに脆い前提・晴れ間の区画が無いことを「無い」とも書かれていないと記した。学生 2 人は空の一覧を「確かめられていない前提が無い」のか「誰も載せていない」のか読めなかった。応答の本文と描画関数を突き合わせた。 run: uxsim/runs/c-r25-doubt-r1/20261022T123418Z-31805（seq10〜13）・uxsim/runs/c-r25-doubt-r1/20261022T124129Z-35838（seq20）・uxsim/runs/c-r25-doubt-r1/20261022T125348Z-41947（seq9）・uxsim/runs/c-r25-doubt-r1/20261022T125655Z-43791（seq9・12）。"
resolution:
  perspective: [first_class_state]
  note: >-
    空・未実行・受付・失敗の理由をサーバの事実文（core/doubt/scope.py の SCOPE_NO_DOCUMENTS_NOTE / ATLAS_EMPTY_NOTE / OPEN_ASSUMPTIONS_EMPTY_NOTE / LEARNER_OPEN_ASSUMPTIONS_EMPTY_NOTE / SECTION_EMPTY_NOTE / BATCH_SCHEDULED_NOTE / CORPUS_AUDIT_NOTES）で返し、画面はそのまま描く。doubt-atlas.js のバッチボタンは応答を読み状態行に出す（runBatch）、空の地図は理由と原稿スタジオの入口を添える。ゼミ前ブリーフの空の区画は見出しを残して empty_notes を淡色で出し、discuss 開幕の「この論文が確かめていないこと」も paper_unverified_empty_note で区画を残す。学生の一覧は取得失敗と空を分ける。コーパス監査は reason（no_documents / no_analysis / corpus_too_small）で実行しなかった理由を言い分ける。IK-0852 と同時に入れたので「材料なし」が先に判定される。
  landed_in:
    - "backend/core/doubt/scope.py"
    - "backend/api/routes/doubt.py"
    - "frontend/public/js/doubt-atlas.js"
    - "frontend/public/js/admin.js"
    - "frontend/public/js/discuss.js"
    - "frontend/public/js/app.js"
    - "backend/tests/test_doubt_course_scope_round25.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "空の理由の文を各画面に書き写さず、サーバの定数 1 箇所から返す。"
  verification:
    methods: [guardrail]
    unverified:
      - "画面での表示（ボタン後の状態行・空の区画）はブラウザで未確認"
related: [IK-0852, IK-0851, IK-0206]
view_of: []
history: []
---

## 課題

症状: 前提の地図でコーパス監査・前提マイニングを押しても画面が変わらず、実行・未実行・受付のどれかが分からない。ゼミ前
ブリーフは空の区画が見出しごと消える。学生の未検証の前提一覧は空でも失敗でも同じ一文。原因: API が返す理由（skipped /
documents / scheduled）を画面が読まず、空の種類を表す値・文言が無い。

## 発見の観点

ペルソナの通し受講（reproduction）と応答の読み取り（data_inspection）。

## 解決の観点

解決済み（2026-10-23）。空になった理由を値として運び、画面は空の種類ごとに事実文を出す（IK-0851 と同じ解き方）。

## 一般化

fail-soft の縮退と該当なしが同じ「何も出さない」で表されると区別できない。確かめ方は、論文 1 本のコース・材料の届かない
コース・非同期の起動直後・取得失敗のそれぞれで、別々の事実文が出ることを見ること。

第 2 周の再演: 残存（一部）。コーパス監査・負荷再計算の結果と前提マイニングの受付はサーバの note として返り、doubt-atlas.js が状態行に出す（ハーネスの投影が状態行を写していなかったため頭脳は STILL と読んだ — 偽所見）。前提マイニングは受付の後、再読込しても「候補はありませんでした」の結果の事実文が無く、候補ゼロが「動いていない」に読める（IK-0904 に含めた）。run: uxsim/runs/c-r25-doubt-r2/20261022T215750Z-73306（seq5・6）。

第 2 波で是正（2026-10-23）: 前提の一覧 GET が mining_note（範囲に前提マイニング由来の候補があるか）を毎回返す（IK-0904 と同じ波。画面描画は残り）。ハーネスの状態行の投影は H11。
