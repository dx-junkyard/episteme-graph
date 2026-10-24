-- Migration 091: 承認・共有レイヤー(C層) — 説明の course_id を「出所」として任意にする
--
-- 課題: IK-0891（第 25 周 面 A 第 2 周）。説明の権限・範囲は IK-0861 の是正で document 単位に
--   移った（course_id は範囲ではなく出所）のに、component_explanations.course_id は
--   migration 021 の NOT NULL + REFERENCES learning_courses(id) のままだった。コース外の
--   パイプライン部品（course_id が空）への独自解釈の追加が FK 違反で 500 になり、標準の説明の
--   遅延生成も同じ理由で黙って失敗していた。
-- このファイルが DDL の正本。適用は backend/core/migrations.py のランナーが起動時に行う
-- （冪等・毎起動・番号順に全ファイルを再実行）。
--
-- 何をするか（既存行の値は変えない）:
--   1. course_id の NOT NULL を外す（NULL = 出所のコースなし）。
--   2. course_id の FK を ON DELETE CASCADE から ON DELETE SET NULL に張り替える
--      （出所のコースが消えても説明・承認・引用の履歴は残す — P4）。
--   集計ビュー component_explanation_endorsement_summary は course_id を参照しないので
--   再作成は要らない。
--
-- 設計上の要点:
--   - 行を消さない。本ファイルに DELETE 文は無い。シード行も入れない。
--   - SQL 本文は psql で読める plain SQL で書く。

ALTER TABLE component_explanations ALTER COLUMN course_id DROP NOT NULL;

DO $$
DECLARE
    fk_name TEXT;
BEGIN
    -- course_id 列に掛かる learning_courses への FK のうち、ON DELETE SET NULL でないものを外す。
    FOR fk_name IN
        SELECT con.conname
          FROM pg_constraint con
          JOIN pg_attribute att
            ON att.attrelid = con.conrelid AND att.attnum = ANY (con.conkey)
         WHERE con.conrelid = 'component_explanations'::regclass
           AND con.contype = 'f'
           AND att.attname = 'course_id'
           AND con.confdeltype <> 'n'
    LOOP
        EXECUTE format('ALTER TABLE component_explanations DROP CONSTRAINT %I', fk_name);
    END LOOP;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint con
          JOIN pg_attribute att
            ON att.attrelid = con.conrelid AND att.attnum = ANY (con.conkey)
         WHERE con.conrelid = 'component_explanations'::regclass
           AND con.contype = 'f'
           AND att.attname = 'course_id'
    ) THEN
        ALTER TABLE component_explanations
            ADD CONSTRAINT component_explanations_course_id_fkey
            FOREIGN KEY (course_id) REFERENCES learning_courses(id) ON DELETE SET NULL;
    END IF;
END
$$;
