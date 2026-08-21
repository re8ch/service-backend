package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	resourceapi "k8s.io/api/resource/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
	"k8s.io/dynamic-resource-allocation/kubeletplugin"
	"k8s.io/dynamic-resource-allocation/resourceslice"
)

const driverName = "usb.devices.re8ch.com"

type USBDevice struct {
	Name, VendorID, ProductID, Serial, SysfsPath, DeviceNode string
}

func readTrim(path string) (string, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(string(raw)), nil
}

var invalidName = regexp.MustCompile(`[^a-z0-9-]+`)

func deviceName(sysfs, vendor, product, serial string) string {
	base := serial
	if base == "" {
		base = filepath.Base(sysfs)
	}
	base = strings.Trim(invalidName.ReplaceAllString(strings.ToLower(base), "-"), "-")
	if base == "" {
		base = "unknown"
	}
	name := fmt.Sprintf("usb-%s-%s-%s", strings.ToLower(vendor), strings.ToLower(product), base)
	if len(name) > 63 {
		name = name[:63]
	}
	return strings.Trim(name, "-")
}

func discover(root, devRoot string) ([]USBDevice, error) {
	paths, err := filepath.Glob(filepath.Join(root, "*"))
	if err != nil {
		return nil, err
	}
	var devices []USBDevice
	seen := map[string]bool{}
	for _, path := range paths {
		vendor, err := readTrim(filepath.Join(path, "idVendor"))
		if err != nil {
			continue
		}
		product, err := readTrim(filepath.Join(path, "idProduct"))
		if err != nil {
			continue
		}
		busRaw, err := readTrim(filepath.Join(path, "busnum"))
		if err != nil {
			continue
		}
		devRaw, err := readTrim(filepath.Join(path, "devnum"))
		if err != nil {
			continue
		}
		bus, err1 := strconv.Atoi(busRaw)
		dev, err2 := strconv.Atoi(devRaw)
		if err1 != nil || err2 != nil {
			continue
		}
		serial, _ := readTrim(filepath.Join(path, "serial"))
		node := filepath.Join(devRoot, fmt.Sprintf("%03d", bus), fmt.Sprintf("%03d", dev))
		if _, err := os.Stat(node); err != nil {
			continue
		}
		name := deviceName(path, vendor, product, serial)
		if seen[name] {
			return nil, fmt.Errorf("duplicate stable USB identity %s", name)
		}
		seen[name] = true
		devices = append(devices, USBDevice{Name: name, VendorID: vendor, ProductID: product, Serial: serial, SysfsPath: path, DeviceNode: node})
	}
	return devices, nil
}

type cdiSpec struct {
	CDIVersion string      `json:"cdiVersion"`
	Kind       string      `json:"kind"`
	Devices    []cdiDevice `json:"devices"`
}
type cdiDevice struct {
	Name           string   `json:"name"`
	ContainerEdits cdiEdits `json:"containerEdits"`
}
type cdiEdits struct {
	DeviceNodes []cdiNode `json:"deviceNodes"`
}
type cdiNode struct {
	Path        string `json:"path"`
	HostPath    string `json:"hostPath"`
	Permissions string `json:"permissions"`
}

func writeCDI(path string, devices []USBDevice) error {
	if err := os.MkdirAll(path, 0755); err != nil {
		return err
	}
	spec := cdiSpec{CDIVersion: "0.8.0", Kind: driverName + "/device"}
	for _, device := range devices {
		spec.Devices = append(spec.Devices, cdiDevice{Name: device.Name,
			ContainerEdits: cdiEdits{DeviceNodes: []cdiNode{{Path: device.DeviceNode, HostPath: device.DeviceNode, Permissions: "rw"}}}})
	}
	raw, err := json.MarshalIndent(spec, "", "  ")
	if err != nil {
		return err
	}
	tmp := filepath.Join(path, ".re8ch-usb.json.tmp")
	if err := os.WriteFile(tmp, append(raw, '\n'), 0644); err != nil {
		return err
	}
	return os.Rename(tmp, filepath.Join(path, "re8ch-usb.json"))
}

type driver struct {
	sync.RWMutex
	devices map[string]USBDevice
}

func (d *driver) PrepareResourceClaims(_ context.Context, claims []*resourceapi.ResourceClaim) (map[types.UID]kubeletplugin.PrepareResult, error) {
	results := make(map[types.UID]kubeletplugin.PrepareResult, len(claims))
	for _, claim := range claims {
		var prepared []kubeletplugin.Device
		if claim.Status.Allocation == nil {
			results[claim.UID] = kubeletplugin.PrepareResult{Err: errors.New("claim has no allocation")}
			continue
		}
		for _, allocation := range claim.Status.Allocation.Devices.Results {
			if allocation.Driver != driverName {
				continue
			}
			d.RLock()
			device, ok := d.devices[allocation.Device]
			d.RUnlock()
			if !ok {
				prepared = nil
				results[claim.UID] = kubeletplugin.PrepareResult{Err: fmt.Errorf("allocated USB device %s is not present", allocation.Device)}
				break
			}
			if _, err := os.Stat(device.DeviceNode); err != nil {
				prepared = nil
				results[claim.UID] = kubeletplugin.PrepareResult{Err: fmt.Errorf("USB node %s disappeared: %w", device.DeviceNode, err)}
				break
			}
			prepared = append(prepared, kubeletplugin.Device{Requests: []string{allocation.Request}, PoolName: allocation.Pool,
				DeviceName: allocation.Device, CDIDeviceIDs: []string{driverName + "/device=" + allocation.Device}})
		}
		if _, failed := results[claim.UID]; !failed {
			results[claim.UID] = kubeletplugin.PrepareResult{Devices: prepared}
		}
	}
	return results, nil
}

