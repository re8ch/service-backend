package main

import (
	"context"
	"encoding/json"
	"testing"
	"time"
)

func TestReadyCollaborationScopesAreBoundToCaller(t *testing.T) {
	s := testServer(t)
	scopes, _ := json.Marshal([]string{"mail.read", "mail.send"})
	_, err := s.db.Exec(`INSERT INTO collaboration_connections(ref,tenant,subject,request_hash,scopes,state,expires) VALUES(?,?,?,?,?,'Ready',?)`,
		"collaboration://0123456789abcdef0123456789abcdef", "re8ch", "user-1", "hash", string(scopes), time.Now().Add(time.Hour).Unix())
	if err != nil {
		t.Fatal(err)
	}
	got, err := s.readyCollaborationScopes(context.Background(), identity{Tenant: "re8ch", Subject: "user-1"}, "collaboration://0123456789abcdef0123456789abcdef")
	if err != nil || len(got) != 2 || got[0] != "mail.read" || got[1] != "mail.send" {
		t.Fatalf("unexpected scopes: %#v, %v", got, err)
	}
	if _, err = s.readyCollaborationScopes(context.Background(), identity{Tenant: "re8ch", Subject: "other"}, "collaboration://0123456789abcdef0123456789abcdef"); err == nil {
		t.Fatal("cross-subject binding was accepted")
	}
}

func TestBoundProviderArgumentsRejectBindingOverride(t *testing.T) {
	if _, err := boundProviderArguments("collaboration://one", map[string]any{"connectionRef": "collaboration://other"}); err == nil {
		t.Fatal("conflicting provider binding was accepted")
	}
	got, err := boundProviderArguments("collaboration://one", map[string]any{"subject": "test"})
	if err != nil || got["connectionRef"] != "collaboration://one" || got["subject"] != "test" {
		t.Fatalf("binding was not injected safely: %#v, %v", got, err)
	}
}
