package main

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
)

const indexFile = ".storage-collaborator-index.json"

type persistedState struct {
	Workspace Workspace         `json:"workspace"`
	Items     map[string]Item   `json:"items"`
	Paths     map[string]string `json:"paths"`
	Changes   []Change          `json:"changes"`
	Cursor    int64             `json:"cursor"`
}

func (s *Server) loadState() error {
	b, err := os.ReadFile(filepath.Join(s.root, indexFile))
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	var state persistedState
	if err := json.Unmarshal(b, &state); err != nil {
		return err
	}
	if state.Workspace.ID == "" || state.Workspace.RootItemID == "" || state.Items == nil || state.Paths == nil {
		return errors.New("invalid persisted metadata index")
	}
	s.workspace, s.items, s.paths, s.changes, s.cursor = state.Workspace, state.Items, state.Paths, state.Changes, state.Cursor
	return nil
}

func (s *Server) saveState() error {
	state := persistedState{Workspace: s.workspace, Items: s.items, Paths: s.paths, Changes: s.changes, Cursor: s.cursor}
	b, err := json.MarshalIndent(state, "", "  ")
	if err != nil {
		return err
	}
	path := filepath.Join(s.root, indexFile)
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, b, 0640); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}
