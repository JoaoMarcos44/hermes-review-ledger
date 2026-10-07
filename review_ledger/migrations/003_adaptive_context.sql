INSERT INTO ledger_meta VALUES ('schema_lineage','adaptive-v15');
CREATE TABLE context_manifests (
 id TEXT PRIMARY KEY, repository_id INTEGER NOT NULL, run_id TEXT NOT NULL,
 snapshot_key TEXT NOT NULL, protocol_version TEXT NOT NULL, protocol_hash TEXT NOT NULL,
 policy_version TEXT NOT NULL, selections_json TEXT NOT NULL, request_fingerprint TEXT NOT NULL,
 content_digest TEXT NOT NULL, delivery_receipt TEXT NOT NULL, created_at TEXT NOT NULL,
 request_json TEXT NOT NULL,
 FOREIGN KEY(repository_id,run_id) REFERENCES runs(repository_id,id)
);
CREATE INDEX context_run ON context_manifests(repository_id,run_id,created_at);

CREATE TABLE optional_skill_versions (
    id TEXT PRIMARY KEY,
    repository_id INTEGER NOT NULL REFERENCES repositories(id),
    qualified_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version > 0),
    content_digest TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    instructions TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
    created_at TEXT NOT NULL,
    UNIQUE(repository_id,qualified_id,version),
    UNIQUE(repository_id,qualified_id,content_digest),
    UNIQUE(repository_id,id)
);
CREATE TABLE optional_skill_resources (
    repository_id INTEGER NOT NULL,
    version_id TEXT NOT NULL,
    path TEXT NOT NULL,
    content TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    PRIMARY KEY(version_id,path),
    FOREIGN KEY(repository_id,version_id) REFERENCES optional_skill_versions(repository_id,id)
);
CREATE INDEX optional_skills_eligible ON optional_skill_versions(repository_id,enabled,qualified_id,version);

ALTER TABLE lesson_uses ADD COLUMN contribution TEXT NOT NULL DEFAULT 'unknown' CHECK(contribution IN ('useful','redundant','unknown'));
ALTER TABLE lesson_uses ADD COLUMN feedback_applicability TEXT NOT NULL DEFAULT 'unknown' CHECK(feedback_applicability IN ('applicable','inapplicable','unknown'));
ALTER TABLE lesson_uses ADD COLUMN supporting_observation_ids_json TEXT NOT NULL DEFAULT '[]';
CREATE TABLE improvement_proposals (
 id TEXT PRIMARY KEY, repository_id INTEGER NOT NULL, run_id TEXT NOT NULL,
 target_version_id TEXT NOT NULL, candidate_version_id TEXT,
 detector_outcome_id TEXT, status TEXT NOT NULL CHECK(status IN ('review_needed','proposed','applied','rejected')),
 changes_json TEXT NOT NULL, reason TEXT NOT NULL, expected_benefit TEXT NOT NULL,
 evaluation_references_json TEXT NOT NULL, created_at TEXT NOT NULL,
 FOREIGN KEY(repository_id,run_id) REFERENCES runs(repository_id,id),
 FOREIGN KEY(repository_id,target_version_id) REFERENCES lesson_versions(repository_id,id),
 FOREIGN KEY(repository_id,candidate_version_id) REFERENCES lesson_versions(repository_id,id),
 FOREIGN KEY(repository_id,detector_outcome_id) REFERENCES lesson_uses(repository_id,id),
 UNIQUE(repository_id,id), UNIQUE(candidate_version_id), UNIQUE(detector_outcome_id)
);
CREATE TABLE improvement_outcomes (
 repository_id INTEGER NOT NULL, improvement_id TEXT NOT NULL, outcome_id TEXT NOT NULL,
 PRIMARY KEY(improvement_id,outcome_id),
 FOREIGN KEY(repository_id,improvement_id) REFERENCES improvement_proposals(repository_id,id),
 FOREIGN KEY(repository_id,outcome_id) REFERENCES lesson_uses(repository_id,id)
);
CREATE INDEX improvement_scope_run ON improvement_proposals(repository_id,run_id,created_at,id);
CREATE INDEX improvement_scope_target ON improvement_proposals(repository_id,target_version_id,created_at,id);
CREATE TABLE improvement_aggregates (
 repository_id INTEGER NOT NULL, version_id TEXT NOT NULL, summary_json TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(repository_id,version_id),
 FOREIGN KEY(repository_id,version_id) REFERENCES lesson_versions(repository_id,id)
);
CREATE INDEX lesson_outcome_window ON lesson_uses(repository_id,version_id,updated_at,id);

CREATE TABLE usage_events (
 event_id TEXT PRIMARY KEY,
 repository_id INTEGER NOT NULL REFERENCES repositories(id),
 run_id TEXT,
 tool TEXT NOT NULL,
 source TEXT NOT NULL CHECK(source IN ('adapter','fixture','log_inference')),
 created_at TEXT NOT NULL,
 request_chars INTEGER NOT NULL,
 request_bytes INTEGER NOT NULL,
 response_chars INTEGER NOT NULL,
 response_bytes INTEGER NOT NULL,
 response_digest TEXT NOT NULL,
 latency_ms REAL NOT NULL,
 candidate_count INTEGER,
 selected_count INTEGER,
 omitted_count INTEGER,
 duplicate_response INTEGER NOT NULL CHECK(duplicate_response IN (0,1)),
 confirmed_delivery INTEGER NOT NULL CHECK(confirmed_delivery IN (0,1)),
 FOREIGN KEY(repository_id,run_id) REFERENCES runs(repository_id,id)
);
CREATE INDEX usage_run_time ON usage_events(repository_id,run_id,created_at);
CREATE INDEX usage_response_identity ON usage_events(repository_id,run_id,tool,response_digest);
CREATE INDEX usage_retention ON usage_events(created_at);
