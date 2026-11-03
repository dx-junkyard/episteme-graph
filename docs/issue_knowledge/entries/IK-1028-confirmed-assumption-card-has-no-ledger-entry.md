---
id: IK-1028
title: "前提の地図の「前提候補のレビュー（表示: 確定済み）」のカードには台帳を開く手が無く、出所・状態が英語の内部語のバッジ「manual」「confirmed」のまま描かれる — 確定した前提にスコープを記帳するには散布図・未検証合意リストからその点を探すしかなく、論文に結ばれていない前提は台帳行も点も無いので、確定 → スコープの記帳に辿り着けない"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/features/stakes_ledger_design.md"
  - "docs/features/doubt_layer_issues.md"
feature_context:
  realizing: "教員が確定した暗黙の前提を、その場で台帳に開いて検証スコープを記帳する"
  layers: [doubt_d, frontend_admin_ui]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [information]
    governance: [completion]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（コードと run）: doubt-atlas.js loadAssumptions は確定済みのカードに statement と a.origin / a.status の生の値のバッジだけを描き、
    台帳を開くボタンを持たない（候補のときだけ［確定］［却下］）。台帳を開く入口は散布図の点と未検証合意リストの行だけで、それらは
    get_assumption_atlas / compile_open_assumptions が epistemic_ledger の行（document_id がコースの論文集合）から作る。手動の前提
    （POST /assumptions）は document_ids が空だと台帳行を作らない（「記帳の時に作る」）ので点にも行にもならない。接続軸 information =
    確定した前提の id がカードから台帳へ渡らない。統制軸 completion = 確定で手続が終わったように見え、確定の目的（スコープを付ける）の
    入口が無い。処理軸 wording = 語彙表を通さない英語の内部語。構造軸 none。
generalization:
  level: general
  general_form: "人が確定した項目の一覧が、確定の次にすべき操作への入口を持たず、別の画面で同じ項目を探し直させる — 探し先に載る条件を満たさない項目は行き止まりになる"
pattern: last-mile-missing
discovery:
  perspective: [trace_walk, symptom_report]
  note: "te-04 が本人のコースの確定済みの手動の前提を表示に切り替え、その台帳を開こうとして入口が無く（runner も開ける点・行を見つけられない）、［負荷再計算］後の散布図・合意リストにも前提の点が無いことを確かめた。応答の document_ids は空。run: uxsim/runs/c-r25-doubt-r6/20261027T122831Z-7319（seq6〜10）。"
resolution:
  perspective: [carry_through, vocabulary_table]
  note: >-
    GET /courses/{id}/assumptions の各項目に origin_label / status_label（label_vocab.ASSUMPTION_ORIGIN_LABELS / ASSUMPTION_STATUS_LABELS — migration 030 の CHECK と同じ語彙）を足し、確定済みには ledger_document_id（結ばれた論文のうちコースの範囲を優先した先頭）か、論文に結ばれていなければ ledger_note（ASSUMPTION_NOT_LINKED_TO_DOCUMENT_NOTE）を返す。doubt-atlas.js のカードは生の origin / status を描かず日本語の札を描き、［台帳を開く］（doubt-atlas.assumption-open-ledger）で散布図と同じ renderNodeLedgerSection を開く。アンカー・マニュアル節・ADMIN_UI_ANCHORS の 3 点セット。 第 7 波（2026-10-28・残り）: 同じ文面の確定済みの前提を見分けられるよう、ledger_document_id がコースの範囲の論文のときだけ ledger_document_line（「結ばれた論文: 『…』…」・題名は _document_title_entry の正本）を返し、カードはサーバの文を素通しで描く（範囲外の論文の名前は出さない）。
  landed_in:
    - "backend/core/label_vocab.py"
    - "backend/api/routes/doubt.py"
    - "frontend/public/js/doubt-atlas.js"
    - "frontend/public/admin.html"
    - "backend/core/help_kb/admin_ui_anchors.py"
    - "docs/manual/teacher/18-admin-doubt-atlas.md"
    - "backend/tests/test_doubt_round25_wave6.py"
    - "backend/tests/test_admin_help_ui_anchors.py"
    - "backend/tests/test_doubt_round25_wave7.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "台帳の詳細は散布図・合意リストと同じ renderNodeLedgerSection 1 本を開き、別の詳細を作らない。"
  verification:
    methods: [guardrail]
    unverified:
      - "第 7 波（2026-10-28）で足した結ばれた論文の題名の事実文（ledger_document_line・コースの範囲の論文だけ）を画面で読み直していない"
      - "確定済みのカードから台帳を開いてスコープを記帳するまでを実機（砂場）で操作していない（第 7 周 te-02 × H41）"
      - "論文に結ばれていない前提に台帳行を作る行キーの規則は決めていない（事実文で止めた）"
