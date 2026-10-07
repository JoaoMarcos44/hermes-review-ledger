CREATE TABLE critic_runs (
 id TEXT PRIMARY KEY, repository_id INTEGER NOT NULL, run_id TEXT NOT NULL,
 generation INTEGER NOT NULL, owner_session TEXT NOT NULL, attempt_id TEXT NOT NULL UNIQUE,
 dedup_key TEXT NOT NULL, packet_json TEXT NOT NULL, packet_hash TEXT NOT NULL,
 config_json TEXT NOT NULL, prompt_hash TEXT NOT NULL, contract_hash TEXT NOT NULL,
 execution TEXT NOT NULL CHECK(execution IN ('prepared','running','returned','failed','unknown','abandoned')),
 freshness TEXT NOT NULL CHECK(freshness IN ('current','stale','needs_revalidation')),
 error_code TEXT, metadata_json TEXT NOT NULL DEFAULT '{}',
 created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
 FOREIGN KEY(repository_id,run_id) REFERENCES runs(repository_id,id),
 UNIQUE(repository_id,id), UNIQUE(repository_id,run_id,dedup_key)
);
CREATE TABLE critic_findings (
 repository_id INTEGER NOT NULL, critic_run_id TEXT NOT NULL, finding_id TEXT NOT NULL,
 PRIMARY KEY(critic_run_id,finding_id),
 FOREIGN KEY(repository_id,critic_run_id) REFERENCES critic_runs(repository_id,id),
 FOREIGN KEY(repository_id,finding_id) REFERENCES findings(repository_id,id)
);
CREATE TABLE critic_sources (
 repository_id INTEGER NOT NULL, critic_run_id TEXT NOT NULL, observation_id TEXT NOT NULL,
 PRIMARY KEY(critic_run_id,observation_id),
 FOREIGN KEY(repository_id,critic_run_id) REFERENCES critic_runs(repository_id,id),
 FOREIGN KEY(repository_id,observation_id) REFERENCES observations(repository_id,id)
);
CREATE TABLE critic_input_assessments (
 repository_id INTEGER NOT NULL, critic_run_id TEXT NOT NULL, assessment_id TEXT NOT NULL,
 PRIMARY KEY(critic_run_id,assessment_id),
 FOREIGN KEY(repository_id,critic_run_id) REFERENCES critic_runs(repository_id,id),
 FOREIGN KEY(repository_id,assessment_id) REFERENCES assessments(repository_id,id)
);
CREATE TABLE critic_items (
 id TEXT PRIMARY KEY, repository_id INTEGER NOT NULL, critic_run_id TEXT NOT NULL,
 finding_id TEXT NOT NULL, position TEXT NOT NULL CHECK(position IN ('support','challenge','insufficient_information')),
 response_json TEXT NOT NULL, provenance TEXT NOT NULL CHECK(provenance='critic_generated'),
 FOREIGN KEY(repository_id,critic_run_id) REFERENCES critic_runs(repository_id,id),
 FOREIGN KEY(repository_id,finding_id) REFERENCES findings(repository_id,id),
 UNIQUE(repository_id,id), UNIQUE(critic_run_id,finding_id)
);
CREATE TABLE critic_objections (
 id TEXT PRIMARY KEY, repository_id INTEGER NOT NULL, item_id TEXT NOT NULL, response_json TEXT NOT NULL,
 FOREIGN KEY(repository_id,item_id) REFERENCES critic_items(repository_id,id), UNIQUE(repository_id,id)
);
CREATE TABLE critic_assessments (
 id TEXT PRIMARY KEY, repository_id INTEGER NOT NULL, objection_id TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('pending','supported','refuted','inconclusive','not_applicable')),
 basis TEXT NOT NULL CHECK(basis IN ('none','inspection','behavior')),
 rationale TEXT NOT NULL, limitations TEXT NOT NULL, actor TEXT NOT NULL,
 provenance TEXT NOT NULL CHECK(provenance IN ('agent_reported','local_operator')),
 freshness TEXT NOT NULL CHECK(freshness IN ('current','needs_revalidation','historical')),
 created_at TEXT NOT NULL,
 FOREIGN KEY(repository_id,objection_id) REFERENCES critic_objections(repository_id,id), UNIQUE(repository_id,id)
);
CREATE TABLE critic_assessment_sources (
 repository_id INTEGER NOT NULL, assessment_id TEXT NOT NULL, observation_id TEXT NOT NULL,
 PRIMARY KEY(assessment_id,observation_id),
 FOREIGN KEY(repository_id,assessment_id) REFERENCES critic_assessments(repository_id,id),
 FOREIGN KEY(repository_id,observation_id) REFERENCES observations(repository_id,id)
);
CREATE TABLE critic_lesson_links (
 repository_id INTEGER NOT NULL, version_id TEXT NOT NULL, assessment_id TEXT NOT NULL,
 PRIMARY KEY(version_id,assessment_id),
 FOREIGN KEY(repository_id,version_id) REFERENCES lesson_versions(repository_id,id),
 FOREIGN KEY(repository_id,assessment_id) REFERENCES critic_assessments(repository_id,id)
);
CREATE INDEX critic_run_history ON critic_runs(repository_id,run_id,created_at,id);
