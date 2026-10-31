---
id: IK-0998
title: "教員の台帳で疑義を取り下げると、サーバは取り下げ済みの行を返し続けるのに画面（doubt-atlas.js）は取り下げ済みを一覧から外すため、取り下げた疑義が跡形も無く消えたように見え、記録が保持されたのか削除されたのかを教員が確かめられない"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/features/stakes_ledger_design.md §16.8"
  - "docs/architecture/persona_enactment_testing_design.md §22.3"
feature_context:
  realizing: "教員が主張の台帳で残した疑義を取り下げ、あとでその取り下げを読み返す"
  layers: [doubt_d, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [information]
    governance: [none]
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
    接続軸 information: 台帳 GET（routes/doubt.py）の challenges[] は status=withdrawn・status_label「取り下げ済み」の行を含むが、doubt-atlas.js の疑義区画は openChallenges = challenges.filter(status !== 'withdrawn') だけを描き、取り下げ済みの行を出す場所も折りたたみも無い。届いている情報が画面で落ちる。構造軸 representation: 「取り下げ」（P4 で行を残す状態遷移）を画面では不在で表している。処理軸 none・統制軸 none（取り下げ API は status 遷移で保持している — 削除ではない）。
generalization:
  level: repo_pattern
  general_form: "状態遷移で保持している記録を、一覧の画面が終端状態だけ外して描くため、利用者には削除と保持の区別がつかない"
pattern: negative-decision-second-class
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    te-04 が疑義2 を取り下げた直後と、台帳を開き直した後の両方で、生応答の challenges[] には c10f3eee（status withdrawn・取り下げ済み）があり、観察（doubt-atlas.js の逐語ミラー）には疑義1 しか無かった。run: uxsim/runs/c-r25-doubt-r5/20261027T070946Z-33422（seq11・14）。
resolution:
  perspective: [first_class_state, carry_through]
  note: >-
    台帳 GET は従来どおり取り下げ済みの疑義を返し、doubt-atlas.js が疑義区画の末尾に折りたたみ「取り下げ済みの疑義（読み取りのみ）」（data-ui-anchor=doubt-atlas.withdrawn-challenges・操作ボタンなし・件数なし）で型・向き・投稿者・理由・状態・提案カードを描く。取り下げ API は事実文 CHALLENGE_WITHDRAWN_NOTE を返し、画面は台帳を読み直してその文を添える。マニュアル節 #withdrawn-challenges・ADMIN_UI_ANCHORS の 3 点を揃えた。
  landed_in:
    - "frontend/public/js/doubt-atlas.js"
    - "backend/api/routes/doubt.py"
    - "backend/core/label_vocab.py"
    - "backend/core/help_kb/admin_ui_anchors.py"
    - "docs/manual/teacher/18-admin-doubt-atlas.md"
    - "backend/tests/test_doubt_round25_wave5.py"
  verification:
    methods: [guardrail]
    unverified:
      - "画面の描画はソースの静的検査だけで、ブラウザで取り下げ → 開き直しを操作していない（node 不在で JS の構文検査もスキップ）"
      - "ハーネスの open_challenges 投影は未改修（次の周で合わせる）"
related: [IK-0968, IK-0904, IK-0009]
view_of: []
history: []
---

## 課題

症状: 取り下げた疑義が台帳の画面から消える。確認ダイアログも「取り下げました」の事実文も無く、開き直しても出ない。教員は記録が残ったのか消えたのか確かめられない。

原因: サーバは保持しているが、画面は取り下げ済みを描く区画を持たない（未対応の疑義だけを描く）。

## 発見の観点

再現（te-04 の取り下げ → 開き直し）と生応答の照合。

## 解決の観点

第 25 周 面 B 第 5 波（2026-10-27・docs/features/stakes_ledger_design.md §16.8）で解決。台帳 GET は従来どおり取り下げ済みの疑義を返し、doubt-atlas.js が疑義区画の末尾に折りたたみ「取り下げ済みの疑義（読み取りのみ）」（data-ui-anchor=doubt-atlas.withdrawn-challenges・操作ボタンなし・件数なし）で型・向き・投稿者・理由・状態・提案カードを描く。取り下げ API は事実文 CHALLENGE_WITHDRAWN_NOTE を返し、画面は台帳を読み直してその文を添える。マニュアル節 #withdrawn-challenges・ADMIN_UI_ANCHORS の 3 点を揃えた。

起票時の是正案: 是正案: 疑義区画の末尾に「取り下げ済みの疑義（N件）」の折りたたみを置き、型・理由・取り下げた人・日時を読み取り専用で出す（再開の操作は作らない）。取り下げの直後は「取り下げました。記録は台帳に残ります。」の事実文を 1 行出す。ハーネスの `open_challenges` 投影も同じ折りたたみに合わせる。件数は出さない設計でもよい（その場合は「取り下げ済みの疑義があります」）。

## 一般化

却下・取り下げ・見送りのような否定の判断を状態で残しても、画面が終端状態を描かなければ利用者には削除と同じに見える。確かめ方は、終端状態に遷移させた直後と開き直した後の両方で、その行が読めることを見ること。

第 6 周の再演: 一部 GONE（2026-10-27）。te-01 の Cepheus の主張の台帳の疑義区画の末尾に「▸ 取り下げ済みの疑義（読み取りのみ）」の折りたたみが出た（前の周に取り下げた疑義がある主張）。ただし今周の取り下げそのものは runner が pick「疑義3」を解決できず送られず（harness H39）、取り下げ直後の note と折りたたみの中身は未観察。run: uxsim/runs/c-r25-doubt-r6/20261027T122508Z-5219（seq6・10・11・14）。

第 7 周の再演: 取り下げ直後の note は GONE・折りたたみの中身は未観察（2026-10-28）。te-03 が自分の疑義 2 件の 1 件を提案化（POST …/proposals 200・状態「検証提案へ昇格済み」+ 提案カード）→ 着手（PATCH 200）→ もう 1 件を取り下げ（POST …/withdraw 200・note「取り下げました。記録は台帳に残り、「取り下げ済みの疑義」で読めます。」が画面に出た）→ 開き直しても提案カードと「▸ 取り下げ済みの疑義（読み取りのみ）」が残る。提案化・取り下げは 7 周目で初めて run で通った（harness H39 の是正後）。折りたたみの中身（型・理由・取り下げた人）は場面に開く手（admin.doubt.withdrawn_challenges_open）が入っていないので読まれていない（harness H44）。run: uxsim/runs/c-r25-doubt-r7/20261027T171640Z-52350（seq7〜11・14）。

第 8 周の再演: 折りたたみの中身 GONE（2026-10-28・harness H44 の是正後に初めて開いた）。型・向き・記帳者・理由・状態「取り下げ済み」が読め、開き直しても残る。残り: 記帳・取り下げの日時と取り下げた人が無く、同文の取り下げ 2 件を区別できない — 起票時の是正案の「取り下げた人・日時」の部分を IK-1060 として起票した。run: uxsim/runs/c-r25-doubt-r8/20261027T223539Z-94356（seq11・12・16）。