related: [IK-0853, IK-0970, IK-1000]
view_of: []
history: []
---

## 課題

症状: te-04 が前提の地図で「確定済み」に切り替えると、手動で確定した前提「磁場の乱れが小さく、乱流と磁場のエネルギー等分配が成り立つという前提で…」が 1 件、
末尾に「［manual / confirmed］」のバッジ付きで出た（第 6 周で初めて確定済みの前提が本人のコースに出た — 運転の欠け H35 は解消）。
しかしカードから台帳を開く手が無く、散布図・未検証合意リストにも前提の点・行が無いため、スコープの記帳は 6 周目も未到達。

原因: 台帳への入口が散布図と合意リストにしか無く、両者は document_id を持つ台帳行からしか作られない。この前提は論文に結ばれておらず台帳行が無い（周の前の用意で
te-04 の編集できる教材がコースの sources に無かった — harness 側 H41）。論文に結ばれた前提でも、確定したカードから直接は開けない。

## 発見の観点

操作列の追跡（確定済みの表示 → 台帳 → スコープ）と利用者の詰まり。

## 解決の観点

未解決。カードに入口を置く + 台帳行の遅延作成の規則 + 語彙表。

## 一般化

確定・承認の一覧は「次の一手」への入口を持つべき。型 last-mile-missing。

## 第 6 波の是正（2026-10-27）

カードに［台帳を開く］・日本語の札・論文に結ばれない前提は事実文。設計書 §16.9。

第 7 周の再演: GONE（入口）・残りあり（2026-10-28）。te-02 の 1bad7d9f で「確定済み」に切り替えると、札が「［教員が手で記帳 / 確定済み］」（生の manual / confirmed ではない）になり、Cepheus TeX に結んだ前提のカードに［台帳を開く］、結ばれていない旧い前提に「この前提はどの論文にも結ばれていないため、台帳の行がありません（…）」が出た。残り（軽・新しい課題にはしない）: 同じ文面の確定済みの前提が 2 件並び、［台帳を開く］側にも結ばれた論文の題名がカードに無いので見分けられない（結ばれていない側だけが事実文で分かる）。［台帳を開く］→ スコープの記帳は場面の手（admin.doubt.ledger_open は scratch の確定済み一覧しか見ず、カードの［台帳を開く］の手 assumption_open_ledger が場面に無い）で 7 周目も未到達（harness H44・頭脳の from 指定のせいではない）。run: uxsim/runs/c-r25-doubt-r7/20261027T172037Z-54876（seq6〜9）。

第 8 周の再演: 残り（結ばれた論文名）GONE（2026-10-28）。確定済みカードの［台帳を開く］の直後に「結ばれた論文: 『Magnetic field Topology … Cepheus B …』（［台帳を開く］はこの論文の台帳の行を開きます）」が出て、te-02 は前提 → 台帳 → スコープ記帳まで 8 周目で初めて到達した。残り（軽）: 同文の確定済み前提が 3 件（周の前の用意で重なった）並び、同じ論文に結ばれた 2 件を区別する手掛かり（確定者・日付）がカードに無い。記帳後に地図・合意リストへ載らない件は IK-1058。run: uxsim/runs/c-r25-doubt-r8/20261027T223914Z-96688（seq7・9・10）。
