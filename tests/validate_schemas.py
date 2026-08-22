import json
from pathlib import Path

import jsonschema
import yaml

ROOT = Path(__file__).parents[1]


def validate(pattern, schema_name):
    schema = json.loads((ROOT / "schemas" / schema_name).read_text())
    for path in ROOT.glob(pattern):
        document = yaml.safe_load(path.read_text())
        for item in document.get("items", [document]):
            if "apiVersion" not in item:
                item = {"apiVersion": document["apiVersion"], "kind": document["kind"].removesuffix("List"), **item}
            jsonschema.validate(item, schema)


validate("plugins/*.yaml", "plugin-definition.schema.json")
validate("inventory/*.yaml", "infrastructure-target.schema.json")
validate("catalogs/*/modules/*/module.yaml", "workspace-module.schema.json")

lock_schema = json.loads((ROOT / "schemas/workspace-lock.schema.json").read_text())
jsonschema.validate(yaml.safe_load((ROOT / "workspace/workspace.lock.yaml").read_text()), lock_schema)
