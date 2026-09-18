---
id: IK-0012
title: 持ち出しの受け口が役割だけで通し、対象ごとの可視性も記帳も見ない
status: resolved
recorded_at: 2026-09-10
resolved_at: 2026-09-10
sources:
  - docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md §2 F10
  - docs/architecture/six_lenses_2026-09-10/04_community.md
  - docs/architecture/six_lenses_2026-09-10/known_issues_architecture.md H-04
feature_context:
  realizing: 教材やコースの成果を束として外へ持ち出せるようにする
  layers: [export_bundle, auth_visibility]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [condition]
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
    処理: 持ち出しも一覧も意図どおり動く。
    構造: 可視性を判定する仕組みは既にあり、受け口がそれを呼んでいないだけである。
    判定の置き場所の問題と読むかで迷った。
    接続: 対象ごとの可視性という条件が外向きの受け口まで伝わらない。
    統制: 境界を越える読み出しが記帳の対象になっていない。承認の手続を扱う値で受けてよいかで迷った。
generalization:
  level: general
  general_form: 役割だけで通す受け口が、対象ごとの可視性を見ないまま広い範囲を出してしまう
pattern: scope-widened-silently
discovery:
  perspective: [boundary_walk, invariant_audit]
  note: >-
    出口（外へ出るもの）と境界（誰に見えるか）を交差させて歩いた。中の画面は対象ごとに
    可視性を見ているのに、外へ出す受け口は役割の判定だけで通していた。同じ観点でメンバー
    一覧を見ると、連絡先が全員へ返っていた。
resolution:
  perspective: [fail_closed, carry_through]
  note: >-
    受け口に対象単位の閲覧判定を置き、通らないものは存在しないものとして扱う。束には
    出所と発行状態を添えて、どこから出たものかを持ち出し先でも辿れるようにした。持ち出しは
    状態を変えない操作だが、境界を越えるので記帳の対象に含めた。連絡先の開示は管理者に絞った。
  landed_in:
    - backend/api/routes/export.py
    - backend/api/routes/groups.py
    - docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md §4 第1波 8
related: [IK-0013, IK-0022]
view_of: []
history:
  - date: '2026-09-19'
    field: classification
    from: primary=connection facets=[connection.condition, governance.review]
    to: axes=processing=[none]; structure=[none]; connection=[condition]; governance=[review]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

症状は、対象を選べる立場にありさえすれば、自分に見えないはずの教材やコースまで束として
持ち出せてしまうことである。受け口は役割を確かめるだけで、その対象が自分に見えるものか
どうかを見ていなかった。持ち出しの記録も残らない。同じ観点で見たグループのメンバー一覧は、
連絡先を全員へ返していた。

原因は、対象ごとの可視性という条件が受け口まで伝わっていないことにある。画面側は対象ごとに
判定しているので、通常の操作では矛盾が見えない。外へ出る経路だけが粗い条件で通っていた。

## 発見の観点

「外へ出るもの」と「誰に見えるか」を交差させて歩いた。内側の読み取り経路と外側の持ち出し
経路で判定の粒度が違うことに気づき、そこから同じ粒度の差をメンバー一覧にも見つけた。

## 解決の観点

役割の条件を強める案では、対象の射程が変わらない。受け口に対象単位の閲覧判定を置き、
通らないものは無いものとして返す形にした。あわせて束に出所と発行状態を添え、持ち出しを
記帳の対象にした。持ち出しは状態を変えないが境界を越えるからである。

## 一般化

外部出力・一括ダウンロード・機械可読 API すべてで再発する。内側の画面が細かく判定して
いるほど、外向きの粗さが見えにくい。辞書の `scope-widened-silently` に対応し、処方は
「境界を越える経路は、内側と同じ粒度の条件で判定する」。
