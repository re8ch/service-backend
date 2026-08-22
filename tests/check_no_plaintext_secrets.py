import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
PATTERN = re.compile(r"(?i)(password|private[_-]?key|client[_-]?secret|access[_-]?key)\s*:\s*(?!REDACTED|ENC\[|\$\{|ref:|null)[^\s#]+")

violations = []
for path in ROOT.rglob("*"):
    if not path.is_file() or {".git", ".venv", "__pycache__"} & set(path.parts) or path.suffix not in {".yaml", ".yml", ".json", ".py", ".md"}:
        continue
    for number, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
        if PATTERN.search(line):
            violations.append(f"{path.relative_to(ROOT)}:{number}")
if violations:
    raise SystemExit("possible plaintext secrets: " + ", ".join(violations))
