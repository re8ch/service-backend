"""Session-local reuse of identical, bounded Kustomize inputs for contract tests."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import shutil
import subprocess

import yaml

ROOT = Path(__file__).resolve().parents[1]
YAML_SAFE_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


class KustomizeRender:
    def __init__(self, root=ROOT):
        self.root = Path(root).resolve()
        # Reuse the reviewed dependency closure, rather than maintaining another
        # Kustomize input scanner. Each instance owns its scanner and cache.
        spec = importlib.util.spec_from_file_location(
            "render_inputs", ROOT / "operations/ci/render_evidence.py"
        )
        self.inputs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.inputs)
        self.inputs.ROOT = self.root
        self.cache = {}
        self.executed = 0
        self.reused = 0

    def __call__(self, root, *, kubectl=False):
        directory = (self.root / root).resolve()
        relative = str(directory.relative_to(self.root))
        executable = None if kubectl else shutil.which("kustomize")
        verb = "build"
        if executable is None:
            executable = shutil.which("kubectl")
            verb = "kustomize"
        if executable is None:
            raise FileNotFoundError("kustomize/kubectl executable is required")
        binary = Path(executable).resolve()
        stat = binary.stat()
        command = [str(binary), verb, str(directory)]
        key = self.inputs.input_key(relative, {
            "command": command,
            "cwd": str(self.root),
            "binary": [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns],
        })
        if key is not None and key in self.cache:
            documents = self.cache[key]
            self.reused += 1
        else:
            output = subprocess.check_output(command, cwd=self.root, text=True)
            documents = list(yaml.load_all(output, Loader=YAML_SAFE_LOADER))
            # Failures are never stored. Remote/unknown inputs have no key and
            # must render on every request, just as they did before reuse.
            if key is not None:
                self.cache[key] = documents
            self.executed += 1
        return deepcopy(documents)
