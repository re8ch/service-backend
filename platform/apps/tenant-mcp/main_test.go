package main

import (
	"database/sql"
	"path/filepath"
	"testing"
)

func testServer(t *testing.T) *server {
	t.Helper()
	s, err := open(filepath.Join(t.TempDir(), "skills.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { s.db.Close() })
	return s
}

func TestTenantDraftReviewAndIsolation(t *testing.T) {
	s := testServer(t)
	draft, err := s.submit(request{Tenant: "acme", Subject: "owner", Name: "custom-order", Description: "Create custom order", Workflow: "Plan, then call approved tools."}, false)
	if err != nil {
		t.Fatal(err)
	}
	ref := draft.(skill).Ref
	if result, err := s.search(request{Tenant: "acme", Query: "custom order"}); err != nil || len(result.(map[string]any)["matches"].([]skill)) != 0 {
		t.Fatalf("pending skill was visible: %v %v", result, err)
	}
	if _, err := s.review(request{SkillRef: ref, Subject: "reviewer", Publish: true}); err != nil {
		t.Fatal(err)
	}
	result, err := s.search(request{Tenant: "acme", Query: "custom order"})
	if err != nil || len(result.(map[string]any)["matches"].([]skill)) != 1 {
		t.Fatalf("published skill not visible: %v %v", result, err)
	}
	if _, err := s.load(request{Tenant: "other", SkillRef: ref, Instruction: "Run this order"}); err == nil {
		t.Fatal("cross-tenant load succeeded")
	}
	loaded, err := s.load(request{Tenant: "acme", SkillRef: ref, Instruction: "Run this order"})
	if err != nil || loaded.(map[string]any)["task"] != "Run this order" {
		t.Fatalf("load failed: %v %v", loaded, err)
	}
	if _, err := s.revoke(request{SkillRef: ref, Subject: "reviewer"}); err != nil {
		t.Fatal(err)
	}
	if _, err := s.load(request{Tenant: "acme", SkillRef: ref, Instruction: "Run this order"}); err == nil {
		t.Fatal("revoked skill loaded")
	}
}

func TestPlatformPublicationReplacesOldVersion(t *testing.T) {
	s := testServer(t)
	base := request{Tenant: "admin", TargetTenant: "*", Subject: "platform", Name: "special-contract", Description: "Special contract", Workflow: "Review the contract."}
	first, err := s.submit(base, true)
	if err != nil {
		t.Fatal(err)
	}
	base.Workflow = "Review the revised contract."
	second, err := s.submit(base, true)
	if err != nil {
		t.Fatal(err)
	}
	if second.(skill).Version != 2 {
		t.Fatalf("wrong version: %v", second)
	}
	if _, err := s.load(request{Tenant: "acme", SkillRef: first.(skill).Ref, Instruction: "Review"}); err == nil {
		t.Fatal("old version remained callable")
	}
	if _, err := s.load(request{Tenant: "acme", SkillRef: second.(skill).Ref, Instruction: "Review"}); err != nil {
		t.Fatal(err)
	}
}

func TestPersonalSkillFollowsIdentityAcrossConnections(t *testing.T) {
	s := testServer(t)
	draft, err := s.submit(request{Tenant: "re8ch", Subject: "h-user-id", Name: "userhub-pipeline", Description: "Monitor User Hub release pipeline", Workflow: "Inspect PR, CI, release and approval status.", Visibility: "personal"}, false)
	if err != nil {
		t.Fatal(err)
	}
	ref := draft.(skill).Ref
	if draft.(skill).Visibility != "personal" {
		t.Fatal("personal scope was not recorded")
	}
	if _, err = s.review(request{SkillRef: ref, Subject: "reviewer", Publish: true}); err != nil {
		t.Fatal(err)
	}
	for _, caller := range []request{
		{Tenant: "re8ch", Subject: "another-user", Query: "userhub pipeline", SkillRef: ref, Instruction: "Check release"},
		{Tenant: "other", Subject: "h-user-id", Query: "userhub pipeline", SkillRef: ref, Instruction: "Check release"},
	} {
		result, err := s.search(caller)
		if err != nil || len(result.(map[string]any)["matches"].([]skill)) != 0 {
			t.Fatalf("personal skill leaked through search: %v %v", result, err)
		}
		if _, err := s.load(caller); err == nil {
			t.Fatal("personal skill loaded by wrong identity or tenant")
		}
	}
	owner := request{Tenant: "re8ch", Subject: "h-user-id", Query: "userhub pipeline", SkillRef: ref, Instruction: "Check release"}
	result, err := s.search(owner)
	if err != nil || len(result.(map[string]any)["matches"].([]skill)) != 1 {
		t.Fatalf("owner cannot find personal skill: %v %v", result, err)
	}
	if _, err := s.load(owner); err != nil {
		t.Fatalf("owner cannot load personal skill: %v", err)
	}
	// A new server process reading the same volume retains the identity binding.
	path := filepath.Join(t.TempDir(), "persistent-skills.db")
	persisted, err := open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := persisted.submit(request{Tenant: "re8ch", Subject: "h-user-id", Name: "cross-device", Description: "Cross-device workflow", Workflow: "Read status.", Visibility: "personal"}, false); err != nil {
		t.Fatal(err)
	}
	persisted.db.Close()
	restarted, err := open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer restarted.db.Close()
	items, err := restarted.pending()
	if err != nil || len(items.(map[string]any)["items"].([]skill)) != 1 {
		t.Fatalf("personal draft did not survive restart: %v %v", items, err)
	}
}

func TestDynamicSkillSchemaUpgradesExistingDatabase(t *testing.T) {
	path := filepath.Join(t.TempDir(), "legacy-skills.db")
	legacy, err := sql.Open("sqlite", path)
	if err != nil {
		t.Fatal(err)
	}
	_, err = legacy.Exec(`CREATE TABLE dynamic_skills (ref TEXT PRIMARY KEY, tenant TEXT NOT NULL, name TEXT NOT NULL, version INTEGER NOT NULL, description TEXT NOT NULL, workflow TEXT NOT NULL, status TEXT NOT NULL, subject TEXT NOT NULL, reviewer TEXT, created_at TEXT NOT NULL, reviewed_at TEXT)`)
	legacy.Close()
	if err != nil {
		t.Fatal(err)
	}
	s, err := open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer s.db.Close()
	if _, err := s.submit(request{Tenant: "re8ch", Subject: "h-user-id", Name: "personal-test", Description: "Personal test", Workflow: "Read status.", Visibility: "personal"}, false); err != nil {
		t.Fatalf("schema upgrade failed: %v", err)
	}
}
