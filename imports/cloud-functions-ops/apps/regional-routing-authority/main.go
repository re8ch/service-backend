package main

import (
	"bufio"
	"context"
	"crypto/tls"
	"encoding/binary"
	"encoding/json"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

type candidate struct {
	ID               string            `json:"id"`
	Region           string            `json:"region"`
	IP               string            `json:"ip"`
	LocalIP          string            `json:"localIp,omitempty"`
	Priority         int               `json:"priority"`
	PriorityByRegion map[string]int    `json:"priorityByRegion"`
	Probes           map[string]string `json:"probes"`
}

type observed struct {
	Ready       bool      `json:"ready"`
	StatusCode  int       `json:"statusCode"`
	LatencyMS   int64     `json:"latencyMs"`
	Successes   int       `json:"-"`
	Failures    int       `json:"-"`
	ObservedAt  time.Time `json:"observedAt"`
	FailureText string    `json:"failure,omitempty"`
}

type authority struct {
	node        string
	region      string
	zones       map[string]string
	nameservers []string
	candidates  []candidate
	mu          sync.RWMutex
	health      map[string]map[string]observed
}

type rankedCandidate struct {
	ID         string    `json:"id"`
	Region     string    `json:"region"`
	IP         string    `json:"ip"`
	Score      int       `json:"score"`
	State      string    `json:"state"`
	StatusCode int       `json:"statusCode,omitempty"`
	LatencyMS  int64     `json:"latencyMs,omitempty"`
	ObservedAt time.Time `json:"observedAt"`
	Reason     string    `json:"reason,omitempty"`
}

func env(name, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(name)); value != "" {
		return value
	}
	return fallback
}

func main() {
	var candidates []candidate
	if err := json.Unmarshal([]byte(env("CANDIDATES_JSON", "[]")), &candidates); err != nil {
		log.Fatal(err)
	}
	if len(candidates) == 0 {
		log.Fatal("CANDIDATES_JSON is empty")
	}
	zones := map[string]string{"api.re8ch.com.": "api", "db-ro.re8ch.com.": "db-ro", "db-rw.re8ch.com.": "db-rw"}
	a := &authority{
		node: env("NODE_NAME", "unknown"), region: env("NODE_REGION", "unknown"), zones: zones,
		nameservers: strings.Fields(env("NAMESERVERS", "ns-sh.re8ch.com. ns-gz.re8ch.com. ns-us.re8ch.com.")),
		candidates:  candidates, health: map[string]map[string]observed{},
	}
	for _, c := range candidates {
		if c.ID == a.node {
			a.region = c.Region
			break
		}
	}
	for _, service := range zones {
		a.health[service] = map[string]observed{}
	}
	a.probeAll()
	go a.probeLoop()
	go a.serveUDP()
	go a.serveTCP()
	http.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok\n"))
	})
	http.HandleFunc("/v1/discovery", a.discovery)
	log.Printf("node=%s region=%s dns=:53 http=:8080", a.node, a.region)
	log.Fatal(http.ListenAndServe(":8080", nil))
}

func (a *authority) probeLoop() {
	ticker := time.NewTicker(10 * time.Second)
	defer ticker.Stop()
	for range ticker.C {
		a.probeAll()
	}
}

func (a *authority) probeAll() {
	var wg sync.WaitGroup
	for _, c := range a.candidates {
		for service, rawURL := range c.Probes {
			c, service, rawURL := c, service, rawURL
			wg.Add(1)
			go func() { defer wg.Done(); a.probe(c, service, rawURL) }()
		}
	}
	wg.Wait()
}

