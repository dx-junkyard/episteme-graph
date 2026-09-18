---
id: IK-0201
title: 公開手段の契約をサーバ側だけ差し替え、呼び出し側と案内が旧契約のまま残って学習者がコースに到達できない
status: resolved
recorded_at: 2026-07-16
resolved_at: 2026-07-16
sources:
  - docs/architecture/vision_ux_gap_survey_2026-07.md §2 G1
  - docs/architecture/issue_494_implementation_review_2026-07-16.md
feature_context:
  realizing: 教員が作った教材・コースを学習者へ開示する（公開とグループ共有の2経路）
  layers: [auth_visibility, frontend_admin_ui, guidance_g]
classification:
  axes:
    processing: [logic]
    structure: [aggregation]
    connection: [contract]
    governance: [completion]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理: グループ経由の招待承諾は、同名の関数が二重に定義され後の定義が前を潰したことで到達
    不能になっていた。この一点を消さなければ承諾は動かないので値を置く。変更で過去の処理を
    壊した退行と読む余地も残るため確信は中。
    構造: 公開という同じ事実が開示範囲と公開フラグの二つの値で表され、正本が一本化されて
    いない。片方を直しても両者が独立に動く余地は残る。表現そのものの不足との境界で迷った。
    接続: 撤去された入口の後継はサーバに実在して単体では動くのに、呼び出し側と案内が旧い契約の
    ままで、新旧いずれの契約でも一本の経路が成立しない。
    統制: 撤去されたことは検査で固定されたが、後継へ移し終えたことを「済み」とみなす基準が無い。
    更新を確かめる手続の不足と読む余地も残る。
generalization:
  level: general
  general_form: >-
    提供側が入口の契約を差し替えたのに利用側と案内が旧契約のまま残り、機能へ到達する経路が消える
pattern: contract-changed-one-side
discovery:
  perspective: [doc_code_diff, inventory]
  note: >-
    バックエンドの全エンドポイントとフロントの呼び出しを静的に突合し、後継 API を呼ぶコードが
    ひとつも無いことが分かった。撤去自体はテストで固定されており、撤去側だけが守られていた。
resolution:
  perspective: [canonical_source, explicit_contract]
  note: >-
    後継 API を唯一の公開手段と定め、画面・操作手順書・案内機構の宣言をその一本へ揃えた。
    併せて公開状態の導出（開示範囲と公開フラグ）を一方向の従属関係にし、両者が独立に動く
    余地を無くした。以後は「入口の契約を変えるときは呼び出し側と案内を同じ変更で更新する」を
    運用チェックリストに載せている。
  landed_in:
    - frontend/public/js/admin.js
    - backend/api/routes/admin.py
    - docs/admin_operations/course.md
    - docs/development_checklist.md §2
related: [IK-0203, IK-0204]
view_of: [IK-0203]
history:
  - date: '2026-09-19'
    field: classification
    from: primary=connection facets=[connection.contract, governance.completion]
    to: axes=processing=[none]; structure=[none]; connection=[contract]; governance=[completion]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
  - date: '2026-09-19'
    field: classification.axes
    from: processing=[none]; structure=[none]; connection=[contract]; governance=[completion]
    to: processing=[logic]; structure=[aggregation]; connection=[contract]; governance=[completion]
    reason: 軸ごとの再判定で、招待の承諾を塞いでいた同名関数の二重定義を処理軸に、公開の事実が
      二つの値に分かれていた点を構造軸に置いた
---

## 課題

**症状**: 学習者が教員のコースに到達する公式の2経路（公開・グループ共有）が、どちらも画面から
使えなかった。公開は画面にボタンが無く、グループは招待の承諾処理が同名関数の二重定義で到達不能になっており、どちらも別々の原因で塞がっていた。

**原因**: 公開 API が意図的に撤去され、開示範囲 API が後継として実装されたが、契約の差し替えが
サーバ側で完結していた。呼び出し側（管理画面）は後継を一度も呼ばず、案内（操作手順書・
capability 宣言・次にやること）は撤去済みの旧 API を指し続けた。撤去されたことはテストで
固定されていたが、「後継へ移し替えたこと」は誰も固定していない。

副次の発見として、開示範囲を public 以外へ戻しても公開フラグが真のまま残り、公開状態の判定が
二つの独立した値に分裂していた。

**軸ごとの再判定（2026-09-19）**: 処理軸には、招待の承諾を塞いでいた同名関数の二重定義を置く
（契約を揃えてもこの一点は残るため）。構造軸には、公開という同じ事実が開示範囲と公開フラグの
二つの値で表されていた点を置く。接続軸の契約の不一致は変えていない。統制軸は、撤去は検査で
固定されたのに「後継へ移し終えた」とみなす基準が無いことに当たる。

## 発見の観点

`doc_code_diff` と `inventory`。バックエンドのエンドポイント一覧とフロントの呼び出し一覧を
機械的に突合し、「サーバにあるのに誰も呼ばない」集合を作ったときに最上位に出た。症状
（学生の画面にコースが無い）からは、原因が撤去済み API の後継にあることまで辿りにくい。

## 解決の観点

`canonical_source`（公開手段を後継 API の一本に定める）+ `explicit_contract`（案内の宣言と
手順書を実在の経路に一致させる規約）。公開フラグは開示範囲からの導出に変え、
同じ事実が二つの値で表される状態を解消した。「旧契約を消すときに新契約への移行を同じ
変更で終える」ことを運用側の規律として明文化している。

## 一般化

同じ型は「API の廃止と後継の導入」「保存形式の移行」「語彙の改名」など、提供側と利用側が
別々に更新できるあらゆる境界で再発する。撤去をテストで固定すると撤去側だけが守られ、
移行側が守られないという非対称も併せて記録する価値がある。辞書の型は
`contract-changed-one-side`。
