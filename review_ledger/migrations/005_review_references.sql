CREATE TABLE external_review_references (
 id TEXT PRIMARY KEY, repository_id INTEGER NOT NULL, review_id TEXT NOT NULL,
 run_id TEXT NOT NULL, finding_id TEXT NOT NULL,
 provider TEXT NOT NULL CHECK(provider='github'),
 event_type TEXT NOT NULL CHECK(event_type IN ('review','issue_comment','review_comment')),
 external_id TEXT NOT NULL, url TEXT NOT NULL, source_revision TEXT,
 origin_at TEXT, source_updated_at TEXT, captured_at TEXT NOT NULL,
 body TEXT NOT NULL CHECK(length(body)<=8000),
 body_chars INTEGER NOT NULL CHECK(body_chars>=0 AND body_chars<=8000),
 body_bytes INTEGER NOT NULL CHECK(body_bytes>=0 AND body_bytes<=32000),
 content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
 version INTEGER NOT NULL CHECK(version>0),
 provenance TEXT NOT NULL CHECK(provenance='agent_reported'),
 valid INTEGER NOT NULL DEFAULT 1 CHECK(valid IN (0,1)),
 invalid_reason TEXT, invalidated_at TEXT,
 CHECK((valid=1 AND invalid_reason IS NULL AND invalidated_at IS NULL) OR
       (valid=0 AND invalid_reason IS NOT NULL AND invalidated_at IS NOT NULL)),
 FOREIGN KEY(repository_id,review_id) REFERENCES reviews(repository_id,id),
 FOREIGN KEY(repository_id,run_id) REFERENCES runs(repository_id,id),
 FOREIGN KEY(repository_id,finding_id) REFERENCES findings(repository_id,id),
 UNIQUE(repository_id,id),
 UNIQUE(repository_id,review_id,finding_id,provider,event_type,external_id,version),
 UNIQUE(repository_id,review_id,finding_id,provider,event_type,external_id,content_sha256)
);
CREATE INDEX external_reference_review ON external_review_references(repository_id,review_id,captured_at,id);
CREATE INDEX external_reference_run ON external_review_references(repository_id,run_id,id);
