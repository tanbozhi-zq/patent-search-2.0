"""Check repository documentation without network access or new dependencies."""

import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.query.dsl_builder import build_search_dsl
from app.schemas.search import SearchRequest

FENCES = re.compile(r"^```([^\n]*)\n(.*?)^```\s*$", re.MULTILINE | re.DOTALL)
LINKS = re.compile(r"\[[^\]\n]*\]\((<[^>]+>|[^\s)]+)(?:\s+\"[^\"]*\")?\)")


def heading_ids(text: str) -> set[str]:
    """GitHub-style IDs for the plain/inline-code headings used in this repo."""
    seen: dict[str, int] = {}
    result = set()
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", FENCES.sub("", text), re.MULTILINE):
        base = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        number = seen.get(base, 0)
        result.add(f"{base}-{number}" if number else base)
        seen[base] = number + 1
    return result


def check_document(path: Path, root: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    errors = []
    label = path.relative_to(root).as_posix()
    for raw in LINKS.findall(FENCES.sub("", text)):
        target = urlsplit(raw.strip("<>"))
        if target.scheme or target.netloc:
            continue  # External availability requires an independent live check.
        destination = (path.parent / unquote(target.path)).resolve() if target.path else path
        if not destination.is_relative_to(root.resolve()) or not destination.exists():
            errors.append(f"{label}: missing repository link {raw}")
        elif target.fragment and destination.suffix == ".md":
            if unquote(target.fragment) not in heading_ids(destination.read_text(encoding="utf-8")):
                errors.append(f"{label}: missing heading {raw}")
    for language, body in FENCES.findall(text):
        if language.strip() != "json":
            continue
        try:
            value = json.loads(body)
        except ValueError:
            errors.append(f"{label}: invalid JSON code block")
            continue
        if label == "docs/api.md" and isinstance(value, dict) and ("q" in value or "semantic_text" in value):
            try:
                request = SearchRequest.model_validate(value)
                if request.mode == "boolean":
                    build_search_dsl(request)
            except (ValueError, TypeError):
                errors.append(f"{label}: search example violates request contract")
    if label == "docs/api.md":
        for payload in re.findall(r"-d '([^']+)'", text):
            try:
                request = SearchRequest.model_validate(json.loads(payload))
                if request.mode == "boolean":
                    build_search_dsl(request)
            except (ValueError, TypeError):
                errors.append(f"{label}: curl example violates request contract")
    return errors


def main() -> int:
    files = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "*.md"],
        cwd=ROOT,
    ).decode().split("\0")
    documents = sorted({ROOT / name for name in files if name and (ROOT / name).is_file()})
    errors = [error for path in documents for error in check_document(path, ROOT)]
    for error in errors:
        print(error)
    print(f"Documentation: {len(documents)} files, {len(errors)} errors")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
