-- Migration 008: lecture display_text column (Issue #88)
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS display_text TEXT;
