from __future__ import annotations

import argparse
import asyncio
import json
import sys
import os
import subprocess
import time
from pathlib import Path

import aiohttp

from .config import Config, duration_seconds
from .exchange import TestnetClient
from .journal import write_json
from .runner import execute

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = (
    _PACKAGE_ROOT.parent.parent
    if _PACKAGE_ROOT.parent.name == "src" and (_PACKAGE_ROOT.parent.parent / "pyproject.toml").is_file()
    else _PACKAGE_ROOT.parent
)
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "liquidity_mm_testnet.yaml"


async def check(config: Config, state_dir: Path) -> dict:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        client = TestnetClient(session)
        result, _, _ = await client.preflight(config)
        private = await client.connect_private()
        await private.close()
        result["private_websocket_authenticated"] = True
        write_json(state_dir / "preflight.json", result)
        return result


def start(config_path: Path, config: Config, duration: float, mode: str, state_dir: Path) -> dict:
    """Explicit bounded launch; no scheduler or startup task is installed."""
    state_dir.mkdir(parents=True, exist_ok=True)
    log_path = state_dir / ("launch-" + str(int(time.time())) + ".log")
    command = [sys.executable, "-m", "vibetrading.liquidity_mm", "--config", str(config_path.resolve()),
               "run", "--mode", mode, "--duration", str(duration) + "s"]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with log_path.open("w", encoding="utf-8") as log:
        child = subprocess.Popen(command, cwd=PROJECT_ROOT, stdin=subprocess.DEVNULL, stdout=log,
                                 stderr=subprocess.STDOUT, creationflags=flags, start_new_session=os.name != "nt")
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise RuntimeError(f"Strategy process exited during startup; inspect {log_path}")
        status_path = state_dir / "status.json"
        if status_path.exists():
            try:
                status = json.loads(status_path.read_text(encoding="utf-8"))
                if status.get("pid") == child.pid and status.get("running"):
                    return {"started": True, "pid": child.pid, "run_id": status["run_id"], "mode": mode,
                            "duration_seconds": duration, "log": str(log_path), "status": str(status_path)}
            except (OSError, ValueError):
                pass
        time.sleep(.25)
    return {"started": True, "initializing": True, "pid": child.pid, "mode": mode,
            "duration_seconds": duration, "log": str(log_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Residual-flow market making: Bybit testnet only")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="Read-only credentials, units, fees, balance, WebSocket preflight")
    run = sub.add_parser("run", help="Run only for the explicitly supplied duration")
    run.add_argument("--duration", required=True, type=duration_seconds)
    run.add_argument("--mode", choices=("shadow", "testnet"), default="shadow")
    background = sub.add_parser("start", help="Start an explicitly bounded background process")
    background.add_argument("--duration", required=True, type=duration_seconds)
    background.add_argument("--mode", choices=("shadow", "testnet"), default="testnet")
    sub.add_parser("status")
    sub.add_parser("stop", help="Request cancellation, reduction and bounded cleanup")
    replay = sub.add_parser("replay", help="Replay recorded receive-time events conservatively")
    replay.add_argument("journal", type=Path)
    replay.add_argument("--latency", type=float, choices=(5, 10, 20), default=5)
    replay.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        config = Config.load(args.config)
        state_dir = Path(config.state_dir)
        if not state_dir.is_absolute():
            state_dir = PROJECT_ROOT / state_dir
        if args.command == "check":
            result = asyncio.run(check(config, state_dir))
        elif args.command == "run":
            result = asyncio.run(execute(config, args.mode, args.duration, PROJECT_ROOT))
        elif args.command == "start":
            result = start(args.config, config, args.duration, args.mode, state_dir)
        elif args.command == "stop":
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / "STOP").write_text("stop requested\n", encoding="utf-8")
            result = {"stop_requested": True, "state_dir": str(state_dir)}
        elif args.command == "replay":
            from .replay import replay_journal
            result = replay_journal(args.journal, config, args.latency)
            if args.output:
                write_json(args.output, result)
        else:
            path = state_dir / "status.json"
            result = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"state": "NOT_STARTED", "running": False}
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        if (args.command == "check" and not result.get("ready")) or (args.command == "run" and result.get("state") == "HALT"):
            sys.exit(2)
    except Exception as exc:
        print(json.dumps({"error": str(exc) if isinstance(exc, (RuntimeError, ValueError, FileNotFoundError)) else type(exc).__name__,
                          "environment": "bybit-testnet"}, ensure_ascii=False), file=sys.stderr)
        sys.exit(2)
