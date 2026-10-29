package main

import (
	"encoding/json"
	"os"
	"testing"
)

func TestRateCardCoversEveryServiceMeter(t *testing.T) {
	_, idx, err := loadRateCard()
	if err != nil {
		t.Fatal(err)
	}
	var classes []struct {
		ServiceClass string            `json:"serviceClass"`
		Meters       map[string]string `json:"meters"`
	}
	data, err := os.ReadFile("service_catalog.json")
	if err != nil {
		t.Fatal(err)
	}
	if err = json.Unmarshal(data, &classes); err != nil {
		t.Fatal(err)
	}
	for _, c := range classes {
		for meter, unit := range c.Meters {
			if _, ok := idx[c.ServiceClass+"|"+meter+"|"+unit]; !ok {
				t.Errorf("rate card misses %s/%s (%s)", c.ServiceClass, meter, unit)
			}
		}
	}
}

func TestRateUsagePricesBillableMetersOnly(t *testing.T) {
	usage := map[string]any{"tenantRef": "re8ch", "items": []map[string]any{
		{"serviceClass": "ai.model-gateway.v1", "resourceRef": "r1", "meter": "input_tokens", "unit": "token", "quantity": 2e6, "eventCount": 4},
		{"serviceClass": "ai.model-gateway.v1", "resourceRef": "r1", "meter": "requests", "unit": "request", "quantity": 99.0, "eventCount": 1},
		{"serviceClass": "unknown.v1", "resourceRef": "r2", "meter": "x", "unit": "y", "quantity": 1.0, "eventCount": 1},
	}}
	got, err := rateUsage(usage)
	if err != nil {
		t.Fatal(err)
	}
	if got["total"] != 4.0 || got["accountingPosted"] != false || got["pricingApplied"] != true {
		t.Fatalf("unexpected result: %+v", got)
	}
	if len(got["unrated"].([]map[string]any)) != 1 || len(got["lines"].([]map[string]any)) != 2 {
		t.Fatalf("unexpected lines: %+v", got)
	}
}
