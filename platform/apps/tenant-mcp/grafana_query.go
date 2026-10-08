package main

import (
	"context"
	"errors"
	"fmt"
	"math"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/VictoriaMetrics/metricsql"
)

var durationPattern = regexp.MustCompile(`^(\d+)(ms|s|m|h|d|w)$`)
var relativeTimePattern = regexp.MustCompile(`^now-(\d+(?:ms|s|m|h|d|w))$`)
var refIDPattern = regexp.MustCompile(`^[A-Za-z][A-Za-z0-9_]{0,15}$`)
var sqlStartPattern = regexp.MustCompile(`(?i)^\s*(select|with)\b`)
var sqlForbiddenPattern = regexp.MustCompile(`(?i)(--|/\*|\*/|\b(copy|call|do|execute|insert|update|delete|alter|drop|create|grant|revoke|truncate)\b)`)

func durationMS(value string) (int64, error) {
	match := durationPattern.FindStringSubmatch(value)
	if match == nil {
		return 0, errors.New("invalid duration")
	}
	amount, err := strconv.ParseInt(match[1], 10, 64)
	if err != nil {
		return 0, errors.New("invalid duration")
	}
	unit := map[string]int64{"ms": 1, "s": 1000, "m": 60000, "h": 3600000, "d": 86400000, "w": 604800000}[match[2]]
	if amount > math.MaxInt64/unit {
		return 0, errors.New("invalid duration")
	}
	return amount * unit, nil
}

func grafanaTime(value any, current int64) (int64, error) {
	if value == nil || value == "now" {
		return current, nil
	}
	if number, ok := value.(float64); ok {
		return int64(number), nil
	}
	if number, ok := value.(int); ok {
		return int64(number), nil
	}
	if text, ok := value.(string); ok {
		if match := relativeTimePattern.FindStringSubmatch(text); match != nil {
			duration, err := durationMS(match[1])
			return current - duration, err
		}
	}
	return 0, errors.New("time must be epoch milliseconds, now, or now-<duration>")
}

func queryText(query map[string]any) string {
	model := object(query["model"])
	for _, key := range []string{"expr", "query", "rawSql"} {
		if value := asString(model, key); value != "" {
			return value
		}
	}
	return ""
}

func validateSQL(sql string) error {
	trimmed := strings.TrimSpace(sql)
	if !sqlStartPattern.MatchString(trimmed) {
		return errors.New("SQL must be a SELECT or WITH query")
	}
	if strings.Contains(strings.TrimRight(trimmed, ";"), ";") || sqlForbiddenPattern.MatchString(trimmed) {
		return errors.New("SQL contains a forbidden statement or token")
	}
	return nil
}

// MetricsQL supports both binary `or` and or-delimited filter groups inside a
// selector. Every selector branch must carry the tenant equality, not merely
// one matching substring somewhere in the query.
func validateMetricsTenantScope(query, tenant string) error {
	expr, err := metricsql.Parse(query)
	if err != nil {
		return errors.New("invalid metrics query")
	}
	selectors, scoped := 0, true
	metricsql.VisitAll(expr, func(node metricsql.Expr) {
		selector, ok := node.(*metricsql.MetricExpr)
		if !ok {
			return
		}
		selectors++
		if len(selector.LabelFilterss) == 0 {
			scoped = false
		}
		for _, group := range selector.LabelFilterss {
			matched := false
			for _, filter := range group {
				if filter.Label == "tenant" && filter.Value == tenant && !filter.IsNegative && !filter.IsRegexp {
					matched = true
				}
			}
			if !matched {
				scoped = false
			}
		}
	})
	if selectors == 0 || !scoped {
		return errors.New("every metrics selector branch requires an exact authenticated tenant matcher")
	}
	return nil
}

