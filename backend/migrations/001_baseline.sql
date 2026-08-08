-- Migration 001: baseline schema stamp.
-- All tables are created by init_db's CREATE TABLE IF NOT EXISTS
-- statements. This migration exists solely to record version 1 so that
-- future incremental migrations (002_*, 003_*, ...) are applied in
-- version order. No schema changes are made here.
SELECT 1;
