package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
)

func TestDiscoverAndCDI(t *testing.T) {
	root := t.TempDir()
	sysfs := filepath.Join(root, "sys")
	dev := filepath.Join(root, "dev")
	cdi := filepath.Join(root, "cdi")
	p := filepath.Join(sysfs, "1-2")
	if err := os.MkdirAll(p, 0755); err != nil {
		t.Fatal(err)
	}
	for name, value := range map[string]string{"idVendor": "1234\n", "idProduct": "abcd\n", "busnum": "1\n", "devnum": "7\n", "serial": "SER 1\n"} {
		if err := os.WriteFile(filepath.Join(p, name), []byte(value), 0644); err != nil {
			t.Fatal(err)
		}
	}
	node := filepath.Join(dev, "001", "007")
	if err := os.MkdirAll(filepath.Dir(node), 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(node, nil, 0644); err != nil {
		t.Fatal(err)
	}
	devices, err := discover(sysfs, dev)
	if err != nil {
		t.Fatal(err)
	}
	if len(devices) != 1 || devices[0].Name != "usb-1234-abcd-ser-1" {
		t.Fatalf("unexpected: %#v", devices)
	}
	if err := writeCDI(cdi, devices); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(filepath.Join(cdi, "re8ch-usb.json"))
	if err != nil {
		t.Fatal(err)
	}
	if len(raw) == 0 {
		t.Fatal("empty CDI spec")
	}
}

func TestDuplicateIdentityFails(t *testing.T) {
	root := t.TempDir()
	sysfs := filepath.Join(root, "sys")
	dev := filepath.Join(root, "dev")
	for i, path := range []string{"1-2", "1-3"} {
		p := filepath.Join(sysfs, path)
		os.MkdirAll(p, 0755)
		for name, value := range map[string]string{"idVendor": "1234", "idProduct": "abcd", "busnum": "1", "devnum": strconv.Itoa(i + 1), "serial": "same"} {
			os.WriteFile(filepath.Join(p, name), []byte(value), 0644)
		}
		node := filepath.Join(dev, "001", fmt.Sprintf("%03d", i+1))
		os.MkdirAll(filepath.Dir(node), 0755)
		os.WriteFile(node, nil, 0644)
	}
	if _, err := discover(sysfs, dev); err == nil {
		t.Fatal("expected duplicate error")
	}
}

func TestTopologyNameHasNoDots(t *testing.T) {
	name := deviceName("/sys/bus/usb/devices/1-14.1", "1604", "10c0", "")
	if strings.Contains(name, ".") || name != "usb-1604-10c0-1-14-1" {
		t.Fatalf("invalid DRA device name %q", name)
	}
}
