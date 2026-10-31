package main

// User-data adapter for the existing self-service runtime. The caller resolves
// a fresh, owner-scoped broker binding before constructing this client. No
// provider credential or arbitrary protocol method is part of a tool argument.
import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"encoding/xml"
	"errors"
	"fmt"
	"io"
	"mime"
	"net/http"
	"net/url"
	"path"
	"sort"
	"strings"
	"time"
	_ "time/tzdata"
	"unicode/utf8"
)

const (
	collaborationCore        = "urn:ietf:params:jmap:core"
	collaborationMail        = "urn:ietf:params:jmap:mail"
	collaborationCalendars   = "urn:ietf:params:jmap:calendars"
	collaborationContacts    = "urn:ietf:params:jmap:contacts"
	collaborationFiles       = "urn:ietf:params:jmap:filenode"
	collaborationMaxJSON     = 8 << 20
	collaborationMaxDownload = 5 << 20
	collaborationMaxText     = 256 << 10
)

type collaborationDataAccess struct {
	Tenant, Subject, AccountID, CredentialRef string
	Bearer, Username, Password                string
	Scopes, FileRoots                         []string
	ExpiresAt                                 time.Time
	Revoked                                   bool
}

type collaborationDataClient struct {
	access  collaborationDataAccess
	base    *url.URL
	connect *url.URL
	http    *http.Client
}

type collaborationDataContent struct {
	Data              []byte
	ContentType, ETag string
}

func newCollaborationDataClient(id identity, access collaborationDataAccess, configuredBaseURL string) (*collaborationDataClient, error) {
	if id.Service || id.Subject == "" || id.Tenant == "" || access.Tenant != id.Tenant || access.Subject != id.Subject || access.AccountID == "" || access.CredentialRef == "" || access.Revoked || !access.ExpiresAt.After(time.Now()) {
		return nil, errors.New("active user-owned collaboration binding required")
	}
	if (access.Bearer == "" && (access.Username == "" || access.Password == "")) || (access.Bearer != "" && (access.Username != "" || access.Password != "")) {
		return nil, errors.New("exactly one provider authentication method required")
	}
	u, err := url.Parse(configuredBaseURL)
	if err != nil || u.Host == "" || (u.Scheme != "https" && u.Scheme != "http") || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Path != "" && u.Path != "/") {
		return nil, errors.New("invalid configured collaboration origin")
	}
	connectOrigin, err := collaborationConnectOrigin(u.String())
	if err != nil {
		return nil, err
	}
	connect, _ := url.Parse(connectOrigin)
	for _, scope := range access.Scopes {
		if scope != "mail.read" && scope != "calendar.read" && scope != "calendar.respond" && scope != "contacts.read" && scope != "files.read" {
			return nil, errors.New("unsupported collaboration scope")
		}
	}
	c := &collaborationDataClient{access: access, base: u, connect: connect, http: &http.Client{Timeout: 30 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}}
	for _, root := range access.FileRoots {
		target, err := c.sameOrigin(root)
		if err != nil || !strings.HasPrefix(target.Path, "/dav/file/") || !strings.HasSuffix(target.Path, "/") {
			return nil, errors.New("invalid authorized file collection")
		}
	}
	return c, nil
}

func (c *collaborationDataClient) require(scope string) error {
	if c.access.Revoked || !c.access.ExpiresAt.After(time.Now()) {
		return errors.New("collaboration binding expired or revoked")
	}
	for _, s := range c.access.Scopes {
		if s == scope {
			return nil
		}
	}
	return errors.New("collaboration scope not granted")
}

// The configured origin is trusted deployment configuration; protocol responses
// and user file references cannot select a new host or a noncanonical path.
func (c *collaborationDataClient) sameOrigin(raw string) (*url.URL, error) {
	u, err := url.Parse(raw)
	if err != nil || u.User != nil || u.RawQuery != "" || u.Fragment != "" {
		return nil, errors.New("invalid provider resource reference")
	}
	for _, part := range strings.Split(u.Path, "/") {
		if part == "." || part == ".." {
			return nil, errors.New("noncanonical provider resource path")
		}
	}
	u = c.base.ResolveReference(u)
	if u.Scheme != c.base.Scheme || u.Host != c.base.Host || strings.ContainsAny(u.Path, "\\\x00\r\n") {
		return nil, errors.New("provider resource outside configured origin")
	}
	decoded := u.Path
	// Reject double-encoded traversal rather than relying on differing proxy and
	// upstream URL normalisation rules.
	for i := 0; i < 3; i++ {
		if strings.Contains(strings.ToLower(decoded), "%2f") || strings.Contains(strings.ToLower(decoded), "%5c") {
			return nil, errors.New("encoded path separator denied")
		}
		for _, part := range strings.Split(decoded, "/") {
			if part == "." || part == ".." {
				return nil, errors.New("noncanonical provider resource path")
			}
		}
		next, e := url.PathUnescape(decoded)
		if e != nil {
			return nil, errors.New("invalid provider resource path")
		}
		if next == decoded {
			break
		}
		decoded = next
	}
	if strings.ContainsAny(decoded, "\\\x00\r\n") || strings.Contains(decoded, "%") {
		return nil, errors.New("noncanonical provider resource path")
	}
	if strings.Contains(strings.ToLower(u.EscapedPath()), "%2f") || strings.Contains(strings.ToLower(u.EscapedPath()), "%5c") {
		return nil, errors.New("encoded path separator denied")
	}
	return u, nil
}

