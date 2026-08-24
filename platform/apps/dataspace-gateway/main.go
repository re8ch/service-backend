package main

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"mime"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

type Item struct {
	ID          string    `json:"id"`
	WorkspaceID string    `json:"workspaceId"`
	ParentID    string    `json:"parentId,omitempty"`
	Name        string    `json:"name"`
	Kind        string    `json:"kind"`
	Size        int64     `json:"size"`
	ContentType string    `json:"contentType,omitempty"`
	ETag        string    `json:"etag"`
	Version     int64     `json:"version"`
	ModifiedAt  time.Time `json:"modifiedAt"`
	Deleted     bool      `json:"deleted,omitempty"`
}
type Change struct {
	Cursor int64  `json:"cursor"`
	Type   string `json:"type"`
	Item   Item   `json:"item"`
}
type Workspace struct {
	ID         string `json:"id"`
	Name       string `json:"name"`
	RootItemID string `json:"rootItemId"`
	QuotaBytes int64  `json:"quotaBytes,omitempty"`
}

type Server struct {
	root      string
	auth      *authenticator
	mu        sync.RWMutex
	workspace Workspace
	items     map[string]Item
	paths     map[string]string
	changes   []Change
	cursor    int64
}

func newServer(root, token string) (*Server, error) {
	auth, err := newAuthenticator(context.Background(), token, os.Getenv("DATASPACE_OIDC_ISSUER"), os.Getenv("DATASPACE_OIDC_AUDIENCE"))
	if err != nil {
		return nil, err
	}
	if err := os.MkdirAll(filepath.Join(root, "workspace", ".uploads"), 0750); err != nil {
		return nil, err
	}
	wid, rid := env("DATASPACE_WORKSPACE_ID", newID()), env("DATASPACE_ROOT_ITEM_ID", newID())
	s := &Server{root: root, auth: auth, workspace: Workspace{ID: wid, Name: env("DATASPACE_WORKSPACE_NAME", "My DataSpace"), RootItemID: rid}, items: map[string]Item{}, paths: map[string]string{}}
	if err := s.loadState(); err != nil {
		return nil, err
	}
	if len(s.items) > 0 {
		return s, nil
	}
	r := Item{ID: rid, WorkspaceID: wid, Name: s.workspace.Name, Kind: "folder", ETag: "root-1", Version: 1, ModifiedAt: time.Now().UTC()}
	s.items[rid], s.paths[rid] = r, ""
	if err := s.saveState(); err != nil {
		return nil, err
	}
	return s, nil
}

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	if r.URL.Path == "/healthz" {
		json.NewEncoder(w).Encode(map[string]any{"ok": true})
		return
	}
	if !s.auth.authorize(r) {
		problem(w, 401, "unauthorized")
		return
	}
	parts := split(r.URL.Path)
	if r.Method == "GET" && r.URL.Path == "/v1/workspaces" {
		json.NewEncoder(w).Encode(map[string]any{"workspaces": []Workspace{s.workspace}})
		return
	}
	if len(parts) >= 2 && parts[0] == "v1" && parts[1] == "items" {
		s.itemsRoute(w, r, parts[2:])
		return
	}
	if len(parts) == 3 && parts[0] == "v1" && parts[1] == "workspaces" && r.Method == "GET" {
		s.changesRoute(w, r, parts[2])
		return
	}
	problem(w, 404, "not found")
}

func (s *Server) itemsRoute(w http.ResponseWriter, r *http.Request, p []string) {
	if len(p) == 0 {
		problem(w, 404, "item id required")
		return
	}
	id := p[0]
	s.mu.Lock()
	defer s.mu.Unlock()
	item, ok := s.items[id]
	if !ok || item.Deleted {
		problem(w, 404, "item not found")
		return
	}
	if len(p) == 2 && p[1] == "children" && r.Method == "GET" {
		var out []Item
		for _, v := range s.items {
			if v.ParentID == id && !v.Deleted {
				out = append(out, v)
			}
		}
		sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
		json.NewEncoder(w).Encode(map[string]any{"items": out})
		return
	}
	if len(p) == 2 && p[1] == "content" && r.Method == "GET" {
		s.download(w, r, item)
		return
	}
	switch r.Method {
	case "GET":
		json.NewEncoder(w).Encode(item)
	case "POST":
		s.create(w, r, item)
	case "PATCH":
		s.update(w, r, item)
	case "DELETE":
		s.remove(w, r, item)
	default:
		problem(w, 405, "method not allowed")
	}
}

