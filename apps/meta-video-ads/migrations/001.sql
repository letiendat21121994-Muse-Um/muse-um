CREATE TABLE workspace(id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, expires_at INTEGER NOT NULL);
CREATE TABLE batch(
  id TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES workspace(id), name TEXT NOT NULL,
  context TEXT NOT NULL DEFAULT '{}', config TEXT NOT NULL, created_at TEXT NOT NULL,
  published_at TEXT, campaign_ref TEXT UNIQUE, simulate_failure INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE video(
  id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batch(id), filename TEXT NOT NULL, mime TEXT NOT NULL,
  status TEXT NOT NULL, created_at TEXT NOT NULL, error TEXT, duration REAL, thumb INTEGER NOT NULL DEFAULT 0,
  sample TEXT, overrides TEXT NOT NULL DEFAULT '{}', included INTEGER NOT NULL DEFAULT 1,
  group_key TEXT NOT NULL DEFAULT '', destination TEXT NOT NULL DEFAULT '',
  tracking TEXT NOT NULL DEFAULT '', use_ai INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE analysis(
  video_id TEXT PRIMARY KEY REFERENCES video(id) ON DELETE CASCADE, data TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE variant(
  id TEXT PRIMARY KEY, video_id TEXT NOT NULL REFERENCES analysis(video_id) ON DELETE CASCADE,
  data TEXT NOT NULL, selected INTEGER NOT NULL DEFAULT 0, edited INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, UNIQUE(id,video_id)
);
CREATE TABLE ad_set(
  id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batch(id), group_key TEXT NOT NULL, ref TEXT NOT NULL UNIQUE,
  UNIQUE(batch_id,group_key)
);
CREATE TABLE ad(
  id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batch(id), video_id TEXT NOT NULL REFERENCES video(id),
  variant_id TEXT NOT NULL, group_key TEXT NOT NULL, name TEXT NOT NULL, snapshot TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'queued', media_ref TEXT, creative_ref TEXT, ad_ref TEXT UNIQUE,
  error TEXT, simulated_once INTEGER NOT NULL DEFAULT 0, published_at TEXT,
  UNIQUE(batch_id,variant_id), FOREIGN KEY(variant_id,video_id) REFERENCES variant(id,video_id)
);
CREATE TABLE media_mapping(
  video_id TEXT PRIMARY KEY REFERENCES video(id), ref TEXT NOT NULL UNIQUE
);
CREATE TABLE report_sync(batch_id TEXT PRIMARY KEY REFERENCES batch(id), synced_at TEXT NOT NULL);
CREATE TABLE snapshot(
  ad_id TEXT NOT NULL REFERENCES ad(id), day TEXT NOT NULL, values_json TEXT NOT NULL,
  PRIMARY KEY(ad_id,day)
);
CREATE TABLE job(
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, target TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
  next_at INTEGER NOT NULL, lease TEXT, lease_until INTEGER, error TEXT,
  UNIQUE(kind,target)
);
CREATE INDEX job_ready ON job(status,next_at,lease_until);
CREATE INDEX batches_owner ON batch(owner,created_at);
CREATE INDEX videos_batch ON video(batch_id,created_at);
CREATE TABLE activity(
  id INTEGER PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batch(id), message TEXT NOT NULL, created_at TEXT NOT NULL
);
