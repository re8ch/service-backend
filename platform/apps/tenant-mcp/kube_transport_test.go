package main

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync/atomic"
	"testing"
	"time"
)

func kubeTestCertificate(t *testing.T) (tls.Certificate, []byte) {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	serial, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 120))
	if err != nil {
		t.Fatal(err)
	}
	template := &x509.Certificate{SerialNumber: serial, Subject: pkix.Name{CommonName: "test Kubernetes API"}, NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour), IPAddresses: []net.IP{net.ParseIP("127.0.0.1")}, IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageDigitalSignature | x509.KeyUsageCertSign, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}}
	der, err := x509.CreateCertificate(rand.Reader, template, template, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	keyDER, err := x509.MarshalECPrivateKey(key)
	if err != nil {
		t.Fatal(err)
	}
	ca := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
	cert, err := tls.X509KeyPair(ca, pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: keyDER}))
	if err != nil {
		t.Fatal(err)
	}
	return cert, ca
}

func TestKubeRequestsReuseConnectionsAndReadRotatedToken(t *testing.T) {
	cert, ca := kubeTestCertificate(t)
	var connections atomic.Int64
	var lastToken atomic.Value
	server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		lastToken.Store(r.Header.Get("Authorization"))
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"ok":true}`))
	}))
	server.TLS = &tls.Config{Certificates: []tls.Certificate{cert}, MinVersion: tls.VersionTLS12}
	server.Config.ConnState = func(_ net.Conn, state http.ConnState) {
		if state == http.StateNew {
			connections.Add(1)
		}
	}
	server.StartTLS()
	defer server.Close()
	dir := t.TempDir()
	tokenFile, caFile := filepath.Join(dir, "token"), filepath.Join(dir, "ca.crt")
	if err := os.WriteFile(caFile, ca, 0600); err != nil {
		t.Fatal(err)
	}
	var cache kubeTransportCache
	t.Cleanup(func() {
		if cache.client != nil {
			cache.client.CloseIdleConnections()
		}
	})
	var first *http.Client
	for i := 0; i < 60; i++ {
		token := "first-test-token"
		if i >= 30 {
			token = "rotated-test-token"
		}
		if err := os.WriteFile(tokenFile, []byte(token), 0600); err != nil {
			t.Fatal(err)
		}
		client, base, bearer, err := cache.fromFiles(server.URL, tokenFile, caFile)
		if err != nil {
			t.Fatal(err)
		}
		if first == nil {
			first = client
		} else if client != first {
			t.Fatal("unchanged endpoint/CA replaced HTTP pool")
		}
		if _, err = kubeRequestHTTP(context.Background(), client, base, bearer, http.MethodGet, "/api", nil); err != nil {
			t.Fatal(err)
		}
		if lastToken.Load() != "Bearer "+token {
			t.Fatal("request reused stale service-account token")
		}
	}
	if got := connections.Load(); got != 1 {
		t.Fatalf("60 sequential requests opened %d TCP connections, want 1", got)
	}
	transport := first.Transport.(*http.Transport)
	if transport.IdleConnTimeout != kubernetesTransportDefaults.IdleConnTimeout || transport.IdleConnTimeout <= 0 {
		t.Fatal("normal idle lifecycle was lost")
	}
	if transport.Proxy != nil {
		t.Fatal("Kubernetes traffic inherited an environment proxy")
	}
	if transport.MaxConnsPerHost != kubernetesTransportDefaults.MaxConnsPerHost {
		t.Fatal("introduced a concurrency cap")
	}
}

func TestKubeCARotationRetiresIdlePoolAndUsesNewTrust(t *testing.T) {
	certA, caA := kubeTestCertificate(t)
	certB, caB := kubeTestCertificate(t)
	var active atomic.Pointer[tls.Certificate]
	active.Store(&certA)
	var connections, closed atomic.Int64
	server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { _, _ = w.Write([]byte(`{"ok":true}`)) }))
	server.TLS = &tls.Config{MinVersion: tls.VersionTLS12, GetCertificate: func(*tls.ClientHelloInfo) (*tls.Certificate, error) { return active.Load(), nil }}
	server.Config.ConnState = func(_ net.Conn, state http.ConnState) {
		if state == http.StateNew {
			connections.Add(1)
		}
		if state == http.StateClosed {
			closed.Add(1)
		}
	}
	server.StartTLS()
	server.TLS.Certificates = nil
	defer server.Close()
	var cache kubeTransportCache
	t.Cleanup(func() {
		if cache.client != nil {
			cache.client.CloseIdleConnections()
		}
	})
	old, err := cache.forCA(server.URL, caA)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = kubeRequestHTTP(context.Background(), old, server.URL, "test-token", http.MethodGet, "/api", nil); err != nil {
		t.Fatal(err)
	}
	active.Store(&certB)
	next, err := cache.forCA(server.URL, caB)
	if err != nil {
		t.Fatal(err)
	}
	if old == next {
		t.Fatal("CA rotation reused old trust/pool")
	}
	if _, err = kubeRequestHTTP(context.Background(), next, server.URL, "test-token", http.MethodGet, "/api", nil); err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(time.Second)
	for closed.Load() < 1 && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	if closed.Load() != 1 || connections.Load() != 2 {
		t.Fatalf("old idle pool was not retired: closed=%d opened=%d", closed.Load(), connections.Load())
	}
	if client, err := cache.forCA(server.URL, []byte("invalid CA")); err == nil || client != nil {
		t.Fatal("invalid rotated trust fell back to previous client")
	}
}

func TestKubeEndpointChangeReplacesPool(t *testing.T) {
	cert, ca := kubeTestCertificate(t)
	makeServer := func() *httptest.Server {
		s := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { _, _ = w.Write([]byte(`{"ok":true}`)) }))
		s.TLS = &tls.Config{Certificates: []tls.Certificate{cert}, MinVersion: tls.VersionTLS12}
		s.StartTLS()
		t.Cleanup(s.Close)
		return s
	}
	a, b := makeServer(), makeServer()
	var cache kubeTransportCache
	t.Cleanup(func() {
		if cache.client != nil {
			cache.client.CloseIdleConnections()
		}
	})
	first, err := cache.forCA(a.URL, ca)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = kubeRequestHTTP(context.Background(), first, a.URL, "test-token", http.MethodGet, "/api", nil); err != nil {
		t.Fatal(err)
	}
	next, err := cache.forCA(b.URL, ca)
	if err != nil {
		t.Fatal(err)
	}
	if first == next {
		t.Fatal("endpoint change reused previous transport")
	}
	if _, err = kubeRequestHTTP(context.Background(), next, b.URL, "test-token", http.MethodGet, "/api", nil); err != nil {
		t.Fatal(err)
	}
}
