---
id: IK-0326
title: ルート登録検査が app.routes の内部表現に依存し、FastAPI の版が変わると prefix 抜きのパスを見て静かに空振りする
status: resolved
recorded_at: 2026-09-19
resolved_at: 2026-09-19
sources:
  - docs/issue_knowledge/entries/IK-0324-sql-percent-escape-written-for-one-consumer.md
  - CLAUDE.md
feature_context:
  realizing: Copilot の capability・指標カタログ・各層のルーターが実在する API パスに結び付いていることをテストで固定する
  layers: [tests_guardrails, admin_copilot, indicator_catalog]
classification:
  axes:
    processing: [none]
    structure: [aggregation]
    connection: [contract, version]
    governance: [review]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸: 検査は FastAPI の `app.routes` が平坦で `path` に prefix 込みの実パスを持つという暗黙の契約に
    依存していた。0.139 以降は子ルーターが遅延ラッパーのまま置かれ、prefix はラッパー側に移った。
    依存ライブラリ側の契約が変わり、版（CI は最新 0.141・ローカルは 0.136）によって結果が変わる。
    構造軸: ルート列挙が 5 つのテストで各自 `app.routes` を歩く形に散り、正本のヘルパーはあったが
    ラッパーを開くだけで prefix を合成しておらず、自身も「0.139 以降に対応」と述べながら空振りしていた。
    統制軸: CI とローカルで依存の版が揃っているかを確かめる手続が無く、さらに CI は migration 適用
    ステップで止まっていたため、登録検査の空振りは 1 週間見えなかった。処理軸: 各テストの比較処理
    自体は自分の前提の下で正しい。
generalization:
  level: general
  general_form: 検査が依存ライブラリの内部表現を暗黙の契約として読み、版が上がると検査対象を見失って静かに通るか静かに落ちる
pattern: guardrail-does-not-cover-new-path
discovery:
  perspective: [guardrail_failure, reproduction]
  note: >-
    CI で登録検査 10 件が「一致するルートが無い」と落ちた。ローカルでは通るので依存の版差を疑い、
    CI と同じ版の FastAPI を隔離環境に入れて `app.include_router` の結果を観察し、遅延ラッパーの
    `include_context.prefix` に prefix が保持され中のルートには付かないことを確認した。ローカルの
    venv を同じ版に上げて失敗を再現し、修正後に通ることを見た。
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: >-
    ルート列挙の正本ヘルパーを、ラッパーを再帰的に開きながら include 時の prefix を連結した実パスの
    代理オブジェクトを返す形に直した（0.139 未満の平坦な形でも同じ結果）。`app.routes` を直接歩いて
    いたテストをヘルパー経由に寄せ、テストが `app.routes` を直接歩くこと自体を禁じる検査と、
    入れ子 include の prefix 合成を固定する単体検査を足した。ローカルの venv は CI と同じ版に上げた。
  landed_in:
    - backend/tests/guardrail_helpers.py
    - backend/tests/test_route_registry_guardrails.py
    - backend/tests/test_admin_assistant.py
related: [IK-0212, IK-0120, IK-0324]
view_of: []
history: []
---

## 課題

**症状**: CI で、Copilot capability の API パス実在検査・指標カタログの経路検査・各層ルーターの登録
検査（計 10 件）が「一致するルートが無い」で落ちる。ローカルでは全件通る。

**原因**: FastAPI 0.139 以降、`app.include_router(...)` は子ルーターの route を `app.routes` に展開せず
遅延ラッパーのまま持ち、include 時の `prefix` はラッパーの `include_context` に保持される。中の
ルートの `path` に prefix は付かない。テストは「`app.routes` は平坦で `path` は実パス」という
ライブラリの内部表現を暗黙の契約として読んでいた。CI は依存を毎回最新で入れ（0.141）、ローカルは
0.136 のままだった。

軸ごとの判断: 接続軸は依存側の契約変更と版のずれ。構造軸は列挙処理が複数テストに散り、正本の
ヘルパーも prefix を合成していなかったこと。統制軸は版の一致を確かめる手続が無く、CI が別ステップで
止まっていたため 1 週間検出できなかったこと。

## 発見の観点

CI の失敗が起点。ローカルで通ることから版差を疑い、CI と同じ版を隔離環境に入れて `include_router`
の結果を直接観察した。ラッパーの属性を読み、prefix の所在を確認した。ローカルの venv を同じ版に
上げて失敗を再現し、修正の効果を同じ環境で確認した。

## 解決の観点

**正本の一本化**が主。ルート列挙は 1 つのヘルパーに集め、ヘルパーが版差を吸収する（ラッパーを
開き、prefix を連結した実パスの代理を返す。属性は元のルートへ委譲するので依存注入の検査も
そのまま動く）。**ガードレールの是正**が従。`app.routes` を直接歩く検査を禁じ、入れ子 include の
prefix 合成を固定する。

解消方法として残す発見:
- 依存ライブラリの内部表現を読む検査は、**ライブラリの版を上げたときに一緒に壊れる契約**を
  持っている。読む箇所を 1 つのヘルパーに閉じ込めれば、版差の吸収も 1 箇所で済む。
- 「対応済み」と書かれたヘルパーが対応していなかった。**ヘルパー自身の単体検査**（合成した
  prefix が実パスになる）を持たないと、ヘルパーは対応を宣言しただけになる。
- ローカルと CI の依存の版が違うと、ローカルの全件通過は CI の成功を意味しない。**再現は CI と
  同じ版で行う**（隔離環境で版を入れて観察する手順が最短だった）。

## 一般化

「ガードレールが新しい経路・表現を覆わない」型の一例。依存ライブラリの版上げが「新しい表現」を
持ち込む場面で出る。IK-0212（検査が名前列挙・字面に依り新実装を覆えない）と同じ族で、
IK-0324（片方の実行経路にだけ合わせた規約）と同じ日に、同じ CI の赤から見つかった。
