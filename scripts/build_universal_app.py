#!/usr/bin/env python3
"""build_universal_app.py — Build a 100% self-contained macOS 12+ Universal 2 (Intel + Apple Silicon) .app bundle.

Bundles:
1. Standalone Universal 2 Python 3.11 runtime (merged x86_64 + aarch64 via llvm-lipo).
2. Universal 2 PySide6, Shiboken6, and Cryptodome compiled Mach-O extensions.
3. Universal 2 libusb-1.0.dylib.
4. Pure Python dependencies (requests, pyusb, pyserial, colorama, certifi, urllib3, idna, charset_normalizer).
5. All application sources, MTKClient, and assets.
6. Compiled Universal 2 Mach-O launcher targeting macOS 12+.
7. Apple codesignature with entitlements.plist.
"""

import os
import sys
import shutil
import zipfile
import tarfile
import plistlib
import subprocess
import tempfile
import urllib.request
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from precompile_tree import compile_tree, compile_tree_if_stale, find_sources  # noqa: E402  (sibling build helper)

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist" / "macos"
_BUILD_BRAND = (os.environ.get("BUILD_BRAND") or "").strip().lower().replace("-", "_")
if not _BUILD_BRAND and (ROOT / "src" / "_build_brand.py").exists():
    for _line in (ROOT / "src" / "_build_brand.py").read_text(encoding="utf-8").splitlines():
        if _line.startswith("BUILD_BRAND"):
            _BUILD_BRAND = _line.split("=")[-1].strip().strip('"\'')
            break
IS_MEDIATEK_INSTALLER_BUILD = _BUILD_BRAND in ("mediatek_installer", "generic_mtk")
APP_DISPLAY_NAME = "MediaTek Installer" if IS_MEDIATEK_INSTALLER_BUILD else "Updater CE"
BUNDLE_ID = (
    "com.innioasis.mediatekinstaller" if IS_MEDIATEK_INSTALLER_BUILD else "com.innioasis.updater"
)
# INNIOASIS_APP_OUT lets a rehearsal (or CI) build somewhere other than dist/:
# the real build deletes and rebuilds dist/*.app in place, which would wipe an
# app the user is currently testing.
APP_DIR = Path(os.environ.get("INNIOASIS_APP_OUT") or (DIST / f"{APP_DISPLAY_NAME}.app"))
CONTENTS = APP_DIR / "Contents"
MACOS = CONTENTS / "MacOS"
RESOURCES = CONTENTS / "Resources"
FRAMEWORKS = CONTENTS / "Frameworks"
APP_CODE = RESOURCES / "app"
PYTHON_DIR = RESOURCES / "python"
SITE_PACKAGES = PYTHON_DIR / "lib" / "python3.11" / "site-packages"
ASSETS = ROOT / "assets"
DARWIN_LIBUSB = ROOT / "vendor" / "mtkclient" / "mtkclient" / "Darwin" / "libusb-1.0.dylib"
CACHE_DIR = ROOT / ".cache" / "macos_universal"