func validateGrafanaSpec(spec map[string]any, binding grafanaBinding, datasources []map[string]any) (int64, int64, error) {
	if _, exists := spec["orgId"]; exists {
		return 0, 0, errors.New("organization identifiers are derived from the authenticated tenant")
	}
	if _, exists := spec["organizationId"]; exists {
		return 0, 0, errors.New("organization identifiers are derived from the authenticated tenant")
	}
	queries := slice(spec["queries"])
	if len(queries) < 1 || len(queries) > 8 {
		return 0, 0, errors.New("queries must contain between 1 and 8 entries")
	}
	now := time.Now().UnixMilli()
	rangeSpec := object(spec["timeRange"])
	from := rangeSpec["from"]
	if from == nil {
		from = "now-1h"
	}
	to := rangeSpec["to"]
	if to == nil {
		to = "now"
	}
	start, err := grafanaTime(from, now)
	if err != nil {
		return 0, 0, err
	}
	end, err := grafanaTime(to, now)
	if err != nil {
		return 0, 0, err
	}
	if start >= end || end-start > 31*24*60*60*1000 {
		return 0, 0, errors.New("time range must be positive and no longer than 31 days")
	}
	types := map[string]string{}
	kinds := map[string]string{}
	for _, item := range datasources {
		uid := asString(item, "uid")
		types[uid] = asString(item, "type")
		if supported := datasourceCapability(item)["queryKinds"].([]string); len(supported) == 1 {
			kinds[uid] = supported[0]
		}
	}
	seen := map[string]bool{}
	tenantMatcher := regexp.MustCompile(`tenant\s*=~?\s*["']` + regexp.QuoteMeta(binding.TenantLabel) + `["']`)
	for _, raw := range queries {
		query := object(raw)
		uid, ref := asString(query, "datasourceUid"), asString(query, "refId")
		dsType, ok := types[uid]
		if !ok {
			return 0, 0, errors.New("datasource is not visible to the authenticated tenant")
		}
		if !refIDPattern.MatchString(ref) || seen[ref] {
			return 0, 0, errors.New("query refId must be unique and identifier-like")
		}
		seen[ref] = true
		points := 1000
		if value, ok := query["maxDataPoints"]; ok {
			points = intValue(value)
		}
		if points < 1 || points > 5000 {
			return 0, 0, errors.New("maxDataPoints must be between 1 and 5000")
		}
		kind, text := strings.ToLower(asString(query, "queryKind")), queryText(query)
		if kinds[uid] == "" || kind != "" && kind != kinds[uid] {
			return 0, 0, errors.New("query kind is not supported by the selected datasource")
		}
		dsType = strings.ToLower(dsType)
		if kinds[uid] == "promql" {
			model := object(query["model"])
			if asString(model, "expr") == "" || model["query"] != nil || model["rawSql"] != nil {
				return 0, 0, errors.New("metrics queries require a single expr field")
			}
			if err := validateMetricsTenantScope(text, binding.TenantLabel); err != nil {
				return 0, 0, err
			}
		}
		if kinds[uid] == "logsql" {
			if !tenantMatcher.MatchString(text) {
				return 0, 0, errors.New("tenant matcher is required in logs queries")
			}
		}
		if kinds[uid] == "sql" || dsType == "postgres" || dsType == "mysql" || dsType == "mssql" {
			if err := validateSQL(text); err != nil {
				return 0, 0, err
			}
		}
	}
	return start, end, nil
}

func normalizeFrames(value any) (map[string]any, error) {
	results := map[string]any{}
	total := 0
	for ref, raw := range object(object(value)["results"]) {
		item := object(raw)
		frames := []map[string]any{}
		for _, rawFrame := range slice(item["frames"]) {
			frame := object(rawFrame)
			data := object(frame["data"])
			longest := 0
			for _, column := range slice(data["values"]) {
				longest = max(longest, len(slice(column)))
			}
			total += longest
			frames = append(frames, map[string]any{"schema": object(frame["schema"]), "data": data})
		}
		results[ref] = map[string]any{"frames": frames, "error": item["error"]}
	}
	if total > 50000 {
		return nil, errors.New("query returned more than 50000 points; increase interval or reduce the range")
	}
	return map[string]any{"results": results, "pointCount": total}, nil
}

func (s *server) grafanaQuery(ctx context.Context, tenant string, client *grafanaClient, spec map[string]any, datasources []map[string]any) (map[string]any, error) {
	binding, err := tenantGrafanaBinding(tenant)
	if err != nil {
		if !strings.HasPrefix(tenant, "personal-") || client == nil || client.orgID <= 1 {
			return nil, err
		}
		binding = grafanaBinding{OrgID: client.orgID, TenantLabel: tenant}
	}
	start, end, err := validateGrafanaSpec(spec, binding, datasources)
	if err != nil {
		return nil, err
	}
	byUID := map[string]map[string]any{}
	for _, item := range datasources {
		byUID[asString(item, "uid")] = item
	}
	queries := []map[string]any{}
	for _, raw := range slice(spec["queries"]) {
		entry := object(raw)
		model := map[string]any{}
		for key, value := range object(entry["model"]) {
			model[key] = value
		}
		uid := asString(entry, "datasourceUid")
		points := 1000
		if entry["maxDataPoints"] != nil {
			points = intValue(entry["maxDataPoints"])
		}
		intervalText := asString(object(byUID[uid]["jsonData"]), "timeInterval")
		if intervalText == "" {
			intervalText = "1s"
		}
		minimum, err := durationMS(intervalText)
		if err != nil {
			return nil, err
		}
		requested := int64(0)
		if entry["intervalMs"] != nil {
			requested = int64(intValue(entry["intervalMs"]))
			if requested < minimum {
				return nil, errors.New("intervalMs is below the datasource minimum interval")
			}
		}
		interval := max(minimum, int64(math.Ceil(float64(end-start)/float64(points))), requested)
		model["refId"], model["datasource"], model["maxDataPoints"], model["intervalMs"] = entry["refId"], map[string]any{"uid": uid}, points, interval
		queries = append(queries, model)
	}
	value, err := client.request(ctx, http.MethodPost, "/api/ds/query", map[string]any{"from": fmt.Sprint(start), "to": fmt.Sprint(end), "queries": queries}, 30*time.Second, false)
	if err != nil {
		return nil, err
	}
	result, err := normalizeFrames(value)
	if err != nil {
		return nil, err
	}
	timezone := object(spec["timeRange"])["timezone"]
	if timezone == nil {
		timezone = "UTC"
	}
	hash, err := grafanaHash(spec)
	if err != nil {
		return nil, err
	}
	result["timeRange"], result["queryHash"], result["warnings"] = map[string]any{"from": start, "to": end, "timezone": timezone}, hash, []any{}
	ref := s.resultPut(tenant, result)
	result["resultRef"], result["resultUri"] = ref, "re8ch://tenant/"+tenant+"/grafana/results/"+ref
	return result, nil
}