func (s *Server) create(w http.ResponseWriter, r *http.Request, parent Item) {
	var in struct{ Name, Kind, ContentType string }
	if json.NewDecoder(r.Body).Decode(&in) != nil || !validName(in.Name) || (in.Kind != "file" && in.Kind != "folder") {
		problem(w, 400, "invalid item")
		return
	}
	for _, v := range s.items {
		if !v.Deleted && v.ParentID == parent.ID && v.Name == in.Name {
			problem(w, 409, "name exists")
			return
		}
	}
	id := newID()
	rel := filepath.Join(s.paths[parent.ID], in.Name)
	abs := filepath.Join(s.root, "workspace", rel)
	if in.Kind == "folder" {
		if err := os.Mkdir(abs, 0750); err != nil {
			problem(w, 500, err.Error())
			return
		}
	} else {
		if err := os.WriteFile(abs, nil, 0640); err != nil {
			problem(w, 500, err.Error())
			return
		}
	}
	now := time.Now().UTC()
	it := Item{ID: id, WorkspaceID: s.workspace.ID, ParentID: parent.ID, Name: in.Name, Kind: in.Kind, ContentType: in.ContentType, Version: 1, ETag: etag(id, 1), ModifiedAt: now}
	s.items[id], s.paths[id] = it, rel
	s.emit("created", it)
	if err := s.saveState(); err != nil {
		problem(w, 500, err.Error())
		return
	}
	w.WriteHeader(201)
	json.NewEncoder(w).Encode(it)
}

