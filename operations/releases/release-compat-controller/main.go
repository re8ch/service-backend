package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/types"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/handler"
	"sigs.k8s.io/controller-runtime/pkg/healthz"
	"sigs.k8s.io/controller-runtime/pkg/metrics"
	metricsserver "sigs.k8s.io/controller-runtime/pkg/metrics/server"
)

var (
	staticGVK       = schema.GroupVersionKind{Group: "delivery.re8ch.com", Version: "v1alpha1", Kind: "StaticRelease"}
	helmGVK         = schema.GroupVersionKind{Group: "helm.toolkit.fluxcd.io", Version: "v2", Kind: "HelmRelease"}
	opGVK           = schema.GroupVersionKind{Group: "delivery.re8ch.com", Version: "v1alpha1", Kind: "ReleaseOperation"}
	helmRepoGVK     = schema.GroupVersionKind{Group: "source.toolkit.fluxcd.io", Version: "v1", Kind: "HelmRepository"}
	helmChartGVK    = schema.GroupVersionKind{Group: "source.toolkit.fluxcd.io", Version: "v1", Kind: "HelmChart"}
	conditionMetric = prometheus.NewGaugeVec(prometheus.GaugeOpts{Name: "gotk_reconcile_condition", Help: "The current condition status of a GitOps Toolkit resource."}, []string{"kind", "name", "namespace", "type", "status"})
	resourceMetric  = prometheus.NewGaugeVec(prometheus.GaugeOpts{Name: "gotk_resource_info", Help: "Information about a GitOps Toolkit resource."}, []string{"kind", "name", "namespace", "revision", "suspended"})
	suspendMetric   = prometheus.NewGaugeVec(prometheus.GaugeOpts{Name: "gotk_suspend_status", Help: "The suspend status of a GitOps Toolkit resource."}, []string{"kind", "name", "namespace"})
	durationMetric  = prometheus.NewHistogramVec(prometheus.HistogramOpts{Name: "gotk_reconcile_duration_seconds", Help: "The duration in seconds of a GitOps Toolkit resource reconciliation."}, []string{"kind", "name", "namespace"})
)

type reconciler struct{ client.Client }

func (r *reconciler) projectSource(ctx context.Context, release *unstructured.Unstructured, ready, reason, now string) error {
	repository := "rook-release"
	if release.GetName() == "ceph-csi-drivers" {
		repository = "ceph-csi-operator"
	}
	digest := nestedString(release.Object, "spec", "chartDigest")
	targets := []struct {
		gvk            schema.GroupVersionKind
		name, revision string
	}{
		{helmRepoGVK, repository, "static-lock@" + digest},
		{helmChartGVK, "flux-system-" + release.GetName(), nestedString(release.Object, "spec", "releaseID") + "@" + digest},
	}
	for _, target := range targets {
		object := &unstructured.Unstructured{}
		object.SetGroupVersionKind(target.gvk)
		if err := r.Get(ctx, types.NamespacedName{Namespace: release.GetNamespace(), Name: target.name}, object); err != nil {
			return client.IgnoreNotFound(err)
		}
		status := map[string]any{
			"observedGeneration": object.GetGeneration(),
			"conditions":         []any{map[string]any{"type": "Ready", "status": ready, "reason": reason, "message": "projected from signed static release", "lastTransitionTime": now, "observedGeneration": object.GetGeneration()}},
			"artifact":           map[string]any{"revision": target.revision, "digest": digest, "url": "https://github.com/re8ch/service-backend", "lastUpdateTime": now},
		}
		if requested := object.GetAnnotations()["reconcile.fluxcd.io/requestedAt"]; requested != "" {
			status["lastHandledReconcileAt"] = requested
		}
		if err := unstructured.SetNestedMap(object.Object, status, "status"); err != nil {
			return err
		}
		if err := r.Status().Update(ctx, object); err != nil {
			return err
		}
	}
	return nil
}

func nestedString(object map[string]any, fields ...string) string {
	value, _, _ := unstructured.NestedString(object, fields...)
	return value
}

func nestedBool(object map[string]any, fields ...string) bool {
	value, _, _ := unstructured.NestedBool(object, fields...)
	return value
}