LAUNCHER_C = r'''
extern int _NSGetExecutablePath(char* buf, unsigned int* bufsize);
extern int access(const char *path, int mode);
extern int execv(const char *path, char *const argv[]);
extern int execve(const char *path, char *const argv[], char *const envp[]);
extern void exit(int status);
extern char *getenv(const char *name);
extern int setenv(const char *name, const char *value, int overwrite);
extern unsigned long strlen(const char *s);
extern char *strcpy(char *dest, const char *src);
extern char *strncpy(char *dest, const char *src, unsigned long n);
extern char *strcat(char *dest, const char *src);
extern char *strrchr(const char *s, int c);
extern int strcmp(const char *s1, const char *s2);
extern int snprintf(char *str, unsigned long size, const char *format, ...);
extern int puts(const char *s);

#define X_OK 1

int main(int argc, char **argv, char **envp) {
    char exe_path[1024];
    unsigned int size = sizeof(exe_path);
    if (_NSGetExecutablePath(exe_path, &size) != 0) {
        puts("Error: Could not resolve executable path.");
        exit(1);
    }

    char *last_slash = strrchr(exe_path, '/');
    if (!last_slash) exit(1);
    *last_slash = '\0';

    last_slash = strrchr(exe_path, '/');
    if (!last_slash) exit(1);
    *last_slash = '\0';
    char contents_dir[1024];
    strncpy(contents_dir, exe_path, sizeof(contents_dir));

    char resources_dir[1024];
    snprintf(resources_dir, sizeof(resources_dir), "%s/Resources", contents_dir);

    char frameworks_dir[1024];
    snprintf(frameworks_dir, sizeof(frameworks_dir), "%s/Frameworks", contents_dir);

    char python_dir[1024];
    snprintf(python_dir, sizeof(python_dir), "%s/python", resources_dir);

    char site_packages[1024];
    snprintf(site_packages, sizeof(site_packages), "%s/lib/python3.11/site-packages", python_dir);

    char qt_lib_dir[1024];
    snprintf(qt_lib_dir, sizeof(qt_lib_dir), "%s/PySide6/Qt/lib", site_packages);

    char qt_plugins_dir[1024];
    snprintf(qt_plugins_dir, sizeof(qt_plugins_dir), "%s/PySide6/Qt/plugins", site_packages);

    char qt_platforms_dir[1024];
    snprintf(qt_platforms_dir, sizeof(qt_platforms_dir), "%s/platforms", qt_plugins_dir);

    char app_dir[1024];
    snprintf(app_dir, sizeof(app_dir), "%s/app", resources_dir);

    // Bytecode, not source: the bundle ships launcher.pyc (see
    // precompile_tree.py) and CPython runs a sourceless .pyc as a script.
    char launcher_script[1024];
    snprintf(launcher_script, sizeof(launcher_script), "%s/launcher.pyc", app_dir);

    char python_bin[1024];
    snprintf(python_bin, sizeof(python_bin), "%s/bin/python3.11", python_dir);

    if (access(python_bin, X_OK) != 0) {
        snprintf(python_bin, sizeof(python_bin), "%s/bin/python3", python_dir);
    }

    // Set PYTHONHOME and PYTHONPATH
    setenv("PYTHONHOME", python_dir, 1);
    char pypath[4096];
    snprintf(pypath, sizeof(pypath), "%s:%s/vendor/mtkclient:%s", app_dir, app_dir, site_packages);
    setenv("PYTHONPATH", pypath, 1);

    // Set CA bundle for HTTPS requests
    char ssl_cert[1024];
    snprintf(ssl_cert, sizeof(ssl_cert), "%s/certifi/cacert.pem", site_packages);
    setenv("SSL_CERT_FILE", ssl_cert, 1);

    // Ensure terminal/GUI output is unbuffered
    setenv("PYTHONUNBUFFERED", "1", 1);

    // Set dynamic linker paths
    char dyld[4096];
    snprintf(dyld, sizeof(dyld), "%s:%s/lib:%s", frameworks_dir, python_dir, qt_lib_dir);
    setenv("DYLD_FRAMEWORK_PATH", dyld, 1);
    setenv("DYLD_LIBRARY_PATH", dyld, 1);
    setenv("DYLD_FALLBACK_LIBRARY_PATH", dyld, 1);

    // Set Qt plugin paths
    setenv("QT_PLUGIN_PATH", qt_plugins_dir, 1);
    setenv("QT_QPA_PLATFORM_PLUGIN_PATH", qt_platforms_dir, 1);
    setenv("INNIOASIS_APP_ROOT", contents_dir, 1);

    // Fallback search if bundled python executable is missing
    if (access(python_bin, X_OK) != 0) {
        const char *search_paths[] = {
            "/opt/homebrew/bin/python3",
            "/usr/local/bin/python3",
            "/usr/bin/python3",
            0
        };
        for (int i = 0; search_paths[i] != 0; i++) {
            if (access(search_paths[i], X_OK) == 0) {
                strncpy(python_bin, search_paths[i], sizeof(python_bin));
                break;
            }
        }
    }

    char *new_argv[128];
    int arg_idx = 0;
    new_argv[arg_idx++] = python_bin;
    new_argv[arg_idx++] = launcher_script;
    for (int i = 1; i < argc && arg_idx < 126; i++) {
        new_argv[arg_idx++] = argv[i];
    }
    new_argv[arg_idx] = 0;

    // Use execv so the child Python process inherits all variables set via setenv
    execv(new_argv[0], new_argv);
    puts("Error: Failed to launch Updater CE.");
    exit(1);
    return 1;
}
'''

LIB_SYSTEM_TBD = '''--- !tapi-tbd
tbd-version: 4
targets: [ x86_64-macos, arm64-macos ]
install-name: /usr/lib/libSystem.B.dylib
current-version: 1319
compatibility-version: 1
exports:
  - targets: [ x86_64-macos, arm64-macos ]
    symbols: [ _puts, _exit, _execv, _execve, _getenv, _setenv, _access, _strlen, _strcpy, _strncpy, _strcat, _strrchr, _strcmp, _snprintf, _system, __NSGetExecutablePath, ___stack_chk_fail, ___stack_chk_guard, _memcpy, _memset, _bzero ]
...
'''


