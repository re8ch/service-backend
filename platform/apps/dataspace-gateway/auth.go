package main

import (
	"context"
	"errors"
	"net/http"
	"strings"

	"github.com/coreos/go-oidc/v3/oidc"
)

type authenticator struct {
	deviceToken string
	verifier    *oidc.IDTokenVerifier
}

func newAuthenticator(ctx context.Context, deviceToken, issuer, audience string) (*authenticator, error) {
	a := &authenticator{deviceToken: deviceToken}
	if issuer != "" {
		if audience == "" {
			return nil, errors.New("DATASPACE_OIDC_AUDIENCE is required with DATASPACE_OIDC_ISSUER")
		}
		provider, err := oidc.NewProvider(ctx, issuer)
		if err != nil {
			return nil, err
		}
		a.verifier = provider.Verifier(&oidc.Config{ClientID: audience})
	}
	if a.deviceToken == "" && a.verifier == nil {
		return nil, errors.New("at least one authentication method is required")
	}
	return a, nil
}

func (a *authenticator) authorize(r *http.Request) bool {
	token := strings.TrimSpace(strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer "))
	if token == "" {
		return false
	}
	if a.deviceToken != "" && token == a.deviceToken {
		return true
	}
	if a.verifier == nil {
		return false
	}
	_, err := a.verifier.Verify(r.Context(), token)
	return err == nil
}
