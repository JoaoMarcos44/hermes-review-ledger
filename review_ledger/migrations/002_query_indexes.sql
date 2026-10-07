CREATE INDEX assessment_finding_run ON assessments(repository_id,finding_id,run_id);
CREATE INDEX assessment_run_finding ON assessments(repository_id,run_id,finding_id);
CREATE INDEX finding_origin_run ON findings(repository_id,origin_run_id,id);
CREATE INDEX lesson_recall_order ON lesson_versions(repository_id,state,lesson_id,version,id);
