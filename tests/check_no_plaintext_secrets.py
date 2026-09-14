import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
PATTERN = re.compile(
    r"(?i)(password|private[_-]?key|client[_-]?secret|access[_-]?key)"
    r"\s*:\s*(?!REDACTED|ENC\[|ref:|null)[^\s#]+"
)
SAFE_VALUE = re.compile(
    r"""(?ix)
    ^(?:
        [\"']?\$\{?[A-Z_][A-Z0-9_]*(?::-[^}]*)?\}?[\"']?
      | [\"']?env://[A-Z_][A-Z0-9_]*[\"']?
      | [\"']{2}
      | <[^>]+>
    )[,;]?$
    """
)

violations = []
for path in ROOT.rglob("*"):
    if not path.is_file() or {".git", ".venv", "__pycache__"} & set(path.parts) or path.suffix not in {".yaml", ".yml", ".json", ".py", ".md"}:
        continue
    for number, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = PATTERN.search(line)
        if not match:
            continue
        # Python type annotations such as `password: str` are declarations,
        # not credential values.
        if path.suffix == ".py" and re.search(
            r"(?i)(password|private[_-]?key|client[_-]?secret|access[_-]?key)"
            r"\s*:\s*(?:str|bytes)(?:\s*\|\s*None)?(?:\s*[,)=]|$)",
            line,
        ):
            continue
        # Do not match the `PASSWORD:-fallback` fragment inside shell
        # parameter expansion embedded in YAML block scalars.
        prefix = line[: match.start()]
        if prefix.rfind("${") > prefix.rfind("}"):
            continue
        value = line[match.end(1) :].split(":", 1)[1].strip()
        if SAFE_VALUE.match(value):
            continue
        # GitHub expression references name a secret but do not contain its
        # value. Accept only the exact `secrets.NAME` expression form.
        if re.fullmatch(
            r'''["']?\$\{\{\s*secrets\.[A-Z_][A-Z0-9_]*\s*\}\}["']?,?''',
            value,
        ):
            continue
        violations.append(f"{path.relative_to(ROOT)}:{number}")
if violations:
    raise SystemExit("possible plaintext secrets: " + ", ".join(violations))