func condition(status map[string]any, wanted string) (string, string) {
	items, _, _ := unstructured.NestedSlice(status, "conditions")
	for _, raw := range items {
		item, ok := raw.(map[string]any)
		if ok && item["type"] == wanted {
			value, _ := item["status"].(string)
			reason, _ := item["reason"].(string)
			return value, reason
		}
	}
	return "Unknown", "Progressing"
}

func operationName(release, action, key string) string {
	sum := sha256.Sum256([]byte(release + "\x00" + action + "\x00" + key))
	return release + "-" + hex.EncodeToString(sum[:])[:16]
}

func (r *reconciler) dependenciesReady(ctx context.Context, release *unstructured.Unstructured) error {
	deps, _, _ := unstructured.NestedStringSlice(release.Object, "spec", "dependencies")
	for _, name := range deps {
		item := &unstructured.Unstructured{}
		item.SetGroupVersionKind(staticGVK)
		if err := r.Get(ctx, types.NamespacedName{Namespace: release.GetNamespace(), Name: name}, item); err != nil {
			return fmt.Errorf("dependency %s unavailable: %w", name, err)
		}
		status, _ := item.Object["status"].(map[string]any)
		if value, _ := condition(status, "Healthy"); value != "True" {
			return fmt.Errorf("dependency %s is not healthy", name)
		}
	}
	return nil
}

func (r *reconciler) createOperation(ctx context.Context, release, helm *unstructured.Unstructured, action, key string) error {
	if key == "" {
		return nil
	}
	if err := r.dependenciesReady(ctx, release); err != nil {
		return err
	}
	name := operationName(release.GetName(), action, key)
	existing := &unstructured.Unstructured{}
	existing.SetGroupVersionKind(opGVK)
	if err := r.Get(ctx, types.NamespacedName{Namespace: release.GetNamespace(), Name: name}, existing); err == nil {
		return nil
	}
	approved := nestedString(release.Object, "status", "acceptedDigest")
	if approved == "" {
		approved = nestedString(release.Object, "spec", "bundleDigest")
	}
	contract := nestedString(release.Object, "spec", "contractDigest")
	if approved == "" || contract == "" {
		return errors.New("release has no accepted signed digest")
	}
	op := &unstructured.Unstructured{Object: map[string]any{
		"apiVersion": "delivery.re8ch.com/v1alpha1", "kind": "ReleaseOperation",
		"metadata": map[string]any{"name": name, "namespace": release.GetNamespace(), "labels": map[string]any{"delivery.re8ch.com/release": release.GetName()}},
		"spec": map[string]any{"releaseRef": release.GetName(), "action": action, "approvedDigest": approved,
			"contractDigest": contract, "requestedBy": "flux-cli", "reason": "Flux compatibility annotation",
			"idempotencyKey": key, "timeoutSeconds": int64(1800)},
	}}
	op.SetGroupVersionKind(opGVK)
	return r.Create(ctx, op)
}