func (c *collaborationDataClient) request(ctx context.Context, method, target string, payload []byte, headers map[string]string, limit int64) ([]byte, http.Header, error) {
	u, err := c.sameOrigin(target)
	if err != nil {
		return nil, nil, err
	}
	destination := *u
	destination.Scheme = c.connect.Scheme
	destination.Host = c.connect.Host
	req, err := http.NewRequestWithContext(ctx, method, destination.String(), bytes.NewReader(payload))
	if err != nil {
		return nil, nil, errors.New("invalid provider request")
	}
	if c.access.Bearer != "" {
		req.Header.Set("Authorization", "Bearer "+c.access.Bearer)
	} else {
		req.SetBasicAuth(c.access.Username, c.access.Password)
	}
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, nil, errors.New("collaboration provider unavailable")
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, nil, fmt.Errorf("collaboration provider returned HTTP %d", resp.StatusCode)
	}
	if resp.ContentLength > limit {
		return nil, nil, errors.New("provider response exceeds content limit")
	}
	data, err := io.ReadAll(io.LimitReader(resp.Body, limit+1))
	if err != nil {
		return nil, nil, errors.New("provider response could not be read")
	}
	if int64(len(data)) > limit {
		return nil, nil, errors.New("provider response exceeds content limit")
	}
	return data, resp.Header, nil
}

type collaborationSession struct {
	APIURL       string                     `json:"apiUrl"`
	DownloadURL  string                     `json:"downloadUrl"`
	Capabilities map[string]json.RawMessage `json:"capabilities"`
	Accounts     map[string]struct {
		Capabilities map[string]json.RawMessage `json:"accountCapabilities"`
	} `json:"accounts"`
}

func (c *collaborationDataClient) session(ctx context.Context, capability string) (collaborationSession, error) {
	var result collaborationSession
	raw, _, err := c.request(ctx, "GET", "/jmap/session", nil, map[string]string{"Accept": "application/json"}, 1<<20)
	if err != nil {
		return result, err
	}
	if json.Unmarshal(raw, &result) != nil {
		return result, errors.New("invalid JMAP session")
	}
	a, ok := result.Accounts[c.access.AccountID]
	if !ok {
		return result, errors.New("bound provider account is not accessible")
	}
	if _, ok = result.Capabilities[capability]; !ok {
		return result, errors.New("provider capability unsupported")
	}
	if _, ok = a.Capabilities[capability]; !ok {
		return result, errors.New("account capability unsupported")
	}
	if _, err = c.sameOrigin(result.APIURL); err != nil || result.APIURL == "" {
		return result, errors.New("invalid JMAP API origin")
	}
	return result, nil
}

func (c *collaborationDataClient) jmap(ctx context.Context, capability, method string, args map[string]any) (map[string]any, error) {
	sess, err := c.session(ctx, capability)
	if err != nil {
		return nil, err
	}
	args["accountId"] = c.access.AccountID
	body, _ := json.Marshal(map[string]any{"using": []string{collaborationCore, capability}, "methodCalls": []any{[]any{method, args, "0"}}})
	raw, _, err := c.request(ctx, "POST", sess.APIURL, body, map[string]string{"Content-Type": "application/json", "Accept": "application/json"}, collaborationMaxJSON)
	if err != nil {
		return nil, err
	}
	var response struct {
		Calls [][]json.RawMessage `json:"methodResponses"`
	}
	if json.Unmarshal(raw, &response) != nil || len(response.Calls) != 1 || len(response.Calls[0]) != 3 {
		return nil, errors.New("invalid JMAP response")
	}
	var name, callID string
	if json.Unmarshal(response.Calls[0][0], &name) != nil || json.Unmarshal(response.Calls[0][2], &callID) != nil || callID != "0" {
		return nil, errors.New("invalid JMAP response correlation")
	}
	if name == "error" {
		return nil, errors.New("JMAP method rejected")
	}
	if name != method {
		return nil, errors.New("unexpected JMAP response method")
	}
	var result map[string]any
	if json.Unmarshal(response.Calls[0][1], &result) != nil || result["accountId"] != c.access.AccountID {
		return nil, errors.New("invalid JMAP response account")
	}
	return result, nil
}

