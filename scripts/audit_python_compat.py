#!/usr/bin/env python3
"""audit_python_compat.py — what would break (or warn) on Python 3.15.

Python 3.15.0 shipped on 2026-10-09. This app runs on the interpreter a build
bundles (3.11 on macOS today, the build venv elsewhere) and is meant to stay
runnable on 3.15 the moment the toolchain allows it, so the code has to avoid
what 3.15 removed and what it newly deprecates (those become removals in 3.16 /
3.17).

``vendor/mtkclient`` is the Chinese build's fork, byte for byte, and is *not*
ours to modernise (see AGENTS.md): its findings are reported separately and
never fail the run.

    python scripts/audit_python_compat.py [--root .] [--vendor]
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# Directories that are not our code: vendored forks, build output, caches.
SKIP_DIRS = {
    ".git",
    ".venv",
    ".venv-build",
    ".cache",
    "build",
    "dist",
    "node_modules",
    "__pycache__",
    "tools",
    "tests",
}


@dataclass(frozen=True)
class Rule:
    """One API that is gone or going, and why it matters."""

    severity: str  # "removed" (already fatal on 3.15) or "deprecated"
    version: str  # the Python that removes it ("3.15" = gone now)
    pattern: str
    message: str

    def compiled(self) -> re.Pattern[str]:
        return re.compile(self.pattern)


RULES: tuple[Rule, ...] = (
    # --- removed in 3.15 (raise at import/use on 3.15) -----------------
    Rule("removed", "3.15", r"\b(?:import|from)\s+sre_(?:compile|constants|parse)\b",
         "the sre_* modules were removed; use the public re API"),
    Rule("removed", "3.15", r"\bCGIHTTPRequestHandler\b",
         "http.server.CGIHTTPRequestHandler was removed"),
    Rule("removed", "3.15", r"\bSetPointerType\s*\(",
         "ctypes.SetPointerType was removed"),
    Rule("removed", "3.15", r"\.load_module\s*\(",
         "loader.load_module() was removed; use exec_module()"),
    Rule("removed", "3.15", r"\.is_reserved\s*\(",
         "pathlib.PurePath.is_reserved was removed; use os.path.isreserved()"),
    Rule("removed", "3.15", r"\bplatform\.java_ver\s*\(",
         "platform.java_ver was removed"),
    Rule("removed", "3.15", r"\bRLock\s*\([^)]",
         "threading.RLock() takes no arguments as of 3.15"),
    Rule("removed", "3.15", r"\bno_type_check_decorator\b",
         "typing.no_type_check_decorator was removed"),
    Rule("removed", "3.15", r"wave\.(?:getmark|setmark|getmarkers)\s*\(",
         "the wave mark methods were removed"),
    Rule("removed", "3.15", r"is_python_build\s*\([^)]*check_home",
         "sysconfig.is_python_build(check_home=...) was removed"),
    # --- gone in earlier releases: still worth catching in new code ----
    Rule("removed", "3.12", r"\b(?:import|from)\s+(?:distutils|imp|smtpd)\b",
         "removed in 3.12 (distutils/imp/smtpd)"),
    Rule("removed", "3.13", r"\b(?:import|from)\s+(?:cgi|cgitb|crypt|nntplib|pipes|telnetlib|uu|xdrlib|lib2to3)\b",
         "removed in 3.13"),
    # --- deprecated: removal scheduled for 3.16 / 3.17 ------------------
    Rule("deprecated", "3.16", r"asyncio\.iscoroutinefunction\s*\(",
         "deprecated; use inspect.iscoroutinefunction()"),
    Rule("deprecated", "3.16", r"\b(?:get|set)_event_loop_policy\s*\(",
         "the asyncio policy system is deprecated; use asyncio.run/Runner"),
    Rule("deprecated", "3.16", r"\b(?:Abstract|Default|WindowsProactor|WindowsSelector)EventLoopPolicy\b",
         "the asyncio policy classes are deprecated"),
    Rule("deprecated", "3.17", r"\bprofile\.(?:run|runctx|Profile)\b",
         "the profile module is deprecated; use profiling.tracing"),
    Rule("deprecated", "3.17", r"\bMacOSXOSAScript\b",
         "webbrowser.MacOSXOSAScript is deprecated; use webbrowser.MacOS"),
    Rule("deprecated", "3.12+", r"\bdatetime\.datetime\.utcnow\s*\(",
         "utcnow() is deprecated; use datetime.now(timezone.utc)"),
    Rule("deprecated", "3.12+", r"\bdatetime\.datetime\.utcfromtimestamp\s*\(",
         "utcfromtimestamp() is deprecated; use fromtimestamp(..., timezone.utc)"),
    Rule("deprecated", "3.15", r"(?:Handler|logging|logger)\w*\([^)]*\bstrm\s*=",
         "logging handlers: the 'strm' argument is deprecated; use 'stream'"),
)


# This file names every pattern it looks for, so it must not scan itself.
SELF = Path(__file__).name


def iter_python_files(root: Path, skip: set[str]) -> list[Path]:
    return sorted(
        p
        for p in root.rglob("*.py")
        if p.is_file() and p.name != SELF and not any(part in skip for part in p.parts)
    )


def scan(root: Path, skip: set[str]) -> tuple[list[tuple[Path, int, Rule, str]], list[tuple[Path, int, Rule, str]]]:
    """Return (our findings, vendored findings)."""
    ours: list[tuple[Path, int, Rule, str]] = []
    vendored: list[tuple[Path, int, Rule, str]] = []
    for path in iter_python_files(root, skip):
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        vendored_file = "vendor" in path.parts
        for number, line in enumerate(lines, start=1):
            if line.lstrip().startswith("#"):
                continue
            for rule in RULES:
                if rule.pattern and rule.compiled().search(line):
                    (vendored if vendored_file else ours).append((path, number, rule, line.strip()))
    return ours, vendored


def _print(title: str, findings: list[tuple[Path, int, Rule, str]], root: Path) -> None:
    if not findings:
        return
    print(f"\n{title}")
    for path, number, rule, line in findings:
        try:
            shown = path.relative_to(root)
        except ValueError:
            shown = path
        print(f"  {rule.severity} ({rule.version}) {shown}:{number}  {rule.message}")
        print(f"      {line[:120]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit for Python 3.15 removals and deprecations.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--vendor", action="store_true", help="also show findings inside vendor/")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    skip = set(SKIP_DIRS) - {"vendor"}
    ours, vendored = scan(root, skip)

    if not ours:
        print("Python compatibility audit: no removed or deprecated API use found in our code.")
    _print("Findings in our code:", ours, root)
    if args.vendor:
        _print("Findings in vendor/ (reported only — not ours to change):", vendored, root)

    fatal = [f for f in ours if f[2].severity == "removed"]
    return 1 if fatal else 0


if __name__ == "__main__":
    raise SystemExit(main())
