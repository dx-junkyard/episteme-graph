---
id: IK-1050
title: "同一性の候補のカードで判断を下した後の行が判断の記録として読めない — リンクの［見送り］は理由を取らず（API も reason を受けない）、見送ったリンクは英語の札「[rejected]」と保存値の「別の論文の記述と本文の意味が近い」に戻り、見送った概念は理由・誰・いつを出さずに［確定］［見送り］を出し続ける"
status: resolved
recorded_at: 2026-10-28
resolved_at: 2026-10-28
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/concept_registry_design.md"
feature_context:
  realizing: "教員が同一性の候補を見送り、後から（自分や別の教員が）なぜ・誰が・いつ見送ったかを読み、必要なら判断をやり直す"
  layers: [concept_registry, frontend_admin_ui]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [information]
    governance: [review]
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
    統制軸 review: 見送りは理由必須（KR7・admin.js _libraryPromptDismissReason のコメント）なのに、同一性リンクの reject は routes/deliberation.py reject_identity_link が body を取らず、admin.js（3100 行）も理由を聞かずに POST する — 否定の判断の記録に理由の欄が無い（エントリの見送り・取り消しは理由を取るので非対称）。接続軸 information: 第 6 波の是正（IK-1020 の PARTNER_REPLACED_REASON・confirm_question）は routes/library.py の `status == "candidate"` の行にだけ掛かり、判断した行には保存値の reason（「別の論文の記述と…」= 同じ論文の PDF 版なのに）がそのまま戻る。見送った概念のカードは review_note・decided_by・decided_at を描かず、`entry.review_status !== "confirmed"` の分岐で dismissed にも［確定］［見送り］を出す（admin.js 3053）。処理軸 wording: リンクの札は _libraryStatusChipHtml → _libraryReviewStatusLabels（candidate / confirmed / dismissed）で引くので rejected に訳語が無く英語のまま出る（詳細側の _libraryIdentityLinkStatusLabels には「却下」がある）。構造軸 none: status 遷移と review_note の受け皿はある。
generalization:
  level: repo_pattern
  general_form: "判断前の行だけを整えて判断後の行の描き方を設計しないと、否定の判断の後に理由・帰属・状態の訳語が落ち、判断前の注記も古い保存値に戻る"
pattern: negative-decision-second-class
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    第 25 周 第 7 周 te-05 (a) が片側の置き換わったリンク 2 本を理由付きのつもりで見送り（runner の注記「リンクの［見送り］の画面には理由の欄が無い」）、概念 1 件を理由付きで見送ってから見送り済みを含めて一覧を読み直した（uxsim/runs/c-astro-r25-share-r7/20261027T171357Z-51091 seq3・6・7・8）。応答の reason と描画の分岐（library.py の candidate 限定・admin.js の札の表）を辿った。
resolution:
  perspective: [state_transition, vocabulary_table]
  note: >-
    同一性リンクの［見送り］を理由必須にした（reject API が body reason を取り空は 422・理由は行の revoke_reason 列 = 否定の判断の理由の列として読み替え・監査にも残す・一覧と詳細と W層の prompt）。判断した行は片側の置き換わり（PARTNER_REPLACED_REASON を判断した行にも保存値より先に出す・元の文は recorded_reason）→ decision_line → decision_reason_line の順に出し、札はサーバの IDENTITY_LINK_STATUS_LABELS（status_label。rejected=見送り・revoked=確定を取り消し）で引く。見送った概念のカードは decision_line / decision_reason_line / restore_note を出し、判断のボタンは候補のときだけ。
  landed_in:
    - "backend/core/deliberation/identity_links.py"
    - "backend/api/routes/deliberation.py"
    - "backend/api/routes/library.py"
    - "frontend/public/js/admin.js"
    - "frontend/public/js/deliberation.js"
    - "docs/features/concept_registry_design.md"
    - "backend/tests/test_share_round25_r7_fixes.py"
    - "backend/tests/test_share_round25_r8_fixes.py"
  principles:
    - principle: sync-by-stable-key-and-protect-human-decisions
      use: consulted
      note: "否定の判断を取り消し・ラベルの見送りと同じ記録の形（印・人・日付・理由）にそろえた。固有の差分: migration を増やさず revoke_reason 列を否定の判断の理由として読み替えた。"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場の画面での見送り → 再読込の通し（第 8 周）"
      - "ハーネス（runner/share_exec.py）が reject に reason を送るかは harness 側の追随待ち"
related: [IK-1020, IK-0953, IK-1024, IK-0982]
view_of: []
history:
  - date: '2026-10-31'
    field: resolution
    from: 第 7 波の是正
    to: 第 8 波の追補
    reason: 第 8 周の残り（確定済み概念の確定者）を是正。エントリ詳細に review_decision_line、同一性の候補カードの確定済み概念に decision_line（判断した人の記録があるときだけ）
---

## 課題

