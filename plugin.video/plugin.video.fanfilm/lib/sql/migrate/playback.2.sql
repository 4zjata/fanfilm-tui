-- Migrate to playback.db version 2.

BEGIN;

DROP TABLE IF EXISTS trakt_playback;

-- update db version
UPDATE db SET version = 2;

COMMIT;
