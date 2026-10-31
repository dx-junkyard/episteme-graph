---
id: IK-1007
title: "共有された教員の原稿スタジオに出す「このコースの所有者: ◯◯」と［案をコピー］の宛先が、users.display_name をそのまま出すが、この列はログインの照合に使うユーザー名そのもの（auth_login が display_name = username で引く）なので、人の名前ではなく所有者のログイン ID を他の教員へ開示している"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
  - "docs/features/disclosure_axes_design.md"
  - "docs/features/account_lifecycle_management_design.md §16"
feature_context:
  realizing: "編集共有された教員が、書き換えの案を誰に渡せばよいかを原稿スタジオで知る"
  layers: [lecture_studio, auth_visibility, disclosure_axes]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [meaning]
    governance: [review]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸 representation: users に「人に見せる名前」と「ログインの識別子」を分ける列が無く、display_name が両方を兼ねる
    （backend/api/routes/auth.py の auth_login は `WHERE display_name = :username`）。接続軸 meaning: IK-0978 の是正
    （routes/lecture_studio/topics.py が `COALESCE(u.display_name, '')` を owner_display_name に入れる）は「表示名」の意味で
    受け取ったが、実体はログイン ID。統制軸 review: オーナー判断（judge_studio_r4 §6-1）は「所有者の名前を見せてよいか」で、
    ログイン ID の開示は判断の対象に入っていなかった。処理軸 none: 値の取り出しは正しい。
generalization:
  level: general
  general_form: "人に見せる名前と認証の識別子が同じ列にあり、名前を開示するつもりの経路が識別子を開示する"
pattern: id-namespace-conflated
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    共有教員 te-03 の原稿スタジオで「このコースの所有者: uxsim_te_01_rookie_seminar」、［案をコピー］で「所有者（uxsim_te_01_rookie_seminar）に
    渡してください」と出た。頭脳は「人の名前ではなくログイン用の名前」と記した。course-structure の owner_display_name の出所を辿ると
    users.display_name で、ログインの照合列と同じだった。run: uxsim/runs/c-astro-r25-studio-r5/20261027T071438Z-61813 seq3・8。
resolution:
  perspective: [representation_change, canonical_source]
  note: >-
    第 25 周 第 5 波（オーナー判断: ログイン ID は見せない・人に見せる名前の列を足し、空なら名前なしの事実文）。
    migration 094 で users.profile_name を足し（既存行はバックフィルしない）、帰属・所有者・査読者・記帳者の表示を
    core/account_display.py（display_label は profile_name だけを読み、空・削除済みは None）に寄せた。
    原稿スタジオの owner_display_name と［案をコピー］・教材一覧の edit_request_note は profile_name を読み、空なら
    既存の名前なしの文に戻る。ほかに分野の地図・D層・C層・共有部品・SB層・つまずきデータの 10 面を同じ正本に
    置き換え、display_name を SQL で読む経路を allowlist（ログイン照合・アカウント管理・別表の同名列・グループ招待・
    運用統計・墓標化）に限る AST 走査のガードレールを置いた。本人の登録口は PATCH /api/me/profile と両画面の
    「👤 名前」。可視性 6 軸の account の第 2 軸を現物に合わせて書き換えた。
  landed_in:
    - "backend/db/094_users_profile_name.sql"
    - "backend/core/account_display.py"
    - "backend/api/routes/my_profile.py"
    - "backend/api/routes/lecture_studio/topics.py"
    - "backend/api/routes/admin.py"
    - "frontend/public/js/profile-name.js"
    - "backend/tests/test_account_display_guardrails.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "10 面の帰属の名前を面ごとに直さず、core/account_display.py 1 箇所と全 SQL 文字列の AST 走査で固定した。"
  verification:
    methods: [guardrail]
    unverified:
      - "ブラウザでの「👤 名前」の登録と、共有先の画面に名前が出ることは未確認"
      - "砂場 DB への migration 094 の適用と通し受講での再演は未実施"
      - "グループの参加者一覧・招待はログイン名のまま（招待をログイン名で行う設計・残課題）"
related: [IK-0978, IK-0974, IK-0407]
view_of: []
history: []
---

## 課題

症状: 共有された教員に、所有者の「名前」としてログイン ID（砂場では uxsim_te_01_rookie_seminar）が出る。教員一覧（IK-0974 の
edit_request_note も同じ列）にも同じ値が出得る。

原因: users の display_name がログインの照合列を兼ねており、名前として見せる列が別に無い。IK-0978 の是正はこの列を「表示名」として開示した。

## 発見の観点

教員の通し受講（reproduction）で見た文の値を、API → SQL → 認証の照合まで辿った（trace_walk）。

## 解決の観点

第 5 波で解決（表現の変更 + 正本 1 箇所）。人に見せる名前の列 profile_name を足し、表示は
core/account_display.py だけを通す。以下は起票時の候補。

候補: ①users に人に見せる名前の列を足し（既定は空）、開示はその列だけを読む（空なら「所有者に渡してください」の名前なしの文 —
IK-0978 の既存の分岐）②当面、開示面（原稿スタジオ・教材一覧の依頼文）は名前なしの文に戻す。ログイン ID を他人に見せない点は
可視性 6 軸の教員向け宣言に書く。どちらもオーナー判断（名前の開示）の範囲。

## 一般化

名前を開示する経路を足すときは、その値が認証に使われていないかを確かめる。確かめ方: 開示する列でログインを試し、通るなら識別子の開示である。

第 6 周の再演（2026-10-27）: **GONE**。共有教員 te-04 の原稿スタジオに「このコースの所有者: 水野 早紀」（登録名）、［案をコピー］の知らせも「所有者（水野 早紀）に渡してください」。ログイン ID は出ない。run: uxsim/runs/c-astro-r25-studio-r6/20261027T122747Z-6889 seq3・8。

第 7 周の再演（第 25 周・面 D 第 7 周、2026-10-28）: **GONE 継続**。te-05 の原稿スタジオの owner_notice は「このコースの所有者: 水野 早紀」、［案をコピー］の知らせも登録名。ログイン ID は出ない。run: uxsim/runs/c-astro-r25-studio-r7/20261027T171857Z-53968 seq3・8。

第 8 周の再演（2026-10-28）: **GONE 継続**（「このコースの所有者: 水野 早紀」）。run: uxsim/runs/c-astro-r25-studio-r8/20261027T223730Z-95543 seq3。
