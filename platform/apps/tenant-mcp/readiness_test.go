package main

import "testing"

func TestReadyForTrafficKeepsDiscoveryAvailableDuringDependencyOutage(t *testing.T) {
	checks := map[string]string{
		"dynamicSkills": "ok", "stateStore": "ok", "redemptionRegistry": "ok",
		"claimServiceCredential": "ok", "auth": "failed", "tenantAuthorizer": "failed",
		"supplyChain": "failed",
	}
	if !readyForTraffic(checks, false) {
		t.Fatal("remote dependency outage removed the MCP endpoint")
	}
	if readyForTraffic(checks, true) {
		t.Fatal("required supply chain failure must block readiness")
	}
	checks["stateStore"] = "failed"
	if readyForTraffic(checks, false) {
		t.Fatal("local state failure must block readiness")
	}
}