def is_macho(path: Path) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
        return magic in (
            b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf",
            b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe",
            b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"
        )
    except Exception:
        return False


def ensure_cached_assets():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    targets = {
        "cpython_arm64.tar.gz": "https://github.com/astral-sh/python-build-standalone/releases/download/20260924/cpython-3.11.16%2B20260924-aarch64-apple-darwin-install_only_stripped.tar.gz",
        "cpython_x86_64.tar.gz": "https://github.com/astral-sh/python-build-standalone/releases/download/20260924/cpython-3.11.16%2B20260924-x86_64-apple-darwin-install_only_stripped.tar.gz",
        "pyside6_essentials.whl": "https://files.pythonhosted.org/packages/80/a9/51549844900837c70e5c89cbd840956c2533732ef4f05c2b656e35ca8182/pyside6_essentials-6.9.3-cp39-abi3-macosx_12_0_universal2.whl",
        "shiboken6.whl": "https://files.pythonhosted.org/packages/6b/ed/eac552326349629f8c3a89b57a754a860d9665aea78c172777395905d36b/shiboken6-6.9.3-cp39-abi3-macosx_12_0_universal2.whl",
        "pyside6.whl": "https://files.pythonhosted.org/packages/fe/15/1fa71e44d3b345b9eca82d38cf7e6d7505168b978fe98b3610c0e25d8c0e/pyside6-6.9.3-cp39-abi3-macosx_12_0_universal2.whl",
        "pycryptodome.whl": "https://files.pythonhosted.org/packages/db/6c/a1f71542c969912bb0e106f64f60a56cc1f0fabecf9396f45accbe63fa68/pycryptodome-3.23.0-cp37-abi3-macosx_10_9_universal2.whl",
        "pyobjc_core.whl": "https://files.pythonhosted.org/packages/95/df/d2b290708e9da86d6e7a9a2a2022b91915cf2e712a5a82e306cb6ee99792/pyobjc_core-12.1-cp311-cp311-macosx_10_9_universal2.whl",
        "pyobjc_framework_cocoa.whl": "https://files.pythonhosted.org/packages/3f/07/5760735c0fffc65107e648eaf7e0991f46da442ac4493501be5380e6d9d4/pyobjc_framework_Cocoa-12.1-cp311-cp311-macosx_10_9_universal2.whl",
    }
    pure_deps = ["requests", "pyusb", "pyserial", "colorama", "certifi", "urllib3", "idna", "charset_normalizer"]

    import ssl
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl._create_unverified_context()

    for name, url in targets.items():
        dest = CACHE_DIR / name
        # A developer or CI job can repeat a build after changing pinned
        # versions; cached wheel names are stable but their contents aren't.
        # Validate the wheel's embedded filename before trusting a cache hit.
        expected_filename = url.rsplit("/", 1)[-1]
        cached_ok = False
        if dest.exists() and dest.stat().st_size:
            if dest.name.startswith("cpython_"):
                # Archive name/version is pinned in the URL; validate that the
                # cached standalone runtime is the one the builder requested.
                cached_ok = True
            else:
                try:
                    with zipfile.ZipFile(dest) as cached:
                        metadata = [n for n in cached.namelist() if n.endswith(".dist-info/WHEEL")]
                        cached_ok = bool(metadata) and any(
                            f"{Path(name.split('/')[0]).name.split('-', 1)[0].lower()}"
                            in expected_filename.lower()
                            for name in metadata
                        )
                        # Check package version/tag from the archive metadata too.
                        wheel_text = cached.read(metadata[0]).decode("utf-8", "replace") if metadata else ""
                        expected_tokens = expected_filename.lower().replace("_", "-").split("-")
                        cached_ok = cached_ok and any(
                            line.lower().startswith("tag:") and "macosx_12_0_universal2" in line.lower()
                            for line in wheel_text.splitlines()
                        )
                        # WHEEL tags do not carry the distribution version; the
                        # wheel filename stem encoded in RECORD's dist-info does.
                        dist_info = Path(metadata[0]).parts[0] if metadata else ""
                        cached_ok = cached_ok and expected_filename.lower().startswith(dist_info.lower().replace("_", "-").replace(".dist-info", ""))
                except (OSError, zipfile.BadZipFile):
                    cached_ok = False
        if not cached_ok:
            print(f">>> Downloading {name}...")
            req = urllib.request.Request(url, headers={"User-Agent": "updater-builder"})
            with urllib.request.urlopen(req, context=ctx) as resp, open(dest, "wb") as f:
                shutil.copyfileobj(resp, f)

    for pkg in pure_deps:
        dest = CACHE_DIR / f"{pkg}.whl"
        if not dest.exists() or dest.stat().st_size == 0:
            print(f">>> Downloading {pkg}.whl from PyPI...")
            req = urllib.request.urlopen(f"https://pypi.org/pypi/{pkg}/json", context=ctx)
            data = json.loads(req.read())
            whls = [u for u in data["urls"] if u["filename"].endswith(".whl") and ("none-any" in u["filename"] or "py3-none" in u["filename"])]
            if whls:
                url = whls[-1]["url"]
                with urllib.request.urlopen(url, context=ctx) as resp, open(dest, "wb") as f:
                    shutil.copyfileobj(resp, f)