func (a *authority) probe(c candidate, service, rawURL string) {
	started := time.Now()
	req, _ := http.NewRequestWithContext(context.Background(), http.MethodGet, rawURL, nil)
	dialer := &net.Dialer{Timeout: 3 * time.Second}
	probeIP := c.IP
	if c.ID == a.node && c.LocalIP != "" {
		probeIP = c.LocalIP
	}
	client := &http.Client{Timeout: 4 * time.Second, Transport: &http.Transport{
		TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS12, ServerName: req.URL.Hostname()},
		DialContext: func(ctx context.Context, network, _ string) (net.Conn, error) {
			return dialer.DialContext(ctx, network, net.JoinHostPort(probeIP, "443"))
		},
	}}
	response, err := client.Do(req)
	o := observed{ObservedAt: time.Now(), LatencyMS: time.Since(started).Milliseconds()}
	if err == nil {
		o.StatusCode = response.StatusCode
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 4096))
		_ = response.Body.Close()
	} else {
		o.FailureText = err.Error()
	}
	a.mu.Lock()
	previous := a.health[service][c.ID]
	eligibleStatus := response != nil && (response.StatusCode >= 200 && response.StatusCode < 400 || response.StatusCode == 401 || response.StatusCode == 403)
	if err == nil && eligibleStatus {
		o.Successes = previous.Successes + 1
		o.Ready = previous.Ready || o.Successes >= 2
	} else {
		o.Failures = previous.Failures + 1
		o.Ready = previous.Ready && o.Failures < 2
	}
	a.health[service][c.ID] = o
	a.mu.Unlock()
}

func (a *authority) ranked(service string) []rankedCandidate {
	a.mu.RLock()
	defer a.mu.RUnlock()
	result := make([]rankedCandidate, 0, len(a.candidates))
	for _, c := range a.candidates {
		o := a.health[service][c.ID]
		score := 0
		state := "unavailable"
		if o.Ready {
			state = "ready"
			priority := c.Priority
			if regionalPriority, ok := c.PriorityByRegion[a.region]; ok {
				priority = regionalPriority
			}
			score = 70 - priority - int(min(o.LatencyMS/20, 20))
			if c.ID == a.node {
				score += 20
			}
			if c.Region == a.region {
				score += 10
			}
		}
		result = append(result, rankedCandidate{ID: c.ID, Region: c.Region, IP: c.IP, Score: score, State: state, StatusCode: o.StatusCode, LatencyMS: o.LatencyMS, ObservedAt: o.ObservedAt, Reason: o.FailureText})
	}
	sort.SliceStable(result, func(i, j int) bool {
		if result[i].Score == result[j].Score {
			return result[i].ID < result[j].ID
		}
		return result[i].Score > result[j].Score
	})
	return result
}

func (a *authority) discovery(w http.ResponseWriter, r *http.Request) {
	service := strings.TrimSpace(r.URL.Query().Get("service"))
	if service == "db" {
		service = strings.TrimSpace(r.URL.Query().Get("mode"))
		if service != "" {
			service = "db-" + service
		}
	}
	if _, ok := map[string]bool{"api": true, "db-ro": true, "db-rw": true}[service]; !ok {
		http.Error(w, "service must be api, db-ro, db-rw, or db with mode", 400)
		return
	}
	ranked := a.ranked(service)
	selected := ""
	for _, c := range ranked {
		if c.State == "ready" {
			selected = c.ID
			break
		}
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "public, max-age=20, stale-while-revalidate=60")
	_ = json.NewEncoder(w).Encode(map[string]any{"version": 1, "generation": time.Now().UTC().Truncate(10 * time.Second).Unix(), "service": service, "selected": selected, "ttlSeconds": 30, "servedBy": a.node, "client": clientIP(r), "candidates": ranked})
}

func clientIP(r *http.Request) string {
	if value := strings.TrimSpace(strings.Split(r.Header.Get("X-Forwarded-For"), ",")[0]); value != "" {
		return value
	}
	host, _, _ := net.SplitHostPort(r.RemoteAddr)
	return host
}

func (a *authority) serveUDP() {
	conn, err := net.ListenPacket("udp", ":53")
	if err != nil {
		log.Fatal(err)
	}
	buffer := make([]byte, 4096)
	for {
		n, addr, err := conn.ReadFrom(buffer)
		if err != nil {
			continue
		}
		response := a.dnsResponse(buffer[:n])
		if len(response) > 0 {
			_, _ = conn.WriteTo(response, addr)
		}
	}
}

func (a *authority) serveTCP() {
	listener, err := net.Listen("tcp", ":53")
	if err != nil {
		log.Fatal(err)
	}
	for {
		conn, err := listener.Accept()
		if err != nil {
			continue
		}
		go a.handleTCP(conn)
	}
}

