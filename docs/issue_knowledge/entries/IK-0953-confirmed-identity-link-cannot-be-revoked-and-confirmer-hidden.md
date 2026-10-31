---
id: IK-0953
title: "確定した同一性リンクを取り消す経路が無く（遷移は候補からだけ）、作成者がその場で自分の候補を確定でき、確定の行に誰が確定したかも出ない"
status: resolved
recorded_at: 2026-10-25
resolved_at: 2026-10-25
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "教員が論文の部品と共通部品の同一性リンクを確定し、誤りに気づいたら直す"
  layers: [deliberation_w, concept_registry, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [none]
    governance: [review, assignment]
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
    統制軸 review: backend/core/deliberation/identity_links.py:266-310 の decide は UPDATE ... WHERE status =
    'candidate' だけで、confirmed から先へ動く遷移が無い。routes/deliberation.py:688-703 も confirm / reject の 2
    本だけで、deliberation.js:1996-2003 は候補の行にしか［確定］［却下］を描かない。確定した結び付きを撤回・訂正する手続が存在しない。統制軸 assignment:
    作成者（created_by）と確定者（decided_by）が同じでも止まらず（te-05 は 1 手で作って確定した）、確定の行は
    provenance_label「教員が確定」だけで確定者の名前を出さない（_identity_link_response に decided_by の表示名が無い）。処理・接続・構造は正しく動いている（none）—
    誤りの確定が学生に届き続けるのは手続の欠落。
generalization:
  level: repo_pattern
  general_form: "人の確定を記録する手続に、確定を撤回する遷移と確定者の表示が無く、誤った確定が下流に届き続ける"
pattern: negative-decision-second-class
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    委任型の教員 te-05 が粒度の違う対（構造ネットワーク ⇄ TOV
    方程式）を、注意文を流し読みして作成直後に確定し（uxsim/runs/c-astro-r25-share-r4/20261024T232812Z-98848 seq6-7）、同じ周の学生 st-08・st-01
    が「共通部品として」で意味のずれに戸惑った（uxsim/runs/c-astro-r25-share-r4/20261024T233212Z-21482
    seq11・uxsim/runs/c-astro-r25-share-r4/20261024T233359Z-43631 seq7-8）。確定を戻す操作を画面と route・core で探して無いことを確かめた。
resolution:
  perspective: [state_transition]
  note: >-
    確定から先へ動く遷移を足した。migration 093 が element_identity_links の status に revoked を足し、revoked_by / revoked_at / revoke_reason の列を持たせる（確定者 decided_by は上書きしない・DELETE なし）。core identity_links.revoke（confirmed からのみ・理由必須・帰属必須）と route POST /identity-links/{id}/revoke（理由空は 422・確定でない行は 409・権限は確定と同じ論文の編集・監査 identity_link.revoked に確定者も残す）。行の応答に describe_actors（作成者・確定者・取り消した人の表示名、作成者 = 確定者なら「作成者が確定しました。」）と revoke_note。deliberation.js の確定の行とライブラリの「結びついた教材の部品」の確定の行に［確定を取り消す］（理由を prompt・アンカー deliberation.identity-link-revoke）。学生側は confirmed だけを読む既存の述語のままで消える。KB library.md#revoke-identity-link・マニュアル 24-admin-deliberation#identity-link-revoke。作成者の自己確定は禁じない（推奨どおり事実で並べる）。
  landed_in:
    - "backend/db/093_identity_link_revoke.sql"
    - "backend/core/deliberation/identity_links.py"
    - "backend/core/deliberation/schema.py"
    - "backend/api/routes/deliberation.py"
    - "frontend/public/js/deliberation.js"
    - "frontend/public/js/admin.js"
    - "docs/admin_operations/library.md"
    - "docs/manual/teacher/24-admin-deliberation.md"
    - "backend/tests/test_share_round25_r4_fixes.py"
    - "backend/api/routes/library.py"
    - "backend/tests/test_share_round25_r6_fixes.py"
  principles:
    - principle: sync-by-stable-key-and-protect-human-decisions
      use: consulted
      note: "人の確定を上書きせず別の列で取り消しを記録し、確定者と取り消した人を両方残す形を採った。固有の差分: 遷移は人間の操作だけで、AI・照合の候補からは動かない。"
  verification:
    methods: [guardrail]
    unverified:
      - "実 DB での migration 093 の適用と CHECK の張り替え"
      - "実画面での取り消しと学生の共通部品の消滅の通し"
related: [IK-0917, IK-0920, IK-0926]
view_of: []
history: []
---

## 課題

症状: 確定した同一性リンクの行にはボタンが無く、教員はそれを取り消せない。Copilot に「取り消すときはどこから」と問うても答えが無い（KB にも手順が無い）。確定の行は「教員が確定」とだけ言い、作成者本人がその場で確定したかどうかは読み手に分からない。原因: 同一性リンクの状態遷移が candidate → confirmed / rejected の一方向で、confirmed からの遷移（撤回）を持たない。

## 発見の観点

再現（te-05 の (a')）と経路の追跡（確定 → 学生の部品の文脈）。

## 解決の観点

是正済み（第 25 周・面 A 第 4 波・2026-10-25）。

旧案: 是正案: confirmed → revoked（行は残す・理由必須・監査）を足し、確定の行に［取り消す］と確定者の表示名を出す。学生の「共通部品として」は revoked を読まない（既存の _confirmed_identity_link が confirmed だけを読むので追加の分岐は要らない）。作成者の自己確定を許すかはオーナー判断の候補（judge_share_r4.md §5）。

## 一般化

否定・撤回の判断が肯定の判断より弱い型。確かめ方は、確定 → 取り消し → 学生の部品の文脈を開き直し、共通部品が消え、ライブラリの詳細に取り消しの事実と理由が残ることを砂場で見ること。

## 第 5 周の再演（第 25 周・面 A 第 5 周、2026-10-27）

第 5 周の再演: 未評価（確定したリンクが無く［確定を取り消す］が出る行が無い — H35 でリンクの確認が未実行）。run: uxsim/runs/c-astro-r25-share-r5/20261027T071135Z-43804

## 第 6 周の再演（第 25 周・面 A 第 6 周、2026-10-27）

第 6 周の再演: 取り消しの操作は未評価（場面の手の組み方で、エントリを開く手と［確定を取り消す］が同じ choose の代替になっていて、確認 → 開く → 取り消すが 1 run で通らない — harness_fixes_share.md H39）。入口と確定者は見えた: 詳細の確定の行「〔確定〕 確定: 岡村 千尋 ［確定を取り消す］」。残り: 確定の日時が無い。同一性の候補の一覧では確定したリンクの行が［候補］とボタンが消えるだけで、確定の印・確定者・確定の知らせが無い（admin.js の状態チップは confirmed を描かない）。run: uxsim/runs/c-astro-r25-share-r6/20261027T122225Z-3503 seq3-5

## 第 6 波の是正（第 25 周・面 A 第 6 波、2026-10-27）

一覧の印・確定者・日時を足した。同一性の候補の一覧（routes/library.py）とエントリ詳細・W層の一覧（routes/deliberation.py）は、決めたリンクの行に identity_links.decision_line の「〔確定〕 確定: 名前・日付」（見送り・取り消しも同型）を出し、admin.js は確定の行にこの印を描く。リンクの確定・見送りの応答は outcome_fact（判断した人・日付・学生に出る条件 = 凍結した版があるとき）を返し、admin.js が知らせに出す。取り消しの操作そのものの評価は第 7 周（H39 の段構成の是正後）に持ち越す。

## 第 7 周の再演（第 25 周・面 A 第 7 周、2026-10-28）

第 7 周の再演: 取り消しは GONE（初評価・到達）。te-05 がエントリ詳細の確定の行で［確定を取り消す］を押すと「取り消すと、この結び付きは確定ではなくなり、学生の画面の「共通部品」から消えます。記録（誰が確定し、誰がいつ・なぜ取り消したか）は残ります。理由は必須です。」→ 行が残り「〔確定を取り消し〕 取り消し: 西川 智也」+「取り消しの理由: …」（uxsim/runs/c-astro-r25-share-r7/20261027T171357Z-51091 seq5）。別の教員 te-02 の画面にも残った（20261027T172320Z-56507 seq3）。詳細の確定の行の日時も GONE（「確定: 岡村 千尋・2026-10-27」）。残り: 取り消しの行に日時が無く（admin.js 3434 は revoked_by_label だけ）、取り消した後は元の確定者・確定日が行から消える — 確認の文の「誰が確定し、誰がいつ」と食い違う。

## 第 7 波の是正（第 25 周・面 A、2026-10-28）

第 7 波の是正（2026-10-28・残り）: 取り消しの行に日時と元の確定を出す（revoke_line「〔取り消し済み〕 取り消し: 名前・日付（元の確定: 名前・日付）」— 一覧・詳細・W層）。

## 第 8 周の再演（第 25 周・面 A 第 8 周、2026-10-28・judge_share_r8）

GONE（残りの日時と元の確定）。「〔取り消し済み〕 取り消し: 西川 智也・2026-10-27（元の確定: 岡村 千尋・2026-10-27）」+ 理由（uxsim/runs/c-astro-r25-share-r8/20261027T223603Z-94618 seq10）。取り消しは今周の場面では対象なし（前提①以外の確定リンク 0 本）。取り消した組を戻す手が無いことは IK-1062。
