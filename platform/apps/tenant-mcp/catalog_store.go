package main

import (
	"context"
	"database/sql"
	_ "embed"
	"errors"
	"fmt"
	"strings"

	_ "github.com/jackc/pgx/v5/stdlib"
)

//go:embed catalog-postgresql.sql
var catalogPostgreSQLSchema string

func openCatalogPostgreSQL(ctx context.Context, dsn string) (*sql.DB, error) {
	if strings.TrimSpace(dsn) == "" {
		return nil, errors.New("MCP catalog PostgreSQL DSN is empty")
	}
	db, err := sql.Open("pgx", dsn)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(10)
	if err = db.PingContext(ctx); err == nil {
		_, err = db.ExecContext(ctx, catalogPostgreSQLSchema)
	}
	if err != nil {
		db.Close()
		return nil, fmt.Errorf("initialize MCP catalog PostgreSQL: %w", err)
	}
	return db, nil
}

func (s *server) catalogDB() *sql.DB {
	if s.catalog != nil {
		return s.catalog
	}
	return s.ledger
}

func (s *server) catalogSQL(query string) string {
	if s.catalog == nil {
		return query
	}
	var out strings.Builder
	argument := 1
	for _, r := range query {
		if r == '?' {
			fmt.Fprintf(&out, "$%d", argument)
			argument++
		} else {
			out.WriteRune(r)
		}
	}
	return out.String()
}
