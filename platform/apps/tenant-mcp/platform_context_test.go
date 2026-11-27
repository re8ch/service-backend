package main

import (
	"reflect"
	"testing"
)

func TestKubectlContextSelectionPreservesCommandAndIdentityGuards(t *testing.T) {
	tests := []struct {
		name   string
		input  platformKubectlInput
		ref    string
		argv   []string
		denied bool
	}{
		{name: "existing default", input: platformKubectlInput{Argv: []string{"get", "pods"}}, argv: []string{"get", "pods"}},
		{name: "split context", input: platformKubectlInput{Argv: []string{"get", "pods", "--context", "re8ch-atlantic"}}, ref: "re8ch-atlantic", argv: []string{"get", "pods"}},
		{name: "typed and context agree", input: platformKubectlInput{ClusterRef: "re8ch", Argv: []string{"--context=re8ch", "get", "pods"}}, ref: "re8ch", argv: []string{"get", "pods"}},
		{name: "exec argument is not a selector", input: platformKubectlInput{Argv: []string{"exec", "pod", "--", "program", "--context=argument"}}, argv: []string{"exec", "pod", "--", "program", "--context=argument"}},
		{name: "identity override still reaches existing backend guard", input: platformKubectlInput{Argv: []string{"--context=re8ch-atlantic", "--server=https://unreviewed.invalid", "get", "pods"}}, ref: "re8ch-atlantic", argv: []string{"--server=https://unreviewed.invalid", "get", "pods"}},
		{name: "missing context", input: platformKubectlInput{Argv: []string{"get", "pods", "--context"}}, denied: true},
		{name: "empty context", input: platformKubectlInput{Argv: []string{"--context=", "get", "pods"}}, denied: true},
		{name: "context without command", input: platformKubectlInput{Argv: []string{"--context=re8ch"}}, denied: true},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			before := append([]string{}, test.input.Argv...)
			ref, argv, err := platformKubectlSelection(test.input)
			if test.denied {
				if err == nil {
					t.Fatal("invalid selection accepted")
				}
				return
			}
			if err != nil || ref != test.ref || !reflect.DeepEqual(argv, test.argv) {
				t.Fatalf("selection changed command: %q %#v %v", ref, argv, err)
			}
			if !reflect.DeepEqual(before, test.input.Argv) {
				t.Fatal("mutated caller argv")
			}
		})
	}
}