func collaborationPage(position, limit int) (int, error) {
	if limit == 0 {
		limit = 50
	}
	if position < 0 || position > 100000 || limit < 1 || limit > 50 {
		return 0, errors.New("position must be 0..100000 and limit 1..50")
	}
	return limit, nil
}
func collaborationID(id string) error {
	if id == "" || len(id) > 1024 || strings.ContainsAny(id, "\r\n\x00") {
		return errors.New("invalid resource id")
	}
	return nil
}
func collaborationMap(v any) map[string]any                   { m, _ := v.(map[string]any); return m }
func collaborationString(m map[string]any, key string) string { s, _ := m[key].(string); return s }
func collaborationItems(m map[string]any) []any               { items, _ := m["list"].([]any); return items }
func collaborationProject(m map[string]any, fields []string) map[string]any {
	out := map[string]any{}
	for _, f := range fields {
		if v, ok := m[f]; ok {
			out[f] = v
		}
	}
	return out
}
func collaborationList(result map[string]any, fields []string, limit int) map[string]any {
	source := collaborationItems(result)
	items := []any{}
	for i, item := range source {
		if i >= limit {
			break
		}
		items = append(items, collaborationProject(collaborationMap(item), fields))
	}
	return map[string]any{"items": items, "state": result["state"], "truncated": len(source) > limit, "untrustedContent": true}
}

var collaborationMailSummary = []string{"id", "mailboxIds", "threadId", "from", "to", "subject", "receivedAt", "size", "hasAttachment", "preview", "keywords"}
var collaborationMailDetail = []string{"id", "mailboxIds", "threadId", "from", "to", "cc", "subject", "receivedAt", "size", "hasAttachment", "textBody", "htmlBody", "attachments", "bodyValues"}

func (c *collaborationDataClient) Mailboxes(ctx context.Context) (map[string]any, error) {
	if err := c.require("mail.read"); err != nil {
		return nil, err
	}
	r, err := c.jmap(ctx, collaborationMail, "Mailbox/get", map[string]any{"properties": []string{"id", "name", "parentId", "role", "totalEmails", "unreadEmails", "myRights"}})
	if err != nil {
		return nil, err
	}
	return collaborationList(r, []string{"id", "name", "parentId", "role", "totalEmails", "unreadEmails", "myRights"}, 50), nil
}
func (c *collaborationDataClient) MailQuery(ctx context.Context, text, mailboxID string, position, limit int) (map[string]any, error) {
	if err := c.require("mail.read"); err != nil {
		return nil, err
	}
	limit, err := collaborationPage(position, limit)
	if err != nil {
		return nil, err
	}
	if len(text) > 2048 {
		return nil, errors.New("mail search text too long")
	}
	filter := map[string]any{}
	if text != "" {
		filter["text"] = text
	}
	if mailboxID != "" {
		if err = collaborationID(mailboxID); err != nil {
			return nil, err
		}
		filter["inMailbox"] = mailboxID
	}
	q, err := c.jmap(ctx, collaborationMail, "Email/query", map[string]any{"filter": filter, "position": position, "limit": limit, "sort": []any{map[string]any{"property": "receivedAt", "isAscending": false}}})
	if err != nil {
		return nil, err
	}
	ids, ok := q["ids"].([]any)
	if !ok || len(ids) > limit {
		return nil, errors.New("invalid bounded mail query response")
	}
	if len(ids) == 0 {
		return map[string]any{"items": []any{}, "position": position, "mayHaveMore": false, "untrustedContent": true}, nil
	}
	r, err := c.jmap(ctx, collaborationMail, "Email/get", map[string]any{"ids": ids, "properties": collaborationMailSummary})
	if err != nil {
		return nil, err
	}
	result := collaborationList(r, collaborationMailSummary, limit)
	result["position"] = position
	result["nextPosition"] = position + len(ids)
	result["mayHaveMore"] = len(ids) == limit
	return result, nil
}
func (c *collaborationDataClient) MailRead(ctx context.Context, emailID string) (map[string]any, error) {
	if err := c.require("mail.read"); err != nil {
		return nil, err
	}
	if err := collaborationID(emailID); err != nil {
		return nil, err
	}
	r, err := c.jmap(ctx, collaborationMail, "Email/get", map[string]any{"ids": []string{emailID}, "properties": collaborationMailDetail, "fetchAllBodyValues": true, "maxBodyValueBytes": 128 << 10})
	if err != nil {
		return nil, err
	}
	if len(collaborationItems(r)) != 1 || collaborationMap(collaborationItems(r)[0])["id"] != emailID {
		return nil, errors.New("mail not found")
	}
	// maxBodyValueBytes is per MIME part. Enforce an aggregate inline budget too.
	item := collaborationMap(collaborationItems(r)[0])
	values := collaborationMap(item["bodyValues"])
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	remaining := 128 << 10
	truncated := false
	for _, key := range keys {
		part := collaborationMap(values[key])
		value := collaborationString(part, "value")
		if len(value) > remaining {
			value = value[:remaining]
			for !utf8.ValidString(value) && len(value) > 0 {
				value = value[:len(value)-1]
			}
			part["value"] = value
			part["isTruncated"] = true
		}
		remaining -= len(value)
		if part["isTruncated"] == true {
			truncated = true
		}
	}
	out := collaborationList(r, collaborationMailDetail, 1)
	out["truncated"] = truncated
	return out, nil
}

