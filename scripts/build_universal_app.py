#!/usr/bin/env python3
def _read_app_version(default="2.5.6"):
    """One source of truth: src/config.py's APP_VERSION."""
    import re as _re
    from pathlib import Path as _Path
    bases = []
    spec_path = globals().get("SPECPATH")
    if spec_path:
        bases.append(_Path(spec_path))
    bases.append(_Path.cwd())
    for base in bases:
        target = base / "src" / "config.py"
        if not target.exists():
            continue
        match = _re.search(r'APP_VERSION\s*=\s*"([^"]+)"', target.read_text())
        if match:
            return match.group(1)
    return default


APP_VERSION = _read_app_version()

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
_target_app_out = os.environ.get("INNIOASIS_APP_OUT")
APP_DISPLAY_NAME = os.environ.get("APP_NAME") or (
    Path(_target_app_out).stem if _target_app_out else (
        "MediaTek Installer" if IS_MEDIATEK_INSTALLER_BUILD else "Innioasis Updater"
    )
)
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

#define R_OK 4
#define W_OK 2
#define X_OK 1
#define F_OK 0

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
    if (access(launcher_script, 0) != 0) {
        snprintf(launcher_script, sizeof(launcher_script), "%s/launcher.py", app_dir);
    }

    // The interpreter is installed under the app's own name (see
    // write_interpreter_identity): macOS names a process after the executable it
    // was launched as, so a bare python3.11 announced itself in the Dock and in
    // the login-item banner as "python3.11".
    char python_bin[1024];
    snprintf(python_bin, sizeof(python_bin), "%s/bin/@@APP_EXECUTABLE@@", python_dir);

    if (access(python_bin, X_OK) != 0) {
        snprintf(python_bin, sizeof(python_bin), "%s/bin/python3.11", python_dir);
    }
    if (access(python_bin, X_OK) != 0) {
        snprintf(python_bin, sizeof(python_bin), "%s/bin/python3", python_dir);
    }

    // Set PYTHONHOME and PYTHONPATH
    setenv("PYTHONHOME", python_dir, 1);
    char pypath[4096];
    snprintf(pypath, sizeof(pypath), "%s:%s/vendor/mtkclient:%s", app_dir, app_dir, site_packages);
    setenv("PYTHONPATH", pypath, 1);

    // Set CA bundle for HTTPS requests if present
    char ssl_cert[1024];
    snprintf(ssl_cert, sizeof(ssl_cert), "%s/certifi/cacert.pem", site_packages);
    if (access(ssl_cert, R_OK) == 0) {
        setenv("SSL_CERT_FILE", ssl_cert, 1);
    }

    // Ensure terminal/GUI output is unbuffered
    setenv("PYTHONUNBUFFERED", "1", 1);

    // The bundle is code-signed. A writable .app (dist/, a copy in Downloads)
    // lets CPython drop __pycache__ beside the shipped bytecode, which changes
    // a sealed resource and invalidates the signature on the very first run.
    setenv("PYTHONDONTWRITEBYTECODE", "1", 1);

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
        # Qt 6.7.3 sat on macOS 11 wheels but predates Tahoe/Golden Gate: its
        # Aqua style paints the combos as flat white fields with an accent
        # arrow and mis-reads the host appearance, which is the dropdown /
        # dark-window regression users hit in 2.5.2. 6.9.3 is the version 2.5.1
        # shipped and is the oldest one that draws modern macOS controls. Its
        # wheels need macOS 12, which is the bundle minimum anyway.
        "pyside6_essentials.whl": "https://files.pythonhosted.org/packages/80/a9/51549844900837c70e5c89cbd840956c2533732ef4f05c2b656e35ca8182/pyside6_essentials-6.9.3-cp39-abi3-macosx_12_0_universal2.whl",
        "shiboken6.whl": "https://files.pythonhosted.org/packages/6b/ed/eac552326349629f8c3a89b57a754a860d9665aea78c172777395905d36b/shiboken6-6.9.3-cp39-abi3-macosx_12_0_universal2.whl",
        "pyside6.whl": "https://files.pythonhosted.org/packages/fe/15/1fa71e44d3b345b9eca82d38cf7e6d7505168b978fe98b3610c0e25d8c0e/pyside6-6.9.3-cp39-abi3-macosx_12_0_universal2.whl",
        "pycryptodome.whl": "https://files.pythonhosted.org/packages/db/6c/a1f71542c969912bb0e106f64f60a56cc1f0fabecf9396f45accbe63fa68/pycryptodome-3.23.0-cp37-abi3-macosx_10_9_universal2.whl",
        "pycryptodomex.whl": "https://files.pythonhosted.org/packages/bf/ae/48679004ab908818326a62c35586b51ccd6ba0e3a03d0ae637cd13e8df99/pycryptodomex-3.24.1-cp37-abi3-macosx_10_9_universal2.whl",
        "pyobjc_core.whl": "https://files.pythonhosted.org/packages/95/df/d2b290708e9da86d6e7a9a2a2022b91915cf2e712a5a82e306cb6ee99792/pyobjc_core-12.1-cp311-cp311-macosx_10_9_universal2.whl",
        "pyobjc_framework_cocoa.whl": "https://files.pythonhosted.org/packages/3f/07/5760735c0fffc65107e648eaf7e0991f46da442ac4493501be5380e6d9d4/pyobjc_framework_cocoa-12.1-cp311-cp311-macosx_10_9_universal2.whl",
        "pyobjc_framework_quartz.whl": "https://files.pythonhosted.org/packages/b7/ef/dcd22b743e38b3c430fce4788176c2c5afa8bfb01085b8143b02d1e75201/pyobjc_framework_quartz-12.1-cp311-cp311-macosx_10_9_universal2.whl",
        # UserNotifications: the only macOS channel that takes part in the
        # host's permission and presentation settings, so notices appear as
        # banners. The older NSUserNotification API files them silently.
        "pyobjc_framework_usernotifications.whl": "https://files.pythonhosted.org/packages/d1/96/aa25bb0727e661a352d1c52e7288e25c12fe77047f988bb45557c17cf2d7/pyobjc_framework_usernotifications-12.1-cp311-cp311-macosx_10_9_universal2.whl",
        "capstone.whl": "https://files.pythonhosted.org/packages/da/f3/43f4dbfc36c7740d512621a9d42b276ac21bb6ca7cb974d4c8a19060d387/capstone-5.0.9-py3-none-macosx_10_9_universal2.whl",
    }
    pure_deps = [
        "requests",
        "pyusb",
        "pyserial",
        "colorama",
        "certifi",
        "urllib3",
        "idna",
        "charset_normalizer",
        "pyqt-liquidglass",
    ]

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
                            line.lower().startswith("tag:") and "universal2" in line.lower()
                            for line in wheel_text.splitlines()
                        )
                        # WHEEL tags do not carry the distribution version; the
                        # wheel filename stem encoded in RECORD's dist-info does.
                        dist_info = Path(metadata[0]).parts[0] if metadata else ""
                        # Wheel filenames use underscores where dist-info names
                        # use hyphens (pyside6_essentials-6.9.3 vs
                        # PySide6_Essentials-6.9.3.dist-info). Comparing the raw
                        # strings never matched, so every build re-downloaded
                        # hundreds of megabytes it already had in the cache.
                        dist_stem = dist_info.lower().replace(".dist-info", "").replace("_", "-")
                        cached_ok = cached_ok and expected_filename.lower().replace("_", "-").startswith(dist_stem)
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