func (d *driver) replace(devices []USBDevice) {
	next := make(map[string]USBDevice, len(devices))
	for _, device := range devices {
		next[device.Name] = device
	}
	d.Lock()
	d.devices = next
	d.Unlock()
}

func (d *driver) UnprepareResourceClaims(_ context.Context, claims []kubeletplugin.NamespacedObject) (map[types.UID]error, error) {
	result := make(map[types.UID]error, len(claims))
	for _, claim := range claims {
		result[claim.UID] = nil
	}
	return result, nil
}
func (d *driver) HandleError(_ context.Context, err error, msg string) { slog.Error(msg, "error", err) }

func published(node string, devices []USBDevice) resourceslice.DriverResources {
	apiDevices := make([]resourceapi.Device, 0, len(devices))
	for _, d := range devices {
		attrs := map[resourceapi.QualifiedName]resourceapi.DeviceAttribute{
			"usb.devices.re8ch.com/vendor_id":  {StringValue: &d.VendorID},
			"usb.devices.re8ch.com/product_id": {StringValue: &d.ProductID},
			"usb.devices.re8ch.com/site":       {StringValue: ptr("restricted-node-6dc3f140f9")},
		}
		if d.Serial != "" {
			attrs["usb.devices.re8ch.com/serial"] = resourceapi.DeviceAttribute{StringValue: &d.Serial}
		}
		apiDevices = append(apiDevices, resourceapi.Device{Name: d.Name, Attributes: attrs,
			Capacity: map[resourceapi.QualifiedName]resourceapi.DeviceCapacity{"usb.devices.re8ch.com/devices": {Value: resource.MustParse("1")}}})
	}
	return resourceslice.DriverResources{Pools: map[string]resourceslice.Pool{node: {Slices: []resourceslice.Slice{{Devices: apiDevices}}}}}
}
func ptr(s string) *string { return &s }

func run(ctx context.Context) error {
	node := os.Getenv("NODE_NAME")
	if node == "" {
		return errors.New("NODE_NAME is required")
	}
	sysfs := os.Getenv("USB_SYSFS_ROOT")
	if sysfs == "" {
		sysfs = "/sys/bus/usb/devices"
	}
	devRoot := os.Getenv("USB_DEV_ROOT")
	if devRoot == "" {
		devRoot = "/dev/bus/usb"
	}
	cdiRoot := os.Getenv("CDI_ROOT")
	if cdiRoot == "" {
		cdiRoot = "/etc/cdi"
	}
	pluginPath := filepath.Join(kubeletplugin.KubeletPluginsDir, driverName)
	if err := os.MkdirAll(pluginPath, 0750); err != nil {
		return fmt.Errorf("create kubelet plugin directory: %w", err)
	}
	devices, err := discover(sysfs, devRoot)
	if err != nil {
		return err
	}
	if err := writeCDI(cdiRoot, devices); err != nil {
		return fmt.Errorf("write CDI: %w", err)
	}
	config, err := rest.InClusterConfig()
	if err != nil {
		return err
	}
	client, err := kubernetes.NewForConfig(config)
	if err != nil {
		return err
	}
	d := &driver{devices: map[string]USBDevice{}}
	d.replace(devices)
	help, err := kubeletplugin.Start(ctx, d, kubeletplugin.KubeClient(client), kubeletplugin.NodeName(node),
		kubeletplugin.DriverName(driverName), kubeletplugin.PluginDataDirectoryPath(pluginPath),
		kubeletplugin.CDIDirectory(cdiRoot))
	if err != nil {
		return err
	}
	defer help.Stop()
	if err := help.PublishResources(ctx, published(node, devices)); err != nil {
		return err
	}
	slog.Info("USB DRA driver ready", "node", node, "devices", len(devices))
	go func() {
		ticker := time.NewTicker(10 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				next, err := discover(sysfs, devRoot)
				if err != nil {
					slog.Error("USB rescan failed", "error", err)
					continue
				}
				if err := writeCDI(cdiRoot, next); err != nil {
					slog.Error("CDI refresh failed", "error", err)
					continue
				}
				if err := help.PublishResources(ctx, published(node, next)); err != nil {
					slog.Error("ResourceSlice refresh failed", "error", err)
					continue
				}
				d.replace(next)
			}
		}
	}()
	<-ctx.Done()
	return nil
}

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()
	if err := run(ctx); err != nil && !errors.Is(err, context.Canceled) {
		slog.Error("driver failed", "error", err)
		os.Exit(1)
	}
}
