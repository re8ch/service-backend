package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func collaborationFixture(t *testing.T, handler http.HandlerFunc) (*collaborationDataClient, *httptest.Server) {
	t.Helper()
	srv := httptest.NewServer(handler)
	t.Cleanup(srv.Close)
	c, err := newCollaborationDataClient(identity{Tenant: "team", Subject: "alice"}, collaborationDataAccess{Tenant: "team", Subject: "alice", AccountID: "acct", CredentialRef: "credential://stalwart/team/opaque", Bearer: "provider-secret", Scopes: []string{"mail.read", "calendar.read", "calendar.respond", "files.read"}, FileRoots: []string{"/dav/file/alice/"}, ExpiresAt: time.Now().Add(time.Hour)}, srv.URL)
	if err != nil {
		t.Fatal(err)
	}
	return c, srv
}
func collaborationSessionFixture(w http.ResponseWriter, r *http.Request) bool {
	if r.URL.Path != "/jmap/session" {
		return false
	}
	writeJSON(w, 200, map[string]any{"apiUrl": "/jmap/", "downloadUrl": "/jmap/download/{accountId}/{blobId}/{name}", "capabilities": map[string]any{collaborationMail: map[string]any{}, collaborationCalendars: map[string]any{}}, "accounts": map[string]any{"acct": map[string]any{"accountCapabilities": map[string]any{collaborationMail: map[string]any{}, collaborationCalendars: map[string]any{}}}}})
	return true
}
func collaborationDecodeCall(t *testing.T, r *http.Request) (string, map[string]any) {
	t.Helper()
	var input struct {
		Calls [][]json.RawMessage `json:"methodCalls"`
	}
	if err := json.NewDecoder(r.Body).Decode(&input); err != nil || len(input.Calls) != 1 {
		t.Fatalf("invalid call: %v", err)
	}
	var method string
	var args map[string]any
	json.Unmarshal(input.Calls[0][0], &method)
	json.Unmarshal(input.Calls[0][1], &args)
	if args["accountId"] != "acct" {
		t.Errorf("unbound account: %v", args["accountId"])
	}
	return method, args
}
func collaborationMethodResult(w http.ResponseWriter, method string, result map[string]any) {
	result["accountId"] = "acct"
	writeJSON(w, 200, map[string]any{"methodResponses": []any{[]any{method, result, "0"}}})
}

func TestCollaborationConstructorOwnerExpiryAndScope(t *testing.T) {
	id := identity{Tenant: "team", Subject: "alice"}
	base := collaborationDataAccess{Tenant: "team", Subject: "alice", AccountID: "acct", CredentialRef: "opaque", Bearer: "secret", ExpiresAt: time.Now().Add(time.Hour)}
	for _, test := range []struct {
		name   string
		mutate func(*collaborationDataAccess)
	}{
		{"other user", func(a *collaborationDataAccess) { a.Subject = "bob" }},
		{"other tenant", func(a *collaborationDataAccess) { a.Tenant = "other" }},
		{"expired", func(a *collaborationDataAccess) { a.ExpiresAt = time.Now().Add(-time.Second) }},
		{"revoked", func(a *collaborationDataAccess) { a.Revoked = true }},
		{"admin scope", func(a *collaborationDataAccess) { a.Scopes = []string{"sysAccountGet"} }},
		{"both credentials", func(a *collaborationDataAccess) { a.Username = "alice"; a.Password = "secret" }},
		{"foreign file root", func(a *collaborationDataAccess) { a.FileRoots = []string{"https://evil.test/dav/file/alice/"} }},
	} {
		t.Run(test.name, func(t *testing.T) {
			a := base
			test.mutate(&a)
			if _, err := newCollaborationDataClient(id, a, "https://provider.test"); err == nil {
				t.Fatal("invalid grant accepted")
			}
		})
	}
	id.PlatformAdmin = true
	base.Subject = "bob"
	if _, err := newCollaborationDataClient(id, base, "https://provider.test"); err == nil {
		t.Fatal("platform administrator bypassed user isolation")
	}
}

