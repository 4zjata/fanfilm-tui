BEGIN;

ALTER TABLE search ADD COLUMN "key" TEXT NOT NULL DEFAULT '';
UPDATE search SET key = LOWER(query);

-- update db version
UPDATE db SET version = 1;

COMMIT;
