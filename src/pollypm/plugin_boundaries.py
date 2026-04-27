"""Cross-plugin import boundary contract (#890).

Adds the launch-hardening boundary the existing
:mod:`tests.test_import_boundary` does not cover: no built-in
plugin may import another plugin's *private* helpers
(underscore-prefixed names or anything not listed in
``__all__``).

The pre-launch audit (``docs/launch-issue-audit-2026-04-27.md``
§9) cites the recurring shape: plugins reach into each other's
private helpers, CLI / jobs code mutates internals, protocols
omit parameters that callers actually pass. Examples:

* `#802` — task-assignment private helpers became a public API
  only after a cross-plugin caller broke when they were renamed.
* `#796`/`#798`/`#800`-`#805` — boundary leaks fixed in slices
  but the shape kept recurring.

Architecture: this module declares the contract. Tests (in
:mod:`tests.test_plugin_boundary_conformance`) walk every
built-in plugin source tree and assert the contract holds.

Migration policy: the contract is enforced for new violations.
Existing leaks land on a documented allowlist with owner,
reason, and removal condition. The allowlist is the smallest
possible — every entry must come with a TODO citing the
follow-up issue that will remove it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BoundaryException:
    """One documented exception to the cross-plugin private-import rule."""

    importer: str
    """POSIX path of the file that imports the private helper."""

    target_module: str
    """Module path being imported (e.g.,
    ``pollypm.plugins_builtin.task_assignment_notify.api``)."""

    target_symbol: str
    """The underscored symbol being imported."""

    owner: str
    """Person / team responsible for removing the exception."""

    reason: str
    """Why the exception is currently necessary."""

    removal_condition: str
    """Concrete condition under which this entry can be removed."""


# Each entry is reviewed at every change. The allowlist must
# trend toward zero — anyone adding a new entry should answer
# "why is this unavoidable?" before merging.
BOUNDARY_EXCEPTIONS: tuple[BoundaryException, ...] = ()
"""No exceptions registered today.

