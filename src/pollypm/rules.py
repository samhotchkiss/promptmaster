from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import Path


@dataclass(slots=True)
class RuleFile:
    name: str
    description: str
    trigger: str
    source_path: Path
    display_path: str
    content: str


def _parse_rule_metadata(path: Path, content: str) -> RuleFile:
    name = path.stem
    description = ""
    trigger = ""
    for line in content.splitlines()[:20]:
        stripped = line.strip()
        lowered = stripped.casefold()
        if lowered.startswith("description:"):
            description = stripped.split(":", 1)[1].strip()
        elif lowered.startswith("trigger:"):
            trigger = stripped.split(":", 1)[1].strip()
    if not description:
        description = f"Instructions for {name} work"
    if not trigger:
        trigger = f"When doing {name} work"
    display_path = str(path)
    try:
        home = Path.home().resolve()
        resolved = path.resolve()
        if resolved.is_relative_to(home):
            display_path = f"~/{resolved.relative_to(home)}"
    except ValueError:
        pass
    return RuleFile(
        name=name,
        description=description,
        trigger=trigger,
        source_path=path,
        display_path=display_path,
        content=content,
    )


def _builtin_rules_dir() -> Path:
    return Path(str(resources.files("pollypm.defaults.rules")))


def _scan_rule_dir(directory: Path, *, display_base: str | None = None) -> dict[str, RuleFile]:
    if not directory.exists():
        return {}
    merged: dict[str, RuleFile] = {}
    for path in sorted(directory.glob("*.md")):
        content = path.read_text()
        rule = _parse_rule_metadata(path, content)
        if display_base is not None:
            rule.display_path = f"{display_base}/{path.name}"
        merged[rule.name] = rule
    return merged


def discover_rules(project_root: Path) -> dict[str, RuleFile]:
    merged = _scan_rule_dir(_builtin_rules_dir(), display_base="pollypm/defaults/rules")
    merged.update(_scan_rule_dir(Path.home() / ".pollypm" / "rules", display_base="~/.pollypm/rules"))
    merged.update(_scan_rule_dir(project_root / ".pollypm" / "rules", display_base=".pollypm/rules"))
    return merged


def render_rules_manifest(project_root: Path) -> str:
    rules = discover_rules(project_root)
    if not rules:
        return ""
    lines = [
        "## Available Rules",
        "You have specialized instructions for these scenarios. Read the relevant file before starting that type of work.",
    ]
    for name in sorted(rules):
        rule = rules[name]
        lines.append(f"- {rule.name}: {rule.description} -> {rule.display_path} ({rule.trigger})")
    return "\n".join(lines)
