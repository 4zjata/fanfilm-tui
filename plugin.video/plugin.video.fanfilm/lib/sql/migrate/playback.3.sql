BEGIN;

ALTER TABLE trakt_playback RENAME COLUMN "xref::dbid" TO "xref::ffid";
ALTER TABLE trakt_playback RENAME COLUMN "dbid" TO "ffid";

-- update db version
UPDATE db SET version = 3;

COMMIT;
