"""Portable install-first packages: ``pre_stage`` pairing and resolution.

These tests build the folder shape the packager writes, then verify the app
side of the contract: the target is chosen over its prerequisite, the pair is
validated by repository and version, browsing either folder lands on the
target, and pruning keeps both firmware trees.
"""

from pathlib import Path
from types import SimpleNamespace

from src import fw_package
from src.fw_package import locate_package, read_fw_package_ini, read_package

SCATTER = """\
- partition_index: SYS0
  partition_name: preloader
  file_name: preloader.bin
  is_download: true
- partition_index: SYS1
  partition_name: boot
  file_name: boot.img
  is_download: true
"""


def _firmware(folder: Path, ini: str, marker: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "MT6582_Android_scatter.txt").write_text(SCATTER, encoding="utf-8")
    (folder / "preloader.bin").write_text(marker, encoding="utf-8")
    (folder / "boot.img").write_text(marker, encoding="utf-8")
    (folder / "fw_package.ini").write_text(ini, encoding="utf-8")
    return folder


def _bundle(root: Path, *, target_version: str = "3.2.0=>") -> tuple[Path, Path]:
    base = _firmware(
        root / "pre_stage",
        "[fw_package]\n"
        "name = Original Software\n"
        "version = 3.2.0\n"
        "model = Y2\n"
        "repo = y1-community/y1-stock-rom\n",
        "base",
    )
    target = _firmware(
        root / "SP_Flash_Tool",
        "[fw_package]\n"
        "name = Reborn\n"
        "version = 1.0\n"
        "model = Y2\n"
        "repo = someone/reborn\n"
        "install_first = y1-community/y1-stock-rom\n"
        f"install_first_version = {target_version}\n",
        "target",
    )
    # A portable root carries the target's metadata beside the app as well.
    (root / "fw_package.ini").write_text(
        (target / "fw_package.ini").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return target, base


def test_ini_carries_repo_and_install_first_fields(tmp_path):
    ini = tmp_path / "fw_package.ini"
    ini.write_text(
        "[fw_package]\n"
        "name = Reborn\n"
        "version = 1.0\n"
        "repo = someone/reborn\n"
        "install_first = y1-community/y1-stock-rom\n"
        "install_first_version = 3.2.0=>\n",
        encoding="utf-8",
    )
    meta = read_fw_package_ini(ini)
    assert meta.repo == "someone/reborn"
    assert meta.install_first == "y1-community/y1-stock-rom"
    assert meta.install_first_version == "3.2.0=>"


def test_target_attaches_matching_pre_stage_package(tmp_path):
    root = tmp_path / "rom"
    target, base = _bundle(root)

    pkg = locate_package(root)

    assert pkg is not None
    assert pkg.folder == target.resolve()
    assert pkg.install_first_package is not None
    assert pkg.install_first_package.folder == base.resolve()
    assert pkg.install_first_package.name == "Original Software"
    assert pkg.install_first_package.version == "3.2.0"


def test_portable_app_finds_target_and_its_pre_stage_base(tmp_path, monkeypatch):
    monkeypatch.setattr(fw_package, "IS_MAC", False)
    root = tmp_path / "portable"
    target, base = _bundle(root)
    (root / "InnioasisUpdater.exe").write_bytes(b"x")

    pkg = fw_package.find_bundled_firmware(app_dir=root)

    assert pkg is not None
    assert pkg.folder == target.resolve()
    assert pkg.install_first_package is not None
    assert pkg.install_first_package.folder == base.resolve()


def test_version_mismatch_leaves_the_base_for_the_remote_flow(tmp_path):
    root = tmp_path / "rom"
    _target, _base = _bundle(root, target_version="=3.3.0")

    pkg = locate_package(root)

    assert pkg is not None
    assert pkg.install_first_package is None


def test_model_mismatch_leaves_the_base_for_the_remote_flow(tmp_path):
    root = tmp_path / "rom"
    base = _firmware(
        root / "pre_stage",
        "[fw_package]\n"
        "name = Original Software\n"
        "version = 3.2.0\n"
        "model = Y1\n"
        "repo = y1-community/y1-stock-rom\n",
        "base",
    )
    target = _firmware(
        root / "SP_Flash_Tool",
        "[fw_package]\n"
        "name = Reborn\n"
        "version = 1.0\n"
        "model = Y2\n"
        "repo = someone/reborn\n"
        "install_first = y1-community/y1-stock-rom\n"
        "install_first_version = 3.2.0=>\n",
        "target",
    )
    (root / "fw_package.ini").write_text(
        (target / "fw_package.ini").read_text(encoding="utf-8"), encoding="utf-8"
    )

    pkg = locate_package(root)

    assert pkg is not None
    assert pkg.folder == target.resolve()
    assert pkg.install_first_package is None
    assert fw_package.packages_are_pair(pkg, locate_package(base)) is False


def test_selecting_pre_stage_resolves_to_its_target(tmp_path):
    root = tmp_path / "rom"
    target, base = _bundle(root)

    from_base = locate_package(base)
    from_scatter = locate_package(base, require_ini=False)

    assert from_base is not None and from_base.folder == target.resolve()
    assert from_scatter is not None and from_scatter.folder == target.resolve()
    assert from_base.install_first_package is not None


def test_selecting_target_folder_attaches_pre_stage(tmp_path):
    root = tmp_path / "rom"
    target, base = _bundle(root)

    pkg = locate_package(target)

    assert pkg is not None
    assert pkg.folder == target.resolve()
    assert pkg.install_first_package is not None
    assert pkg.install_first_package.folder == base.resolve()


def test_macos_dmg_lowercase_firmware_pre_stage_resolves_from_either_folder(tmp_path):
    """The DMG layout keeps target and base inside one lowercase firmware tree."""
    root = tmp_path / "dmg"
    base = _firmware(
        root / "firmware" / "pre_stage",
        "[fw_package]\n"
        "name = Original Software\n"
        "version = 3.2.0\n"
        "model = Y2\n"
        "repo = y1-community/y1-stock-rom\n",
        "base",
    )
    target = _firmware(
        root / "firmware",
        "[fw_package]\n"
        "name = Reborn\n"
        "version = 1.0\n"
        "model = Y2\n"
        "repo = someone/reborn\n"
        "install_first = y1-community/y1-stock-rom\n"
        "install_first_version = 3.2.0=>\n",
        "target",
    )

    for selected in (target, base, root):
        pkg = fw_package.resolve_selected_package(selected)
        assert pkg is not None and pkg.folder == target.resolve(), selected
        assert pkg.install_first_package is not None
        assert pkg.install_first_package.folder == base.resolve()


def test_extract_worker_opens_the_target_for_a_portable_folder(tmp_path):
    """The folder path must not reject the two scatters as an ambiguity."""
    from src.flash_service import ExtractWorker

    root = tmp_path / "portable"
    target, _base = _bundle(root)
    stub = SimpleNamespace(
        progress=SimpleNamespace(emit=lambda *_: None),
        _cancelled=False,
    )

    extracted = ExtractWorker._extract(stub, str(root))

    assert Path(extracted).resolve() == target.resolve()


def test_extract_worker_run_accepts_a_portable_scatter_pair(tmp_path):
    from src.flash_service import ExtractWorker

    root = tmp_path / "portable"
    target, _base = _bundle(root)
    finished = []
    stub = SimpleNamespace(
        package_path=str(root),
        _cancelled=False,
        _extract=lambda _path: str(root),
        finished=SimpleNamespace(emit=lambda *args: finished.append(args)),
    )

    ExtractWorker.run(stub)

    assert finished and finished[0][0] is True, finished
    assert Path(finished[0][1]).resolve() == root.resolve()
    target_pkg = locate_package(target)
    assert target_pkg is not None
    assert locate_package(root).scatter.resolve() == target_pkg.scatter.resolve()


def test_scatter_discovery_ignores_a_nested_pre_stage(tmp_path):
    """The target flash path must not see its own prerequisite as a second layout."""
    from src.flash_service import find_scatter_files

    root = tmp_path / "rom"
    target, base = _bundle(root)
    # Move the base inside the target, as the macOS beside-app package does.
    nested = target / "pre_stage"
    base.rename(nested)

    assert [p.resolve() for p in find_scatter_files(target)] == [
        (target / "MT6582_Android_scatter.txt").resolve()
    ]
    assert [p.resolve() for p in find_scatter_files(nested)] == [
        (nested / "MT6582_Android_scatter.txt").resolve()
    ]


def test_extract_worker_keeps_both_trees_from_a_portable_zip(tmp_path, monkeypatch):
    import zipfile

    from src import downloads
    import src.flash_service as flash_service
    from src.flash_service import ExtractWorker

    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir()
    monkeypatch.setattr(downloads, "downloads_dir", lambda: downloads_dir)
    monkeypatch.setattr(flash_service, "downloads_dir", lambda: downloads_dir)

    root = tmp_path / "portable"
    target, base = _bundle(root)
    archive = downloads_dir / "portable.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for path in sorted(root.rglob("*")):
            zf.write(path, path.relative_to(root).as_posix())

    stub = SimpleNamespace(
        progress=SimpleNamespace(emit=lambda *_: None),
        _cancelled=False,
        _extract_zip=lambda pkg, dest: ExtractWorker._extract_zip(stub, pkg, dest),
        _extract_rar=lambda *a: None,
    )
    extracted = Path(ExtractWorker._extract(stub, str(archive)))
    pkg = locate_package(extracted)

    assert pkg is not None
    assert pkg.folder == (extracted / "SP_Flash_Tool").resolve()
    assert pkg.repo == "someone/reborn"
    assert pkg.install_first_package is not None
    assert pkg.install_first_package.folder.name == base.name
    assert (pkg.install_first_package.folder / "boot.img").is_file()


def test_prune_keeps_both_install_first_packages(tmp_path):
    root = tmp_path / "rom"
    target, base = _bundle(root)
    (root / "README.txt").write_text("guide", encoding="utf-8")
    (root / "installer").mkdir()
    (root / "installer" / "Updater.exe").write_bytes(b"x")
    (target / "notes.txt").write_text("not firmware", encoding="utf-8")
    (base / "notes.txt").write_text("not firmware", encoding="utf-8")

    removed = fw_package.prune_to_firmware_package(root)

    assert (target / "preloader.bin").is_file()
    assert (target / "boot.img").is_file()
    assert (base / "preloader.bin").is_file()
    assert (base / "boot.img").is_file()
    assert (target / "fw_package.ini").is_file()
    assert (base / "fw_package.ini").is_file()
    assert not (target / "notes.txt").exists()
    assert not (base / "notes.txt").exists()
    assert not (root / "README.txt").exists()
    assert removed


def test_local_package_without_repo_still_pairs_by_pre_stage_convention(tmp_path):
    root = tmp_path / "rom"
    base = _firmware(
        root / "pre_stage",
        "[fw_package]\nname = Original Software\nversion = 3.2.0\n",
        "base",
    )
    target = _firmware(
        root / "Firmware",
        "[fw_package]\n"
        "name = Reborn\n"
        "version = 1.0\n"
        "install_first = y1-community/y1-stock-rom\n"
        "install_first_version = 3.2.0=>\n",
        "target",
    )

    pkg = read_package(target, require_ini=False)
    assert pkg is not None
    attached = fw_package.attach_local_prerequisite(pkg)
    assert attached is not None
    assert attached.install_first_package is not None
    assert attached.install_first_package.folder == base.resolve()


def test_legacy_numbered_folders_pick_the_target_and_pair_its_base(tmp_path, monkeypatch):
    """Windows 7/8 and 32-bit Linux packs have two numbered flash tool folders."""
    monkeypatch.setattr(fw_package, "IS_MAC", False)
    root = tmp_path / "legacy"
    base = _firmware(
        root / "2. Install Original Software first with flash_tool",
        "[fw_package]\n"
        "name = Original Software\n"
        "version = 3.2.0\n"
        "model = Y2\n"
        "repo = y1-community/y1-stock-rom\n",
        "base",
    )
    target = _firmware(
        root / "3. Install Reborn with flash_tool",
        "[fw_package]\n"
        "name = Reborn\n"
        "version = 1.0\n"
        "model = Y2\n"
        "repo = someone/reborn\n"
        "install_first = y1-community/y1-stock-rom\n"
        "install_first_version = 3.2.0=>\n",
        "target",
    )

    bundled = fw_package.find_bundled_firmware(app_dir=root)

    assert bundled is not None
    assert bundled.folder == target.resolve()
    assert bundled.install_first_package is not None
    assert bundled.install_first_package.folder == base.resolve()

    # Selecting the base folder also resolves to the target the pack is for.
    from_base = fw_package.locate_package(base)
    assert from_base is not None and from_base.folder == target.resolve()