def compile_universal_launcher(out_path: Path):
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        c_file = tdp / "launcher.c"
        c_file.write_text(LAUNCHER_C)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        if sys.platform == "darwin":
            subprocess.run([
                "clang", "-arch", "x86_64", "-arch", "arm64",
                "-mmacosx-version-min=12.0",
                str(c_file), "-o", str(out_path)
            ], check=True)
            os.chmod(out_path, 0o755)
            return

        tbd_file = tdp / "libSystem.tbd"
        tbd_file.write_text(LIB_SYSTEM_TBD)

        x86_obj = tdp / "x86.o"
        arm_obj = tdp / "arm.o"
        x86_bin = tdp / "x86_bin"
        arm_bin = tdp / "arm_bin"

        subprocess.run([
            "clang", "-target", "x86_64-apple-macos12", "-fno-stack-protector",
            "-c", str(c_file), "-o", str(x86_obj)
        ], check=True)
        subprocess.run([
            "clang", "-target", "arm64-apple-macos12", "-fno-stack-protector",
            "-c", str(c_file), "-o", str(arm_obj)
        ], check=True)

        linker = "ld64.lld" if shutil.which("ld64.lld") else "lld"
        subprocess.run([
            linker, "-arch", "x86_64", "-platform_version", "macos", "12.0.0", "12.0.0",
            str(x86_obj), str(tbd_file), "-o", str(x86_bin)
        ], check=True)
        subprocess.run([
            linker, "-arch", "arm64", "-platform_version", "macos", "12.0.0", "12.0.0",
            str(arm_obj), str(tbd_file), "-o", str(arm_bin)
        ], check=True)

        lipo = "llvm-lipo" if shutil.which("llvm-lipo") else "lipo"
        subprocess.run([
            lipo, "-create", str(x86_bin), str(arm_bin), "-output", str(out_path)
        ], check=True)
        os.chmod(out_path, 0o755)


def build_universal_python(dest_python: Path):
    print(">>> Assembling Universal 2 Standalone Python runtime...")
    lipo = "llvm-lipo" if shutil.which("llvm-lipo") else "lipo"

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        arm_dir = tdp / "arm64"
        x86_dir = tdp / "x86_64"

        with tarfile.open(CACHE_DIR / "cpython_arm64.tar.gz") as tf:
            tf.extractall(arm_dir)
        with tarfile.open(CACHE_DIR / "cpython_x86_64.tar.gz") as tf:
            tf.extractall(x86_dir)

        arm_py = arm_dir / "python"
        x86_py = x86_dir / "python"

        if dest_python.exists():
            shutil.rmtree(dest_python)
        shutil.copytree(arm_py, dest_python, symlinks=True)

        # Merge all Mach-O binaries into Universal 2
        for root, _, files in os.walk(dest_python):
            for fname in files:
                target_f = Path(root) / fname
                rel_path = target_f.relative_to(dest_python)
                x86_f = x86_py / rel_path

                if is_macho(target_f) and x86_f.exists() and is_macho(x86_f):
                    tmp_uni = target_f.with_suffix(".universal_tmp")
                    try:
                        subprocess.run([lipo, "-create", str(x86_f), str(target_f), "-output", str(tmp_uni)], check=True)
                        tmp_uni.replace(target_f)
                        os.chmod(target_f, 0o755)
                    except Exception:
                        if tmp_uni.exists():
                            tmp_uni.unlink()

    # Clean unneeded developer scripts in python/bin
    bin_dir = dest_python / "bin"
    if bin_dir.exists():
        for f in list(bin_dir.iterdir()):
            if f.name not in ("python3.11", "python3"):
                if f.is_dir():
                    shutil.rmtree(f)
                else:
                    f.unlink()

    # Clean unneeded standard library modules
    lib_dir = dest_python / "lib" / "python3.11"
    if lib_dir.exists():
        for unneeded in ["idlelib", "turtledemo", "turtle.py", "ensurepip", "tkinter", "test", "tests"]:
            p = lib_dir / unneeded
            if p.is_dir():
                shutil.rmtree(p)
            elif p.is_file():
                p.unlink()


