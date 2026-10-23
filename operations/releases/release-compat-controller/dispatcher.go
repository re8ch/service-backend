package main

import (
	"bytes"
	"context"
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"regexp"
	"strconv"
	"time"

	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

var operationListGVK = schema.GroupVersionKind{Group: "delivery.re8ch.com", Version: "v1alpha1", Kind: "ReleaseOperationList"}

type dispatcher struct {
	client                         client.Client
	http                           *http.Client
	appID, installationID, keyPath string
}

func operationExpired(item *unstructured.Unstructured, now time.Time) bool {
	timeout, found, err := unstructured.NestedInt64(item.Object, "spec", "timeoutSeconds")
	return err == nil && found && timeout > 0 && now.After(item.GetCreationTimestamp().Time.Add(time.Duration(timeout)*time.Second))
}

func b64(value []byte) string { return base64.RawURLEncoding.EncodeToString(value) }

func (d *dispatcher) appJWT() (string, error) {
	raw, err := os.ReadFile(d.keyPath)
	if err != nil {
		return "", err
	}
	block, _ := pem.Decode(raw)
	if block == nil {
		return "", errors.New("invalid GitHub App key")
	}
	var key *rsa.PrivateKey
	if parsed, err := x509.ParsePKCS8PrivateKey(block.Bytes); err == nil {
		key, _ = parsed.(*rsa.PrivateKey)
	} else if parsed, err := x509.ParsePKCS1PrivateKey(block.Bytes); err == nil {
		key = parsed
	}
	if key == nil || key.N.BitLen() < 2048 {
		return "", errors.New("invalid GitHub App RSA key")
	}
	now := time.Now().Unix()
	header := b64([]byte(`{"alg":"RS256","typ":"JWT"}`))
	payload, _ := json.Marshal(map[string]any{"iat": now - 30, "exp": now + 540, "iss": d.appID})
	unsigned := header + "." + b64(payload)
	digest := sha256.Sum256([]byte(unsigned))
	signature, err := rsa.SignPKCS1v15(rand.Reader, key, crypto.SHA256, digest[:])
	if err != nil {
		return "", err
	}
	return unsigned + "." + b64(signature), nil
}

func (d *dispatcher) github(ctx context.Context, method, path, token string, body any) ([]byte, error) {
	if !regexp.MustCompile(`^/[A-Za-z0-9_./-]+$`).MatchString(path) {
		return nil, errors.New("invalid GitHub path")
	}
	var encoded []byte
	if body != nil {
		encoded, _ = json.Marshal(body)
	}
	req, err := http.NewRequestWithContext(ctx, method, "https://api.github.com"+path, bytes.NewReader(encoded))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Accept", "application/vnd.github+json")
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-GitHub-Api-Version", "2022-11-28")
	response, err := d.http.Do(req)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	data, err := io.ReadAll(io.LimitReader(response.Body, 65537))
	if err != nil || len(data) > 65536 || response.StatusCode < 200 || response.StatusCode >= 300 {
		return nil, fmt.Errorf("GitHub request failed: %d", response.StatusCode)
	}
	return data, nil
}

func (d *dispatcher) dispatch(ctx context.Context, operation string) error {
	jwt, err := d.appJWT()
	if err != nil {
		return err
	}
	data, err := d.github(ctx, "POST", "/app/installations/"+d.installationID+"/access_tokens", jwt,
		map[string]any{"repositories": []string{"service-backend"}, "permissions": map[string]string{"actions": "write", "metadata": "read"}})
	if err != nil {
		return err
	}
	var token struct {
		Token string `json:"token"`
	}
	if json.Unmarshal(data, &token) != nil || token.Token == "" {
		return errors.New("invalid installation token")
	}
	_, err = d.github(ctx, "POST", "/repos/re8ch/service-backend/actions/workflows/ceph-static-release.yml/dispatches", token.Token,
		map[string]any{"ref": "main", "inputs": map[string]string{"operation_id": operation}})
	return err
}

func (d *dispatcher) run(ctx context.Context) error {
	list := &unstructured.UnstructuredList{}
	list.SetGroupVersionKind(operationListGVK)
	if err := d.client.List(ctx, list, client.InNamespace("flux-system")); err != nil {
		return err
	}
	for index := range list.Items {
		item := &list.Items[index]
		phase := nestedString(item.Object, "status", "phase")
		if phase != "" && phase != "Pending" {
			continue
		}
		now := time.Now().UTC()
		if operationExpired(item, now) {
			status := map[string]any{"phase": "Failed", "finishedAt": now.Format(time.RFC3339),
				"terminalResult": map[string]any{"outcome": "expired", "message": "operation expired before dispatch"}}
			if err := unstructured.SetNestedMap(item.Object, status, "status"); err != nil {
				return err
			}
			if err := d.client.Status().Update(ctx, item); err != nil {
				return err
			}
			continue
		}
		release := nestedString(item.Object, "spec", "releaseRef")
		if release != "rook-ceph" && release != "ceph-csi-drivers" && release != "rook-ceph-cluster" {
			continue
		}
		if err := d.dispatch(ctx, item.GetName()); err != nil {
			return err
		}
		status := map[string]any{"phase": "Dispatched", "dispatchedAt": now.Format(time.RFC3339)}
		if err := unstructured.SetNestedMap(item.Object, status, "status"); err != nil {
			return err
		}
		if err := d.client.Status().Update(ctx, item); err != nil {
			return err
		}
	}
	return nil
}

func runDispatcher() {
	appID, installID, keyPath := os.Getenv("GITHUB_APP_ID"), os.Getenv("GITHUB_INSTALLATION_ID"), os.Getenv("GITHUB_PRIVATE_KEY_FILE")
	if _, err := strconv.ParseUint(appID, 10, 64); err != nil {
		panic("invalid GITHUB_APP_ID")
	}
	if _, err := strconv.ParseUint(installID, 10, 64); err != nil {
		panic("invalid GITHUB_INSTALLATION_ID")
	}
	config := ctrl.GetConfigOrDie()
	kube, err := client.New(config, client.Options{Scheme: runtime.NewScheme()})
	if err != nil {
		panic(err)
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil
	d := &dispatcher{client: kube, http: &http.Client{Transport: transport, Timeout: 15 * time.Second,
		CheckRedirect: func(*http.Request, []*http.Request) error { return errors.New("redirect denied") }},
		appID: appID, installationID: installID, keyPath: keyPath}
	ctx := ctrl.SetupSignalHandler()
	for {
		if err := d.run(ctx); err != nil {
			fmt.Fprintln(os.Stderr, err)
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(10 * time.Second):
		}
	}
}
