BEGIN;

ALTER TABLE directory_content RENAME COLUMN "xref::dbid" TO "xref::ffid";

-- update db version
UPDATE db SET version = 6;

COMMIT;
