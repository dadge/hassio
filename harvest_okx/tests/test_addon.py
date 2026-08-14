#!/usr/bin/env python3
"""Static checks on the harvester add-on manifest, run.sh and panel.

These catch the class of mistake that only shows up as a Supervisor error on
the Pi -- an option present in `options` but missing from `schema`, a
translation for a field that no longer exists, a runtime config that forgets to
pass something the bot reads. Cheap to check here, tedious to debug there.

Run:  python tests/test_harvest_addon.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ADDON = Path(__file__).resolve().parent.parent
REPO = ADDON.parent

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if not ok else ""))
    if not ok:
        failures.append(name)


def load_yaml(path: Path) -> dict:
    try:
        import yaml
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except ImportError:
        print("  (PyYAML missing -- skipping YAML-parsed checks)")
        return {}


def main() -> int:
    cfg = load_yaml(ADDON / "config.yaml")
    if not cfg:
        return 0

    print("\n[manifest]")
    check("slug matches directory", cfg.get("slug") == "harvest_okx", str(cfg.get("slug")))
    check("ingress enabled", cfg.get("ingress") is True)
    # The add-on linter rejects ingress_port when it merely restates the
    # default, so the manifest omits it -- but the port the bot binds must
    # still match whatever the Supervisor ends up using.
    effective_port = cfg.get("ingress_port", 8099)
    bot_src = (ADDON / "rootfs" / "opt" / "harvest" / "bot.py").read_text(encoding="utf-8")
    check("the bot binds the effective ingress port",
          f'("0.0.0.0", {effective_port})' in bot_src, f"expected port {effective_port}")
    sibling = REPO / "freqtrade_okx" / "config.yaml"
    if sibling.exists():
        # Two add-ons sharing a slug would collide in the store; only worth
        # asserting when the sibling is actually checked out beside this one.
        check("slug differs from the freqtrade add-on",
              cfg.get("slug") != load_yaml(sibling).get("slug"))
    check("defaults to dry-run", cfg["options"]["mode"] == "dry-run")
    check("live confirmation defaults false",
          cfg["options"]["i_understand_live_trading"] is False)

    print("\n[options vs schema]")
    opts, schema = set(cfg["options"]), set(cfg["schema"])
    check("every option has a schema entry", opts <= schema, str(opts - schema))
    check("every schema entry has a default", schema <= opts, str(schema - opts))

    print("\n[translations]")
    tr = load_yaml(ADDON / "translations" / "en.yaml")
    if tr:
        described = set(tr.get("configuration", {}))
        check("every option is documented", opts <= described, str(opts - described))
        check("no translation for a removed option", described <= opts, str(described - opts))

    print("\n[run.sh]")
    run_sh = (ADDON / "rootfs" / "run.sh").read_text(encoding="utf-8")
    check("sets strict mode", "set -Eeuo pipefail" in run_sh)
    check("forces dry-run after an update", 'MODE="dry-run"' in run_sh and "last_version" in run_sh)
    check("requires the live confirmation", 'I_UNDERSTAND" == "true"' in run_sh)
    check("requires credentials for live", "requires okx_api_key" in run_sh)
    check("runtime config is not world-readable", 'chmod 600 "$RUNTIME_FILE"' in run_sh)
    check("execs the bot", "exec python3 /opt/harvest/bot.py" in run_sh)
    # The runtime file is the only channel between run.sh and the bot, so every
    # key the bot reads must be written here.
    bot_py = (ADDON / "rootfs" / "opt" / "harvest" / "bot.py").read_text(encoding="utf-8")
    read_keys = set(re.findall(r'self\.cfg\["(\w+)"\]', bot_py)) | \
                set(re.findall(r'cfg\["(\w+)"\]', bot_py))
    runtime_block = run_sh.split("jq '{", 1)[1].split("}'", 1)[0]
    missing = {k for k in read_keys if k not in runtime_block and k != "mode"}
    check("runtime config passes every key the bot reads", not missing, str(missing))

    print("\n[panel]")
    panel = (ADDON / "rootfs" / "opt" / "ha-panel" / "index.html").read_text(encoding="utf-8")
    check("API calls are relative (ingress-safe)",
          'fetch("/api' not in panel and "fetch('/api" not in panel)
    check("live mode is visually distinct", ".banner.live" in panel)
    check("explains how to switch to live", "i_understand_live_trading" in panel)

    print("\n[images]")
    # PNG dimensions come straight out of the IHDR chunk: 8-byte signature,
    # 4-byte length, 4-byte type, then width and height as big-endian uint32.
    # Cheaper than making the whole test suite depend on Pillow.
    for name in ("icon.png", "logo.png"):
        f = ADDON / name
        if not f.exists():
            check(f"{name} exists", False, "missing")
            continue
        raw = f.read_bytes()
        sig_ok = raw[:8] == b"\x89PNG\r\n\x1a\n"
        w = int.from_bytes(raw[16:20], "big")
        h = int.from_bytes(raw[20:24], "big")
        check(f"{name} is a valid PNG", sig_ok and raw[12:16] == b"IHDR")
        check(f"{name} is square", w == h and w > 0, f"{w}x{h}")
        check(f"{name} matches the 256px convention", (w, h) == (256, 256), f"{w}x{h}")
        check(f"{name} is a sane size", 1_000 < len(raw) < 500_000, f"{len(raw)} bytes")

    print("\n[docker]")
    dockerfile = (ADDON / "Dockerfile").read_text(encoding="utf-8")
    check("ccxt pinned", re.search(r'ccxt==\d', dockerfile) is not None)
    check("ADDON_VERSION exported for the update guard", "ADDON_VERSION=${BUILD_VERSION}" in dockerfile)

    print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