func (a *authority) handleTCP(conn net.Conn) {
	defer conn.Close()
	_ = conn.SetDeadline(time.Now().Add(5 * time.Second))
	reader := bufio.NewReader(conn)
	lengthBytes := make([]byte, 2)
	if _, err := io.ReadFull(reader, lengthBytes); err != nil {
		return
	}
	n := int(binary.BigEndian.Uint16(lengthBytes))
	if n < 12 || n > 4096 {
		return
	}
	query := make([]byte, n)
	if _, err := io.ReadFull(reader, query); err != nil {
		return
	}
	response := a.dnsResponse(query)
	if len(response) == 0 {
		return
	}
	frame := make([]byte, 2+len(response))
	binary.BigEndian.PutUint16(frame, uint16(len(response)))
	copy(frame[2:], response)
	_, _ = conn.Write(frame)
}

func parseName(packet []byte, offset int) (string, int, bool) {
	labels := []string{}
	start := offset
	for {
		if offset >= len(packet) {
			return "", start, false
		}
		n := int(packet[offset])
		offset++
		if n == 0 {
			break
		}
		if n > 63 || offset+n > len(packet) {
			return "", start, false
		}
		labels = append(labels, string(packet[offset:offset+n]))
		offset += n
	}
	return strings.ToLower(strings.Join(labels, ".")) + ".", offset, true
}

func appendName(dst []byte, name string) []byte {
	for _, label := range strings.Split(strings.TrimSuffix(name, "."), ".") {
		dst = append(dst, byte(len(label)))
		dst = append(dst, []byte(label)...)
	}
	return append(dst, 0)
}

func appendRR(dst []byte, name string, typ uint16, ttl uint32, data []byte) []byte {
	dst = appendName(dst, name)
	fixed := make([]byte, 10)
	binary.BigEndian.PutUint16(fixed[0:2], typ)
	binary.BigEndian.PutUint16(fixed[2:4], 1)
	binary.BigEndian.PutUint32(fixed[4:8], ttl)
	binary.BigEndian.PutUint16(fixed[8:10], uint16(len(data)))
	dst = append(dst, fixed...)
	return append(dst, data...)
}

func (a *authority) dnsResponse(query []byte) []byte {
	if len(query) < 12 {
		return nil
	}
	qd := binary.BigEndian.Uint16(query[4:6])
	if qd != 1 {
		return nil
	}
	name, off, ok := parseName(query, 12)
	if !ok || off+4 > len(query) {
		return nil
	}
	qtype := binary.BigEndian.Uint16(query[off : off+2])
	questionEnd := off + 4
	service, known := a.zones[name]
	flags := uint16(0x8400) | (binary.BigEndian.Uint16(query[2:4]) & 0x0100)
	rcode := uint16(0)
	answers := [][]byte{}
	if !known {
		rcode = 3
	} else {
		switch qtype {
		case 1, 255:
			for _, c := range a.ranked(service) {
				if c.State == "ready" {
					ip := net.ParseIP(c.IP).To4()
					if ip != nil {
						answers = append(answers, appendRR(nil, name, 1, 30, ip))
					}
					break
				}
			}
		case 2:
			for _, ns := range a.nameservers {
				answers = append(answers, appendRR(nil, name, 2, 300, appendName(nil, ns)))
			}
		case 6:
			mname := appendName(nil, a.nameservers[0])
			rname := appendName(nil, "hostmaster.re8ch.com.")
			tail := make([]byte, 20)
			serial, _ := strconv.ParseUint(time.Now().UTC().Format("20060102")+"01", 10, 32)
			binary.BigEndian.PutUint32(tail[0:4], uint32(serial))
			binary.BigEndian.PutUint32(tail[4:8], 60)
			binary.BigEndian.PutUint32(tail[8:12], 60)
			binary.BigEndian.PutUint32(tail[12:16], 86400)
			binary.BigEndian.PutUint32(tail[16:20], 30)
			answers = append(answers, appendRR(nil, name, 6, 300, append(append(mname, rname...), tail...)))
		}
	}
	flags |= rcode
	response := make([]byte, 12)
	copy(response[0:2], query[0:2])
	binary.BigEndian.PutUint16(response[2:4], flags)
	binary.BigEndian.PutUint16(response[4:6], 1)
	binary.BigEndian.PutUint16(response[6:8], uint16(len(answers)))
	response = append(response, query[12:questionEnd]...)
	for _, rr := range answers {
		response = append(response, rr...)
	}
	return response
}

func min(a, b int64) int64 {
	if a < b {
		return a
	}
	return b
}
