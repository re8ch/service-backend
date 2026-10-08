package main

import (
	"context"
	"errors"
	"regexp"
	"sort"
	"strings"
)

var databaseBindingName = regexp.MustCompile(`^[a-z][a-z0-9_]{0,62}$`)

// databasecredential is a metadata-only view over operator-provisioned Pigsty
// Secrets. The template never sends Secret data values back to the MCP process.
const databaseBindingTemplate = `{{range .items}}{{.metadata.name}}{{"\t"}}{{.metadata.uid}}{{"\t"}}{{index .metadata.labels "re8ch.com/database"}}{{"\t"}}{{range $key, $_ := .data}}{{$key}}{{","}}{{end}}{{"\n"}}{{end}}`

func parseDatabaseBindings(output, tenant, namespace string) (map[string]any, error) {
	items := []any{}
	for _, line := range strings.Split(strings.TrimSpace(output), "\n") {
		if line == "" {
			continue
		}
		fields := strings.SplitN(line, "\t", 4)
		if len(fields) != 4 || !validKubernetesName(fields[0]) || !databaseBindingName.MatchString(fields[2]) || fields[1] == "" {
			return nil, errors.New("invalid database credential metadata response")
		}
		keys := []string{}
		for _, key := range strings.Split(strings.TrimSuffix(fields[3], ","), ",") {
			if key != "" {
				if !secretKey.MatchString(key) {
					return nil, errors.New("invalid database credential key metadata")
				}
				keys = append(keys, key)
			}
		}
		sort.Strings(keys)
		items = append(items, map[string]any{"database": fields[2], "secretRef": namespace + "/" + fields[0], "secretUID": fields[1], "keys": keys, "databaseState": "unverified"})
	}
	return map[string]any{"tenantRef": tenant, "namespace": namespace, "kind": "databasecredential", "items": items}, nil
}

func (s *server) databaseBindings(ctx context.Context, id identity, namespace, name string) (any, error) {
	if err := requireGrantedConsumable(ctx, id, "database.postgresql.shared.v1"); err != nil {
		return nil, err
	}
	argv := []string{"kubectl", "--namespace", namespace, "get", "secrets", "-l", "re8ch.com/secret-purpose=postgres-credentials,re8ch.com/tenant=" + namespace, "-o", "go-template=" + databaseBindingTemplate}
	if name != "" {
		if !validKubernetesName(name) {
			return nil, errors.New("invalid database credential Secret name")
		}
		argv = append(argv, "--field-selector", "metadata.name="+name)
	}
	result := commandResult(ctx, argv, nil, 120)
	if result["exit_code"] != 0 {
		return nil, errors.New(asString(result, "stderr"))
	}
	return parseDatabaseBindings(asString(result, "stdout"), id.Tenant, namespace)
}