func (c *collaborationDataClient) MailAttachment(ctx context.Context, emailID, blobID string) (collaborationDataContent, error) {
	var content collaborationDataContent
	r, err := c.MailRead(ctx, emailID)
	if err != nil {
		return content, err
	}
	if err = collaborationID(blobID); err != nil {
		return content, err
	}
	items, _ := r["items"].([]any)
	found := false
	attachments, _ := collaborationMap(items[0])["attachments"].([]any)
	for _, a := range attachments {
		if collaborationMap(a)["blobId"] == blobID {
			found = true
		}
	}
	if !found {
		return content, errors.New("attachment does not belong to selected mail")
	}
	sess, err := c.session(ctx, collaborationMail)
	if err != nil {
		return content, err
	}
	target := strings.NewReplacer("{accountId}", url.PathEscape(c.access.AccountID), "{blobId}", url.PathEscape(blobID), "{name}", "attachment", "{type}", "application%2Foctet-stream").Replace(sess.DownloadURL)
	// Optional type query from the JMAP URL template is not required for reading
	// the blob, and is removed before the strict same-origin URL validation.
	u, err := url.Parse(target)
	if err != nil || sess.DownloadURL == "" {
		return content, errors.New("invalid attachment download template")
	}
	u.RawQuery = ""
	if strings.ContainsAny(u.String(), "{}") {
		return content, errors.New("unsupported attachment download template")
	}
	content.Data, _, err = c.request(ctx, "GET", u.String(), nil, nil, collaborationMaxDownload)
	content.ContentType = "application/octet-stream"
	return content, err
}

var collaborationCalendarFields = []string{"id", "name", "color", "timeZone", "isDefault", "myRights"}
var collaborationEventFields = []string{"id", "uid", "calendarIds", "title", "description", "start", "duration", "timeZone", "utcStart", "utcEnd", "recurrenceId", "participants", "isOrigin", "isDraft"}

var collaborationAddressBookFields = []string{"id", "name", "description", "sortOrder", "isDefault", "isSubscribed", "myRights"}
var collaborationContactSummary = []string{"id", "uid", "addressBookIds", "kind", "name", "emails", "phones", "organizations", "titles"}
var collaborationContactDetail = []string{"id", "uid", "addressBookIds", "kind", "name", "emails", "phones", "onlineServices", "organizations", "titles", "addresses", "notes", "members", "created", "updated"}

func (c *collaborationDataClient) AddressBooks(ctx context.Context) (map[string]any, error) {
	if err := c.require("contacts.read"); err != nil {
		return nil, err
	}
	r, err := c.jmap(ctx, collaborationContacts, "AddressBook/get", map[string]any{"properties": collaborationAddressBookFields})
	if err != nil {
		return nil, err
	}
	return collaborationList(r, collaborationAddressBookFields, 50), nil
}

func (c *collaborationDataClient) ContactQuery(ctx context.Context, text, addressBookID string, position, limit int) (map[string]any, error) {
	if err := c.require("contacts.read"); err != nil {
		return nil, err
	}
	limit, err := collaborationPage(position, limit)
	if err != nil {
		return nil, err
	}
	if len(text) > 2048 {
		return nil, errors.New("contact search text too long")
	}
	filter := map[string]any{}
	if text != "" {
		filter["text"] = text
	}
	if addressBookID != "" {
		if err = collaborationID(addressBookID); err != nil {
			return nil, err
		}
		filter["inAddressBook"] = addressBookID
	}
	q, err := c.jmap(ctx, collaborationContacts, "ContactCard/query", map[string]any{"filter": filter, "position": position, "limit": limit, "sort": []any{map[string]any{"property": "updated", "isAscending": false}}})
	if err != nil {
		return nil, err
	}
	ids, ok := q["ids"].([]any)
	if !ok || len(ids) > limit {
		return nil, errors.New("invalid bounded contact query response")
	}
	if len(ids) == 0 {
		return map[string]any{"items": []any{}, "position": position, "mayHaveMore": false, "untrustedContent": true}, nil
	}
	r, err := c.jmap(ctx, collaborationContacts, "ContactCard/get", map[string]any{"ids": ids, "properties": collaborationContactSummary})
	if err != nil {
		return nil, err
	}
	result := collaborationList(r, collaborationContactSummary, limit)
	result["position"] = position
	result["nextPosition"] = position + len(ids)
	result["mayHaveMore"] = len(ids) == limit
	return result, nil
}