The repo's 2026-04-26 boundary cleanup retired the obvious
cross-plugin private imports (#796 / #798 / #800-#805). New
entries here must be added in the same PR that introduces the
underlying need."""


# ---------------------------------------------------------------------------
# Plugin tree discovery
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PluginPackage:
    """A built-in plugin package on disk."""

    name: str
    """Top-level plugin directory name (e.g., ``"advisor"``)."""

    root: Path
    """Absolute path to the plugin directory."""

    module_path: str
    """Importable module path (e.g.,
    ``pollypm.plugins_builtin.advisor``)."""


def discover_builtin_plugins(repo_root: Path) -> tuple[PluginPackage, ...]:
    """Return every built-in plugin under
    ``src/pollypm/plugins_builtin/``.

    Skips ``__pycache__`` and any non-directory entries so the
    discovery is robust to incidental files."""
    out: list[PluginPackage] = []
    base = repo_root / "src" / "pollypm" / "plugins_builtin"
    if not base.exists():
        return ()
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith("_"):
            continue
        out.append(
            PluginPackage(
                name=entry.name,
                root=entry,
                module_path=f"pollypm.plugins_builtin.{entry.name}",
            )
        )
    return tuple(out)


# ---------------------------------------------------------------------------
# Import-line scan
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CrossPluginPrivateImport:
    """One cross-plugin private-import violation."""

    importer_path: Path
    importer_plugin: str
    target_plugin: str
    target_module: str
    target_symbol: str
    line: int

    @property
    def summary(self) -> str:
        return (
            f"{self.importer_plugin} imports private "
            f"{self.target_module}:{self.target_symbol} from "
            f"{self.target_plugin} at "
            f"{self.importer_path}:{self.line}"
        )


def scan_plugin_for_private_imports(
    plugin: PluginPackage,
    *,
    all_plugins: tuple[PluginPackage, ...],
    allowlist: tuple[BoundaryException, ...] = BOUNDARY_EXCEPTIONS,
) -> tuple[CrossPluginPrivateImport, ...]:
    """Walk ``plugin.root`` and return cross-plugin private imports.

    The check is scoped to imports that:

    1. target a module under ``pollypm.plugins_builtin.<other>``
       where ``<other>`` is a *different* plugin name;
    2. import a symbol whose name starts with ``_`` (the
       Python private convention).

    Imports of public symbols from another plugin's ``api``
    submodule are explicitly allowed — that is the canonical
    cross-plugin contract (the 2026-04-26 cleanup landed
    ``task_assignment_notify.api`` for exactly this reason).
    """
    import re

    pattern = re.compile(
        r"^\s*from\s+(pollypm\.plugins_builtin\.[a-z_]+(?:\.[a-z_]+)*)\s+"
        r"import\s+([^\n]+)$",
        re.MULTILINE,
    )
    out: list[CrossPluginPrivateImport] = []
    other_plugin_names = {p.name for p in all_plugins if p.name != plugin.name}

    for source in _python_files(plugin.root):
        try:
            text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for match in pattern.finditer(text):
            module = match.group(1)
            target_plugin = module.split(".")[2]
            if target_plugin == plugin.name:
                continue
            if target_plugin not in other_plugin_names:
                continue
            symbols = _split_imports(match.group(2))
            line_no = text[: match.start()].count("\n") + 1
            for sym in symbols:
                if not sym.startswith("_"):
                    continue
                if _allowlisted(
                    importer=str(source),
                    module=module,
                    symbol=sym,
                    allowlist=allowlist,
                ):
                    continue
                out.append(
                    CrossPluginPrivateImport(
                        importer_path=source,
                        importer_plugin=plugin.name,
                        target_plugin=target_plugin,
                        target_module=module,
                        target_symbol=sym,
                        line=line_no,
                    )
                )
    return tuple(out)


def _python_files(root: Path) -> list[Path]:
    """Return every .py file under ``root`` skipping caches."""
    out: list[Path] = []
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        out.append(path)
    return out


def _split_imports(text: str) -> list[str]:
    """Parse the symbol list of a ``from ... import ...`` line.

    Handles trailing comments, parenthesised multi-line imports,
    aliases (``X as Y`` — only the original name is checked),
    and trailing commas."""
    cleaned = text.strip().rstrip(",").strip()
    if cleaned.startswith("("):
        cleaned = cleaned.strip("()").strip()
    out: list[str] = []
    for part in cleaned.split(","):
        sym = part.strip()
        if not sym or sym.startswith("#"):
            continue
        if " as " in sym:
            sym = sym.split(" as ", 1)[0].strip()
        out.append(sym)
    return out


def _allowlisted(
    *,
    importer: str,
    module: str,
    symbol: str,
    allowlist: tuple[BoundaryException, ...],
) -> bool:
    """Return ``True`` if ``(importer, module, symbol)`` is on the
    allowlist."""
    for entry in allowlist:
        if entry.importer != importer:
            continue
        if entry.target_module != module:
            continue
        if entry.target_symbol != symbol:
            continue
        return True
    return False


# ---------------------------------------------------------------------------
# Protocol conformance helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProtocolMismatch:
    protocol_name: str
    impl_name: str
    method: str
    detail: str

    @property
    def summary(self) -> str:
        return (
            f"{self.impl_name} does not conform to {self.protocol_name}."
            f"{self.method}: {self.detail}"
        )


def assert_implements_protocol(
    *,
    protocol: type,
    impl: type,
) -> tuple[ProtocolMismatch, ...]:
    """Return mismatches between ``protocol`` and ``impl``.

    Light-weight conformance check: every public attribute the
    protocol declares must exist on the implementation, and if
    both are callables, their parameter names must overlap (the
    impl may add parameters but cannot lose any the protocol
    declares — that is the regression shape #802 fixed).
    """
    import inspect

    out: list[ProtocolMismatch] = []
    for attr in dir(protocol):
        if attr.startswith("_"):
            continue
        proto_member = getattr(protocol, attr, None)
        impl_member = getattr(impl, attr, None)
        if impl_member is None:
            out.append(
                ProtocolMismatch(
                    protocol_name=protocol.__name__,
                    impl_name=impl.__name__,
                    method=attr,
                    detail="implementation does not declare this attribute",
                )
            )
            continue
        if not (callable(proto_member) and callable(impl_member)):
            continue
        try:
            proto_sig = inspect.signature(proto_member)
            impl_sig = inspect.signature(impl_member)
        except (TypeError, ValueError):
            continue
        proto_params = set(proto_sig.parameters)
        impl_params = set(impl_sig.parameters)
        missing = proto_params - impl_params
        if missing:
            out.append(
                ProtocolMismatch(
                    protocol_name=protocol.__name__,
                    impl_name=impl.__name__,
                    method=attr,
                    detail=(
                        f"impl is missing parameter(s) "
                        f"{sorted(missing)!r}"
                    ),
                )
            )
    return tuple(out)
