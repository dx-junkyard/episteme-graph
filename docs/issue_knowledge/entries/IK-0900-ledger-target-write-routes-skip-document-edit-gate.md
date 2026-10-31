---
id: IK-0900
title: "台帳の対象単位の書き込み API（スコープの記帳・訂正、検証状態の変更、スコープ候補と覆る条件の候補の確定・却下、覆る条件の記帳・訂正、前提の確定・却下）が TEACHER であることしか見ず、対象の教材の編集権限を確かめない — 根拠の線とシミュレーション候補だけがゲートを持つ非対称で、閲覧しかできない教員が他人の教材の台帳を書き換えられる"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.3"
  - "docs/architecture/persona_enactment_testing_design.md §22.8"
  - "docs/features/doubt_layer_issues.md"
  - "docs/features/stakes_ledger_design.md"
feature_context:
  realizing: "教員が自分の教材の台帳に検証スコープ・検証状態を記帳する"
  layers: [doubt_d, stakes_ledger_sl, auth_visibility]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [condition]
    governance: [none]
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
    接続軸 condition: backend/api/routes/doubt.py の対象単位ルートのうち、/ledger/{t}/{id}/evidence-lines（POST・PATCH）と simulation-candidates の確定・却下は _require_editable_ledger_target（対象の document
    の編集権限）を通すが、/scopes（POST・PATCH）・/verification-status（PUT）・/scope-candidates/*/confirm|dismiss・/falsification-conditions（POST・PATCH）・/falsification-candidates/*/confirm|dismiss・/assumptions/*/confirm|dismiss
    は Depends(_require_teacher) だけで、編集権限の条件が書き込みまで届いていない。構造軸 none（判定の正本 _require_editable_ledger_target は既にある）。IK-0857 はコース単位ルート（/courses/{id}/...）を揃えたが、対象単位ルートは範囲外だった。
generalization:
  level: general
  general_form: "同じ対象への書き込み経路の一部にだけ所有・編集権限のゲートを置き、残りは役割だけで通すため、閲覧だけの利用者が対象の id を指定すれば他人の対象を書き換えられる"
pattern: rule-enforced-per-surface
discovery:
  perspective: [reproduction, inventory]
  note: >-
    閲覧のみの教員 te-01 が、他人の教材（台帳行の document は中性子星の論文）の主グラフのノードの台帳へスコープを記帳でき（200）、検証状態の PUT も通った。doubt.py の書き込みルートを全件列挙してゲートの有無を突き合わせた。run: uxsim/runs/c-r25-doubt-r2/20261022T220507Z-77990（seq12・14・15）。
resolution:
  perspective: [fail_closed, guardrail_fix]
  note: >-
    第 2 波で是正（2026-10-23）。対象単位の書き込みルート（スコープ・検証状態・スコープ候補・覆る条件・覆る条件候補・シミュレーション候補・根拠の線）はすべて _require_editable_ledger_target を通す（ノード・式は指定の論文にその対象が実在することも確かめる）。前提の確定・却下は _require_editable_assumption、手動の前提はコース・論文の編集権限、疑義と検証提案は閲覧できる教員なら残せる（帰属必須の公開の学術行為）。台帳 GET は閲覧ゲート + can_edit（偽なら can_edit_note を出し画面は記帳ボタンを無効にする）。AST ガードレールで /ledger/ の書き込みルート全件を固定。
  landed_in:
    - "backend/api/routes/doubt.py"
    - "backend/core/doubt/scope.py"
    - "frontend/public/js/doubt-atlas.js"
    - "backend/tests/test_doubt_ledger_document_key_round25.py"
  principles:
    - principle: enforce-condition-in-query-with-required-argument
      use: consulted
      note: "権限ゲートの対象 document を経路ごとに解決せず、IK-0899 の必須の document_id から 1 関数で判定する。"
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "書き込みルートを AST で全件走査し、ゲートを迂回するルートを足せないようにした。"
  verification:
    methods: [guardrail]
    unverified:
      - "閲覧のみの教員での端から端の操作（ボタンの無効化）はブラウザで未確認"
related: [IK-0857, IK-0899]
view_of: []
history: []
---

## 課題

症状: 前提の地図・原稿スタジオのノード詳細から開いた台帳で、閲覧の共有しか受けていない教員でもスコープを記帳でき、
検証状態を「直接検証の記帳あり」に変えられる。画面は権限が無いことを先に示さず、入力検査（4 軸のうち 1 つ以上）まで進む。

原因: 対象単位の書き込みルートの大半が役割（TEACHER）しか見ない。根拠の線（P4-R8）だけが対象の教材の編集権限を
要求していて、同じ台帳の他の記帳と規律が揃っていない。IK-0899 の衝突があるため、対象の document の解決自体も
別の論文に着地しうる（ゲートを入れるときは document を必須にしてから判定する）。

## 発見の観点

再現: 閲覧のみの教員に他人の教材の台帳を開かせて記帳させ、応答が 200 だった。棚卸し: doubt.py の書き込みルートを全件
列挙してゲートの有無を表にした。

## 解決の観点

第 2 波で是正（2026-10-23。frontmatter の resolution）。以下は起票時の是正案。是正案:

- 対象単位の書き込みルートの先頭で `_require_editable_ledger_target` を通す（scopes・verification-status・scope/falsification
  の候補の確定・却下・覆る条件の記帳と訂正・前提の確定・却下）。疑義（challenges）と提案は「閲覧できる教員なら誰でも
  帰属付きで残せる」とするか「編集権限」とするかを決めてから揃える（D層の疑義は帰属必須の公開の学術行為なので、
  推奨は閲覧権限）。
- 画面は台帳 GET の応答に `can_edit`（真偽値）を足し、編集できない教員には記帳ボタンを無効にして理由を事実文で出す。
- ガードレール: 対象単位の書き込みルートの先頭の文がゲートであることを AST で固定する（IK-0857 のガードレールの
  対象を `/ledger/` `/assumptions/` に広げる）。
- IK-0899 の是正（document を含むキー）の後に入れると、ゲートが別の論文の権限を見ることが無くなる。

## 一般化

同じ対象に書き込む経路が並ぶとき、権限ゲートを経路ごとに書くと必ず揃わない。確かめ方は、閲覧だけの利用者で
全書き込み経路を叩き、どれも閉じることを見ること。

第 4 周の再演（2026-10-25）: 画面側は GONE — 閲覧だけの te-01 が PINN の 2 つのノード（どちらも台帳行なし・exists:false）で「この論文の編集権限が無いため、台帳への記帳は
できません（閲覧と疑義の記帳はできます）」を読み、記帳ボタンが無効だった。ただし［AIに候補を出してもらう］だけ無効化から漏れている（IK-0969）。サーバの拒否は run では
未確認のまま（runner が UI の無効化で書き込みを送らなかった — harness_fixes_doubt.md H30）。行のある対象の側も未確認（周の前の行の用意が Cepheus に置かれた — H31）。
run: uxsim/runs/c-r25-doubt-r4/20261024T232812Z-98847（seq5〜7）。

第 5 周の再演: サーバの弁は迂回されていないが run では権限の拒否そのものを未観察（2026-10-27）。te-02 の画面を経ない記帳は根拠なしの body で、入力検査の 422「根拠（原文の引用など）を入力してください。」で止まった（PINN の台帳は増えていない）。権限検査（_require_editable_ledger_target・404）は入力検査の後にあり、要求がそこへ届いていない — 順序の課題は IK-0999、完全な body での再プローブは harness H34（第 6 周）。run: uxsim/runs/c-r25-doubt-r5/20261027T070623Z-23504（seq8）。

第 6 周の再演: サーバの弁を run で初めて当てた（2026-10-27）。閲覧だけの te-05 の画面を経ない有効な記帳は 404 で拒否（画面側は第 4 周から GONE）。拒否文の言語と意味（「見つからない」）は IK-1027。run: uxsim/runs/c-r25-doubt-r6/20261027T122225Z-3508（seq7・8）。