func (c *collaborationDataClient) ContactRead(ctx context.Context, contactID string) (map[string]any, error) {
	if err := c.require("contacts.read"); err != nil {
		return nil, err
	}
	if err := collaborationID(contactID); err != nil {
		return nil, err
	}
	r, err := c.jmap(ctx, collaborationContacts, "ContactCard/get", map[string]any{"ids": []string{contactID}, "properties": collaborationContactDetail})
	if err != nil {
		return nil, err
	}
	if len(collaborationItems(r)) != 1 || collaborationMap(collaborationItems(r)[0])["id"] != contactID {
		return nil, errors.New("contact not found")
	}
	return collaborationList(r, collaborationContactDetail, 1), nil
}

func (c *collaborationDataClient) Calendars(ctx context.Context) (map[string]any, error) {
	if err := c.require("calendar.read"); err != nil {
		return nil, err
	}
	r, err := c.jmap(ctx, collaborationCalendars, "Calendar/get", map[string]any{"properties": collaborationCalendarFields})
	if err != nil {
		return nil, err
	}
	return collaborationList(r, collaborationCalendarFields, 50), nil
}
func collaborationWindow(after, before, timeZone string) error {
	a, e := time.Parse(time.RFC3339, after)
	b, e2 := time.Parse(time.RFC3339, before)
	if e != nil || e2 != nil || !b.After(a) || b.Sub(a) > 31*24*time.Hour {
		return errors.New("calendar window must be RFC3339 and at most 31 days")
	}
	if _, e = time.LoadLocation(timeZone); e != nil {
		return errors.New("valid IANA time zone required")
	}
	return nil
}
func (c *collaborationDataClient) CalendarQuery(ctx context.Context, after, before, timeZone string, position, limit int) (map[string]any, error) {
	if err := c.require("calendar.read"); err != nil {
		return nil, err
	}
	if err := collaborationWindow(after, before, timeZone); err != nil {
		return nil, err
	}
	limit, err := collaborationPage(position, limit)
	if err != nil {
		return nil, err
	}
	a, _ := time.Parse(time.RFC3339, after)
	b, _ := time.Parse(time.RFC3339, before)
	loc, _ := time.LoadLocation(timeZone)
	q, err := c.jmap(ctx, collaborationCalendars, "CalendarEvent/query", map[string]any{"filter": map[string]any{"after": a.In(loc).Format("2006-01-02T15:04:05"), "before": b.In(loc).Format("2006-01-02T15:04:05")}, "timeZone": timeZone, "expandRecurrences": true, "position": position, "limit": limit})
	if err != nil {
		return nil, err
	}
	ids, ok := q["ids"].([]any)
	if !ok || len(ids) > limit {
		return nil, errors.New("invalid bounded calendar query response")
	}
	if len(ids) == 0 {
		return map[string]any{"items": []any{}, "position": position, "mayHaveMore": false, "untrustedContent": true}, nil
	}
	r, err := c.jmap(ctx, collaborationCalendars, "CalendarEvent/get", map[string]any{"ids": ids, "properties": collaborationEventFields, "timeZone": timeZone})
	if err != nil {
		return nil, err
	}
	result := collaborationList(r, collaborationEventFields, limit)
	result["nextPosition"] = position + len(ids)
	result["mayHaveMore"] = len(ids) == limit
	return result, nil
}
func (c *collaborationDataClient) CalendarRead(ctx context.Context, eventID, timeZone string) (map[string]any, error) {
	if err := c.require("calendar.read"); err != nil {
		return nil, err
	}
	if err := collaborationID(eventID); err != nil {
		return nil, err
	}
	if _, err := time.LoadLocation(timeZone); err != nil {
		return nil, errors.New("valid IANA time zone required")
	}
	r, err := c.jmap(ctx, collaborationCalendars, "CalendarEvent/get", map[string]any{"ids": []string{eventID}, "properties": collaborationEventFields, "timeZone": timeZone})
	if err != nil {
		return nil, err
	}
	if len(collaborationItems(r)) != 1 || collaborationMap(collaborationItems(r)[0])["id"] != eventID {
		return nil, errors.New("calendar event not found")
	}
	return collaborationList(r, collaborationEventFields, 1), nil
}

