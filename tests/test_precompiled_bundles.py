"""Bundled builds must ship bytecode, not source.

Both shipping paths hand the frozen app a directory of Python modules that it
imports at runtime: the macOS bundle copies ``src/`` and the vendored
``mtkclient`` into ``Resources/app``, and the PyInstaller specs add the vendored
tree as *data* (the package is excluded from the PYZ on purpose). Until now that
directory held source, which every launch re-parsed -- and could never cache,
because a .app on a read-only .dmg or an install under Program Files cannot
write ``__pycache__``.

These tests pin the helper that produces bytecode-only trees, the sourceless
detection that lets a bundle find its own mtkclient, and the build wiring that
calls both.
"""

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from precompile_tree import compile_tree, compile_tree_if_stale, find_sources  # noqa: E402
from src import paths  # noqa: E402


def _write_tree(root: Path) -> None:
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("VALUE = 'package'\n", encoding="utf-8")
    (root / "pkg" / "mod.py").write_text("def hello():\n    return 'hello'\n", encoding="utf-8")
    (root / "script.py").write_text(
        "from pkg.mod import hello\n\nprint('script-ok', hello())\n", encoding="utf-8"
    )
    (root / "data.bin").write_bytes(b"\x00\x01\x02")


def test_compile_tree_leaves_only_bytecode(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _write_tree(src)

    dest = tmp_path / "out"
    stats = compile_tree([src], dest, python=sys.executable)

    assert find_sources(dest) == [], "every .py must have become .pyc"
    assert stats.errors == []
    assert (dest / "pkg" / "__init__.pyc").is_file()
    assert (dest / "pkg" / "mod.pyc").is_file()
    assert (dest / "script.pyc").is_file()
    # Non-Python files are payload, not code: copied through untouched.
    assert (dest / "data.bin").read_bytes() == b"\x00\x01\x02"


def test_sourceless_package_imports_and_a_pyc_runs_as_the_entry_script(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _write_tree(src)
    dest = tmp_path / "out"
    compile_tree([src], dest, python=sys.executable)

    imported = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "from pkg.mod import hello; import pkg; print(pkg.VALUE, hello())",
            str(dest),
        ],
        capture_output=True,
        text=True,
    )
    assert imported.returncode == 0, imported.stderr
    assert imported.stdout.strip() == "package hello"

    # The macOS bundle's C launcher runs launcher.pyc as the entry script.
    ran = subprocess.run([sys.executable, str(dest / "script.pyc")], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip() == "script-ok hello"


def test_compile_tree_keeps_uncompilable_source_in_lenient_mode(tmp_path):
    """A syntax error in an optional/vendor module must not make the bundle
    less importable than before the precompile step."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "bad.py").write_text("def incomplete(:\n    pass\n", encoding="utf-8")
    (src / "good.py").write_text("VALUE = 42\n", encoding="utf-8")

    dest = tmp_path / "out"
    stats = compile_tree([src], dest, python=sys.executable, strict=False, quiet=True)

    assert stats.errors, "compile failure should be observable"
    assert (dest / "bad.py").is_file(), "failed source must remain importable/diagnosable"
    assert (dest / "good.pyc").is_file()
    assert not (dest / "good.py").exists()


def test_bytecode_does_not_leave_a_duplicate_in_pycache(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _write_tree(src)
    dest = tmp_path / "out"

    compile_tree([src], dest, python=sys.executable, quiet=True)

    assert not list(dest.rglob("__pycache__")), "duplicate pyc cache should not ship"
    assert not list(dest.rglob("*.py"))


def test_compile_tree_if_stale_reuses_then_rebuilds(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _write_tree(src)
    dest = tmp_path / "out"

    compile_tree_if_stale([src], dest, python=sys.executable, quiet=True)
    first = (dest / "pkg" / "mod.pyc").stat().st_mtime_ns

    compile_tree_if_stale([src], dest, python=sys.executable, quiet=True)
    assert (dest / "pkg" / "mod.pyc").stat().st_mtime_ns == first, "unchanged tree recompiled"

    time.sleep(0.01)
    (src / "pkg" / "mod.py").write_text("def hello():\n    return 'again'\n", encoding="utf-8")
    compile_tree_if_stale([src], dest, python=sys.executable, quiet=True)
    assert (dest / "pkg" / "mod.pyc").stat().st_mtime_ns != first, "newer source was not recompiled"
    assert find_sources(dest) == []


def test_a_source_file_left_in_a_bundle_forces_a_rebuild(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _write_tree(src)
    dest = tmp_path / "out"
    compile_tree_if_stale([src], dest, python=sys.executable, quiet=True)

    # Somebody copied a source file into the finished tree: the next build must
    # not "reuse" a directory that still contains source.
    (dest / "stray.py").write_text("X = 1\n", encoding="utf-8")
    compile_tree_if_stale([src], dest, python=sys.executable, quiet=True)
    assert find_sources(dest) == []


def test_bytecode_only_bundle_still_finds_mtkclient(tmp_path):
    """``ensure_mtkclient_importable`` must accept ``__init__.pyc``.

    A bundle whose mtkclient is precompiled has no ``__init__.py`` anywhere; if
    the lookup only knew about source, the frozen app would lose the flash
    engine it shipped.
    """
    bundle = tmp_path / "bundle"
    package = bundle / "mtkclient"
    package.mkdir(parents=True)
    (package / "__init__.pyc").write_bytes(b"\x00")

    assert paths._is_package_dir(package)
    assert paths._mtkclient_path_entry(bundle) == str(bundle)

    before = list(sys.path)
    try:
        import unittest.mock as mock

        with mock.patch.object(paths, "MTKCLIENT_DIR", bundle / "mtkclient"), mock.patch.object(
            paths, "BUNDLE_DIR", bundle
        ), mock.patch.object(paths, "REPO_ROOT", tmp_path / "no-dev-checkout"):
            paths.ensure_mtkclient_importable()
        # The package lives one level down, so the tree's parent is the entry.
        assert str(bundle) in sys.path
    finally:
        sys.path[:] = before


def test_bundles_call_the_precompiler():
    """Pin the wiring: a build that stops shipping bytecode is a regression."""
    for spec_name in ("macos.spec", "InnioasisUpdater.spec"):
        spec = (ROOT / spec_name).read_text(encoding="utf-8")
        assert "compile_tree_if_stale([VENDOR_MTK]" in spec, spec_name
        assert "datas.append((str(MTK_PRECOMPILED), \"mtkclient\"))" in spec, spec_name

    builder = (ROOT / "scripts" / "build_universal_app.py").read_text(encoding="utf-8")
    assert 'compile_tree([ROOT / "src"], APP_CODE / "src"' in builder
    assert 'compile_tree([ROOT / "launcher.py"], APP_CODE' in builder
    assert "launcher.pyc" in builder, "the launcher must run the compiled entry script"
