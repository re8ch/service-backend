package resultproxy

import "testing"

func TestSummaryRejectsUnboundedTextAndFalsePass(t *testing.T) {
	e := Envelope{RunID: "run", Target: "target", SchemaSHA256: "digest", Summary: &Summary{
		Version: 1, RunID: "run", Target: "target", SchemaSHA256: "digest", Mode: "read", Engine: "schemathesis", EngineVersion: "4.29.1", Status: "failed", ExitCode: 1,
		Counts:    map[string]uint32{"generated": 0, "withFailures": 0, "withoutChecks": 0, "uniqueFailures": 0, "operationsTotal": 0, "operationsSelected": 0, "operationsTested": 0, "operationsErrored": 0, "operationsSkipped": 0},
		Lifecycle: map[string]string{"setup": "not_run", "test": "failed", "cleanup": "not_run", "reversal": "not_run"}}}
	if !validSummary(e) {
		t.Fatal("valid failed report denied")
	}
	e.Summary.Lifecycle["test"] = "response body"
	if validSummary(e) {
		t.Fatal("free text accepted")
	}
	e.Summary.Lifecycle["test"] = "passed"
	e.Passed = true
	e.Summary.Status = "passed"
	e.Summary.ExitCode = 0
	if validSummary(e) {
		t.Fatal("zero-operation pass accepted")
	}
	e.Summary.Counts["generated"] = 1
	e.Summary.Counts["operationsSelected"] = 1
	e.Summary.Counts["operationsTested"] = 1
	e.Operations = 1
	if !validSummary(e) {
		t.Fatal("valid pass denied")
	}
	e.Summary.AuditRefs = []string{"token=private"}
	if validSummary(e) {
		t.Fatal("arbitrary audit text accepted")
	}
}