func collaborationCalendarAddress(p map[string]any) string {
	if address := collaborationString(p, "calendarAddress"); address != "" {
		return address
	}
	return collaborationString(collaborationMap(p["sendTo"]), "imip")
}
func (c *collaborationDataClient) responseTarget(ctx context.Context, eventID, response string) (string, string, string, error) {
	if err := c.require("calendar.respond"); err != nil {
		return "", "", "", err
	}
	if err := c.require("calendar.read"); err != nil {
		return "", "", "", err
	}
	if response != "accepted" && response != "declined" && response != "tentative" {
		return "", "", "", errors.New("invalid participation status")
	}
	event, err := c.CalendarRead(ctx, eventID, "Etc/UTC")
	if err != nil {
		return "", "", "", err
	}
	items, _ := event["items"].([]any)
	e := collaborationMap(items[0])
	state := collaborationString(event, "state")
	if state == "" || e["isDraft"] == true {
		return "", "", "", errors.New("event is not a versioned invitation")
	}
	identities, err := c.jmap(ctx, collaborationCalendars, "ParticipantIdentity/get", map[string]any{})
	if err != nil {
		return "", "", "", err
	}
	own := map[string]bool{}
	for _, item := range collaborationItems(identities) {
		address := collaborationCalendarAddress(collaborationMap(item))
		if address != "" {
			own[address] = true
		}
	}
	participantID, current := "", ""
	for key, value := range collaborationMap(e["participants"]) {
		p := collaborationMap(value)
		roles := collaborationMap(p["roles"])
		if own[collaborationCalendarAddress(p)] && roles["attendee"] == true && roles["owner"] != true {
			if participantID != "" {
				return "", "", "", errors.New("ambiguous attendee identity")
			}
			participantID = key
			current = collaborationString(p, "participationStatus")
		}
	}
	if participantID == "" {
		return "", "", "", errors.New("binding user is not an attendee")
	}
	// RSVP requires the right in every calendar containing the event.
	ids := []string{}
	for key, value := range collaborationMap(e["calendarIds"]) {
		if value == true {
			ids = append(ids, key)
		}
	}
	if len(ids) == 0 {
		return "", "", "", errors.New("event calendar rights unavailable")
	}
	calendars, err := c.jmap(ctx, collaborationCalendars, "Calendar/get", map[string]any{"ids": ids, "properties": []string{"id", "myRights"}})
	if err != nil {
		return "", "", "", err
	}
	rights := map[string]bool{}
	for _, item := range collaborationItems(calendars) {
		cal := collaborationMap(item)
		rights[collaborationString(cal, "id")] = collaborationMap(cal["myRights"])["mayRSVP"] == true
	}
	for _, id := range ids {
		if !rights[id] {
			return "", "", "", errors.New("calendar RSVP permission required")
		}
	}
	return participantID, current, state, nil
}
func (c *collaborationDataClient) responseHash(eventID, response, state string) string {
	raw, _ := json.Marshal([]string{c.access.Tenant, c.access.Subject, c.access.CredentialRef, c.access.AccountID, eventID, response, state})
	sum := sha256.Sum256(raw)
	return hex.EncodeToString(sum[:])
}
func (c *collaborationDataClient) CalendarResponsePlan(ctx context.Context, eventID, response string) (map[string]any, error) {
	_, current, state, err := c.responseTarget(ctx, eventID, response)
	if err != nil {
		return nil, err
	}
	return map[string]any{"eventId": eventID, "response": response, "currentResponse": current, "expectedState": state, "planHash": c.responseHash(eventID, response, state), "sendsSchedulingMessage": current != response, "decision": "preview"}, nil
}
func (c *collaborationDataClient) CalendarRespond(ctx context.Context, eventID, response, expectedState, planHash string) (map[string]any, error) {
	if expectedState == "" || planHash == "" || planHash != c.responseHash(eventID, response, expectedState) {
		return nil, errors.New("matching user-owned calendar response plan required")
	}
	participant, current, state, err := c.responseTarget(ctx, eventID, response)
	if err != nil {
		return nil, err
	}
	if state != expectedState {
		return nil, errors.New("calendar state changed; refresh response plan")
	}
	if current == response {
		return map[string]any{"eventId": eventID, "response": response, "changed": false, "schedulingState": "unchanged"}, nil
	}
	escaped := strings.ReplaceAll(strings.ReplaceAll(participant, "~", "~0"), "/", "~1")
	r, err := c.jmap(ctx, collaborationCalendars, "CalendarEvent/set", map[string]any{"ifInState": expectedState, "update": map[string]any{eventID: map[string]any{"participants/" + escaped + "/participationStatus": response, "participants/" + escaped + "/expectReply": false}}, "sendSchedulingMessages": true})
	if err != nil {
		return nil, errors.New("calendar response outcome unconfirmed; read state before retrying")
	}
	if _, ok := collaborationMap(r["updated"])[eventID]; !ok {
		return nil, errors.New("calendar response was not applied")
	}
	// Verify the stored attendee response without ever replaying this write.
	verified, readErr := c.CalendarRead(ctx, eventID, "Etc/UTC")
	if readErr != nil {
		return nil, errors.New("calendar response stored but verification unavailable; read state before retrying")
	}
	verifiedItems, _ := verified["items"].([]any)
	actual := collaborationMap(collaborationMap(collaborationMap(verifiedItems[0])["participants"])[participant])
	if actual["participationStatus"] != response {
		return nil, errors.New("calendar response stored but readback differs; refresh event")
	}
	// Native scheduling acceptance is not evidence of remote message delivery.
	return map[string]any{"eventId": eventID, "response": response, "changed": true, "state": r["newState"], "schedulingState": "submitted", "deliveryVerified": false}, nil
}

