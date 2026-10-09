#!/usr/bin/env python3
"""build_universal_app.py — Build a 100% self-contained macOS 13+ Universal 2 (Intel + Apple Silicon) .app bundle.

Bundles:
1. Standalone Universal 2 Python 3.11 runtime (merged x86_64 + aarch64 via llvm-lipo).
2. Universal 2 PySide6, Shiboken6, and Cryptodome compiled Mach-O extensions.
3. Universal 2 libusb-1.0.dylib.
4. Pure Python dependencies (requests, pyusb, pyserial, colorama, certifi, urllib3, idna, charset_normalizer).
5. All application sources, MTKClient, and assets.
6. Compiled Universal 2 Mach-O launcher targeting macOS 13+.
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

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
APP_DIR = DIST / "Updater CE.app"
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

    char launcher_script[1024];
    snprintf(launcher_script, sizeof(launcher_script), "%s/launcher.py", app_dir);

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
        "pyside6_essentials.whl": "https://files.pythonhosted.org/packages/6f/16/0b7ecf89ebada82ed0430be33809ff325761ece83104f8635b1bd101fcd0/pyside6_essentials-6.11.2-cp310-abi3-macosx_13_0_universal2.whl",
        "shiboken6.whl": "https://files.pythonhosted.org/packages/47/44/11bf71c36e71936ab4e923e5dd23599dab527c4488b01860b0f689ce9947/shiboken6-6.11.2-cp310-abi3-macosx_13_0_universal2.whl",
        "pyside6.whl": "https://files.pythonhosted.org/packages/ae/b5/99ff75f604d69eda1c5d1fe85cc43fb697d95eabc30ba86af099c2ddcd96/pyside6-6.11.2-cp310-abi3-macosx_13_0_universal2.whl",
        "pycryptodome.whl": "https://files.pythonhosted.org/packages/db/6c/a1f71542c969912bb0e106f64f60a56cc1f0fabecf9396f45accbe63fa68/pycryptodome-3.23.0-cp37-abi3-macosx_10_9_universal2.whl",
        "pyobjc_core.whl": "https://files.pythonhosted.org/packages/b8/02/b04297ca275c92c57ebbe197e4125b2067759a296541f92e7c3746c10129/pyobjc_core-12.1-cp311-cp311-macosx_10_9_universal2.whl",
        "pyobjc_framework_cocoa.whl": "https://files.pythonhosted.org/packages/f8/f4/0488663ce911965bb7e201b22e15bc32f171092cae6ef4e0bc7f2dca5877/pyobjc_framework_Cocoa-12.1-cp311-cp311-macosx_10_9_universal2.whl",
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
        if not dest.exists() or dest.stat().st_size == 0:
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
                "-mmacosx-version-min=13.0",
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
            "clang", "-target", "x86_64-apple-macos13", "-fno-stack-protector",
            "-c", str(c_file), "-o", str(x86_obj)
        ], check=True)
        subprocess.run([
            "clang", "-target", "arm64-apple-macos13", "-fno-stack-protector",
            "-c", str(c_file), "-o", str(arm_obj)
        ], check=True)

        linker = "ld64.lld" if shutil.which("ld64.lld") else "lld"
        subprocess.run([
            linker, "-arch", "x86_64", "-platform_version", "macos", "13.0.0", "13.0.0",
            str(x86_obj), str(tbd_file), "-o", str(x86_bin)
        ], check=True)
        subprocess.run([
            linker, "-arch", "arm64", "-platform_version", "macos", "13.0.0", "13.0.0",
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
    exe_target = MACOS / "Updater CE"
    compile_universal_launcher(exe_target)

    # 2. Build Universal 2 Python Runtime
    build_universal_python(PYTHON_DIR)

    # 3. Install Universal 2 dependencies into bundled python
    install_universal_packages(SITE_PACKAGES, PYTHON_DIR)

    # 4. Copy Universal 2 libusb-1.0.dylib
    if DARWIN_LIBUSB.exists():
        shutil.copy2(DARWIN_LIBUSB, FRAMEWORKS / "libusb-1.0.dylib")
        shutil.copy2(DARWIN_LIBUSB, MACOS / "libusb-1.0.dylib")

    # 5. Copy Application Source, MTKClient, and Assets
    shutil.copy2(ROOT / "launcher.py", APP_CODE / "launcher.py")
    shutil.copytree(ROOT / "src", APP_CODE / "src", dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(ROOT / "vendor" / "mtkclient", APP_CODE / "vendor" / "mtkclient", dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(ROOT / "vendor" / "mtkclient", SITE_PACKAGES / "mtkclient", dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(ASSETS, APP_CODE / "assets", dirs_exist_ok=True)

    # 6. Icon
    icon_icns = ASSETS / "icon.icns"
    if icon_icns.exists():
        shutil.copy2(icon_icns, RESOURCES / "icon.icns")

    # 7. PkgInfo
    (CONTENTS / "PkgInfo").write_bytes(b"APPL????")

    # 8. Info.plist
    info_plist = {
        "CFBundleDisplayName": "Updater CE",
        "CFBundleExecutable": "Updater CE",
        "CFBundleIconFile": "icon.icns",
        "CFBundleIdentifier": "com.innioasis.updater",
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": "Updater CE",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "3.0.0",
        "CFBundleVersion": "3.0.0",
        "LSApplicationCategoryType": "public.app-category.utilities",
        "LSMinimumSystemVersion": "13.0",
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
    subprocess.run(["file", str(exe_target), str(FRAMEWORKS / "libusb-1.0.dylib"), str(RESOURCES / "icon.icns")])
    total_size_mb = sum(f.stat().st_size for f in APP_DIR.rglob("*") if f.is_file()) / (1024 * 1024)
    print(f"Total Bundle Size: {total_size_mb:.1f} MB")
    print(f"\n=== Build Complete: {APP_DIR} ===")


if __name__ == "__main__":
    build()