func (r *reconciler) Reconcile(ctx context.Context, request ctrl.Request) (ctrl.Result, error) {
	started := time.Now()
	defer func() {
		durationMetric.WithLabelValues("HelmRelease", request.Name, request.Namespace).Observe(time.Since(started).Seconds())
	}()
	release := &unstructured.Unstructured{}
	release.SetGroupVersionKind(staticGVK)
	if err := r.Get(ctx, request.NamespacedName, release); err != nil {
		return ctrl.Result{}, client.IgnoreNotFound(err)
	}
	helm := &unstructured.Unstructured{}
	helm.SetGroupVersionKind(helmGVK)
	if err := r.Get(ctx, request.NamespacedName, helm); err != nil {
		return ctrl.Result{}, err
	}
	annotations := helm.GetAnnotations()
	actions := []struct{ annotation, handled, action string }{
		{"reconcile.fluxcd.io/requestedAt", "lastHandledReconcileAt", "reconcile"},
		{"reconcile.fluxcd.io/forceAt", "lastHandledForceAt", "force-reconcile"},
		{"reconcile.fluxcd.io/resetAt", "lastHandledResetAt", "reset"},
	}
	status, _, _ := unstructured.NestedMap(helm.Object, "status")
	for _, item := range actions {
		key := annotations[item.annotation]
		if key != "" && nestedString(status, item.handled) != key {
			if err := r.createOperation(ctx, release, helm, item.action, key); err != nil {
				return ctrl.Result{RequeueAfter: 20 * time.Second}, err
			}
			status[item.handled] = key
		}
	}
	desiredSuspend := nestedBool(helm.Object, "spec", "suspend")
	currentSuspend := nestedBool(release.Object, "status", "suspended")
	approved := nestedString(release.Object, "status", "acceptedDigest")
	if approved == "" {
		approved = nestedString(release.Object, "spec", "bundleDigest")
	}
	if approved != "" && desiredSuspend != currentSuspend {
		action := "resume"
		if desiredSuspend {
			action = "suspend"
		}
		if err := r.createOperation(ctx, release, helm, action, fmt.Sprintf("generation:%d:%t", helm.GetGeneration(), desiredSuspend)); err != nil {
			return ctrl.Result{RequeueAfter: 20 * time.Second}, err
		}
	}
	ready, reason := condition(func() map[string]any { value, _, _ := unstructured.NestedMap(release.Object, "status"); return value }(), "Healthy")
	now := metav1.Now().Format(time.RFC3339)
	status["observedGeneration"] = helm.GetGeneration()
	status["lastAttemptedRevision"] = nestedString(release.Object, "status", "appliedDigest")
	status["lastAttemptedRevisionDigest"] = nestedString(release.Object, "status", "appliedDigest")
	status["lastAttemptedConfigDigest"] = nestedString(release.Object, "spec", "valuesDigest")
	status["conditions"] = []any{map[string]any{"type": "Ready", "status": ready, "reason": reason, "message": "projected from StaticRelease", "lastTransitionTime": now, "observedGeneration": helm.GetGeneration()}}
	if history, found, _ := unstructured.NestedSlice(release.Object, "status", "history"); found {
		status["history"] = history
	}
	if err := unstructured.SetNestedMap(helm.Object, status, "status"); err != nil {
		return ctrl.Result{}, err
	}
	if err := r.Status().Update(ctx, helm); err != nil {
		return ctrl.Result{}, err
	}
	if err := r.projectSource(ctx, release, ready, reason, now); err != nil {
		return ctrl.Result{}, err
	}
	suspended := desiredSuspend
	conditionMetric.WithLabelValues("HelmRelease", helm.GetName(), helm.GetNamespace(), "Ready", ready).Set(1)
	resourceMetric.WithLabelValues("HelmRelease", helm.GetName(), helm.GetNamespace(), status["lastAttemptedRevision"].(string), fmt.Sprint(suspended)).Set(1)
	if suspended {
		suspendMetric.WithLabelValues("HelmRelease", helm.GetName(), helm.GetNamespace()).Set(1)
	} else {
		suspendMetric.WithLabelValues("HelmRelease", helm.GetName(), helm.GetNamespace()).Set(0)
	}
	return ctrl.Result{RequeueAfter: 30 * time.Second}, nil
}

func main() {
	if os.Getenv("MODE") == "dispatcher" {
		runDispatcher()
		return
	}
	scheme := runtime.NewScheme()
	manager, err := ctrl.NewManager(ctrl.GetConfigOrDie(), ctrl.Options{Scheme: scheme, Metrics: metricsserver.Options{BindAddress: ":8080"}, HealthProbeBindAddress: ":8081", LeaderElection: true, LeaderElectionID: "release-compat-controller.delivery.re8ch.com"})
	if err != nil {
		panic(err)
	}
	metrics.Registry.MustRegister(conditionMetric, resourceMetric, suspendMetric, durationMetric)
	prototype := &unstructured.Unstructured{}
	prototype.SetGroupVersionKind(staticGVK)
	r := &reconciler{Client: manager.GetClient()}
	helmPrototype := &unstructured.Unstructured{}
	helmPrototype.SetGroupVersionKind(helmGVK)
	if err := ctrl.NewControllerManagedBy(manager).For(prototype).Watches(helmPrototype, &handler.EnqueueRequestForObject{}).Named("staticrelease").Complete(r); err != nil {
		panic(err)
	}
	if err := manager.AddHealthzCheck("healthz", healthz.Ping); err != nil {
		panic(err)
	}
	if err := manager.AddReadyzCheck("readyz", healthz.Ping); err != nil {
		panic(err)
	}
	if err := manager.Start(ctrl.SetupSignalHandler()); err != nil {
		fmt.Fprintln(os.Stderr, strings.TrimSpace(err.Error()))
		os.Exit(1)
	}
}
