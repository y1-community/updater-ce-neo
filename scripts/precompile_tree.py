#!/usr/bin/env python3
"""precompile_tree.py — ship bytecode, not source, in the bundled apps.

The macOS bundle copies ``src/`` and the vendored ``mtkclient`` tree into the
``.app`` as plain source, and the PyInstaller specs add the vendored tree as
*data* (the ``mtkclient`` package is deliberately excluded from the PYZ archive
and imported at runtime from a directory on ``sys.path``). Either way the frozen
app re-parses those files on every launch -- and cannot write ``__pycache__``
when the install directory is read-only (Program Files, an .app owned by
another admin, a mounted .dmg), so every launch pays the parse again.

``compile_tree`` copies a tree and replaces each ``.py`` with a sibling ``.pyc``
compiled *by the interpreter that will run it*, using the unchecked-hash
invalidation mode: the bytecode records its source hash but the loader never
checks it, so the module imports with no ``.py`` beside it. Verified on the
bundled interpreters: a sourceless ``.pyc`` is importable as a module and as a
package (``__init__.pyc``), and runnable as a script (``python launcher.pyc``).

Non-Python files (the vendored libusb dylib, ``qt.conf``, READMEs, ...) are
copied untouched.

Used by ``scripts/build_universal_app.py`` and both PyInstaller specs; run it
standalone to inspect what a build would ship::

    python scripts/precompile_tree.py src build/precompiled/src
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

# Never copied: they are outputs of the very step this module performs.
_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
_MANIFEST = ".precompile-manifest.json"
_VALIDATE_PYC = r'''import importlib.util, json, pathlib, struct, sys
root = pathlib.Path(sys.argv[1])
valid = []
for source in root.rglob("*.py"):
    pyc = source.with_suffix(".pyc")
    try:
        data = pyc.read_bytes()
        flags = struct.unpack("<I", data[4:8])[0]
        if (flags & 1) and data[8:16] == importlib.util.source_hash(source.read_bytes()):
            valid.append(source.relative_to(root).as_posix())
    except OSError:
        pass
print(json.dumps(valid))
'''


@dataclass
class CompileStats:
    """What a ``compile_tree`` run produced, for build logs and tests."""

    compiled: int = 0
    copied: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return f"{self.compiled} bytecode files, {self.copied} data files, {len(self.errors)} errors"


def find_sources(root: Path) -> list[Path]:
    """Every ``.py`` still under ``root``.

    An empty result is how a bundle proves it is (nearly) fully precompiled;
    the build audit uses this on the assembled app.
    """
    root = Path(root)
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*.py") if p.is_file())


def _source_hashes(sources: Sequence[Path]) -> dict[str, str]:
    """Hash every source/data file so cached staging trees cannot go stale."""
    result = {}
    for index, root in enumerate(sources):
        if root.is_dir():
            paths = sorted(
                p for p in root.rglob("*")
                if p.is_file()
                and "__pycache__" not in p.parts
                and p.suffix not in (".pyc", ".pyo")
            )
            for path in paths:
                relative = path.relative_to(root).as_posix()
                result[f"{index}/{relative}"] = hashlib.sha256(path.read_bytes()).hexdigest()
        elif root.is_file() and root.suffix not in (".pyc", ".pyo"):
            result[f"{index}/{root.name}"] = hashlib.sha256(root.read_bytes()).hexdigest()
    return result


def compile_tree(
    sources: Sequence[Path | str],
    dest: Path | str,
    *,
    python: Path | str | None = None,
    optimize: int = 0,
    strict: bool = True,
    quiet: bool = False,
    clean: bool = True,
    write_manifest: bool = True,
) -> CompileStats:
    """Copy ``sources`` into ``dest`` and leave only bytecode behind.

    ``sources`` are files or directories; a directory's *contents* land in
    ``dest`` (so ``[src, dest=app/src]`` mirrors ``src/``). ``python`` is the
    interpreter that will run the bundle -- it must match the target's bytecode
    magic (the macOS bundle runs its own 3.11, the PyInstaller builds run the
    build venv). Files the interpreter refuses to compile are reported through
    ``CompileStats.errors``; ``strict`` turns any into a build failure.
    ``clean`` wipes ``dest`` first; pass ``False`` when compiling *into* a
    directory that already holds unrelated files (a staging root, a finished
    ``site-packages``).
    """
    dest = Path(dest)
    sources = [Path(source) for source in sources]
    in_place = any(source.resolve() == dest.resolve() for source in sources)
    if clean and in_place:
        raise ValueError("clean=True cannot compile a source tree in place")
    if in_place and write_manifest:
        raise ValueError("write_manifest=True cannot write build metadata into a bundled package")
    if optimize != 0:
        raise ValueError(
            "sourceless bundles currently require optimize=0; -O/-OO bytecode "
            "changes runtime semantics and cannot be safely mixed with the default cache"
        )
    if clean and dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    input_hashes = _source_hashes(sources)
    for source in sources:
        source = Path(source)
        if source.resolve() == dest.resolve():
            # Compiling a tree in place (the bundle's site-packages): there is
            # nothing to copy, only bytecode to produce.
            continue
        if source.is_dir():
            shutil.copytree(source, dest, dirs_exist_ok=True, ignore=_IGNORE)
        elif source.is_file():
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest / source.name)
        else:
            raise FileNotFoundError(f"precompile source not found: {source}")

    interpreter = str(python) if python is not None else sys.executable
    if python is not None and not Path(interpreter).exists():
        raise FileNotFoundError(f"precompile target interpreter not found: {interpreter}")

    cmd = [
        interpreter,
        "-m",
        "compileall",
        "-q",
        "-b",
        "--invalidation-mode",
        "unchecked-hash",
        "-f",
    ]
    if optimize:
        cmd += ["-o", str(optimize)]
    cmd.append(str(dest))
    proc = subprocess.run(cmd, capture_output=True, text=True)

    stats = CompileStats()
    for line in (proc.stderr or "").splitlines():
        if line.strip().startswith("***") or "Error compiling" in line:
            stats.errors.append(line.strip())
    if proc.returncode != 0 and not stats.errors:
        output = "\n".join(part for part in (proc.stdout, proc.stderr) if part)
        stats.errors.append(f"compileall exited {proc.returncode}: {output.strip()[:400]}")

    # Delete source only when its matching sourceless bytecode was produced
    # from this exact source. In allow-errors mode (third-party packages/fork),
    # keep failed .py files so the app can still import them; a stale pyc must
    # never turn a recoverable compile warning into an import crash.
    validation = subprocess.run(
        [interpreter, "-c", _VALIDATE_PYC, str(dest)],
        capture_output=True,
        text=True,
    )
    try:
        valid_bytecode = set(json.loads(validation.stdout))
    except (json.JSONDecodeError, TypeError):
        valid_bytecode = set()
        stats.errors.append(f"Could not validate generated bytecode: {(validation.stderr or '').strip()[:300]}")
    compiled = 0
    copied = 0
    for item in sorted(dest.rglob("*")):
        if not item.is_file():
            continue
        if item.suffix == ".py":
            relative = item.relative_to(dest).as_posix()
            if relative in valid_bytecode:
                item.unlink()
                compiled += 1
            else:
                stats.errors.append(f"No valid bytecode produced for {relative}; keeping source")
        elif item.suffix in (".pyc", ".pyo"):
            # Count bytecode below once, after all sources have been examined.
            pass
        else:
            copied += 1
    compiled = sum(1 for p in dest.rglob("*.pyc") if p.is_file()) + sum(
        1 for p in dest.rglob("*.pyo") if p.is_file()
    )
    # ``compileall -b`` also leaves duplicate entries in __pycache__; the
    # sibling .pyc is the sourceless import path we ship, so remove caches to
    # avoid nearly doubling bytecode in the bundle. Failed source remains
    # importable without a cache, just as it was before this build step.
    for cache_dir in sorted(dest.rglob("__pycache__"), reverse=True):
        if cache_dir.is_dir():
            shutil.rmtree(cache_dir)
    for pycache_file in list(dest.rglob("*.pyc")) + list(dest.rglob("*.pyo")):
        try:
            pycache_file.relative_to(dest)
        except ValueError:
            continue
        if pycache_file.parent.name == "__pycache__":
            pycache_file.unlink()

    stats.compiled = compiled
    stats.copied = copied

    # Keep build metadata out of the copied tree: PyInstaller would otherwise
    # collect it as a runtime data file. A sibling dotfile remains reusable by
    # subsequent builds and is ignored by package discovery.
    manifest_path = dest.parent / f".{dest.name}{_MANIFEST}"
    if input_hashes and write_manifest:
        manifest = {
            "python": subprocess.run(
                [interpreter, "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip(),
            "magic": subprocess.run(
                [interpreter, "-c", "import importlib.util; print(importlib.util.MAGIC_NUMBER.hex())"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip(),
            "optimize": optimize,
            "sources": input_hashes,
        }
        manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")

    if stats.errors and strict:
        raise RuntimeError(
            f"precompile_tree: {len(stats.errors)} file(s) failed to compile for {interpreter}: "
            + "; ".join(stats.errors[:5])
        )
    if not quiet:
        print(f">>> Precompiled {dest} -> {stats.summary()}")
    return stats


def compile_tree_if_stale(
    sources: Sequence[Path | str],
    dest: Path | str,
    *,
    python: Path | str | None = None,
    optimize: int = 0,
    strict: bool = True,
    quiet: bool = False,
    write_manifest: bool = True,
) -> CompileStats:
    """``compile_tree`` unless source hashes and the target runtime match.

    The manifest records every source digest, target Python version, and
    optimization level. A tree compiled by 3.13 cannot be reused by 3.11 just
    because its files are fresh; edits to a non-newest source also invalidate it.
    """
    dest = Path(dest)
    sources = [Path(s) for s in sources]
    interpreter = str(python) if python is not None else sys.executable
    manifest_path = dest.parent / f".{dest.name}{_MANIFEST}"
    if write_manifest and dest.is_dir() and not find_sources(dest) and manifest_path.is_file():
        try:
            old = json.loads(manifest_path.read_text(encoding="utf-8"))
            version = subprocess.run(
                [interpreter, "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            magic = subprocess.run(
                [interpreter, "-c", "import importlib.util; print(importlib.util.MAGIC_NUMBER.hex())"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            current_sources = _source_hashes(sources)
            if (
                old.get("python") == version
                and old.get("magic") == magic
                and old.get("optimize") == optimize
                and old.get("sources") == current_sources
                and optimize == 0
            ):
                if not quiet:
                    print(f">>> Reusing precompiled {dest}")
                return CompileStats(compiled=sum(1 for _ in dest.rglob("*.pyc")))
        except (OSError, ValueError, KeyError, json.JSONDecodeError, subprocess.SubprocessError):
            pass
    return compile_tree(
        sources,
        dest,
        python=python,
        optimize=optimize,
        strict=strict,
        quiet=quiet,
        write_manifest=write_manifest,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Precompile a tree into sourceless bytecode for bundling.")
    parser.add_argument("sources", nargs="+", type=Path, help="files or directories to precompile")
    parser.add_argument("dest", type=Path, help="destination directory")
    parser.add_argument("--python", type=Path, default=None, help="interpreter of the target bundle")
    parser.add_argument("--optimize", type=int, default=0, choices=[0, 1, 2])
    parser.add_argument("--allow-errors", action="store_true", help="do not fail on uncompilable files")
    args = parser.parse_args(argv)

    stats = compile_tree(
        args.sources,
        args.dest,
        python=args.python,
        optimize=args.optimize,
        strict=not args.allow_errors,
    )
    leftovers = find_sources(args.dest)
    if leftovers:
        print(f"!!! {len(leftovers)} source file(s) remain in {args.dest}", file=sys.stderr)
        return 1
    return 0 if not stats.errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
