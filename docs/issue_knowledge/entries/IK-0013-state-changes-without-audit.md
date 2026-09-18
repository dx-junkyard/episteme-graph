---
id: IK-0013
title: 教材の物理削除・版の取り込み・原稿の保存が、監査の記帳を伴わずに状態を変える
status: resolved
recorded_at: 2026-09-10
resolved_at: 2026-09-10
sources:
  - docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md §2 F11
  - docs/architecture/six_lenses_2026-09-10/known_issues_architecture.md A-04
  - docs/features/decision_context_design.md
feature_context:
  realizing: 誰がいつ何を変えたかを、あとから一つの台帳で辿れるようにする
  layers: [versioning_v, lecture_studio, decision_context]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [none]
    governance: [review]
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
    処理: 各処理は意図どおりに状態を変えている。
    構造: 記帳の語彙はカタログに正本があり、分かれているのは呼び出しの有無だけである。
    接続: 段階間で落ちているものは無い。
    統制: 記帳が経路の要件ではなく書き手の心がけとして扱われ、漏れたことを知らせるものも無い。
generalization:
  level: general
  general_form: 状態を変える経路の一部が記録の決まりから漏れ、後から何が起きたか辿れない
pattern: unaudited-write-path
discovery:
  perspective: [inventory, invariant_audit]
  note: >-
    「帰属付きで記帳する」という条項に対して、状態を変える経路を棚卸しし、記帳の呼び出しの
    有無で突き合わせた。最も古い層と最も新しい層の両方に漏れがあり、共通点は「主たる機能の
    副作用として状態が変わる」経路だったこと。
resolution:
  perspective: [explicit_contract, guardrail_fix]
  note: >-
    漏れていた経路に共通の記帳を足し、あわせて一括の確定を扱う経路には、提示と適用を別々に
    持つ確定文脈の記帳を広げた。記帳は挙動を変えない追加なので、機能への影響なしに入れられる。
    どの語彙で記帳するかはカタログ側を正本にして、経路ごとの独自語彙を作らないようにした。
  landed_in:
    - backend/api/routes/admin.py
    - backend/api/routes/lecture_studio/topics.py
    - backend/core/decision_context.py
    - docs/features/decision_context_design.md
    - docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md §4 第1波 6
related: [IK-0008, IK-0012]
view_of: []
history:
  - date: '2026-09-19'
    field: classification
    from: primary=governance facets=[governance.review, governance.ordering]
    to: axes=processing=[none]; structure=[none]; connection=[none]; governance=[review,
      ordering]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
  - date: '2026-09-19'
    field: classification.axes
    from: processing=[none]; structure=[none]; connection=[none]; governance=[review, ordering]
    to: processing=[none]; structure=[none]; connection=[none]; governance=[review]
    reason: 軸ごとの再判定で、記帳の欠落は実行や判定の順序の問題ではないと見て順序の値を外した
---

## 課題

症状は、教材が消えた・版が取り込まれた・原稿が書き換わったという事実が、監査の台帳から
辿れないことである。三つの経路がいずれも記帳を呼んでいなかった。

原因は、記帳が経路の要件としてではなく、書き手の心がけとして扱われていたことにある。処理は
どれも正しく状態を変えている。記録の決まりから漏れているだけで、漏れたことを知らせる仕組みも
無かった。

軸ごとに読み直すと、統制のうち順序の要素は見当たらない。記帳が呼ばれる位置が遅いのではなく、
そもそも呼ばれていない。手続と記録の問題として一つの値に絞った。

## 発見の観点

「帰属付きで記帳する」という条項に対して、状態を変える経路を棚卸しし、記帳の呼び出しの
有無で突き合わせた。漏れは古い層と新しい層の両方にあり、共通するのは「主たる機能の副作用と
して状態が変わる」形だった。削除は削除の画面から、保存は編集の画面から呼ばれるので、
記帳を足す場所として意識されにくい。

## 解決の観点

記帳は挙動を変えない追加なので、漏れていた経路に足すだけで解ける。あわせて一括の確定を扱う
経路には、提示と適用を別々に持つ確定文脈の記帳を広げた。語彙はカタログを正本にして、
経路ごとに独自の語を作らないようにした。

## 一般化

状態を変える経路が増え続けるシステムすべてで再発する。記帳の呼び出しは主機能と独立なので、
忘れても動く。辞書の `unaudited-write-path` に対応し、処方は「状態を変える経路の一覧と
記帳の一覧を突き合わせる検査を置く」。
