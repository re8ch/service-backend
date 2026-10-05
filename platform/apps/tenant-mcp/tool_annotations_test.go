package main

import (
	"encoding/json"
	"testing"
)

func TestToolAnnotationSafetyBoundaries(t *testing.T) {
	for _, name := range []string{"platform_tool_call", "platform_user", "platform_organization", "tenant_kubernetes_apply", "artc_kubernetes_admin", "grafana_render", "grafana_exploration_save"} {
		a := toolAnnotations(name)
		if a.ReadOnlyHint || a.DestructiveHint == nil || !*a.DestructiveHint {
			t.Errorf("%s must advertise its mutating/destructive supported behavior", name)
		}
		encoded, err := json.Marshal(a)
		if err != nil {
			t.Fatal(err)
		}
		var wire map[string]any
		if err := json.Unmarshal(encoded, &wire); err != nil {
			t.Fatal(err)
		}
		for _, key := range []string{"readOnlyHint", "destructiveHint", "openWorldHint"} {
			if _, ok := wire[key].(bool); !ok {
				t.Errorf("%s omits %s", name, key)
			}
		}
	}
	for _, name := range []string{"platform_tool_call", "harbor_build_start", "tenant_kubernetes_apply", "dynamic_skill_submit"} {
		if !*toolAnnotations(name).OpenWorldHint {
			t.Errorf("%s can interact with external entities", name)
		}
	}
}

func TestUnknownToolRequiresAnnotationReview(t *testing.T) {
	defer func() {
		if recover() == nil {
			t.Fatal("unreviewed tool was accepted")
		}
	}()
	toolAnnotations("new_unreviewed_tool")
}
