package main

import (
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