症状: te-05 が同一性の候補のリンク「EOS network definition」を見送ると、一覧の上に「結び付きを見送りました（〔見送り〕 見送り: 西川 智也・2026-10-27）。行は残り…」と出るが、行の札は「[rejected]」と英語になり、見送る前に出ていた「組になっていた相手の部品は、解析のやり直しで置き換わりました…」が消えて「別の論文の記述と本文の意味が近いと測定されました。」に戻る（同じ論文の PDF 版なのに「別の論文」）。書いたつもりの理由はどこにも無い — 見送りの手に理由の欄が無く、API も受けない。2 本目のリンクでも同じ形で再現した。概念「Prior-method contrast」を理由付きで見送ると既定の一覧から消え、見送り済みを含めて読み直すと「[見送り]」の札で戻るが、理由・見送った人・日付が無く、［確定］［見送り］が出たまま（見送りの取り消しなのか再度の見送りなのか読めない）。知らせは「理由と記録は残ります」と言う。

原因: 第 6 波の是正（片側の置き換わりの言い方・確認の問い）は候補（candidate）の行だけに掛け、判断した後の行の描き方を設計していない。リンクの見送りは理由の受け皿を持たない。札の訳語表が状態の語彙（rejected）を覆っていない。

## 発見の観点

再現: 見送り → 一覧 → 見送り済みを含めた一覧、の順に同じカードを読んだ。経路の追跡: reject の応答の reason → library.py の candidate 限定の差し替え → admin.js の札の表と概念の操作の分岐。

## 解決の観点

第 7 波で是正（下の節）。当初の案:判断の記録の読み手（理由・帰属・日付・状態の訳語）を、取り消し（IK-0953）・ラベルの見送り（IK-1024）と同じ形に揃える。見送りの理由は必須にする（KR7）。

## 一般化

候補の画面を是正するとき、判断前の行の文面だけを直すと判断後の行が古い保存値・生の状態語に戻る。否定の判断の記録が二級市民になる型（negative-decision-second-class）。確かめ方は、判断前・判断後・判断を含めた再読込の 3 つで同じ行を読み比べること。

## 第 7 波の是正（第 25 周・面 A、2026-10-28）

第 7 波の是正（2026-10-28）: リンクの見送りは理由必須（reject API の body・空は 422）。判断した行は片側の置き換わり → 〔見送り〕 見送り: 名前・日付 → 「見送りの理由: …」の順、札はサーバの status_label（「見送り」）。見送った概念のカードは印・理由・戻し方（詳細の［候補に戻す］）を出し、判断のボタンは候補のときだけ（concept_registry_design.md 第 7 波の追補）。

## 第 8 周の再演（第 25 周・面 A 第 8 周、2026-10-28・judge_share_r8）

GONE（主部）。理由付きの見送り 2 本の行に「〔見送り〕 見送り: 名前・日付」「見送りの理由: …」、理由の無い旧い行は「見送りの理由: 記録なし（理由を取る前に見送られた行です）」、英語の札なし（uxsim/runs/c-astro-r25-share-r8/20261027T223243Z-92712 seq2・3）。見送った概念は「この概念は見送り済みです（この一覧では判断のボタンを出しません）…［候補に戻す］…」で判断のボタンが消えた（seq7）。残り: 確定した概念（Boundary constraints）の詳細の「いまの扱い: 確定」に確定者・日付が無い（seq4）。判断した後の行を戻す手は IK-1062。

## 第 9 周の再演（第 25 周・面 A 第 9 周、2026-10-31・judge_share_r9）

残り（確定済み概念の確定者）は decided_by のある行で GONE（「いまの扱い: 確定 〔確定〕 確定: 岡村 千尋・2026-10-27」）。decided_by が NULL の旧い確定の行（「TOV 方程式（静水圧平衡）」f55ad8ca）は「いまの扱い: 確定」だけで、記録が無いことも言わない（STILL 小・リンクの「記録なし（理由を取る前に見送られた行です）」と非対称）。run: uxsim/runs/c-astro-r25-share-r9/20261031T015651Z-40470（seq3・11）。

## 第 9 波の是正（第 25 周・面 A、2026-10-31）

判断した人の記録が無い概念の確定・見送り（decided_by NULL の旧い行・同梱）は推測で埋めず「確定した人・日付の記録なし（同梱の初期データか、
判断の記録を取る前に確定された行です）」と言う（core/library/schema.entry_decision_unrecorded_line。詳細の review_decision_line と
候補一覧の確定済みカードの decision_line の両方）。テスト backend/tests/test_share_round25_r9_fixes.py::TestRecordsNotSilent。

## 第 10 周の再演（第 25 周・面 A 第 10 周、2026-10-31・judge_share_r10）

残り GONE。判断者の記録が無い確定済みの概念に「いまの扱い: 確定 確定した人・日付の記録なし（同梱の初期データか、判断の記録を取る前に確定された行です）」。run: uxsim/runs/c-astro-r25-share-r10/20261031T052922Z-65582（seq3）。
