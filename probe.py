import re
import ast
import sys
from pathlib import Path

REPO = Path(".")
files = {
    "fs": REPO / "src/flash_service.py",
    "catalog": REPO / "src/catalog.py",
    "select": REPO / "src/ui/select_page.py",
    "icon": REPO / "src/ui/release_icon.py",
    "icons": REPO / "src/release_icons.py",
    "smoke": REPO / "tests/smoke_test.py",
}

texts = {}
parseable = {}
for name, path in files.items():
    if not path.exists():
        texts[name] = ""
        parseable[name] = False
        continue
    raw = path.read_bytes()
    if not raw.endswith(b"\n"):
        path.write_bytes(raw + b"\n")
    try:
        texts[name] = path.read_text(encoding="utf-8")
        ast.parse(texts[name])
        parseable[name] = True
    except Exception as e:
        texts[name] = ""
        parseable[name] = False

patterns = [
    ("flash: MTK_OP_start", "fs", "MTK_OP 패키지의 파일을 불러오기 전에"),
    ("flash: 정품", "fs", "정품"),
    ("flash: 인증", "fs", "인증"),
    ("flash: msg warn msg=[", "fs", 'warn("msg=['),
    ("flash: dark 위로", "fs", "dark 위로"),
    ("flash: extract_zip header", "fs", "def _extract_zip(self, package_path, extract_dir)"),
    ("flash: rar footer 99", "fs", "self.progress.emit(99)\n\n    def _extract_archive"),
    ("flash: on_local_package_ready", "fs", "def on_local_package_ready"),
    ("flash: progress emit 100", "fs", "progress.emit(100)"),
    ("flash: discard_firmware_history after emit", "fs", "discard_firmware_history(extract_dir)"),
    ("catalog: Y2 separate codebase doc", "catalog", "Y2 Rockbox repo is a separate codebase"),
    ("catalog: treated as unrestricted", "catalog", "treated as unrestricted"),
    ("catalog: non-Y1 exemption sentence", "catalog", "Y2, A5, and non-Y1 models are completely exempt from the < 0.5 restriction"),
    ("select: is_rockbox_y2_browsing def", "select", "def is_rockbox_y2_browsing("),
    ("select: Y2 short-circuit in filters", "select", "if self.is_rockbox_y2_browsing():"),
    ("select: release_listing_filters", "select", "def release_listing_filters"),
    ("select: is_jailed_package_candidate", "select", "def is_jailed_package_candidate("),
    ("icon: SoftwareIconJob class", "icon", "class SoftwareIconJob"),
    ("icon: software_icon_urls acc", "icon", "software_icon_urls("),
    ("icon: download_glyph acc", "icon", "download_glyph"),
    ("icon: settings_placeholder path", "icon", "placeholder_settings_pixmap"),
    ("icons: icon_candidate_urls acc", "icons", "def icon_candidate_urls"),
    ("icons: release_asset_urls acc", "icons", "def release_asset_urls"),
    ("icons: software_icon_urls acc", "icons", "def software_icon_urls"),
    ("smoke: Y1 positive gate", "smoke", 'assert release_version_ok("rockbox-y1/rockbox", tag, model="Y1")'),
    ("smoke: Y1 negative gate", "smoke", 'assert not release_version_ok("rockbox-y1/rockbox", tag, model="Y1")'),
    ("smoke: Y2 positive gate", "smoke", 'assert release_version_ok("rockbox-y1/rockbox", "stable-v0.4.9", model="Y2")'),
    ("smoke: Y2 oldest-tag gate", "smoke", 'assert release_version_ok("rockbox-y1/rockbox", "stable-v0.1.0", model="Y2")'),
    ("smoke: show_old_rockbox path", "smoke", "show_old_rockbox"),
    ("smoke: untagged nightly gate", "smoke", "nightly-35e926bd6ea663ab0aac2f138c7f71ae744971f"),
    ("catalog parseable", "catalog", None),
    ("select parseable", "select", None),
    ("release_icons parseable", "icons", None),
]

out = []
any_missing = False
for label, src, pat in patterns:
    if pat is None:
        ok = parseable.get(src, False)
        out.append("{}: {}".format(label, "yes" if ok else "NO"))
        if not ok:
            any_missing = True
        continue
    t = texts.get(src, "")
    m = pat in t
    if not m:
        any_missing = True
    out.append("{}: found={}".format(label, bool(m)))
    if m:
        s = max(0, t.find(pat) - 60)
        e = min(len(t), t.find(pat) + len(pat) + 260)
        out.append("  ctx: " + repr(t[s:e]))
        out.append("  ---")
    else:
        out.append("  MISSING")

Path("probe_out.txt").write_text("\n".join(out) + "\n", encoding="utf-8")
if any_missing:
    raise SystemExit(1)
raise SystemExit(0)
