package main

import (
	"context"
	"database/sql"
	"strconv"
	"strings"
)

// runtimeDB keeps the existing database/sql call sites while binding their
// parameters for the selected authority. PostgreSQL is the production state
// authority; SQLite remains only for hermetic tests and a separately invoked
// offline import path, never as a production runtime fallback.
type runtimeDB struct {
	db      *sql.DB
	dialect string
	owner   bool
}

type runtimeTx struct {
	tx      *sql.Tx
	dialect string
}

func runtimePostgres(db *sql.DB) *runtimeDB {
	return &runtimeDB{db: db, dialect: "postgres"}
}

func (d *runtimeDB) postgres() bool { return d != nil && d.dialect == "postgres" }

func bindRuntimeSQL(query, dialect string) string {
	if dialect != "postgres" {
		return query
	}
	var out strings.Builder
	index := 1
	for _, r := range query {
		if r == '?' {
			out.WriteByte('$')
			out.WriteString(strconv.Itoa(index))
			index++
			continue
		}
		out.WriteRune(r)
	}
	return out.String()
}

func (d *runtimeDB) Exec(query string, args ...any) (sql.Result, error) {
	return d.db.Exec(bindRuntimeSQL(query, d.dialect), args...)
}
func (d *runtimeDB) ExecContext(ctx context.Context, query string, args ...any) (sql.Result, error) {
	return d.db.ExecContext(ctx, bindRuntimeSQL(query, d.dialect), args...)
}
func (d *runtimeDB) Query(query string, args ...any) (*sql.Rows, error) {
	return d.db.Query(bindRuntimeSQL(query, d.dialect), args...)
}
func (d *runtimeDB) QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error) {
	return d.db.QueryContext(ctx, bindRuntimeSQL(query, d.dialect), args...)
}
func (d *runtimeDB) QueryRow(query string, args ...any) *sql.Row {
	return d.db.QueryRow(bindRuntimeSQL(query, d.dialect), args...)
}
func (d *runtimeDB) QueryRowContext(ctx context.Context, query string, args ...any) *sql.Row {
	return d.db.QueryRowContext(ctx, bindRuntimeSQL(query, d.dialect), args...)
}
func (d *runtimeDB) PingContext(ctx context.Context) error { return d.db.PingContext(ctx) }
func (d *runtimeDB) Begin() (*runtimeTx, error) {
	tx, err := d.db.Begin()
	if err != nil {
		return nil, err
	}
	return &runtimeTx{tx: tx, dialect: d.dialect}, nil
}
func (d *runtimeDB) BeginTx(ctx context.Context, opts *sql.TxOptions) (*runtimeTx, error) {
	tx, err := d.db.BeginTx(ctx, opts)
	if err != nil {
		return nil, err
	}
	return &runtimeTx{tx: tx, dialect: d.dialect}, nil
}
func (d *runtimeDB) Close() error {
	if d == nil || !d.owner {
		return nil
	}
	return d.db.Close()
}

func (t *runtimeTx) Exec(query string, args ...any) (sql.Result, error) {
	return t.tx.Exec(bindRuntimeSQL(query, t.dialect), args...)
}
func (t *runtimeTx) ExecContext(ctx context.Context, query string, args ...any) (sql.Result, error) {
	return t.tx.ExecContext(ctx, bindRuntimeSQL(query, t.dialect), args...)
}
func (t *runtimeTx) QueryRow(query string, args ...any) *sql.Row {
	return t.tx.QueryRow(bindRuntimeSQL(query, t.dialect), args...)
}
func (t *runtimeTx) QueryRowContext(ctx context.Context, query string, args ...any) *sql.Row {
	return t.tx.QueryRowContext(ctx, bindRuntimeSQL(query, t.dialect), args...)
}
func (t *runtimeTx) Commit() error   { return t.tx.Commit() }
func (t *runtimeTx) Rollback() error { return t.tx.Rollback() }
