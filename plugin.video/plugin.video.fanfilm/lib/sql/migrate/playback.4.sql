BEGIN;

-- update playback db from v3 to v4 (new name and new columns)
ALTER TABLE trakt_playback RENAME TO scrobble_playback;
ALTER TABLE scrobble_playback ADD COLUMN service_id INTEGER DEFAULT NULL;
ALTER TABLE scrobble_playback ADD COLUMN details_level INTEGER DEFAULT 0;

-- create service table and add base (trakt) service
CREATE TABLE IF NOT EXISTS scrobble_service ("id" INTEGER PRIMARY KEY DEFAULT NULL, "name" TEXT UNIQUE NOT NULL);
INSERT INTO scrobble_service ("name") VALUES ('trakt');

-- set service_id for existing records (only "trakt" exists)
UPDATE scrobble_playback SET service_id = (SELECT id FROM scrobble_service WHERE name = 'trakt');
-- set details_level to 1 (degraded)  for existing records
UPDATE scrobble_playback SET details_level = 1;

-- update db version
UPDATE db SET version = 4;

COMMIT;