func (c *collaborationDataClient) fileURL(href string) (*url.URL, error) {
	if err := c.require("files.read"); err != nil {
		return nil, err
	}
	u, err := c.sameOrigin(href)
	if err != nil {
		return nil, err
	}
	for _, root := range c.access.FileRoots {
		r, e := c.sameOrigin(root)
		if e == nil && (u.Path == strings.TrimSuffix(r.Path, "/") || strings.HasPrefix(u.Path, r.Path)) {
			return u, nil
		}
	}
	return nil, errors.New("file is outside authorized collections")
}

type collaborationDAVResponse struct {
	Href   string `xml:"DAV: href"`
	Status string `xml:"DAV: status"`
	Props  []struct {
		Status string `xml:"DAV: status"`
		Prop   struct {
			Name             string `xml:"DAV: displayname"`
			CurrentPrincipal struct {
				Href string `xml:"DAV: href"`
			} `xml:"DAV: current-user-principal"`
			Size         int64  `xml:"DAV: getcontentlength"`
			Type         string `xml:"DAV: getcontenttype"`
			ETag         string `xml:"DAV: getetag"`
			ResourceType struct {
				Collection *struct{} `xml:"DAV: collection"`
			} `xml:"DAV: resourcetype"`
		} `xml:"DAV: prop"`
	} `xml:"DAV: propstat"`
}

func (c *collaborationDataClient) FilesList(ctx context.Context, href string) (map[string]any, error) {
	if err := c.require("files.read"); err != nil {
		return nil, err
	}
	if len(c.access.FileRoots) == 0 {
		if _, err := c.DiscoverFileRoots(ctx); err != nil {
			return nil, err
		}
	}
	if href == "" {
		items := []any{}
		for _, root := range c.access.FileRoots {
			items = append(items, map[string]any{"href": root, "isCollection": true})
		}
		return map[string]any{"items": items, "partial": false, "truncated": false, "untrustedContent": true}, nil
	}
	u, err := c.fileURL(href)
	if err != nil {
		return nil, err
	}
	payload := []byte(`<d:propfind xmlns:d="DAV:"><d:prop><d:displayname/><d:getcontentlength/><d:getcontenttype/><d:getetag/><d:resourcetype/></d:prop></d:propfind>`)
	raw, _, err := c.request(ctx, "PROPFIND", u.String(), payload, map[string]string{"Depth": "1", "Content-Type": "application/xml"}, 1<<20)
	if err != nil {
		return nil, err
	}
	var doc struct {
		XMLName   xml.Name                   `xml:"DAV: multistatus"`
		Responses []collaborationDAVResponse `xml:"DAV: response"`
	}
	if xml.Unmarshal(raw, &doc) != nil {
		return nil, errors.New("invalid DAV multistatus")
	}
	items := []any{}
	partial := false
	truncated := false
	for _, r := range doc.Responses {
		if r.Status != "" && !strings.Contains(r.Status, " 200 ") {
			partial = true
			continue
		}
		// Relative DAV hrefs resolve against the requested collection, not the host.
		ref, e := url.Parse(r.Href)
		if e != nil {
			partial = true
			continue
		}
		resolved := u.ResolveReference(ref)
		target, e := c.fileURL(resolved.String())
		if e != nil {
			partial = true
			continue
		}
		parent := strings.TrimSuffix(u.Path, "/")
		entry := strings.TrimSuffix(target.Path, "/")
		if entry == parent {
			continue
		}
		if path.Dir(entry) != parent {
			partial = true
			continue
		}
		if len(items) >= 50 {
			truncated = true
			continue
		}
		accepted := false
		for _, ps := range r.Props {
			if !strings.Contains(ps.Status, " 200 ") {
				partial = true
				continue
			}
			p := ps.Prop
			items = append(items, map[string]any{"href": target.EscapedPath(), "name": p.Name, "size": p.Size, "contentType": p.Type, "etag": p.ETag, "isCollection": p.ResourceType.Collection != nil})
			accepted = true
			break
		}
		if !accepted {
			partial = true
		}
	}
	return map[string]any{"items": items, "partial": partial, "truncated": truncated, "untrustedContent": true}, nil
}
func (c *collaborationDataClient) DownloadFile(ctx context.Context, href string) (collaborationDataContent, error) {
	var result collaborationDataContent
	if len(c.access.FileRoots) == 0 {
		if _, err := c.DiscoverFileRoots(ctx); err != nil {
			return result, err
		}
	}
	u, err := c.fileURL(href)
	if err != nil {
		return result, err
	}
	// HEAD is an early size/ACL check; the GET remains stream bounded because
	// metadata can be missing or stale.
	if _, _, err = c.request(ctx, "HEAD", u.String(), nil, nil, collaborationMaxDownload); err != nil {
		return result, err
	}
	var headers http.Header
	result.Data, headers, err = c.request(ctx, "GET", u.String(), nil, nil, collaborationMaxDownload)
	if err != nil {
		return result, err
	}
	result.ContentType = headers.Get("Content-Type")
	result.ETag = headers.Get("ETag")
	return result, nil
}
func (c *collaborationDataClient) FilesRead(ctx context.Context, href string) (map[string]any, error) {
	content, err := c.DownloadFile(ctx, href)
	if err != nil {
		return nil, err
	}
	typ, _, err := mime.ParseMediaType(content.ContentType)
	if err != nil {
		return nil, errors.New("file content type unavailable; use protected download")
	}
	if !(strings.HasPrefix(typ, "text/") || typ == "application/json" || typ == "application/xml") || !utf8.Valid(content.Data) {
		return nil, errors.New("binary content requires protected download")
	}
	if len(content.Data) > collaborationMaxText {
		return nil, errors.New("file exceeds inline text limit; use protected download")
	}
	return map[string]any{"text": string(content.Data), "contentType": typ, "bytes": len(content.Data), "etag": content.ETag, "truncated": false, "untrustedContent": true}, nil
}

