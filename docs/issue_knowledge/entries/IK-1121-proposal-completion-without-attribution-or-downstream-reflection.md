---
id: IK-1121
title: "検証提案の［完了を記録］は状態を「完了」にするだけで、カードに完了した人・日時も何を確かめたかの欄も出さず（提案者の行だけ）、合意リストの行は「検証提案あり」のまま・台帳の検証状態も変わらず、変わらないことの事実文も無い — 取り下げには帰属と日時が出る（IK-1060）のと非対称"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/features/stakes_ledger_design.md"
  - "docs/architecture/persona_enactment_testing_design.md"
feature_context:
  realizing: "教員が検証提案の完了を記録し、後から誰がいつ完了とし、その結果が台帳・合意リスト・学習者の画面にどう現れたかを再構成する"
  layers: [doubt_d, stakes_ledger_sl, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [information]
    governance: [review]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（コード + 生応答）: PATCH /api/admin/doubt/proposals/{id}（routes/doubt.py）は status と updated_at を更新し、監査（theory_review_events・
    AUDIT_ENTITY_VERIFICATION_PROPOSAL・actor 付き）を記帳する。台帳 GET の proposals[] は proposer_name までで、更新日時・完了した人・完了の根拠の欄を持たない
    （te-03 seq10 の生応答: 「検証提案1: 完了」+ 提案 / コーパス外の確認 / 到達可能性 / 提案者）。合意リストの has_verification_proposal は
    status <> 'withdrawn' の件数で、完了も「検証提案あり」に数える（core/doubt/open_assumptions.py）。完了は verification_status を変えない（人の記帳は別操作）が、
    台帳にも合意リストにもその関係の事実文が無い。取り下げ済みの疑義は record_line で「記帳: 名前（日時）／ 取り下げ: 名前（日時）」を出す（IK-1060 解決済み）。
    構造軸 representation = 完了の帰属・時刻・根拠を DTO が持たない。接続軸 information = 監査にある記録が表示に届かず、下流の行（合意リスト）に完了が伝わらない。
    統制軸 review = 肯定の判断（完了）の帰属が再構成できない（vision §6 改訂原則1）。
generalization:
  level: general
  general_form: "判断を状態の 1 語で残し、誰が・いつ・何を根拠にを表示の欄に持たせないと、記録はあっても後から判断を再構成できず、下流の一覧は判断前と同じ見え方のまま残る"
pattern: last-mile-missing
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    第 9 周まで場面外だった［完了を記録］に第 10 周で初めて到達（harness H54 の是正後）。te-03 が完了の直後（seq10）と開き直し（seq16）、合意リストの再読込（seq14）、
    原稿スタジオのノード台帳（seq15）で見え方を読み、取り下げ済みの折りたたみ（seq13・17）と並べた。生応答の proposals[] と route の組み立てを突き合わせた。
    run: uxsim/runs/c-r25-doubt-r10/20261031T053250Z-67645（seq10・13〜17）。学習者側: st-05 の前提一覧は「検証提案あり」・discuss 開幕の「確かめていないこと」には
    提案の印自体が無い（uxsim/runs/c-r25-doubt-r10/20261031T053655Z-69809 seq11・15）。
resolution:
  perspective: [representation_change, carry_through]
  note: >-
    第 25 周 第 10 波: migration 095 で verification_proposals に completed_by / completed_at / completion_note を足し（冪等・
    行削除なし・既存の完了行は監査の actor と時刻から 1 回だけ埋める）、PATCH の completed 遷移で記帳する（任意の completion_note・
    制御文字除去・600 字）。台帳の proposals[] は完了のとき status_line「完了: 名前（日時）」・completion_note_line（空は
    「確かめたことの記帳なし」）・status_note（完了しても検証状態は変わらない — SL2）を返し、doubt-atlas.js が素通しで描く。
    下流は 1 つの語の表 label_vocab.VERIFICATION_PROPOSAL_PRESENCE_LABELS（検証提案あり / （完了）/ （完了と未完了））で、
    合意リスト・学習者の前提一覧・discuss 開幕の「確かめていないこと」が同じ語を使う（verification_proposal_state / _label）。
    検証状態は変えない（人が別に記帳する）。件数は出さない。
  landed_in:
    - "backend/db/095_verification_proposal_completion.sql"
    - "backend/api/routes/doubt.py"
    - "backend/core/label_vocab.py"
    - "backend/core/doubt/open_assumptions.py"
    - "backend/core/discuss/opening.py"
    - "frontend/public/js/doubt-atlas.js"
    - "frontend/public/js/app.js"
    - "backend/tests/test_doubt_round25_wave10.py"
  verification:
    methods: [guardrail]
    unverified:
      - "migration 095 を実 DB（砂場・scratch）に適用していない（砂場への書き込み禁止の波）"
      - "完了の 1 行と下流 3 面の語を画面で確かめていない（第 11 周 te-03 / st-05）"
related: [IK-1060, IK-1002, IK-1007]
view_of: []
history: []
---

## 課題

症状: 提案の［完了を記録］の後、カードは「検証提案1: 完了」と提案者の行だけ。誰がいつ完了としたか、何を確かめたかが読めない。合意リストの行は「検証提案あり」のままで、
検証状態も「検証情報なし」のまま（変わらない理由の文が無い）。取り下げの折りたたみには名前と日時が出るので、肯定の判断だけが帰属を持たない。

## 発見の観点

9 周目まで届かなかった操作に初めて届き、直後・開き直し・下流の一覧を同じ対象で読んだ。

## 解決の観点

未着手。監査にある actor と時刻を DTO に運び、取り下げと同じ形の 1 行で描く。下流の一覧は段階の語で完了を言い分ける。

## 一般化

型 last-mile-missing。否定の判断（取り下げ）に帰属を付けた是正は、肯定の判断（完了）にも同じ形で付ける — 片側だけを直すと非対称が残る。

第 10 波（2026-10-31）: 完了の帰属・日時・根拠を行に残し、カードと下流 3 面に段階の語で届けた（上の resolution）。

## 第 11 周の再演（第 25 周・面 B 第 11 周、2026-10-31・judge_doubt_r11 — 固定コース 1bad7d9f・3 周連続の同じ対象）

GONE（3 面とも画面で確認）。第 10 周に完了した疑義1 の提案のカードに「完了: 岡村 千尋（2026-10-31 14:34）」「確かめたことの記帳なし」「提案の完了を記録しても、検証状態は変わりません（…［検証状態を記帳する］で書きます）。」の 3 行（migration 095 の前に完了した提案にも帰属と日時が付いた）。着手済みのカードに「確かめたこと（任意・完了を記録するときに残ります）」の欄。教員の合意リストと学習者の前提一覧の同じ主張の行が「検証提案あり（完了と未完了）」。未確認: discuss 開幕の「確かめていないこと」の fact_line（今周は開幕を開いていない）・今周の手での完了（te-03 は「まだ読み直していない」と完了を記録しなかった — 正しい判断）。run: 99946 seq6・9 / 20261031T101717Z-1116 seq11。
