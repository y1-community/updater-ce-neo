#!/usr/bin/env python3
"""build_universal_app.py — Build a standalone macOS 13+ Universal 2 (Intel + Apple Silicon) .app bundle.

Can be run on both macOS and Linux (via LLVM/clang/rcodesign toolchain).
"""

import os
import sys
import shutil
import plistlib
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
APP_DIR = DIST / "Innioasis Updater CE.app"
CONTENTS = APP_DIR / "Contents"
MACOS = CONTENTS / "MacOS"
RESOURCES = CONTENTS / "Resources"
FRAMEWORKS = CONTENTS / "Frameworks"
APP_CODE = RESOURCES / "app"
ASSETS = ROOT / "assets"
DARWIN_LIBUSB = ROOT / "vendor" / "mtkclient" / "mtkclient" / "Darwin" / "libusb-1.0.dylib"


LAUNCHER_C = r'''
extern int _NSGetExecutablePath(char* buf, unsigned int* bufsize);
extern int access(const char *path, int mode);
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
    if (!last_slash) {
        exit(1);
    }
    *last_slash = '\0';

    last_slash = strrchr(exe_path, '/');
    if (!last_slash) {
        exit(1);
    }
    *last_slash = '\0';
    char contents_dir[1024];
    strncpy(contents_dir, exe_path, sizeof(contents_dir));

    char frameworks_dir[1024];
    snprintf(frameworks_dir, sizeof(frameworks_dir), "%s/Frameworks", contents_dir);

    char resources_dir[1024];
    snprintf(resources_dir, sizeof(resources_dir), "%s/Resources", contents_dir);

    char app_dir[1024];
    snprintf(app_dir, sizeof(app_dir), "%s/app", resources_dir);

    char launcher_script[1024];
    snprintf(launcher_script, sizeof(launcher_script), "%s/launcher.py", app_dir);

    char *old_dyld = getenv("DYLD_FRAMEWORK_PATH");
    char new_dyld[2048];
    if (old_dyld && strlen(old_dyld) > 0) {
        snprintf(new_dyld, sizeof(new_dyld), "%s:%s", frameworks_dir, old_dyld);
    } else {
        snprintf(new_dyld, sizeof(new_dyld), "%s", frameworks_dir);
    }
    setenv("DYLD_FRAMEWORK_PATH", new_dyld, 1);
    setenv("DYLD_LIBRARY_PATH", new_dyld, 1);
    setenv("DYLD_FALLBACK_LIBRARY_PATH", new_dyld, 1);

    char *old_pypath = getenv("PYTHONPATH");
    char new_pypath[4096];
    if (old_pypath && strlen(old_pypath) > 0) {
        snprintf(new_pypath, sizeof(new_pypath), "%s:%s/vendor/mtkclient:%s:%s",
                 app_dir, app_dir, frameworks_dir, old_pypath);
    } else {
        snprintf(new_pypath, sizeof(new_pypath), "%s:%s/vendor/mtkclient:%s",
                 app_dir, app_dir, frameworks_dir);
    }
    setenv("PYTHONPATH", new_pypath, 1);
    setenv("INNIOASIS_APP_ROOT", contents_dir, 1);

    char python_bin[1024] = {0};
    char candidate[1024];
    snprintf(candidate, sizeof(candidate), "%s/Python.framework/Versions/Current/bin/python3", frameworks_dir);
    if (access(candidate, X_OK) == 0) {
        strncpy(python_bin, candidate, sizeof(python_bin));
    } else {
        snprintf(candidate, sizeof(candidate), "%s/python/bin/python3", frameworks_dir);
        if (access(candidate, X_OK) == 0) {
            strncpy(python_bin, candidate, sizeof(python_bin));
        }
    }

    if (python_bin[0] == '\0') {
        const char *search_paths[] = {
            "/opt/homebrew/bin/python3",
            "/usr/local/bin/python3",
            "/usr/bin/python3",
            "/Library/Frameworks/Python.framework/Versions/3.11/bin/python3",
            "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3",
            "/Library/Frameworks/Python.framework/Versions/3.10/bin/python3",
            0
        };
        for (int i = 0; search_paths[i] != 0; i++) {
            if (access(search_paths[i], X_OK) == 0) {
                strncpy(python_bin, search_paths[i], sizeof(python_bin));
                break;
            }
        }
    }

    if (python_bin[0] == '\0') {
        strncpy(python_bin, "/usr/bin/env", sizeof(python_bin));
    }

    char *new_argv[128];
    int arg_idx = 0;
    if (strcmp(python_bin, "/usr/bin/env") == 0) {
        new_argv[arg_idx++] = "/usr/bin/env";
        new_argv[arg_idx++] = "python3";
    } else {
        new_argv[arg_idx++] = python_bin;
    }
    new_argv[arg_idx++] = launcher_script;
    for (int i = 1; i < argc && arg_idx < 126; i++) {
        new_argv[arg_idx++] = argv[i];
    }
    new_argv[arg_idx] = 0;

    execve(new_argv[0], new_argv, envp);
    puts("Error: Failed to launch Innioasis Updater.");
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
    symbols: [ _puts, _exit, _execve, _getenv, _setenv, _access, _strlen, _strcpy, _strncpy, _strcat, _strrchr, _strcmp, _snprintf, _system, __NSGetExecutablePath, ___stack_chk_fail, ___stack_chk_guard, _memcpy, _memset, _bzero ]
...
'''


