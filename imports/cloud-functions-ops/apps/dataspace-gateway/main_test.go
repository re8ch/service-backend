package main

import (
	"bytes"
	"encoding/json"
	"net/http/httptest"
	"testing"
)

func call(t *testing.T, s *Server, method, path, body, etag string) *httptest.ResponseRecorder {
	t.Helper()
	r := httptest.NewRequest(method, path, bytes.NewBufferString(body))
	r.Header.Set("Authorization", "Bearer test")
	if body != "" {
		r.Header.Set("Content-Type", "application/json")
	}
	if etag != "" {
		r.Header.Set("If-Match", etag)
	}
	w := httptest.NewRecorder()
	s.ServeHTTP(w, r)
	return w
}
func TestFileLifecycleAndCursor(t *testing.T) {
	s, e := newServer(t.TempDir(), "test")
	if e != nil {
		t.Fatal(e)
	}
	w := call(t, s, "POST", "/v1/items/"+s.workspace.RootItemID, `{"name":"hello.txt","kind":"file"}`, "")
	if w.Code != 201 {
		t.Fatalf("create: %d %s", w.Code, w.Body.String())
	}
	var it Item
	json.Unmarshal(w.Body.Bytes(), &it)
	r := httptest.NewRequest("PATCH", "/v1/items/"+it.ID, bytes.NewBufferString("hello"))
	r.Header.Set("Authorization", "Bearer test")
	r.Header.Set("Content-Type", "application/octet-stream")
	r.Header.Set("If-Match", it.ETag)
	w = httptest.NewRecorder()
	s.ServeHTTP(w, r)
	if w.Code != 200 {
		t.Fatalf("upload: %d %s", w.Code, w.Body.String())
	}
	w = call(t, s, "GET", "/v1/workspaces/"+s.workspace.ID+"?view=changes&cursor=0", "", "")
	if w.Code != 200 || !bytes.Contains(w.Body.Bytes(), []byte(`"cursor":2`)) {
		t.Fatalf("changes: %s", w.Body.String())
	}
}
func TestMutationRequiresETag(t *testing.T) {
	s, _ := newServer(t.TempDir(), "test")
	w := call(t, s, "DELETE", "/v1/items/"+s.workspace.RootItemID, "", "")
	if w.Code != 409 {
		t.Fatalf("got %d", w.Code)
	}
}
