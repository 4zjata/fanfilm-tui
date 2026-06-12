BEGIN;

DROP TABLE IF EXISTS trakt_playback;

-- update db version
UPDATE db SET version = 4;

COMMIT;
