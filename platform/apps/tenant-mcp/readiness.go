package main

import (
	"context"
	"net/http"
	"os"
	"strings"
	"sync"
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
	type probeResult struct{ name, status string }
	results := make(chan probeResult, len(urls))
	var probes sync.WaitGroup
	for name, target := range urls {
		probes.Add(1)
		go func() {
			defer probes.Done()
			status := "failed"
			if target != "" {
				req, err := http.NewRequestWithContext(ctx, http.MethodGet, target, nil)
				if err == nil {
					req.Header.Set("Accept", "application/json")
					response, err := client.Do(req)
					if err == nil {
						response.Body.Close()
						if response.StatusCode < 400 {
							status = "ok"
						}
					}
				}
			}
			results <- probeResult{name, status}
		}()
	}
	probes.Wait()
	close(results)
	for result := range results {
		checks[result.name] = result.status
	}
	return readyForTraffic(checks, strings.EqualFold(os.Getenv("HARBOR_SUPPLY_CHAIN_REQUIRED"), "true")), checks
}

func readyForTraffic(checks map[string]string, supplyChainRequired bool) bool {
	// External dependencies fail their own requests closed. Removing the only MCP
	// endpoint during a transient control-plane outage also blocks discovery and
	// unrelated platform tools, so only local state gates endpoint readiness.
	for _, name := range []string{"dynamicSkills", "stateStore", "redemptionRegistry", "claimServiceCredential"} {
		if checks[name] != "ok" {
			return false
		}
	}
	return !supplyChainRequired || checks["supplyChain"] == "ok"
}
