package main

import (
	"context"
	"encoding/json"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	proxy "re8ch.com/api-conformance/result-proxy"
)

func main() {
	var policy proxy.Policy
	if json.Unmarshal([]byte(os.Getenv("RESULT_OIDC_POLICY")), &policy) != nil || policy.Audience == "" || policy.WorkflowSHA == "" {
		log.Fatal("invalid result identity configuration")
	}
	host, port := os.Getenv("KUBERNETES_SERVICE_HOST"), os.Getenv("KUBERNETES_SERVICE_PORT_HTTPS")
	if host == "" || port == "" {
		log.Fatal("Kubernetes endpoint missing")
	}
	api, err := proxy.NewAPITransport("https://"+net.JoinHostPort(host, port), "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt", "/var/run/secrets/kubernetes.io/serviceaccount/token")
	if err != nil {
		log.Fatal("Kubernetes transport unavailable")
	}
	ns := os.Getenv("POD_NAMESPACE")
	if ns == "" {
		log.Fatal("namespace missing")
	}
	handler := &proxy.Handler{Policy: policy, Keys: proxy.NewGitHubKeys(), Store: proxy.KubernetesStore{API: api, Namespace: ns}}
	server := proxy.Server(":8080", handler)
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()
	go func() {
		<-ctx.Done()
		shutdown, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		defer cancel()
		_ = server.Shutdown(shutdown)
	}()
	if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatal("result listener failed")
	}
}
