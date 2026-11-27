package main

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestValidatedStalwartToolEndpointIsPinnedToShadowService(t *testing.T) {
	if got, err := validatedStalwartToolEndpoint(defaultStalwartToolEndpoint); err != nil || got != defaultStalwartToolEndpoint {
		t.Fatalf("default endpoint rejected: %q, %v", got, err)
	}
	for _, endpoint := range []string{
		"https://attacker.example/mcp",
		"http://mcp-stalwart-agent-tools-shadow.toolhive-system.svc.cluster.local:8088/other",
		defaultStalwartToolEndpoint + "?redirect=http://attacker.example",
	} {
		if _, err := validatedStalwartToolEndpoint(endpoint); err == nil {
			t.Fatalf("unsafe endpoint accepted: %s", endpoint)
		}
	}
}

func TestStalwartInvocationRequestBindsCallerConnectionAndScopes(t *testing.T) {
	id := identity{Subject: "user-1", Token: "subject-token", Tenant: "runtime", OrganizationRef: "org-1"}
	request := stalwartInvocationRequest(id, "connection://org-1/mail", []string{"mail.send"})
	if request.Subject != id.Subject || request.SubjectToken != id.Token || request.ActiveOrgRef != id.OrganizationRef || request.BindingRef != "connection://org-1/mail" || request.ToolRef != "tool://8iops/stalwart-agent-tools" || request.Audience != "re8ch-tenant-tool" || request.MaxTTL != invocationTokenMaxTTL || len(request.Scopes) != 1 || request.Scopes[0] != "mail.send" {
		t.Fatalf("invocation request is not fully bound: %#v", request)
	}
}

func TestStalwartWriteInputsMatchDownstreamJSONContract(t *testing.T) {
	mail, err := json.Marshal(stalwartMailSendInput{ConnectionRef: "connection://one", Apply: true, From: "sender@example.com", To: []stalwartMailAddress{{Address: "recipient@example.com", Name: "Recipient"}}, Subject: "subject", Text: "body", PlanHash: "plan", IdempotencyKey: "idempotency"})
	if err != nil {
		t.Fatal(err)
	}
	for _, field := range []string{`"connectionRef"`, `"apply"`, `"from"`, `"to"`, `"subject"`, `"text"`, `"planHash"`, `"idempotencyKey"`} {
		if !strings.Contains(string(mail), field) {
			t.Fatalf("mail_send JSON is missing %s: %s", field, mail)
		}
	}
	if !strings.Contains(string(mail), `"address":"recipient@example.com"`) {
		t.Fatalf("mail_send JSON does not match net/mail address decoding: %s", mail)
	}
	response, err := json.Marshal(stalwartRespondInput{ConnectionRef: "connection://one", EventID: "event", Response: "accepted", Apply: true, PlanHash: "plan", IdempotencyKey: "idempotency"})
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(response), "expectedState") {
		t.Fatalf("calendar_respond leaked legacy-only field: %s", response)
	}
}

func TestStalwartRelayToolAnnotationsCoverPublishedSurface(t *testing.T) {
	readOnly := []string{"stalwart_capabilities", "stalwart_mail_query", "stalwart_mail_read", "stalwart_calendar_query", "stalwart_contacts_query", "stalwart_files_list", "stalwart_files_read"}
	for _, name := range readOnly {
		annotations := toolAnnotations(name)
		if !annotations.ReadOnlyHint || *annotations.DestructiveHint || *annotations.OpenWorldHint {
			t.Fatalf("%s has unsafe annotations: %#v", name, annotations)
		}
	}
	for _, name := range []string{"stalwart_mail_send", "stalwart_calendar_respond"} {
		annotations := toolAnnotations(name)
		if annotations.ReadOnlyHint || !*annotations.DestructiveHint || !*annotations.OpenWorldHint {
			t.Fatalf("%s has unsafe annotations: %#v", name, annotations)
		}
	}
}
