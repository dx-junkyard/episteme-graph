---
id: IK-0360
title: 依存の版に上限が無く、SQLAlchemy 2.1 が `postgresql://` の既定ドライバを psycopg（3 系）に変えたため、psycopg2 しか入れない新規ビルドのイメージが起動時に落ちる（既存の開発スタックは旧版で動き続けるので気づかない）
status: resolved
recorded_at: 2026-09-27
resolved_at: 2026-09-27
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: api-server のコンテナイメージを新規にビルドして起動する（砂場・本番・CI のいずれでも）
  layers: [migrations_db, cycle_verification]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [version, contract]
    governance: [review]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸: `backend/api/requirements.txt` は `sqlalchemy>=2.0,<3` と `psycopg2-binary` を並べるだけで、
    「postgresql:// の既定ドライバは psycopg2」という暗黙の契約を版で固定していない（version）。SQLAlchemy 2.1 は
    その既定を psycopg（3 系）へ変えたため、契約が相手側だけで変わった（contract）。確認: 新規ビルドしたイメージの
    `pip list` は SQLAlchemy 2.1.1 / psycopg2-binary 2.9.13、起動ログは「PostgreSQL not ready: No module named
    'psycopg'」を 10 回繰り返して `sys.exit(1)`。同じ接続文字列を `postgresql+psycopg2://` に変えると起動する。
    開発機の venv は 2.0.49 で通る。統制軸: CI は venv の版で pytest を回し、Dockerfile のビルド・起動を検証する
    経路が無い（review。「新規ビルドが通るか」を見る検証経路の欠落）。処理軸・構造軸: コードの論理・表現の
    問題ではない（none）。
generalization:
  level: general
  general_form: 依存の版に上限を置かず暗黙の既定に頼っていると、相手側の既定変更が新規環境だけを壊し、既存環境は旧版のまま動くので発見が遅れる
pattern: contract-changed-one-side
discovery:
  perspective: [reproduction, external_constraint]
  note: >-
    ペルソナ通し受講テストの砂場（docker overlay・プロジェクト名 episteme-uxsim）を新規ビルドで立ち上げたときに
    起動失敗として再現した。開発スタック（旧版でビルド済み）との差は依存の版だけで、外部（SQLAlchemy）の既定変更から
    逆算した。
resolution:
  perspective: [order_and_budget, guardrail_fix]
  note: >-
    依存の既定に暗黙に頼る箇所を版で固定した（`sqlalchemy>=2.0,<2.1`。psycopg 3 系への乗り換えは接続層と pgvector
    の型登録の実測が要るため第 1 周では選ばない）。再発防止として requirements の契約（psycopg2 しか同梱しない間は
    2.1 未満）を検査するテストを置いた。CI でイメージをビルドして起動する経路の追加は未実施。
  landed_in:
    - backend/api/requirements.txt
    - backend/tests/test_requirements_db_driver_pin.py
    - docs/architecture/persona_enactment_testing_design.md §17
  verification:
    methods: [reproduction_rerun, docker_e2e, guardrail]
    unverified:
      - CI でのイメージビルド・起動検査（経路そのものを足していない）
      - psycopg 3 系での動作（選んでいない案）
      - 本番相当の再構築手順（砂場の overlay でのみ再ビルドした）
related: []
view_of: []
history: []
---

## 課題

新規にビルドした api-server のイメージが、起動時の PostgreSQL 接続で「No module named 'psycopg'」を 10 回繰り返して
終了する。症状は砂場の docker overlay で出たが、原因は砂場固有ではなく、`requirements.txt` の SQLAlchemy に
上限が無いこと。SQLAlchemy 2.1（2026-09 時点の最新）は `postgresql://` の既定ドライバを psycopg2 から psycopg
（3 系）へ変えた。イメージには `psycopg2-binary` しか入っていないため、接続文字列にドライバ名を書かない限り
起動できない。既存の開発スタックは 2.0 系でビルドされたイメージのまま動いているため、この変更は新規ビルドを
行う環境（砂場・本番の再構築・CI でイメージを作る場合）だけで顕在化する。

## 発見の観点

ペルソナ通し受講テストの砂場を設計どおり新規ビルドで立ち上げた（reproduction）。開発スタックとの差分を
依存の版に絞り、SQLAlchemy 側の既定変更という外部制約から逆算した（external_constraint）。

## 解決の観点

requirements に上限（`<2.1`）を置いた（order_and_budget: 依存の版という外部の変化を、こちらの契約の範囲に固定する）。
砂場の overlay からドライバ明示の一時対処を外し、イメージを再ビルドして起動を確かめた（reproduction_rerun / docker_e2e。
イメージ内は SQLAlchemy 2.0 系・接続文字列はドライバ指定なし）。再発防止のテスト（guardrail_fix・従）は
「psycopg2 しか同梱しない間は 2.1 未満」の契約だけを見る。psycopg 3 系へ乗り換える案は実測が要るため選ばなかった。
CI に「イメージをビルドして起動する」経路を足すことは未実施（`unverified` に残す）。

## 一般化

依存の既定値に暗黙に頼る箇所（DB ドライバ・JSON シリアライザ・HTTP クライアントの既定タイムアウト等）は、
相手側の既定変更が新規環境だけを壊す。既存環境が動き続けるため「壊れていない」と誤認しやすい。型は
`contract-changed-one-side`。検証経路の欠落（`guardrail-does-not-cover-new-path`）を従に持つ。
