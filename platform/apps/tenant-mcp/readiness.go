package main

import (
	"context"
	"net/http"
	"os"
	"strings"
	"time"
)

func (s *server) readiness(ctx context.Context) (bool, map[string]string) {
	checks := map[string]string{}
	if err := s.db.PingContext(ctx); err != nil {
		checks["dynamicSkills"] = "failed"
	} else {
		checks["dynamicSkills"] = "ok"
	}
	if s.ledger == nil {
		checks["stateStore"] = "failed"
	} else if err := s.ledger.PingContext(ctx); err != nil {
		checks["stateStore"] = "failed"
	} else {
		checks["stateStore"] = "ok"
	}
	if s.redemptions == nil {
		checks["redemptionRegistry"] = "failed"
	} else if err := s.redemptions.PingContext(ctx); err != nil {
		checks["redemptionRegistry"] = "failed"
	} else {
		checks["redemptionRegistry"] = "ok"
	}
	if os.Getenv("TENANT_CLAIM_SERVICE_TOKEN") == "" {
		checks["claimServiceCredential"] = "failed"
	} else {
		checks["claimServiceCredential"] = "ok"
	}
	urls := map[string]string{"auth": os.Getenv("AUTH_DISCOVERY_URL"), "tenantAuthorizer": strings.TrimRight(os.Getenv("TENANT_CONTROL_URL"), "/") + "/readyz", "supplyChain": strings.TrimRight(os.Getenv("HARBOR_SUPPLY_CHAIN_URL"), "/") + "/healthz"}
	if urls["auth"] == "" {
		urls["auth"] = "https://auth.re8ch.com/.well-known/openid-configuration"
	}
	if os.Getenv("TENANT_CONTROL_URL") == "" {
		urls["tenantAuthorizer"] = ""
	}
	if os.Getenv("HARBOR_SUPPLY_CHAIN_URL") == "" {
		urls["supplyChain"] = "http://harbor-supply-chain.tenant-infrastructure.svc/healthz"
	}
	client := &http.Client{Timeout: 3 * time.Second}
	for name, target := range urls {
		if target == "" {
			checks[name] = "failed"
			continue
		}
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, target, nil)
		if err != nil {
			checks[name] = "failed"
			continue
		}
		req.Header.Set("Accept", "application/json")
		response, err := client.Do(req)
		if err != nil {
			checks[name] = "failed"
			continue
		}
		response.Body.Close()
		if response.StatusCode < 400 {
			checks[name] = "ok"
		} else {
			checks[name] = "failed"
		}
	}
	ready := true
	for name, value := range checks {
		if value != "ok" && (name != "supplyChain" || strings.EqualFold(os.Getenv("HARBOR_SUPPLY_CHAIN_REQUIRED"), "true")) {
			ready = false
		}
	}
	return ready, checks
}
