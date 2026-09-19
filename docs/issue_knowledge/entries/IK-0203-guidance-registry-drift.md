---
id: IK-0203
title: 案内機構が実在しない操作を案内し続け、増築された機能には追随しない
status: resolved
recorded_at: 2026-07-16
resolved_at: 2026-07-18
sources:
  - docs/architecture/vision_ux_gap_survey_2026-07.md §2 G1-6
  - docs/architecture/vision_ux_gap_survey_2026-07-17.md §2 P5
  - docs/development_checklist.md §1
feature_context:
  realizing: 増え続ける管理機能を、説明・道案内・次の一歩として利用者へ案内する
  layers: [guidance_g, admin_copilot, help_kb]
classification:
  axes:
    processing: [none]
    structure: [aggregation]
    connection: [none]
    governance: [review]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理: 宣言・ルール・手順書はいずれも書式として正しく、参照する仕組みも設計どおり動くため
    要素は無い。
    構造: 案内の内容が宣言・ルール・手順書の三系統に分かれて持たれ、どれが正本かが決まって
    いない。一つを直しても他が古いまま残る。集約の不在と更新手続の不在の切り分けで迷った。
    接続: 実装と案内の間に段階の受け渡しは無く、値が途中で落ちているわけではないので要素は無いと
    判断した。後から積んだ層の成果が案内へ現れない点を接続と読む余地は残る。
    統制: 機能を足したときに案内を同じ変更で更新する手続も、追随を確かめる担当も置かれていない。
generalization:
  level: repo_pattern
  general_form: >-
    案内・説明の記述が実装に自動追随せず、作った時点の姿を案内し続ける
discovery:
  perspective: [doc_code_diff, invariant_audit]
  note: >-
    案内の宣言が指す操作を実装側と突合したところ、撤去済みの操作を指す宣言と、宣言だけあって
    実行できない代行操作が見つかった。加えて、後から積んだ層のどれにも「次にやること」が
    現れないことを、ルールの一覧と層の一覧の差分で確認した。
resolution:
  perspective: [explicit_contract, guardrail_fix]
  note: >-
    宣言を実在の経路へ直し、実行できる代行だけを実行可能として明示する印を持たせた
    （できないものは道案内に降格し、案内文で例示しない）。恒久対策として、新機能の変更では
    案内機構を更新したか（更新しない判断も含めて）を明記する規律を置き、道案内の目印と画面側の
    登録の整合を機械検査に入れた。
  landed_in:
    - backend/core/admin_assistant/capabilities.py
    - backend/core/admin_assistant/next_steps.py
    - backend/tests/test_admin_help_inspect_ui_static.py
    - docs/development_checklist.md §1
    - docs/development_checklist.md §4
related: [IK-0201, IK-0221]
view_of: [IK-0201]
pattern: doc-drifts-from-code
history:
  - date: '2026-09-19'
    field: classification
    from: primary=governance facets=[governance.review, structure.aggregation]
    to: axes=processing=[none]; structure=[aggregation]; connection=[none]; governance=[review]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

**症状**: 「次にやること」は存在しない公開ボタンを押すよう促し続け、AI アシスタントは
撤去済みの操作を宣言し、操作手順書もその操作の手順を書いていた。三つの案内が揃って壊れた
導線を案内していた。翌年度の再調査では、代行できると宣言した操作の大半が実行できず、
「次にやること」のルールも後から積んだ層に追随していなかった。

**原因**: 案内の内容は登録簿・ルール・手順書という形で明示的に管理されているが、実装を
変えたときにそれらを更新する手続きが無い。登録されたものだけを案内するという安全設計が、
逆に「登録された古い姿を案内し続ける」形で破れた。

## 発見の観点

`doc_code_diff` と `invariant_audit`。案内の宣言が指す操作を実装と一件ずつ突合し、
さらに「登録簿にある操作の集合」と「実際に到達できる操作の集合」の差を取った。症状としては
利用者からの苦情になりにくい（案内どおり押せないだけ）ため、突合しないと見えない。

## 解決の観点

`explicit_contract` — 宣言と手順書を実在の経路へ直したうえで、「実行できる」ことを印で
明示した（できないものは道案内に降格）。そのうえで `guardrail_fix` — 案内の目印と
画面側の登録の整合を検査に入れ、更新有無を変更のたびに明記する規律を置いた。案内側を
自動生成する案は採らなかった（案内は実装から機械導出できない判断を含むため）。

## 一般化

同じ型は、手順書・ヘルプ・オンボーディング・監視ダッシュボードなど「実装とは別に人が書いた
記述」が実装に追随しないところすべてで再発する。実装の一部として書かれていても（登録簿が
コードであっても）、記述である以上は同型である点が要点。辞書の型は `doc-drifts-from-code`。