func TestCollaborationScopeCheckedBeforeNetwork(t *testing.T) {
	var calls atomic.Int32
	c, _ := collaborationFixture(t, func(w http.ResponseWriter, r *http.Request) { calls.Add(1); w.WriteHeader(500) })
	c.access.Scopes = []string{"files.read"}
	if _, err := c.MailRead(context.Background(), "email"); err == nil {
		t.Fatal("mail access allowed")
	}
	if _, err := c.CalendarRead(context.Background(), "event", "Etc/UTC"); err == nil {
		t.Fatal("calendar access allowed")
	}
	c.access.Revoked = true
	if _, err := c.FilesRead(context.Background(), "/dav/file/alice/a.txt"); err == nil {
		t.Fatal("revoked access allowed")
	}
	if calls.Load() != 0 {
		t.Fatal("unauthorized request reached provider")
	}
}
func TestCollaborationMailQueryBoundsAndBodyBudget(t *testing.T) {
	c, _ := collaborationFixture(t, func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer provider-secret" {
			t.Error("missing provider identity")
		}
		if collaborationSessionFixture(w, r) {
			return
		}
		method, args := collaborationDecodeCall(t, r)
		switch method {
		case "Email/query":
			if args["limit"] != float64(2) || collaborationMap(args["filter"])["text"] != "invoice" {
				t.Errorf("wrong bounded query: %#v", args)
			}
			collaborationMethodResult(w, method, map[string]any{"ids": []string{"mail-1", "mail-2"}})
		case "Email/get":
			if args["fetchAllBodyValues"] == true {
				if args["maxBodyValueBytes"] != float64(128<<10) {
					t.Error("missing body bound")
				}
				collaborationMethodResult(w, method, map[string]any{"state": "s1", "list": []any{map[string]any{"id": "mail-1", "bodyValues": map[string]any{"1": map[string]any{"value": strings.Repeat("a", 100<<10)}, "2": map[string]any{"value": strings.Repeat("b", 100<<10)}}}}})
			} else {
				collaborationMethodResult(w, method, map[string]any{"state": "s1", "list": []any{map[string]any{"id": "mail-1", "subject": "Invoice", "unexpected": "omit"}, map[string]any{"id": "mail-2"}}})
			}
		default:
			t.Errorf("unexpected method %s", method)
		}
	})
	ctx := context.Background()
	result, err := c.MailQuery(ctx, "invoice", "", 0, 2)
	if err != nil {
		t.Fatal(err)
	}
	if result["mayHaveMore"] != true || result["nextPosition"] != 2 {
		t.Fatalf("pagination: %#v", result)
	}
	if collaborationMap(result["items"].([]any)[0])["unexpected"] != nil {
		t.Fatal("unselected property returned")
	}
	if _, err = c.MailQuery(ctx, "", "", 0, 51); err == nil {
		t.Fatal("limit accepted")
	}
	result, err = c.MailRead(ctx, "mail-1")
	if err != nil {
		t.Fatal(err)
	}
	total := 0
	for _, v := range collaborationMap(collaborationMap(result["items"].([]any)[0])["bodyValues"]) {
		total += len(collaborationString(collaborationMap(v), "value"))
	}
	if total != 128<<10 || result["truncated"] != true {
		t.Fatalf("body budget total=%d truncated=%v", total, result["truncated"])
	}
}
func TestCollaborationJMAPRejectsMissingCapabilitiesAndMethodErrors(t *testing.T) {
	for _, mode := range []string{"capability", "method", "account", "correlation"} {
		t.Run(mode, func(t *testing.T) {
			c, _ := collaborationFixture(t, func(w http.ResponseWriter, r *http.Request) {
				if mode == "capability" {
					writeJSON(w, 200, map[string]any{"apiUrl": "/jmap/", "accounts": map[string]any{"acct": map[string]any{}}})
					return
				}
				if collaborationSessionFixture(w, r) {
					return
				}
				method, _ := collaborationDecodeCall(t, r)
				account, callID := "acct", "0"
				if mode == "method" {
					method = "error"
				}
				if mode == "account" {
					account = "someone-else"
				}
				if mode == "correlation" {
					callID = "unexpected"
				}
				writeJSON(w, 200, map[string]any{"methodResponses": []any{[]any{method, map[string]any{"accountId": account, "description": "provider-secret"}, callID}}})
			})
			_, err := c.Mailboxes(context.Background())
			if err == nil || strings.Contains(err.Error(), "provider-secret") {
				t.Fatalf("error handling: %v", err)
			}
		})
	}
}
func TestCollaborationFilePathAndRedirectIsolation(t *testing.T) {
	var leaks atomic.Int32
	other := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { leaks.Add(1) }))
	defer other.Close()
	c, srv := collaborationFixture(t, func(w http.ResponseWriter, r *http.Request) { http.Redirect(w, r, other.URL, 302) })
	for _, href := range []string{"/dav/file/bob/x", "/dav/file/alice/../bob/x", "/dav/file/alice/%2e%2e/bob/x", "/dav/file/alice/%252e%252e/bob/x", "/dav/file/alice/%5c..%5cx", other.URL + "/dav/file/alice/x", srv.URL + "/dav/file/alice/x?secret=q", "/dav/file/alice2/x"} {
		if _, err := c.DownloadFile(context.Background(), href); err == nil {
			t.Errorf("accepted path %s", href)
		}
	}
	if _, err := c.DownloadFile(context.Background(), "/dav/file/alice/x"); err == nil {
		t.Fatal("redirect accepted")
	}
	if leaks.Load() != 0 {
		t.Fatal("credential-bearing redirect followed")
	}
}
func TestCollaborationDAVListingPartialAndBoundedRead(t *testing.T) {
	var getCalls atomic.Int32
	c, _ := collaborationFixture(t, func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case "PROPFIND":
			if r.Header.Get("Depth") != "1" {
				t.Error("unbounded DAV depth")
			}
			w.Header().Set("Content-Type", "application/xml")
			w.WriteHeader(207)
			fmt.Fprint(w, `<d:multistatus xmlns:d="DAV:"><d:response><d:href>/dav/file/alice/a.txt</d:href><d:propstat><d:status>HTTP/1.1 200 OK</d:status><d:prop><d:displayname>A</d:displayname><d:getcontentlength>5</d:getcontentlength><d:getcontenttype>text/plain</d:getcontenttype></d:prop></d:propstat></d:response><d:response><d:href>/dav/file/bob/private</d:href><d:status>HTTP/1.1 200 OK</d:status></d:response><d:response><d:href>/dav/file/alice/missing</d:href><d:status>HTTP/1.1 404 Not Found</d:status></d:response></d:multistatus>`)
		case "HEAD":
			if strings.HasSuffix(r.URL.Path, "huge") {
				w.Header().Set("Content-Length", "99999999")
			}
		case "GET":
			getCalls.Add(1)
			w.Header().Set("Content-Type", "text/plain; charset=utf-8")
			w.Header().Set("ETag", `"v1"`)
			fmt.Fprint(w, "hello")
		}
	})
	result, err := c.FilesList(context.Background(), "/dav/file/alice/")
	if err != nil {
		t.Fatal(err)
	}
	if len(result["items"].([]any)) != 1 || result["partial"] != true {
		t.Fatalf("listing leaked/skipped errors: %#v", result)
	}
	result, err = c.FilesRead(context.Background(), "/dav/file/alice/a.txt")
	if err != nil || result["text"] != "hello" {
		t.Fatalf("read: %v %#v", err, result)
	}
	if _, err = c.DownloadFile(context.Background(), "/dav/file/alice/huge"); err == nil {
		t.Fatal("metadata size limit ignored")
	}
	if getCalls.Load() != 1 {
		t.Fatal("oversized file downloaded")
	}
}
func TestCollaborationDownloadStreamingLimit(t *testing.T) {
	c, _ := collaborationFixture(t, func(w http.ResponseWriter, r *http.Request) {
		if r.Method == "HEAD" {
			return
		}
		w.Header().Set("Content-Type", "application/octet-stream")
		w.WriteHeader(200)
		w.(http.Flusher).Flush()
		fmt.Fprint(w, strings.Repeat("a", collaborationMaxDownload+1))
	})
	if _, err := c.DownloadFile(context.Background(), "/dav/file/alice/file"); err == nil {
		t.Fatal("chunked download exceeded limit")
	}
}
func TestCollaborationCalendarQueryTimezoneAndWindow(t *testing.T) {
	c, _ := collaborationFixture(t, func(w http.ResponseWriter, r *http.Request) {
		if collaborationSessionFixture(w, r) {
			return
		}
		method, args := collaborationDecodeCall(t, r)
		if method != "CalendarEvent/query" {
			t.Fatalf("unexpected method %s", method)
		}
		f := collaborationMap(args["filter"])
		if f["after"] != "2026-10-30T08:00:00" || args["timeZone"] != "Asia/Shanghai" || args["expandRecurrences"] != true {
			t.Errorf("wrong timezone: %#v", args)
		}
		collaborationMethodResult(w, method, map[string]any{"ids": []string{}})
	})
	if _, err := c.CalendarQuery(context.Background(), "2026-10-30T00:00:00Z", "2026-10-31T00:00:00Z", "Asia/Shanghai", 0, 10); err != nil {
		t.Fatal(err)
	}
	if _, err := c.CalendarQuery(context.Background(), "2026-10-01T00:00:00Z", "2026-12-01T00:00:00Z", "Asia/Shanghai", 0, 10); err == nil {
		t.Fatal("unbounded recurrence window accepted")
	}
}
func TestCollaborationCalendarRSVPPlanApplyAndReadback(t *testing.T) {
	var writes atomic.Int32
	state, status := "s1", "needs-action"
	allow := true
	c, _ := collaborationFixture(t, func(w http.ResponseWriter, r *http.Request) {
		if collaborationSessionFixture(w, r) {
			return
		}
		method, args := collaborationDecodeCall(t, r)
		switch method {
		case "CalendarEvent/get":
			collaborationMethodResult(w, method, map[string]any{"state": state, "list": []any{map[string]any{"id": "event", "calendarIds": map[string]any{"cal": true}, "participants": map[string]any{"a/~": map[string]any{"calendarAddress": "mailto:alice@example.com", "roles": map[string]any{"attendee": true}, "participationStatus": status}, "bob": map[string]any{"calendarAddress": "mailto:bob@example.com", "roles": map[string]any{"attendee": true}, "participationStatus": "accepted"}}}}})
		case "ParticipantIdentity/get":
			collaborationMethodResult(w, method, map[string]any{"list": []any{map[string]any{"id": "alice", "calendarAddress": "mailto:alice@example.com"}}})
		case "Calendar/get":
			collaborationMethodResult(w, method, map[string]any{"list": []any{map[string]any{"id": "cal", "myRights": map[string]any{"mayRSVP": allow}}}})
		case "CalendarEvent/set":
			writes.Add(1)
			if args["ifInState"] != "s1" || args["sendSchedulingMessages"] != true {
				t.Errorf("unsafe mutation: %#v", args)
			}
			patch := collaborationMap(collaborationMap(args["update"])["event"])
			if len(patch) != 2 || patch["participants/a~1~0/participationStatus"] != "accepted" || patch["participants/a~1~0/expectReply"] != false {
				t.Errorf("unexpected attendee mutation: %#v", patch)
			}
			state, status = "s2", "accepted"
			collaborationMethodResult(w, method, map[string]any{"newState": state, "updated": map[string]any{"event": nil}})
		default:
			t.Errorf("unexpected method %s", method)
		}
	})
	ctx := context.Background()
	plan, err := c.CalendarResponsePlan(ctx, "event", "accepted")
	if err != nil {
		t.Fatal(err)
	}
	if _, err = c.CalendarRespond(ctx, "event", "declined", "s1", plan["planHash"].(string)); err == nil {
		t.Fatal("changed plan accepted")
	}
	result, err := c.CalendarRespond(ctx, "event", "accepted", "s1", plan["planHash"].(string))
	if err != nil {
		t.Fatal(err)
	}
	if result["changed"] != true || result["deliveryVerified"] != false || writes.Load() != 1 {
		t.Fatalf("write result: %#v", result)
	}
	if _, err = c.CalendarRespond(ctx, "event", "accepted", "s1", plan["planHash"].(string)); err == nil {
		t.Fatal("stale plan replay allowed")
	}
	plan, err = c.CalendarResponsePlan(ctx, "event", "accepted")
	if err != nil {
		t.Fatal(err)
	}
	result, err = c.CalendarRespond(ctx, "event", "accepted", "s2", plan["planHash"].(string))
	if err != nil || result["changed"] != false || writes.Load() != 1 {
		t.Fatal("same RSVP resent")
	}
	allow = false
	if _, err = c.CalendarResponsePlan(ctx, "event", "declined"); err == nil {
		t.Fatal("RSVP rights ignored")
	}
}

