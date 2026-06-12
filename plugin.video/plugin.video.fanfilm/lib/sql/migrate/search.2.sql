BEGIN;

-- remove duplicates (the DB could have no unioque index)
DELETE FROM search
  WHERE rowid > (
    SELECT MIN(rowid) FROM search s2
    WHERE search.search_type = s2.search_type AND search.key = s2.key AND search.options = s2.options
  );


-- backup old table
ALTER TABLE search RENAME TO old_search;

-- create new table
CREATE TABLE IF NOT EXISTS "search" (
	"id" INTEGER PRIMARY KEY DEFAULT NULL,
	"search_name" TEXT NOT NULL,
	"search_type" TEXT NOT NULL,
	"query" TEXT NOT NULL,
	"updated_at" INTEGER NOT NULL,
	"last_used_at" INTEGER NOT NULL DEFAULT 0,
	"options" TEXT NOT NULL DEFAULT '{}',
	"key" TEXT NOT NULL DEFAULT '',
	UNIQUE("search_name", "key", "options")
);

-- add unique index
CREATE UNIQUE INDEX IF NOT EXISTS search_unique_index ON search(search_name, key, options);

-- copy data to new table (and fill new column `search_name` from `search_type`)
INSERT INTO search SELECT id, search_type, search_type, query, updated_at, last_used_at, options, key FROM old_search;

-- drop old table
DROP TABLE old_search;

-- update db version
UPDATE db SET version = 2;

COMMIT;
