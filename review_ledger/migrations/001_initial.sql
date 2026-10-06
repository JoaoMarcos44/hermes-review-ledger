CREATE TABLE ledger_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE repositories (
 id INTEGER PRIMARY KEY, node_id TEXT, name TEXT NOT NULL COLLATE NOCASE UNIQUE,
 authorized_scope TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE reviews (
 id TEXT PRIMARY KEY, repository_id INTEGER NOT NULL REFERENCES repositories(id),
 number INTEGER NOT NULL CHECK(number > 0), title TEXT NOT NULL, url TEXT NOT NULL,
 UNIQUE(repository_id, number), UNIQUE(repository_id, id)
);
CREATE TABLE runs (
 id TEXT PRIMARY KEY, repository_id INTEGER NOT NULL, review_id TEXT NOT NULL,
 snapshot_key TEXT NOT NULL, head_sha TEXT NOT NULL, base_sha TEXT NOT NULL,
 comparison TEXT NOT NULL, config_json TEXT NOT NULL, skill_version TEXT NOT NULL,
 skill_hash TEXT NOT NULL, snapshot_json TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('active','paused','completed','superseded')),
 owner_session TEXT, generation INTEGER NOT NULL DEFAULT 1 CHECK(generation > 0),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
 FOREIGN KEY(repository_id,review_id) REFERENCES reviews(repository_id,id),
 UNIQUE(repository_id,id)
);
CREATE UNIQUE INDEX one_active_comparison ON runs(review_id,snapshot_key) WHERE status IN ('active','paused');
CREATE INDEX run_review ON runs(repository_id,review_id,created_at);
CREATE TABLE observations (
 id TEXT PRIMARY KEY, repository_id INTEGER NOT NULL, run_id TEXT NOT NULL,
 provenance TEXT NOT NULL CHECK(provenance='agent_reported'), kind TEXT NOT NULL,
 outcome TEXT NOT NULL, summary TEXT NOT NULL, details TEXT NOT NULL, limitations TEXT NOT NULL,
 environment TEXT, command_text TEXT, reproduction_patch_sha TEXT, artifact_id TEXT,
 valid INTEGER NOT NULL DEFAULT 1 CHECK(valid IN (0,1)), invalid_reason TEXT,
 created_at TEXT NOT NULL,
 FOREIGN KEY(repository_id,run_id) REFERENCES runs(repository_id,id), UNIQUE(repository_id,id)
);
CREATE INDEX observation_run ON observations(repository_id,run_id,id);
CREATE TABLE findings (
 id TEXT PRIMARY KEY, repository_id INTEGER NOT NULL, origin_run_id TEXT NOT NULL,
 claim TEXT NOT NULL, created_at TEXT NOT NULL,
 FOREIGN KEY(repository_id,origin_run_id) REFERENCES runs(repository_id,id), UNIQUE(repository_id,id)
);
CREATE TABLE assessments (
 id TEXT PRIMARY KEY, repository_id INTEGER NOT NULL, finding_id TEXT NOT NULL, run_id TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('unverified','supported','refuted','inconclusive','not_applicable')),
 freshness TEXT NOT NULL CHECK(freshness IN ('current','needs_revalidation','historical')),
 basis TEXT NOT NULL, rationale TEXT NOT NULL, limitations TEXT NOT NULL, resolution_json TEXT,
 created_at TEXT NOT NULL,
 FOREIGN KEY(repository_id,finding_id) REFERENCES findings(repository_id,id),
 FOREIGN KEY(repository_id,run_id) REFERENCES runs(repository_id,id), UNIQUE(repository_id,id)
);
CREATE INDEX assessment_run ON assessments(repository_id,run_id,created_at);
CREATE TABLE assessment_sources (
 repository_id INTEGER NOT NULL, assessment_id TEXT NOT NULL, observation_id TEXT NOT NULL,
 relation TEXT NOT NULL CHECK(relation IN ('supports','contradicts')),
 PRIMARY KEY(assessment_id,observation_id),
 FOREIGN KEY(repository_id,assessment_id) REFERENCES assessments(repository_id,id),
 FOREIGN KEY(repository_id,observation_id) REFERENCES observations(repository_id,id)
);
CREATE TABLE lesson_versions (
 id TEXT PRIMARY KEY, repository_id INTEGER NOT NULL REFERENCES repositories(id), lesson_id TEXT NOT NULL,
 version INTEGER NOT NULL CHECK(version > 0), previous_id TEXT,
 question TEXT NOT NULL, conditions_json TEXT NOT NULL, exclusions_json TEXT NOT NULL,
 verification TEXT NOT NULL, tags_json TEXT NOT NULL, symbols_json TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('candidate','active','suspended','restricted','retired')),
 reason TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, approved_at TEXT,
 UNIQUE(repository_id,id), UNIQUE(lesson_id,version),
 FOREIGN KEY(repository_id,previous_id) REFERENCES lesson_versions(repository_id,id)
);
CREATE INDEX lesson_scope ON lesson_versions(repository_id,state,id);
CREATE TABLE lesson_sources (
 repository_id INTEGER NOT NULL, version_id TEXT NOT NULL, observation_id TEXT NOT NULL,
 relation TEXT NOT NULL CHECK(relation IN ('supports','contradicts')),
 PRIMARY KEY(version_id,observation_id),
 FOREIGN KEY(repository_id,version_id) REFERENCES lesson_versions(repository_id,id),
 FOREIGN KEY(repository_id,observation_id) REFERENCES observations(repository_id,id)
);
CREATE TABLE lesson_uses (
 id TEXT PRIMARY KEY, repository_id INTEGER NOT NULL, version_id TEXT NOT NULL, run_id TEXT NOT NULL,
 applicability TEXT NOT NULL, usefulness TEXT, behavioral_result TEXT, execution_block TEXT,
 explanation TEXT NOT NULL, result_explanation TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(version_id,run_id), UNIQUE(repository_id,id),
 FOREIGN KEY(repository_id,version_id) REFERENCES lesson_versions(repository_id,id),
 FOREIGN KEY(repository_id,run_id) REFERENCES runs(repository_id,id)
);
CREATE TABLE audit_events (
 id TEXT PRIMARY KEY, repository_id INTEGER NOT NULL REFERENCES repositories(id),
 entity_id TEXT NOT NULL, action TEXT NOT NULL, actor TEXT NOT NULL, detail_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX audit_scope ON audit_events(repository_id,entity_id,created_at);
CREATE TABLE idempotency (
 repository_id INTEGER NOT NULL REFERENCES repositories(id), operation TEXT NOT NULL, scope TEXT NOT NULL,
 request_key TEXT NOT NULL, payload_hash TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(repository_id,operation,scope,request_key)
);