// DiscoverFileRoots uses the provider's native DAV collection listing. Account
// IDs from JMAP are opaque and never interpolated into a DAV path. Default
// discovery includes only the current principal's own collection; a broker can
// separately approve shared collection roots after explicit user consent.
func (c *collaborationDataClient) DiscoverFileRoots(ctx context.Context) ([]string, error) {
	if err := c.require("files.read"); err != nil {
		return nil, err
	}
	if _, err := c.session(ctx, collaborationFiles); err != nil {
		return nil, err
	}
	raw, _, err := c.request(ctx, "PROPFIND", "/dav/file/", []byte(`<d:propfind xmlns:d="DAV:"><d:prop><d:current-user-principal/><d:resourcetype/></d:prop></d:propfind>`), map[string]string{"Depth": "1", "Content-Type": "application/xml"}, 1<<20)
	if err != nil {
		return nil, err
	}
	var doc struct {
		XMLName   xml.Name                   `xml:"DAV: multistatus"`
		Responses []collaborationDAVResponse `xml:"DAV: response"`
	}
	if xml.Unmarshal(raw, &doc) != nil {
		return nil, errors.New("invalid file collection discovery")
	}
	principal := ""
	for _, r := range doc.Responses {
		u, e := c.sameOrigin(r.Href)
		if e != nil || u.Path != "/dav/file/" {
			continue
		}
		for _, ps := range r.Props {
			if !strings.Contains(ps.Status, " 200 ") {
				continue
			}
			p, e := c.sameOrigin(ps.Prop.CurrentPrincipal.Href)
			if e == nil && strings.HasPrefix(p.Path, "/dav/pal/") {
				tail := strings.TrimSuffix(strings.TrimPrefix(p.Path, "/dav/pal/"), "/")
				if tail != "" && !strings.Contains(tail, "/") {
					principal = tail
				}
			}
		}
	}
	if principal == "" {
		return nil, errors.New("current DAV principal unavailable")
	}
	roots := []string{}
	seen := map[string]bool{}
	for _, r := range doc.Responses {
		u, e := c.sameOrigin(r.Href)
		if e != nil || !strings.HasPrefix(u.Path, "/dav/file/") {
			continue
		}
		tail := strings.TrimSuffix(strings.TrimPrefix(u.Path, "/dav/file/"), "/")
		if tail != principal {
			continue
		}
		for _, ps := range r.Props {
			if strings.Contains(ps.Status, " 200 ") && ps.Prop.ResourceType.Collection != nil {
				root := strings.TrimSuffix(u.EscapedPath(), "/") + "/"
				if !seen[root] {
					seen[root] = true
					roots = append(roots, root)
				}
			}
		}
	}
	if len(roots) == 0 {
		return nil, errors.New("provider did not expose an own file collection")
	}
	c.access.FileRoots = roots
	return append([]string(nil), roots...), nil
}