def install_universal_packages(site_packages: Path, dest_python: Path):
    print(">>> Installing Universal 2 Qt & Python dependencies...")
    site_packages.mkdir(parents=True, exist_ok=True)
    whls = sorted(CACHE_DIR.glob("*.whl"))
    for w in whls:
        with zipfile.ZipFile(w) as zf:
            zf.extractall(site_packages)

    # Remove static libraries (*.a) which cause rcodesign index out of bounds panics
    for a_file in list(dest_python.rglob("*.a")):
        a_file.unlink()

    # Prune type annotations (*.pyi)
    for pyi_file in list(site_packages.rglob("*.pyi")):
        pyi_file.unlink()

    # Remove Qt dev apps (Designer, Assistant, Linguist) from PySide6
    for dev_app in list(site_packages.rglob("*.app")):
        if dev_app.is_dir():
            shutil.rmtree(dev_app)

    # Remove unneeded PySide6 components (QML, dev tools, designer plugins)
    pyside_dir = site_packages / "PySide6"
    if pyside_dir.exists():
        for sub in [
            "Qt/qml", "Qt/libexec", "Qt/metatypes", "Qt/translations",
            "scripts", "typesystems", "svgtoqml",
            "Qt/plugins/designer", "Qt/plugins/qmllint", "Qt/plugins/qmltooling",
        ]:
            d = pyside_dir / sub
            if d.is_dir():
                shutil.rmtree(d)
            elif d.is_file():
                d.unlink()