def compile_universal_binary(out_path: Path):
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        c_file = tdp / "launcher.c"
        c_file.write_text(LAUNCHER_C)
        tbd_file = tdp / "libSystem.tbd"
        tbd_file.write_text(LIB_SYSTEM_TBD)

        x86_obj = tdp / "x86.o"
        arm_obj = tdp / "arm.o"
        x86_bin = tdp / "x86_bin"
        arm_bin = tdp / "arm_bin"

        # 1. Compile objects for x86_64 and arm64
        subprocess.run([
            "clang", "-target", "x86_64-apple-macos13", "-fno-stack-protector",
            "-c", str(c_file), "-o", str(x86_obj)
        ], check=True)
        subprocess.run([
            "clang", "-target", "arm64-apple-macos13", "-fno-stack-protector",
            "-c", str(c_file), "-o", str(arm_obj)
        ], check=True)

        # 2. Link each arch against libSystem stub
        linker = "ld64.lld" if shutil.which("ld64.lld") else "lld"
        subprocess.run([
            linker, "-arch", "x86_64", "-platform_version", "macos", "13.0.0", "13.0.0",
            str(x86_obj), str(tbd_file), "-o", str(x86_bin)
        ], check=True)
        subprocess.run([
            linker, "-arch", "arm64", "-platform_version", "macos", "13.0.0", "13.0.0",
            str(arm_obj), str(tbd_file), "-o", str(arm_bin)
        ], check=True)

        # 3. Lipo into Universal 2 binary
        lipo = "llvm-lipo" if shutil.which("llvm-lipo") else "lipo"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([
            lipo, "-create", str(x86_bin), str(arm_bin), "-output", str(out_path)
        ], check=True)
        os.chmod(out_path, 0o755)


def build():
    print(f"=== Building Universal 2 macOS .app bundle at {APP_DIR} ===")
    if APP_DIR.exists():
        shutil.rmtree(APP_DIR)

    MACOS.mkdir(parents=True, exist_ok=True)
    RESOURCES.mkdir(parents=True, exist_ok=True)
    FRAMEWORKS.mkdir(parents=True, exist_ok=True)
    APP_CODE.mkdir(parents=True, exist_ok=True)

    # 1. Universal 2 executable
    exe_target = MACOS / "Innioasis Updater CE"
    compile_universal_binary(exe_target)

    # 2. Universal 2 libusb
    if DARWIN_LIBUSB.exists():
        shutil.copy2(DARWIN_LIBUSB, FRAMEWORKS / "libusb-1.0.dylib")
        shutil.copy2(DARWIN_LIBUSB, MACOS / "libusb-1.0.dylib")

    # 3. Python application code & assets
    shutil.copy2(ROOT / "launcher.py", APP_CODE / "launcher.py")
    shutil.copytree(ROOT / "src", APP_CODE / "src", dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(ROOT / "vendor" / "mtkclient", APP_CODE / "vendor" / "mtkclient", dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(ASSETS, APP_CODE / "assets", dirs_exist_ok=True)

    # 4. Icon
    icon_icns = ASSETS / "icon.icns"
    if icon_icns.exists():
        shutil.copy2(icon_icns, RESOURCES / "icon.icns")

    # 5. PkgInfo
    (CONTENTS / "PkgInfo").write_bytes(b"APPL????")

    # 6. Info.plist
    info_plist = {
        "CFBundleDisplayName": "Innioasis Updater CE",
        "CFBundleExecutable": "Innioasis Updater CE",
        "CFBundleIconFile": "icon.icns",
        "CFBundleIdentifier": "com.innioasis.updater",
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": "Innioasis Updater CE",
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

    # 7. Codesign
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
    print(f"\n=== Build Complete: {APP_DIR} ===")


if __name__ == "__main__":
    build()
