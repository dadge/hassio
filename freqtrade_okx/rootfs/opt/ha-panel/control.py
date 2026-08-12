#!/usr/bin/env python3
"""Control endpoint for the add-on's ingress panel.

Why this exists: Freqtrade's own backtesting/download API is mounted with a
``is_webserver_mode`` dependency, so the bot's REST API cannot serve it while
it is running in trade mode. Rather than run a second Freqtrade process just
to expose those endpoints, this tiny server runs the bundled ``ft-*`` helpers
as background jobs and reports their progress and results.

Security posture:
  * binds to 127.0.0.1 only — nginx re-exposes it under the ingress path,
    which Home Assistant authenticates;
  * never invokes a shell, and every request field is validated against a
    whitelist before it reaches the argument list;
  * runs at most one job at a time.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LISTEN = ("127.0.0.1", 8125)
LOG_FILE = "/data/.addon/job.log"
BACKTEST_RESULTS = "/data/user_data/backtest_results"
LAST_RESULT = ".last_result.json"
LOG_TAIL_LINES = 80
MAX_LOG_BYTES = 64 * 1024

JOBS = {"download": "ft-download-data", "backtest": "ft-backtest"}
CURRENCIES = ("USDT", "USDC")
TIMERANGE_RE = re.compile(r"^\d{8}-(\d{8})?$")

_lock = threading.Lock()
_job: dict = {"name": None, "proc": None, "started": None, "finished": None, "returncode": None}


# ------------------------------------------------------------------ helpers --
def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _running() -> bool:
    proc = _job["proc"]
    return proc is not None and proc.poll() is None


def _reap() -> None:
    """Record the exit code once a finished process is noticed."""
    proc = _job["proc"]
    if proc is not None and proc.poll() is not None and _job["returncode"] is None:
        _job["returncode"] = proc.returncode
        _job["finished"] = _now()


def _log_tail() -> list[str]:
    try:
        with open(LOG_FILE, "rb") as fp:
            fp.seek(0, os.SEEK_END)
            size = fp.tell()
            fp.seek(max(0, size - MAX_LOG_BYTES))
            data = fp.read().decode("utf-8", "replace")
    except OSError:
        return []
    return data.splitlines()[-LOG_TAIL_LINES:]


def _last_backtest() -> dict | None:
    """Summarise the most recent backtest export, if any.

    Only keys Freqtrade actually emits are reported; anything missing is left
    out rather than guessed, so a format change shows up as a blank field
    instead of a wrong number.
    """
    try:
        with open(os.path.join(BACKTEST_RESULTS, LAST_RESULT)) as fp:
            latest = json.load(fp).get("latest_backtest")
        if not latest:
            return None
        path = os.path.join(BACKTEST_RESULTS, latest)
        with open(path) as fp:
            data = json.load(fp)
    except (OSError, ValueError):
        return None

    strategies = data.get("strategy") or {}
    if not strategies:
        return None
    name, stats = next(iter(strategies.items()))
    wanted = (
        "total_trades", "wins", "draws", "losses", "winrate", "profit_factor",
        "profit_total", "profit_total_abs", "max_drawdown_account",
        "backtest_days", "trades_per_day", "stake_currency",
        "backtest_start", "backtest_end", "timeframe",
    )
    summary = {k: stats[k] for k in wanted if k in stats}
    summary["strategy"] = name
    summary["file"] = latest
    exits = stats.get("exit_reason_summary")
    if isinstance(exits, list):
        summary["exit_reasons"] = [
            {"reason": e.get("exit_reason"), "trades": e.get("trades"),
             "profit_total": e.get("profit_total")}
            for e in exits
        ]
    return summary


def _build_argv(payload: dict) -> list[str]:
    """Validate the request and turn it into an argument list.

    Everything is whitelisted: an unknown job, a non-integer day count or an
    unexpected currency is rejected before any process is created.
    """
    job = payload.get("job")
    if job not in JOBS:
        raise ValueError("unknown job %r (expected one of %s)" % (job, ", ".join(JOBS)))
    argv = [JOBS[job]]

    if job == "download":
        days = payload.get("days", 240)
        if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 1000:
            raise ValueError("days must be an integer between 1 and 1000")
        argv.append(str(days))
    else:
        timerange = payload.get("timerange") or ""
        if timerange:
            if not TIMERANGE_RE.match(timerange):
                raise ValueError("timerange must look like 20260101-20260701 or 20260101-")
            argv.append(timerange)

    currency = payload.get("stake_currency")
    if currency:
        if currency not in CURRENCIES:
            raise ValueError("stake_currency must be one of %s" % ", ".join(CURRENCIES))
        argv += ["--stake-currency", currency]
    return argv


def _start(payload: dict) -> dict:
    argv = _build_argv(payload)
    with _lock:
        _reap()
        if _running():
            raise RuntimeError("a %s job is already running" % _job["name"])
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "w") as log:
            log.write("$ %s\n" % " ".join(argv))
            log.flush()
            proc = subprocess.Popen(  # noqa: S603 - argv is whitelisted above
                argv,
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        _job.update({"name": payload["job"], "proc": proc, "started": _now(),
                     "finished": None, "returncode": None})
    return {"ok": True, "job": payload["job"], "argv": argv}


def _abort() -> dict:
    with _lock:
        _reap()
        if not _running():
            return {"ok": False, "error": "no job is running"}
        # The helpers spawn freqtrade as a child; signal the whole group.
        os.killpg(os.getpgid(_job["proc"].pid), signal.SIGTERM)
    return {"ok": True}


def _status() -> dict:
    with _lock:
        _reap()
        return {
            "job": _job["name"],
            "running": _running(),
            "started": _job["started"],
            "finished": _job["finished"],
            "returncode": _job["returncode"],
            "log": _log_tail(),
            "result": _last_backtest(),
        }


# ------------------------------------------------------------------- server --
class Handler(BaseHTTPRequestHandler):
    server_version = "freqtrade-addon-control"

    def _send(self, code: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802 - required by BaseHTTPRequestHandler
        if self.path.rstrip("/") in ("/status", ""):
            self._send(200, _status())
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}") if length else {}
            if not isinstance(payload, dict):
                raise ValueError("expected a JSON object")
        except ValueError as exc:
            self._send(400, {"error": "invalid request body: %s" % exc})
            return

        path = self.path.rstrip("/")
        try:
            if path == "/run":
                self._send(202, _start(payload))
            elif path == "/abort":
                self._send(200, _abort())
            else:
                self._send(404, {"error": "not found"})
        except ValueError as exc:
            self._send(400, {"error": str(exc)})
        except RuntimeError as exc:
            self._send(409, {"error": str(exc)})
        except OSError as exc:
            self._send(500, {"error": "could not start the job: %s" % exc})

    def log_message(self, fmt, *args):  # keep the add-on log readable
        pass


def main() -> None:
    ThreadingHTTPServer(LISTEN, Handler).serve_forever()


if __name__ == "__main__":
    main()
