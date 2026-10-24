---
id: IK-0884
title: "候補の導出規則が変わって候補一覧から消えたカテゴリギャップの「採用」判断が、判断行のまま凍結のゲートに残り、画面のどこにも出ない原因で公開が止まる"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/category_gap_candidates_design.md §5.4"
feature_context:
  realizing: "教員が論文由来の候補を採用し、次版の下書きに取り込んで分野の地図を凍結（公開）する"
  layers: [category_gaps, field_atlas_s, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [aggregation]
    connection: [contract]
    governance: [review]
  axis_confidence:
    processing: medium
    structure: high
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸 aggregation: 「採用済みでまだ次版に反映されていない候補」の判定材料が 2 つに分かれている。候補一覧は
    landscape_gap_signals から毎回導出し（atlas_gaps/store.py derive_candidates — distinct 論文が
    MIN_DOCUMENTS_FOR_CANDIDATE 以上の cluster だけ）判断行をマージする。凍結のゲートは atlas_gap_decisions の
    status='accepted' ∧ applied_version='' の行だけを読む（store.py list_pending_for_freeze）。導出の条件を外れた
    cluster の判断行は、一覧からは消え（見送り済みの表示でも出ない）、ゲートには残る。
    接続軸 contract: IK-0846 の是正で導出側（論文の同一性キーで数える）だけが変わり、ゲート側の契約
    （判断行があれば止める）は変わらなかった。第 1 周に採用された「星間磁場」は同じ論文の PDF 版と TeX 版の対で
    閾値を満たしていたので、是正後は候補にならないが accepted 行は残る。
    統制軸 review: 止める理由（星間磁場）を教員が見て判断し直す画面上の入口が無い。409 の応答で admin.js は
    atlas-reports-section へスクロールするが、そこに当該候補は描かれない。採用の取り消し（候補へ戻す）操作も
    この行には届かない。処理軸 none（各関数の判定は自分の材料に対して正しい）。
    確認: run 20261022T220051Z-74929 の seq 11（gap-candidates 空）・seq 19（freeze 409「…候補が残っています: 星間磁場」）・
    seq 20（開き直しても出ない）、routes/atlas.py _pending_gap_candidates、admin.js の freeze 409 分岐。
generalization:
  level: repo_pattern
  general_form: "読み時に導出する候補集合と、候補に付けた人の判断の行が別々に持たれ、導出規則が変わると判断だけが残って、判断行を読むゲートが画面に見えない対象で操作を止める"
pattern: contract-changed-one-side
discovery:
  perspective: [reproduction, trace_walk]
  note: "経験豊富な教員ペルソナが公開前チェック（エラー・警告ゼロ）の直後に公開して止められ、開き直しても原因の候補を見つけられず諦めた。応答の空の候補一覧と 409 の pending_labels を突き合わせ、判断行と導出の差を確かめた。 run: uxsim/runs/c-astro-r25-atlas-r2/20261022T220051Z-74929（指揮者メモ uxsim/runs/round25/r2_live_notes.md・頭脳の所見 uxsim/runs/round25/BRAIN_NOTES_atlas_r2.md）。"
resolution:
  perspective: [first_class_state, guardrail_fix]
  note: >-
    導出から外れた「採用済み・未反映」の判断行を一級の別枠にした。atlas_gaps/store.py list_orphaned_decisions が accepted ∧ applied_version='' で今回の導出に cluster が無い行を、外れた理由（反復に届かない / 現行版で解消 / 信号なし — derive_candidates(report=) の cluster_states から）と下書きの状態付きで返し、gap-candidates は orphaned_decisions + orphaned_note（atlas_review_display の定数）を返す。admin.js は候補区画の末尾に「候補の条件から外れた採用済みの判断」を描いて［候補へ戻す］（restore = 状態遷移）と［次版の下書きに取り込む…］を置く。凍結のゲートは緩めていない（判断行を読むまま）。辺の判断も同じ構造なので atlas_edges/store.py list_unapplied_accepted から同じ別枠を返す（辺の restore は見送りからしか戻せないので出口は［見送り…］と［反映…］）。
  landed_in:
    - "backend/core/atlas_gaps/store.py"
    - "backend/core/atlas_edges/store.py"
    - "backend/core/atlas_review_display.py"
    - "backend/api/routes/atlas_gaps.py"
    - "backend/api/routes/atlas_edges.py"
    - "frontend/public/js/admin.js"
    - "backend/tests/test_atlas_review_round25_r2.py"
  principles:
    - principle: sync-by-stable-key-and-protect-human-decisions
      use: consulted
      note: "導出規則が変わっても人の判断（採用）を黙って捨てない — ゲートを緩めず判断行を画面に出す。"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場の星間磁場の判断行で区画に出て［候補へ戻す］で凍結できることは再演で未確認"
related: [IK-0846, IK-0885, IK-0851]
view_of: []
history: []
---

## 課題

症状: 公開前チェックは検証エラー・警告ゼロ・影響ゼロと出るのに、[公開する]を押すと「採用済みでまだ次版に反映されていない
候補が残っています: 星間磁場」で止まる。候補の区画は「いまレビューする候補はありません」で、見送り済みも表示にしても
星間磁場は出ない。教員は止めている原因へたどり着けず公開できない。

原因: 候補は信号から毎回導出し、判断は別の表に行として残る。導出の規則（同じ論文の重複を 1 本と数える — IK-0846）が
変わって cluster が候補の条件を外れても、accepted の判断行は残り、凍結のゲートは判断行だけを読む。一覧は導出された候補に
判断を重ねるので、候補の無い判断行は画面に現れない。

## 発見の観点

通し受講の再演（reproduction）で教員が詰まり、応答の候補一覧・409 の detail・ゲートの SQL を順にたどった（trace_walk）。

## 解決の観点

第 2 波（2026-10-23）で是正。判断行を「導出に無い判断」として一級に見せ、戻す・取り込むの入口を同じ区画に置く。ゲートと公開前チェックが
同じ集合を読む（IK-0885）。

## 一般化

候補を読み時導出し判断だけを保存する設計（RE6・カテゴリギャップ G1）では、導出規則・閾値・材料（論文の削除・再解析）が
変わるたびに同じ形が出る。関係（辺）の判断（atlas_edge_decisions）も同じ構造で、辺の候補が導出から外れれば同じ止まり方をする。
確かめ方: 採用した候補が導出の条件を外れる状態（材料を減らす・閾値を上げる）を作り、候補区画に判断が見え、凍結の前に
止まる理由が読めることを見る。

## 第 3 周の再演（2026-10-23・campaign c-astro-r25-atlas-r3）

- GONE: 「候補の条件から外れた採用済みの判断」区画に星間磁場が理由・帰属・［候補へ戻す］［次版の下書きに取り込む…］付きで
  出て、凍結の影響も止める候補と関係を名前と行き先で言い、［公開できません］が押せない（run 20261023T070217Z-13812 seq 5 / 13 / 15）。
- 出口はあるが、外れた判断に根拠の論文と逐語が無いので精読型の教員は判断し直せず保留した — IK-0937 に起票。
