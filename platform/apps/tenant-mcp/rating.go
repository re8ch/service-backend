package main

import (
	_ "embed"
	"encoding/json"
	"fmt"
	"math"
	"sort"
	"sync"
)

//go:embed rate_card.json
var rateCardJSON []byte

type rate struct {
	ServiceClass string  `json:"serviceClass"`
	Meter        string  `json:"meter"`
	Unit         string  `json:"unit"`
	Per          float64 `json:"per"`
	Price        float64 `json:"price"`
	Billable     bool    `json:"billable"`
	Reason       string  `json:"reason,omitempty"`
}

type rateCard struct {
	Version       string `json:"version"`
	Status        string `json:"status"`
	Mode          string `json:"mode"`
	Currency      string `json:"currency"`
	EffectiveFrom string `json:"effectiveFrom"`
	Notice        string `json:"notice"`
	Rates         []rate `json:"rates"`
}

var (
	cardOnce sync.Once
	card     rateCard
	cardErr  error
	cardIdx  map[string]rate
)

func loadRateCard() (rateCard, map[string]rate, error) {
	cardOnce.Do(func() {
		if cardErr = json.Unmarshal(rateCardJSON, &card); cardErr != nil {
			return
		}
		cardIdx = map[string]rate{}
		for _, r := range card.Rates {
			if r.Per <= 0 || r.Price < 0 || math.IsNaN(r.Price) {
				cardErr = fmt.Errorf("invalid rate %s/%s", r.ServiceClass, r.Meter)
				return
			}
			cardIdx[r.ServiceClass+"|"+r.Meter+"|"+r.Unit] = r
		}
	})
	return card, cardIdx, cardErr
}

func round6(v float64) float64 { return math.Round(v*1e6) / 1e6 }

// rateUsage prices billingUsage items under the demo rate card. The result is
// showback only: accountingPosted stays false and nothing is debited.
func rateUsage(usage map[string]any) (map[string]any, error) {
	c, idx, err := loadRateCard()
	if err != nil {
		return nil, err
	}
	items, _ := usage["items"].([]map[string]any)
	lines := []map[string]any{}
	unrated := []map[string]any{}
	totals := map[string]float64{}
	total := 0.0
	for _, item := range items {
		key := fmt.Sprint(item["serviceClass"], "|", item["meter"], "|", item["unit"])
		r, ok := idx[key]
		if !ok {
			unrated = append(unrated, item)
			continue
		}
		qty, _ := item["quantity"].(float64)
		line := map[string]any{"serviceClass": r.ServiceClass, "resourceRef": item["resourceRef"], "meter": r.Meter, "unit": r.Unit,
			"quantity": qty, "eventCount": item["eventCount"], "billable": r.Billable, "per": r.Per, "unitPrice": r.Price, "amount": 0.0}
		if r.Billable {
			amount := round6(qty / r.Per * r.Price)
			line["amount"] = amount
			totals[r.ServiceClass] += amount
			total += amount
		}
		lines = append(lines, line)
	}
	services := []map[string]any{}
	names := make([]string, 0, len(totals))
	for name := range totals {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		services = append(services, map[string]any{"serviceClass": name, "amount": round6(totals[name])})
	}
	return map[string]any{"tenantRef": usage["tenantRef"], "windowStart": usage["windowStart"], "windowEnd": usage["windowEnd"],
		"rateCard":       map[string]any{"version": c.Version, "status": c.Status, "mode": c.Mode, "currency": c.Currency, "notice": c.Notice},
		"pricingApplied": true, "accountingPosted": false, "total": round6(total), "services": services, "lines": lines, "unrated": unrated}, nil
}

// writeBillingMetrics exposes service-level rate-card facts; per-tenant series
// are deliberately omitted to keep metric cardinality bounded.
func writeBillingMetrics(w interface{ Write([]byte) (int, error) }) {
	c, _, err := loadRateCard()
	if err != nil {
		return
	}
	fmt.Fprintf(w, "# HELP re8ch_billing_rate_card_info Active billing rate card.\n# TYPE re8ch_billing_rate_card_info gauge\nre8ch_billing_rate_card_info{version=%q,status=%q,mode=%q,currency=%q} 1\n", c.Version, c.Status, c.Mode, c.Currency)
	fmt.Fprint(w, "# HELP re8ch_billing_rate_card_meters Meters on the rate card by service class and billable flag.\n# TYPE re8ch_billing_rate_card_meters gauge\n")
	counts := map[string]int{}
	for _, r := range c.Rates {
		counts[fmt.Sprintf("service_class=%q,billable=%q", r.ServiceClass, fmt.Sprint(r.Billable))]++
	}
	keys := make([]string, 0, len(counts))
	for k := range counts {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		fmt.Fprintf(w, "re8ch_billing_rate_card_meters{%s} %d\n", k, counts[k])
	}
	billingRated.write(w)
}

type ratedCounters struct {
	mu       sync.Mutex
	requests uint64
	unrated  uint64
}

var billingRated = &ratedCounters{}

func (c *ratedCounters) observe(result map[string]any) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.requests++
	if u, ok := result["unrated"].([]map[string]any); ok {
		c.unrated += uint64(len(u))
	}
}

func (c *ratedCounters) write(w interface{ Write([]byte) (int, error) }) {
	c.mu.Lock()
	defer c.mu.Unlock()
	fmt.Fprintf(w, "# HELP re8ch_billing_charge_requests_total Rated charge reads.\n# TYPE re8ch_billing_charge_requests_total counter\nre8ch_billing_charge_requests_total %d\n", c.requests)
	fmt.Fprintf(w, "# HELP re8ch_billing_unrated_items_total Usage items with no rate-card entry.\n# TYPE re8ch_billing_unrated_items_total counter\nre8ch_billing_unrated_items_total %d\n", c.unrated)
}
