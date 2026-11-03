---
id: IK-0937
title: "候補の条件から外れた採用済みのカテゴリギャップの判断に、触れていた論文の題名と原文の逐語が出ず、教員は［次版の下書きに取り込む…］か［候補へ戻す］かを原文で判断し直せない（信号は残っているのに別枠のカードへ運ばれない）"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/category_gap_candidates_design.md §5.4"
feature_context:
  realizing: "教員が凍結を止めている採用済みの候補を開き、根拠の論文と原文に照らして取り込むか戻すかを決め直す"
  layers: [category_gaps, field_atlas_s, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [information]
    governance: [review]
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
    接続軸 information: 外れた理由が below_threshold の判断は、同じ cluster の active な信号（document_id・題名・
    evidence_quote）がまだ表にある（atlas_gaps/store.py list_orphaned_decisions は _fetch_active_signals を読み、
    latest[key] で 1 行だけ使う）のに、返す要素は proposed_label / layer / reason / decision だけで、論文の列挙と逐語を
    運ばない。通常の候補カード（derive_candidates）は同じ信号から支持論文と逐語を出すので、同じ判断が「候補」の間は
    根拠付き、「外れた判断」になると根拠なしになる。構造軸 representation: 別枠の DTO に根拠の居場所が無い。
    統制軸 review: 人の判断のやり直し（IK-0884 で置いた出口）の材料が欠けるので、出口があっても使えない — 精読型の
    教員は保留を選び凍結が止まったまま（IK-0884 のゲートは正しく働いている）。処理軸 none。
    no_signals（信号が再解析・削除で消えた）の場合は運ぶ材料が無い — その場合は「根拠だった論文の記録はもうありません」
    の事実文が既にある。
    確認: run 20261023T070217Z-13812 seq 5 / 14 / 15（gap-candidates 応答の orphaned_decisions にキーが無い）、
    run 20261023T032742Z-99530 seq 3。
generalization:
  level: repo_pattern
  general_form: "人の判断を一覧の別枠へ移すとき、判断の行だけを運び、判断の根拠だった材料（出所と逐語）が手元にあるのに一緒に運ばないため、判断のやり直しの画面が根拠なしになる"
pattern: available-but-unwired
discovery:
  perspective: [reproduction, trace_walk]
  note: "精読型の教員ペルソナが凍結の影響から［止めている候補を見る］で星間磁場へ移り、出所の論文も原文も無いので凍結を保留した（gave_up）。応答の orphaned_decisions のキーと list_orphaned_decisions の SELECT を突き合わせた。 run: uxsim/runs/c-astro-r25-atlas-r3/20261023T070217Z-13812・20261023T032742Z-99530（頭脳の所見 uxsim/runs/round25/BRAIN_NOTES_atlas_r3.md）。"
resolution:
  perspective: [carry_through, fail_closed]
  note: >-
    導出から外れた判断にも、通常の候補と同じ根拠の形を運ぶ。atlas_gaps/store.py は信号を論文単位に畳む _fold_by_paper と DTO の documents を組む
    _paper_documents を 1 箇所に切り出し、derive_candidates と list_orphaned_decisions（+ 取り込みの確認用
    cluster_documents）が同じ関数を通る。route は services.list_visible_document_ids を visible_document_ids
    に渡し、gate_documents が閲覧できない論文を外して documents_hidden の真偽だけを付ける（fail-closed・件数なし）。admin.js の外れた判断のカードは候補と同じ
    _gapDocumentsHtml で題名・理由・逐語を描き、判断のメモも出す。辺の外れた判断は、採用時に記録した mapping_justification から store.origins_for_decisions
    で根拠の一文（basis_line）を出す（記録が無ければその旨 — 推測で埋めない）。v2 見送り: 信号が消えた判断（no_signals）の根拠の写しを判断行に持つ案は、実例が出るまで保留（オーナー判断 4
    の推奨どおり）。
  landed_in:
    - "backend/core/atlas_gaps/store.py"
    - "backend/core/atlas_edges/store.py"
    - "backend/api/routes/atlas_gaps.py"
    - "backend/api/routes/atlas_edges.py"
    - "frontend/public/js/admin.js"
    - "backend/tests/test_atlas_review_round25_r3.py"
  principles:
    - principle: enforce-condition-in-query-with-required-argument
      use: consulted
      note: "根拠の論文を外へ運ぶ新しい経路に可視性の条件を渡す（gate_documents は None 以外なら閉じる側）。SQL 内ではなく route で受けた可視集合との積なので派生形。"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場の星間磁場の判断で、題名と逐語が別枠のカードに出ることは再演で未確認"
      - "信号が消えた判断の根拠の写し（v2）は未実装"
related: [IK-0884, IK-0849, IK-0846]
view_of: []
history: []
---

## 課題

症状: 「候補の条件から外れた採用済みの判断」区画の星間磁場は、帰属（誰がいつ採用）と外れた理由の一文だけで、どの論文の
どの文から出た候補かが出ない。採用した本人（精読型）が再読しても原文と照らせず、取り込みも戻しもできずに凍結を保留した。

原因: 外れた判断の DTO は判断の行と代表の信号の名前だけを持ち、同じ cluster の信号が持つ論文と逐語を運ばない。

## 発見の観点

通し受講（reproduction）で凍結の止め理由から候補区画へ移った教員が根拠を探して止まり、応答のキーと store の SELECT を辿った（trace_walk）。

## 解決の観点

第 3 波（2026-10-23）で是正。導出から外れた判断にも、通常の候補と同じ根拠の形を運ぶ。

## 一般化

候補を読み時導出し判断だけを保存する設計で、判断を別枠に出すたびに「判断の根拠」が置き去りになる。辺の外れた判断
（atlas_edges の orphaned）も同じ形で、共起の根拠（documents / endpoints）を運ばない。確かめ方: 採用した候補を
条件外にし、別枠のカードに題名と逐語が出ることを見る。
