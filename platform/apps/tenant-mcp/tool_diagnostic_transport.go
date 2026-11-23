package main

import (
	"errors"
	"net/http"
	"net/url"

	"github.com/getsentry/sentry-go"
)

// Only explicitly configured infrastructure origins receive tracing carriers.
// Request URLs, credentials and payloads are never copied into span attributes.
type diagnosticTransport struct {
	base   http.RoundTripper
	origin *url.URL
	phase  string
}

func tracedInfrastructureTransport(base http.RoundTripper, endpoint, phase string) http.RoundTripper {
	if base == nil {
		base = http.DefaultTransport
	}
	origin, err := url.Parse(endpoint)
	if err != nil || origin.Host == "" {
		return base
	}
	return diagnosticTransport{base: base, origin: origin, phase: phase}
}

func (t diagnosticTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	clone := r.Clone(r.Context())
	clone.Header = r.Header.Clone()
	clone.Header.Del("sentry-trace")
	clone.Header.Del("baggage")
	clone.Header.Del("X-Re8ch-Operation-Id")
	if r.URL.Host != t.origin.Host || r.URL.Scheme != t.origin.Scheme || diagnosticState(r.Context()) == nil {
		return t.base.RoundTrip(clone)
	}
	ctx, finish := diagnosticPhase(r.Context(), t.phase)
	clone = clone.WithContext(ctx)
	if span := sentry.SpanFromContext(ctx); span != nil {
		clone.Header.Set("sentry-trace", span.ToSentryTrace())
	}
	clone.Header.Set("X-Re8ch-Operation-Id", diagnosticState(ctx).operationID)
	response, err := t.base.RoundTrip(clone)
	if err != nil {
		finish(err)
		return response, err
	}
	diagnosticHTTPStatus(ctx, response.StatusCode)
	if response.StatusCode >= 500 {
		finish(errors.New("infrastructure HTTP failure"))
	} else {
		finish(nil)
	}
	return response, nil
}
