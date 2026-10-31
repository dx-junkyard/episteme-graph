---
id: IK-1060
title: "台帳の「取り下げ済みの疑義」の折りたたみが型・向き・記帳者・理由・状態だけを出し、記帳した日時・取り下げた日時・取り下げた人を出さないため、同じ文面の取り下げ済みの疑義が 2 件並ぶと、いつどちらが取り下げられたかを後から再構成できない（台帳 GET の challenges[] に日時の欄が無い）"
status: resolved
recorded_at: 2026-10-28
resolved_at: 2026-10-31
sources:
  - "docs/features/stakes_ledger_design.md"
feature_context:
  realizing: "疑義を取り下げても、その判断が誰によっていつ行われたかを後から読める記録として残す"
  layers: [doubt_d]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [none]
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
    確認（コード）: routes/doubt.py の台帳 GET の challenges[] は id / challenge_type / reason / status / status_label / challenger_name /
    challenge_mode / target_element_ref / proposals だけで、created_at・updated_at（取り下げで now() に更新される）を返さない。取り下げ API は
    本人のみ（challenger = 取り下げた人）だが、画面はそれを言わない。監査（theory_review_events）には actor と時刻が残るので、記録はあるが表示に届かない。
    提案カードには created_at があり、肯定側（提案）と否定側（取り下げ）で帰属・時刻の表現が非対称。
    構造軸 representation = 取り下げの日時・主体を DTO に持たない。統制軸 review = 否定の判断の帰属が再構成できない（vision §6 改訂原則1）。
generalization:
  level: general
  general_form: "否定の判断を状態で残しても、いつ・誰がを表示の欄に持たせないと、同じ文面の記録が重なったときに判断の履歴を再構成できない"
pattern: negative-decision-second-class
discovery:
  perspective: [data_inspection, invariant_audit]
  note: "te-03 が 8 周目で初めて取り下げ済みの折りたたみを開き（harness H44 の是正後）、前周と今周の同文の取り下げ 2 件が区別できないと記録した。生応答の challenges[] の欄と route の組み立てを突き合わせた。run: uxsim/runs/c-r25-doubt-r8/20261027T223539Z-94356（seq11・12・16）。"
resolution:
  perspective: [representation_change]
  note: >-
    台帳 GET の challenges[] に created_at・（取り下げ済みなら）withdrawn_at と record_line を足した。record_line の正本は
    label_vocab.challenge_record_line（「記帳: 名前（YYYY-MM-DD HH:MM）／ 取り下げ: 名前（…・記帳者本人）」・日本時間の分まで
    — 同じ日の同文 2 件を見分ける）。取り下げは本人のみなので取り下げた人 = 記帳者（表示名は users.profile_name 経由の
    label_or_unregistered）。doubt-atlas.js の取り下げ済みの折りたたみは各行に record_line を素通しで出す。件数は出さない。
  landed_in:
    - "backend/api/routes/doubt.py"
    - "backend/core/label_vocab.py"
    - "frontend/public/js/doubt-atlas.js"
    - "backend/tests/test_doubt_round25_wave8.py"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場の同文の取り下げ済みの折りたたみを画面で読み直していない（第 9 周 te-03）"
      - "提案カードの created_at は画面に出していない（肯定側の時刻表示は本件の範囲外）"
related: [IK-0998, IK-1007]
view_of: []
history: []
---

## 課題

症状: 主張「$\ddot I$ depends on …」の台帳の「▾ 取り下げ済みの疑義（読み取りのみ）」に、「定義の曖昧さという疑義（向き: 主張と根拠のつながりへ）岡村 千尋 — 「乱流」の定義が…」
が 2 件、状態「取り下げ済み」で並ぶ（第 7 周と第 8 周の取り下げ。文面は場面の材料 second_reason が同じ — harness H51）。どちらがいつ取り下げられたか、
取り下げたのが記帳者本人かは画面から読めない。

## 発見の観点

終端状態の行を開いて、肯定側（提案カード）と同じ手順で「誰が・いつ」を再構成できるかを見た。

## 解決の観点

未解決（上の resolution.note）。

## 一般化

型 negative-decision-second-class。否定の判断の行にも、肯定の判断と同じ帰属と時刻の欄を持たせる。

第 8 波の是正（2026-10-31）: challenges[] に日時と record_line、折りたたみに 1 行（上の resolution）。

第 9 周の再演（2026-10-31）: GONE。取り下げ済みの折りたたみの各行に「記帳: 岡村 千尋（2026-10-28 02:18） ／ 取り下げ: 岡村 千尋（2026-10-28 02:19・記帳者本人）」が付き、同文 3 件（第 7・8・9 周）が日時で区別できる。開き直しても保たれる。主張は第 8 周と同じ 42c4e2e8（te-03 の入口は教材なので比較は有効）。取り下げ直後の知らせ「取り下げました。記録は台帳に残り、「取り下げ済みの疑義」で読めます。」も出る（頭脳の一度目の NEW は読み落としで撤回）。run: uxsim/runs/c-r25-doubt-r9/20261031T012052Z-34781（seq11・12・16）。

第 10 周（2026-10-31・judge_doubt_r10）: GONE の維持。取り下げ済み 4 件（第 7〜10 周の同文）が「記帳: 岡村 千尋（日時） ／ 取り下げ: 岡村 千尋（日時・記帳者本人）」で区別でき、開き直しても保たれる。肯定側（提案の完了）に同じ形が無いことは新規 IK-1121。run: uxsim/runs/c-r25-doubt-r10/20261031T053250Z-67645（seq13・17）。