def build():
    print(f"=== Building Self-Contained Universal 2 macOS .app at {APP_DIR} ===")
    ensure_cached_assets()

    if APP_DIR.exists():
        shutil.rmtree(APP_DIR)

    MACOS.mkdir(parents=True, exist_ok=True)
    RESOURCES.mkdir(parents=True, exist_ok=True)
    FRAMEWORKS.mkdir(parents=True, exist_ok=True)
    APP_CODE.mkdir(parents=True, exist_ok=True)

    # 1. Compile Universal 2 Launcher Executable
    exe_target = MACOS / APP_DISPLAY_NAME
    compile_universal_launcher(exe_target)

    # 2. Build Universal 2 Python Runtime
    build_universal_python(PYTHON_DIR)

    # 3. Install Universal 2 dependencies into bundled python
    install_universal_packages(SITE_PACKAGES, PYTHON_DIR)

    # 4. Copy Universal 2 libusb-1.0.dylib
    if DARWIN_LIBUSB.exists():
        shutil.copy2(DARWIN_LIBUSB, FRAMEWORKS / "libusb-1.0.dylib")
        shutil.copy2(DARWIN_LIBUSB, MACOS / "libusb-1.0.dylib")

    # 5. Precompile the application code, MTKClient and the pure-Python
    #    site-packages into bytecode-only trees. Shipping source made every
    #    launch re-parse files whose __pycache__ can never be written (a
    #    read-only .dmg or an .app another admin owns).
    bundled_python = (
        (PYTHON_DIR / "bin" / "python3.11") if sys.platform == "darwin" else Path(sys.executable)
    )
    compile_tree([ROOT / "launcher.py"], APP_CODE, python=bundled_python, clean=False, write_manifest=False)
    compile_tree([ROOT / "src"], APP_CODE / "src", python=bundled_python, write_manifest=False)
    # This builder extracts the pure-Python wheels into site-packages itself.
    # Precompile in place (without removing readable sources from third parties)
    # so Python can import unchecked-hash bytecode even from a read-only bundle.
    # This tree is already assembled and should not gain a build manifest.
    compile_tree(
        [SITE_PACKAGES], SITE_PACKAGES, python=bundled_python,
        clean=False, strict=False, write_manifest=False,
    )
    # The vendored mtkclient is the Chinese build's fork, byte for byte: only
    # its packaging changes here, never its code (see AGENTS.md).
    compile_tree_if_stale(
        [ROOT / "vendor" / "mtkclient"],
        APP_CODE / "vendor" / "mtkclient",
        python=bundled_python,
        strict=False,
        write_manifest=False,
    )
    compile_tree_if_stale(
        [ROOT / "vendor" / "mtkclient"],
        SITE_PACKAGES / "mtkclient",
        python=bundled_python,
        strict=False,
        write_manifest=False,
    )
    shutil.copytree(ASSETS, APP_CODE / "assets", dirs_exist_ok=True)

    # 5b. Unar (cross-platform .rar extractor parity with Chinese reference release)
    unar_src = ROOT / "tools" / "macos" / "unar"
    if unar_src.exists():
        tools_fw = FRAMEWORKS / "tools"
        tools_fw.mkdir(parents=True, exist_ok=True)
        shutil.copy2(unar_src, tools_fw / "unar")
        os.chmod(tools_fw / "unar", 0o755)
        tools_res = RESOURCES / "tools"
        tools_res.mkdir(parents=True, exist_ok=True)
        try:
            (tools_res / "unar").symlink_to(Path("../Frameworks/tools/unar"))
        except Exception:
            shutil.copy2(unar_src, tools_res / "unar")

    # 6. Icon
    icon_icns = ASSETS / "icon.icns"
    if icon_icns.exists():
        shutil.copy2(icon_icns, RESOURCES / "icon.icns")

    # 7. PkgInfo
    (CONTENTS / "PkgInfo").write_bytes(b"APPL????")

    # 8. Info.plist
    info_plist = {
        "CFBundleDisplayName": APP_DISPLAY_NAME,
        "CFBundleExecutable": APP_DISPLAY_NAME,
        "CFBundleIconFile": "icon.icns",
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": APP_DISPLAY_NAME,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "2.5.1",
        "CFBundleVersion": "2.5.1",
        "LSApplicationCategoryType": "public.app-category.utilities",
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,
        "NSPrincipalClass": "NSApplication",
        "NSHumanReadableCopyright": "Copyright © 2024-2026 Innioasis Community. All rights reserved.",
        "CFBundleDocumentTypes": [
            {
                "CFBundleTypeName": "Firmware Archive",
                "CFBundleTypeRole": "Viewer",
                "LSHandlerRank": "Alternate",
                "LSItemContentTypes": [
                    "com.pkware.zip-archive",
                    "public.zip-archive",
                    "org.rarlab.rar-archive",
                ],
            }
        ],
    }
    with open(CONTENTS / "Info.plist", "wb") as f:
        plistlib.dump(info_plist, f)

    # 9. Codesign with entitlements
    entitlements = ASSETS / "entitlements.plist"
    rcodesign = shutil.which("rcodesign") or "/home/deck/.cargo/bin/rcodesign"
    if Path(rcodesign).exists():
        print(">>> Signing bundle with rcodesign...")
        subprocess.run([
            str(rcodesign), "sign", "--entitlements-xml-file", str(entitlements), str(APP_DIR)
        ], check=True)
    elif shutil.which("codesign"):
        print(">>> Signing bundle with native codesign...")
        subprocess.run([
            "codesign", "--force", "--deep", "--entitlements", str(entitlements), "-s", "-", str(APP_DIR)
        ], check=True)

    print("\nVerification:")
    if shutil.which("file"):
        subprocess.run(["file", str(exe_target), str(FRAMEWORKS / "libusb-1.0.dylib"), str(RESOURCES / "icon.icns")])
    leftover = find_sources(APP_CODE)
    pyc_count = sum(1 for _ in APP_CODE.rglob("*.pyc"))
    print(f"Application code: {pyc_count} bytecode files, {len(leftover)} source files")
    for stray in leftover[:10]:
        print(f"  source left behind: {stray.relative_to(APP_CODE)}")
    site_leftover = [p for p in find_sources(SITE_PACKAGES) if "mtkclient" not in p.parts]
    print(f"site-packages: {sum(1 for _ in SITE_PACKAGES.rglob('*.pyc'))} bytecode files, {len(site_leftover)} source files")
    total_size_mb = sum(f.stat().st_size for f in APP_DIR.rglob("*") if f.is_file()) / (1024 * 1024)
    print(f"Total Bundle Size: {total_size_mb:.1f} MB")
    print(f"\n=== Build Complete: {APP_DIR} ===")


if __name__ == "__main__":
    build()
