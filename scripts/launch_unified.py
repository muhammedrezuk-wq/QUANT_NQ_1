"""Legacy/internal combined launcher; not the official launch contract.

The official contract is the pair of independent market buttons backed by
``launch_market.py``.  This diagnostic launcher is retained for compatibility.
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.15)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def prepare() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "prepare_unified.py")],
        cwd=ROOT,
    )
    if result.returncode:
        raise SystemExit("Unified links could not be prepared")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "verify_unified.py")],
        cwd=ROOT,
    )
    if result.returncode:
        raise SystemExit("Unified release verification failed")


def network_preflight(start_forex: bool, start_crypto: bool) -> None:
    markets = []
    if start_forex:
        markets.append("forex")
    if start_crypto:
        markets.append("crypto")
    for market in markets:
        result = subprocess.run(
            [sys.executable, str(ROOT / "governance" / "network_preflight.py"), "--market", market],
            cwd=ROOT,
        )
        if result.returncode:
            raise SystemExit(f"Network preflight failed for {market}")


def spawn(label: str, command: list[str], port: int, *, extra_env: dict[str, str] | None = None) -> subprocess.Popen | None:
    if listening(port):
        print(f"{label}: already running on {port}")
        return None
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    if extra_env:
        env.update(extra_env)
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        creationflags=flags,
        close_fds=(os.name != "nt"),
    )
    print(f"{label}: started (pid={process.pid}, port={port})")
    return process


def wait_port(port: int, timeout_s: float = 12.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if listening(port):
            return True
        time.sleep(0.15)
    return listening(port)


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the unified QUANT_NQ stack")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--crypto-only", action="store_true")
    parser.add_argument("--forex-only", action="store_true")
    args = parser.parse_args()
    if args.crypto_only and args.forex_only:
        parser.error("--crypto-only and --forex-only cannot be combined")

    start_forex = not args.crypto_only
    start_crypto = not args.forex_only
    network_preflight(start_forex, start_crypto)
    prepare()
    python = sys.executable
    processes: list[subprocess.Popen] = []
    if start_forex:
        for label, script, port, extra in (
            ("Forex Core", "run_forex.py", 8010, {}),
            ("Forex Governance Backend", "run_governance.py", 8092, {"QUANT_GOV_MARKET": "forex"}),
        ):
            command = [python, str(ROOT / "scripts" / script)]
            if "Governance" in label:
                command += ["--market", "forex", "--port", "8092"]
            process = spawn(label, command, port, extra_env=extra)
            if process is not None:
                processes.append(process)
    if start_crypto:
        for label, script, port, extra in (
            ("Crypto Core", "run_crypto.py", 8020, {}),
            ("Crypto Governance Backend", "run_governance.py", 8093, {"QUANT_GOV_MARKET": "crypto"}),
        ):
            command = [python, str(ROOT / "scripts" / script)]
            if "Governance" in label:
                command += ["--market", "crypto", "--port", "8093"]
            process = spawn(label, command, port, extra_env=extra)
            if process is not None:
                processes.append(process)

    # One visible origin: the hub owns 8090. Internal governance backends are
    # localhost-only implementation details and are never exposed in the UI.
    hub_market = "crypto" if args.crypto_only else "forex"
    hub = spawn("Unified Dashboard Hub", [python, str(ROOT / "governance" / "unified_hub.py")], 8090,
                extra_env={"QUANT_HUB_DEFAULT_MARKET": hub_market})
    if hub is not None:
        processes.append(hub)

    # 610 — تلغرام: منصّة المالك المتنقلة. نسخة واحدة (قفل 8098)، والتوكن من
    # الخزنة المشفّرة حصراً (runtime/secrets.enc). بلا توكن يُطبع الخطوات
    # ويخرج بهدوء — بقيّة الستاك ما تتأثر.
    tg = spawn("Telegram 610 (owner mobile)",
               [python, str(ROOT / "governance" / "telegram.py")], 8098)
    if tg is not None:
        processes.append(tg)

    wanted = ([8010, 8092] if start_forex else []) + ([8020, 8093] if start_crypto else []) + [8090, 8098]
    ready = {port: wait_port(port) for port in wanted}
    for port, ok in ready.items():
        print(f"port {port}: {'READY' if ok else 'NOT READY'}")

    if not args.no_browser and listening(8090):
        webbrowser.open("http://127.0.0.1:8090")
    print("Unified dashboard: http://127.0.0.1:8090")
    print("Telegram 610     : owner mobile — lock 8098 "
          "(token: governance/scripts/secrets_admin.py set telegram_bot_token)")
    print("Internal backends: Forex 8092 / Crypto 8093")
    print("Switch button   : فوركس ⇄ كريبتو")
    # Child processes have their own console on Windows. On POSIX keep no
    # artificial foreground wait: this command is also safe for automation.
    return 0 if all(ready.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