def launcher_source() -> str:
    """The launcher C source with this build's interpreter name baked in."""
    name = APP_DISPLAY_NAME.replace("\\", "\\\\").replace('"', '\\"')
    return LAUNCHER_C.replace("@@APP_EXECUTABLE@@", name)


def compile_universal_launcher(out_path: Path):
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        c_file = tdp / "launcher.c"
        c_file.write_text(launcher_source())
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
            "clang", "-target", "x86_64-apple-macos11", "-fno-stack-protector",
            "-c", str(c_file), "-o", str(x86_obj)
        ], check=True)
        subprocess.run([
            "clang", "-target", "arm64-apple-macos11", "-fno-stack-protector",
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
        for unneeded in [
            "idlelib", "turtledemo", "turtle.py", "ensurepip", "tkinter", "test", "tests",
            # Editors/2to3/pydoc data are never imported by the app; removing
            # them saves the precompile, lipo and signature work as well.
            "lib2to3", "distutils", "pydoc_data",
        ]:
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
            # The app uses no Qt SQL module. Its drivers link absolute
            # build-host paths (/usr/local/opt/libiodbc, Postgres.app,
            # ../../g_macosarm_64/...), which would be the only non-universal
            # dependencies left in the arm64 slice and can never resolve on a
            # user's Mac.
            "Qt/plugins/sqldrivers",
        ]:
            d = pyside_dir / sub
            if d.is_dir():
                shutil.rmtree(d)
            elif d.is_file():
                d.unlink()
        # PDF image-format plugin: nothing here renders PDF images, and it is
        # the one remaining plugin whose @rpath dependency (QtPdf) is not
        # shipped, so drop it instead of leaving an unloadable Mach-O behind.
        qpdf_plugin = pyside_dir / "Qt/plugins/imageformats/libqpdf.dylib"
        if qpdf_plugin.is_file():
            qpdf_plugin.unlink()

        # Qt's developer executables: lupdate alone is 82 MB of lipo'd Mach-O.
        # Nothing here starts any of them (the catalogs are JSON, not .ts/.qm,
        # and the app has no QML to lint or ship).
        for dev_tool in ("lupdate", "lrelease", "qmlls", "qmlformat", "qmllint"):
            tool = pyside_dir / dev_tool
            if tool.is_file():
                tool.unlink()

        # QML/Quick, Designer and OpenGL are a second UI stack this app never
        # imports (it is pure QtWidgets). Verified with otool over a built
        # bundle: each family references only itself, and the virtual-keyboard
        # input-context plugin is the one plugin that needs QtQuick, so the
        # whole set leaves together instead of dangling unloadable Mach-O.
        # These also have no importer and no Mach-O reference anywhere in the
        # bundle (checked with otool): the app is QtCore/Gui/Widgets/Network/Svg.
        qt_lib = pyside_dir / "Qt" / "lib"
        for pattern in (
            "QtQuick*", "QtQml*", "QtDesigner*", "QtOpenGL*", "QtSql*",
            "QtHelp*", "QtTest*", "QtLabs*", "QtUiTools*",
            # QtGui itself links QtDBus, so it must stay.
            "QtPrintSupport*", "QtXml*", "QtSvgWidgets*", "QtConcurrent*",
        ):
            for path in qt_lib.glob(pattern):
                if path.is_dir():
                    shutil.rmtree(path)
                elif path.is_file():
                    path.unlink()
        for pattern in (
            "QtQuick*", "QtQml*", "QtDesigner*", "QtOpenGL*", "QtSql*",
            "QtHelp*", "QtTest*", "QtUiTools*", "libpyside6qml*.dylib",
            "QtPrintSupport*", "QtXml*", "QtSvgWidgets*",
            "QtConcurrent*", "QtExampleIcons*",
        ):
            for path in pyside_dir.glob(pattern):
                if path.is_dir():
                    shutil.rmtree(path)
                elif path.is_file():
                    path.unlink()
        vkb_plugin = pyside_dir / "Qt/plugins/platforminputcontexts/libqtvirtualkeyboardplugin.dylib"
        if vkb_plugin.is_file():
            vkb_plugin.unlink()

    # The interpreter never installs anything: drop the packaging toolchain
    # python-build-standalone ships in site-packages. Hundreds of files and
    # ~11 MB that would otherwise be precompiled, lipo'd and signed.
    for pack in ("pip", "setuptools", "pkg_resources", "_distutils_hack"):
        p = site_packages / pack
        if p.is_dir():
            shutil.rmtree(p)
        elif p.is_file():
            p.unlink()
    # That module's .pth shim runs on every interpreter start; with the module
    # gone it prints a ModuleNotFoundError traceback into the app's log.
    distutils_pth = site_packages / "distutils-precedence.pth"
    if distutils_pth.is_file():
        distutils_pth.unlink()

    # C++ headers exist to build bindings against, never to run the app.
    pyside_include = site_packages / "PySide6" / "include"
    if pyside_include.is_dir():
        shutil.rmtree(pyside_include)

    # tkinter is removed above, so its Tcl/Tk runtime is dead weight too.
    tk_lib = dest_python / "lib"
    for name in ("tcl9", "tcl9.0", "tk9.0", "itcl4.3.8"):
        p = tk_lib / name
        if p.is_dir():
            shutil.rmtree(p)
    for pattern in ("libtcl9*.dylib", "libtk9*.dylib", "libitcl4*.dylib"):
        for p in tk_lib.glob(pattern):
            p.unlink()
    for p in (dest_python / "lib" / "python3.11" / "lib-dynload").glob("_tkinter*"):
        p.unlink()


def write_interpreter_identity(python_dir: Path) -> None:
    """Give the bundled interpreter the app's own bundle identity.

    The launcher ``execv``s this interpreter, so the running process is that
    executable rather than ``Contents/MacOS``. macOS names the process, and
    attributes its notifications, to the bundle that contains it: as a bare
    ``Resources/python/bin/python3.11`` with no Info.plist, that is "python3.11"
    with no bundle identifier. The Dock and the login-item banner then announced
    "python3.11", and ``NSUserNotificationCenter`` was nil, which pushed every
    notice onto ``osascript`` and showed it under Script Editor.

    A bundle needs its executable beside its own Info.plist, so the interpreter
    is installed under the app's name as well and the launcher prefers it.
    """
    bin_dir = python_dir / "bin"
    if not bin_dir.is_dir():
        return
    interpreter = bin_dir / "python3.11"
    if not interpreter.exists():
        interpreter = bin_dir / "python3"
    if not interpreter.exists():
        print("  ! Bundled interpreter not found; app identity not applied")
        return

    app_interpreter = bin_dir / APP_DISPLAY_NAME
    if app_interpreter != interpreter and not app_interpreter.exists():
        try:
            app_interpreter.symlink_to(interpreter.name)
        except OSError:
            shutil.copy2(interpreter, app_interpreter)

    info = {
        "CFBundleDisplayName": APP_DISPLAY_NAME,
        "CFBundleExecutable": app_interpreter.name,
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": APP_DISPLAY_NAME,
        "CFBundlePackageType": "APPL",
    }
    icon_icns = ASSETS / ("icon_generic_mtk.icns" if IS_MEDIATEK_INSTALLER_BUILD else "icon.icns")
    if not icon_icns.exists():
        icon_icns = ASSETS / "icon.icns"
    if icon_icns.exists():
        shutil.copy2(icon_icns, bin_dir / "icon.icns")
        info["CFBundleIconFile"] = "icon.icns"
    (bin_dir / "Info.plist").write_bytes(plistlib.dumps(info))


def fetch_online_snapshots():
    """Refresh assets/online with the latest innioasis.app copies (best-effort).

    The app reads these bundled files before trying the network, so a launch on a
    slow or dead connection still lists firmware and shows notices.
    """
    script = ROOT / "scripts" / "fetch_online_data.py"
    if not script.is_file():
        return
    import subprocess

    try:
        subprocess.run([sys.executable, str(script)], cwd=str(ROOT), timeout=180, check=False)
    except Exception as e:
        print(f"  ! Online snapshot refresh skipped: {e}")


def sign_ad_hoc_tree(app_dir: Path, entitlements: Path) -> None:
    """Sign every Mach-O slice ad-hoc, then seal the bundle.

    ``lipo`` merges an unsigned x86_64 slice into a binary that already carries
    an arm64 signature, so the merged interpreter keeps only the arm64
    signature and ``codesign --verify --arch x86_64`` rejects it. Signing only
    ``*.so``/``*.dylib`` plus ``--deep`` does not help either: ``lipo``'d
    binaries such as ``bin/python3.11`` and PySide6's Qt frameworks stay
    linker-signed or half-signed. Each piece is signed here before the bundle,
    so both slices verify on their own and the bundle seal is valid.
    """
    codesign = shutil.which("codesign")
    if not codesign:
        return

    def sign(target: Path) -> None:
        subprocess.run(
            [codesign, "--force", "--timestamp=none", "--sign", "-", str(target)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    for path in app_dir.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        if is_macho(path):
            sign(path)
    for framework in sorted({p for p in app_dir.rglob("*.framework") if p.is_dir()}):
        sign(framework)
    subprocess.run(
        [
            codesign,
            "--force",
            "--timestamp=none",
            "--entitlements",
            str(entitlements),
            "--sign",
            "-",
            str(app_dir),
        ],
        check=True,
    )


def build():
    print(f"=== Building Self-Contained Universal 2 macOS .app at {APP_DIR} ===")
    ensure_cached_assets()
    fetch_online_snapshots()

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
    # 2b. The launcher execs this interpreter, so it wears the app's identity.
    write_interpreter_identity(PYTHON_DIR)

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
    # The 360p themes archive is 62 MB of every build. It is downloaded at first
    # themes install (see theme_pack.THEME_PACK_URL) and merged over the small
    # fonts pack, which stays bundled for offline/font coverage.
    bundled_themes_zip = APP_CODE / "assets" / "360p" / "themes-themepack.zip"
    if bundled_themes_zip.is_file():
        bundled_themes_zip.unlink()

    # 5b. Unar (cross-platform .rar extractor parity with Chinese reference release)
    unar_src = ROOT / "tools" / "macos" / "unar"
    if unar_src.exists():
        tools_fw = FRAMEWORKS / "tools"
        tools_fw.mkdir(parents=True, exist_ok=True)
        shutil.copy2(unar_src, tools_fw / "unar")
        os.chmod(tools_fw / "unar", 0o755)
        tools_app = APP_CODE / "tools"
        tools_app.mkdir(parents=True, exist_ok=True)
        shutil.copy2(unar_src, tools_app / "unar")
        os.chmod(tools_app / "unar", 0o755)
        tools_res = RESOURCES / "tools"
        tools_res.mkdir(parents=True, exist_ok=True)
        try:
            # Relative to Contents/Resources/tools/, so it must climb out of
            # Resources before entering Frameworks. With one ".." the link
            # points at Contents/Resources/Frameworks/tools/unar, which does not
            # exist: it dangles, and codesign --strict then rejects the bundle.
            (tools_res / "unar").symlink_to(Path("../../Frameworks/tools/unar"))
        except Exception:
            shutil.copy2(unar_src, tools_res / "unar")

    # 5c. Bare adb, so the Rockbox themes/fonts stage can do the transfer over
    # ADB itself instead of sending the user to pick a drive folder. Both Mac
    # architectures ship in the universal bundle and ``adb.find_adb_binary``
    # looks under ``tools/adb/<platform>/adb`` (see ``candidate_adb_paths``).
    for plat in ("macos-arm64", "macos-x64"):
        adb_src = ROOT / "tools" / "adb" / plat / "adb"
        if not adb_src.is_file():
            continue
        for base in (RESOURCES / "tools", APP_CODE / "tools", FRAMEWORKS / "tools"):
            dest_dir = base / "adb" / plat
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / "adb"
            shutil.copy2(adb_src, dest)
            os.chmod(dest, 0o755)

    # 6. Icon
    icon_name = "icon_generic_mtk.icns" if IS_MEDIATEK_INSTALLER_BUILD else "icon.icns"
    icon_icns = ASSETS / icon_name
    if not icon_icns.exists():
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
        "CFBundleShortVersionString": APP_VERSION,
        "CFBundleVersion": APP_VERSION,
        "CFBundleGetInfoString": f"{APP_DISPLAY_NAME} by Ryan Specter",
        "LSApplicationCategoryType": "public.app-category.utilities",
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,
        # macOS 26+ applies the Liquid Glass control design to apps built with
        # the new SDK. Qt's Aqua style then draws combo boxes as white fields
        # with a blue chevron capsule instead of the native pop-up buttons, and
        # the window follows the new (grey) materials. Qt documents this key as
        # the official opt-out: it keeps the pre-26 look and metrics, which is
        # what the 2.5.1 builds (built with an older SDK) rendered.
        "UIDesignRequiresCompatibility": True,
        "NSPrincipalClass": "NSApplication",
        "NSHumanReadableCopyright": "Copyright © 2024-2026 Ryan Specter. All rights reserved.",
        "CFBundleDocumentTypes": [
            {
                "CFBundleTypeName": "Firmware Package",
                "CFBundleTypeRole": "Viewer",
                "LSHandlerRank": "Alternate",
                "LSItemContentTypes": [
                    "com.pkware.zip-archive",
                    "public.zip-archive",
                    "org.rarlab.rar-archive",
                    # Dock drops of a firmware folder or a scatter file.
                    "public.folder",
                    "public.plain-text",
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
        sign_ad_hoc_tree(APP_DIR, entitlements)

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