func (s *Server) update(w http.ResponseWriter, r *http.Request, it Item) {
	if !match(r, it) {
		problem(w, 409, "etag mismatch")
		return
	}
	ct := r.Header.Get("Content-Type")
	if strings.HasPrefix(ct, "application/octet-stream") {
		if it.Kind != "file" {
			problem(w, 400, "folder has no content")
			return
		}
		tmp := filepath.Join(s.root, "workspace", ".uploads", newID())
		f, e := os.OpenFile(tmp, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0640)
		if e == nil {
			_, e = io.Copy(f, r.Body)
			f.Close()
		}
		if e == nil {
			e = os.Rename(tmp, filepath.Join(s.root, "workspace", s.paths[it.ID]))
		}
		if e != nil {
			os.Remove(tmp)
			problem(w, 500, e.Error())
			return
		}
		st, _ := os.Stat(filepath.Join(s.root, "workspace", s.paths[it.ID]))
		it.Size = st.Size()
		if it.ContentType == "" {
			it.ContentType = mime.TypeByExtension(filepath.Ext(it.Name))
		}
	} else {
		var in struct{ Name, ParentID string }
		if json.NewDecoder(r.Body).Decode(&in) != nil {
			problem(w, 400, "invalid json")
			return
		}
		if in.Name == "" {
			in.Name = it.Name
		}
		if in.ParentID == "" {
			in.ParentID = it.ParentID
		}
		if !validName(in.Name) {
			problem(w, 400, "invalid name")
			return
		}
		parent, ok := s.items[in.ParentID]
		if !ok || parent.Kind != "folder" {
			problem(w, 400, "invalid parent")
			return
		}
		old := filepath.Join(s.root, "workspace", s.paths[it.ID])
		rel := filepath.Join(s.paths[parent.ID], in.Name)
		if err := os.Rename(old, filepath.Join(s.root, "workspace", rel)); err != nil {
			problem(w, 500, err.Error())
			return
		}
		it.Name, it.ParentID, s.paths[it.ID] = in.Name, in.ParentID, rel
	}
	it.Version++
	it.ETag = etag(it.ID, it.Version)
	it.ModifiedAt = time.Now().UTC()
	s.items[it.ID] = it
	s.emit("updated", it)
	if err := s.saveState(); err != nil {
		problem(w, 500, err.Error())
		return
	}
	json.NewEncoder(w).Encode(it)
}
func (s *Server) remove(w http.ResponseWriter, r *http.Request, it Item) {
	if !match(r, it) {
		problem(w, 409, "etag mismatch")
		return
	}
	if err := os.RemoveAll(filepath.Join(s.root, "workspace", s.paths[it.ID])); err != nil {
		problem(w, 500, err.Error())
		return
	}
	it.Deleted = true
	it.Version++
	it.ETag = etag(it.ID, it.Version)
	it.ModifiedAt = time.Now().UTC()
	s.items[it.ID] = it
	s.emit("deleted", it)
	if err := s.saveState(); err != nil {
		problem(w, 500, err.Error())
		return
	}
	w.WriteHeader(204)
}
func (s *Server) download(w http.ResponseWriter, r *http.Request, it Item) {
	if it.Kind != "file" {
		problem(w, 400, "folder has no content")
		return
	}
	f, e := os.Open(filepath.Join(s.root, "workspace", s.paths[it.ID]))
	if e != nil {
		problem(w, 404, "content missing")
		return
	}
	defer f.Close()
	w.Header().Set("Content-Type", it.ContentType)
	w.Header().Set("ETag", it.ETag)
	http.ServeContent(w, r, it.Name, it.ModifiedAt, f)
}
func (s *Server) changesRoute(w http.ResponseWriter, r *http.Request, wid string) {
	if wid != s.workspace.ID || r.URL.Query().Get("view") != "changes" {
		problem(w, 404, "workspace not found")
		return
	}
	after, _ := strconv.ParseInt(r.URL.Query().Get("cursor"), 10, 64)
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := []Change{}
	for _, c := range s.changes {
		if c.Cursor > after {
			out = append(out, c)
		}
	}
	json.NewEncoder(w).Encode(map[string]any{"changes": out, "cursor": s.cursor, "hasMore": false})
}
func (s *Server) emit(t string, it Item) {
	s.cursor++
	s.changes = append(s.changes, Change{Cursor: s.cursor, Type: t, Item: it})
}
func split(p string) []string {
	p = strings.Trim(p, "/")
	if p == "" {
		return nil
	}
	return strings.Split(p, "/")
}
func validName(v string) bool {
	return v != "" && v != "." && v != ".." && !strings.ContainsAny(v, "/\\\x00")
}
func match(r *http.Request, it Item) bool {
	v := strings.Trim(r.Header.Get("If-Match"), "\"")
	return v != "" && v == it.ETag
}
func etag(id string, v int64) string {
	h := sha256.Sum256([]byte(fmt.Sprintf("%s:%d", id, v)))
	return hex.EncodeToString(h[:12])
}
func newID() string {
	b := make([]byte, 16)
	rand.Read(b)
	b[6] = (b[6] & 15) | 64
	b[8] = (b[8] & 63) | 128
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[:4], b[4:6], b[6:8], b[8:10], b[10:])
}
func env(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}
func problem(w http.ResponseWriter, n int, msg string) {
	w.WriteHeader(n)
	json.NewEncoder(w).Encode(map[string]any{"status": n, "error": msg})
}
func main() {
	s, e := newServer(env("DATASPACE_STORAGE_ROOT", "/data"), os.Getenv("DATASPACE_DEVICE_TOKEN"))
	if e != nil {
		log.Fatal(e)
	}
	addr := env("DATASPACE_LISTEN_ADDR", ":8080")
	log.Printf("dataspace gateway listening on %s", addr)
	log.Fatal(http.ListenAndServe(addr, s))
}