func TestCollaborationFileDiscoveryUsesNativePrincipalAndOwnCollection(t *testing.T) {
	c, _ := collaborationFixture(t, func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/jmap/session" {
			writeJSON(w, 200, map[string]any{"apiUrl": "/jmap/", "capabilities": map[string]any{collaborationFiles: map[string]any{}}, "accounts": map[string]any{"acct": map[string]any{"accountCapabilities": map[string]any{collaborationFiles: map[string]any{}}}}})
			return
		}
		if r.URL.Path != "/dav/file/" || r.Method != "PROPFIND" || r.Header.Get("Depth") != "1" {
			t.Fatalf("unexpected discovery path: %s %s", r.Method, r.URL.Path)
		}
		w.WriteHeader(207)
		fmt.Fprint(w, `<d:multistatus xmlns:d="DAV:"><d:response><d:href>/dav/file/</d:href><d:propstat><d:status>HTTP/1.1 200 OK</d:status><d:prop><d:current-user-principal><d:href>/dav/pal/alice%40example.com/</d:href></d:current-user-principal></d:prop></d:propstat></d:response><d:response><d:href>/dav/file/alice%40example.com/</d:href><d:propstat><d:status>HTTP/1.1 200 OK</d:status><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat></d:response><d:response><d:href>/dav/file/bob%40example.com/</d:href><d:propstat><d:status>HTTP/1.1 200 OK</d:status><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat></d:response></d:multistatus>`)
	})
	c.access.FileRoots = nil
	result, err := c.FilesList(context.Background(), "")
	if err != nil {
		t.Fatal(err)
	}
	items := result["items"].([]any)
	if len(items) != 1 || collaborationMap(items[0])["href"] != "/dav/file/alice%40example.com/" {
		t.Fatalf("discovery included an unapproved shared root: %#v", items)
	}
	if _, err = c.fileURL("/dav/file/bob%40example.com/private"); err == nil {
		t.Fatal("shared account gained automatic access")
	}
}
