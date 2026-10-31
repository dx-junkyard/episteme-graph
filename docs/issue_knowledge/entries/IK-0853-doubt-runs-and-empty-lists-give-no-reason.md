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

第 4 周の再演: 残存（2026-10-25・非同期の部分）。監査の状態行は理由まで読めた。前提マイニングは「受け付けました…再読込すると反映されます」の後、再読込しても
mining_note が「いまこのコースの教材の範囲にはありません」のままで、予約したマイニングが終わって 0 件なのか、まだ走っているのかが画面から分からない（te-05 は
確定の手前で止まった）。是正案: マイニングの最終実行の事実（終わった時刻の有無・対象の論文）を範囲ごとに 1 つ残し、mining_note を「実行済みで候補なし／実行中／未実行」に分ける。
run: uxsim/runs/c-r25-doubt-r4/20261024T233201Z-20444（seq5〜7）。

第 4 波で是正（2026-10-25・非同期の部分）: 前提の一覧の mining_note を「候補あり / 実行中 / 実行し終えて候補なし / 最後まで実行できなかった / いま範囲に候補なし」に分けた（core/doubt/assumption_mining/worker.py の mining_state はプロセス内の最終実行の状態・永続化しない。再起動後は範囲の事実だけ）。doubt-atlas.js loadAssumptions が一覧の頭に 1 行で出す（それまで mining_note は画面に描かれていなかった）。画面は未確認（第 5 周 te-05）。

第 5 周の再演: 監査とマイニングの理由は GONE（2026-10-27）。コーパス監査は「論文が少なく実行しませんでした」、マイニングは区画に「実行し終えました。新しい前提の候補はありませんでした。」。残り（軽）: ①ボタンの下の状態行は「受け付けました。…再読込すると反映されます」のままで区画の完了文と食い違う ②題名が「4本の論文」のコースで監査の応答は documents 2（解析済みで範囲に入る論文の数）— どの論文が数に入ったかが読めない ③確定済みの表示に切り替えても区画の文がマイニングの候補の文のまま。run: uxsim/runs/c-r25-doubt-r5/20261027T071425Z-60143（seq4・5・7）。

第 6 周の再演: 監査は GONE のまま（「コーパス監査を実行しました。新しい前提の候補はありませんでした。」）。残り③は STILL — 確定済みの表示に切り替えても一覧の頭が「前提マイニングで見つかった前提の候補は、いまこのコースの教材の範囲にはありません。」のまま（確定済みの 1 件の上に候補の文）。run: uxsim/runs/c-r25-doubt-r6/20261027T122831Z-7319（seq4・6）。

第 6 波の是正（2026-10-27）: 残り③ — 確定済み・却下の表示では mining_note を null にし、候補の文を出さない（候補・すべての表示だけ）。backend/api/routes/doubt.py list_assumptions。テスト backend/tests/test_doubt_round25_wave6.py。

第 7 周の再演: 残り③ GONE（2026-10-28）。「確定済み」の表示では mining_note が null になり候補の文が出ない（te-02 seq6・7）。監査の結果の事実文「コーパス監査を実行しました。新しい前提の候補はありませんでした。」も維持。run: uxsim/runs/c-r25-doubt-r7/20261027T172037Z-54876（seq4・6）。

第 8 周の再演: 残り① STILL（2026-10-28・軽）。［前提マイニング実行］のボタン下の状態行「受け付けました。結果は少し待ってから再読込すると反映されます。」と、同じ応答の一覧の頭「前提マイニングを実行し終えました。…候補はありませんでした。」が同時に出て、終わったのか待つのかが食い違って読める（一覧の文はプロセス内の最終実行の状態で、今回の受付かどうかを言わない）。run: uxsim/runs/c-r25-doubt-r8/20261027T223914Z-96688（seq5）。

第 9 周（2026-10-31）: 残り① STILL（軽）。前提マイニングの状態行「受け付けました。結果は少し待ってから再読込すると反映されます。」と一覧「前提マイニングを実行し終えました…候補はありませんでした。」が同じ画面に並ぶ。run: uxsim/runs/c-r25-doubt-r9/20261031T003210Z-30688（seq5）。

第 10 周（2026-10-31・judge_doubt_r10）: 残り①は STILL（第 9 周と同型・同じコース）。［前提マイニング実行］の直後、状態行「受け付けました。結果は少し待ってから再読込すると反映されます。」と候補欄「前提マイニングを実行し終えました。…新しい前提の候補はありませんでした。」が同じ画面に並び、te-02 は「待てと言いながら下では終わったと言っている」と書いた（審判 C の所見 1 件）。再読込で状態行が消え解ける。run: uxsim/runs/c-r25-doubt-r10/20261031T052922Z-65583（seq5・6）。

## 第 11 周の再演（第 25 周・面 B 第 11 周、2026-10-31・judge_doubt_r11 — 固定コース 1bad7d9f・3 周連続の同じ対象）

第 11 周（judge_doubt_r11）: STILL（軽）。前提マイニング実行の直後、状態行「受け付けました。結果は少し待ってから再読込…」と候補区画「前提マイニングを実行し終えました…新しい候補はありませんでした」が同時に出て、どちらが今回の結果か読めない（te-02 confused）。run: 98534 seq5。
