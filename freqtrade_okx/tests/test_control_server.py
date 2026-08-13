"""Tests for the ingress panel's control endpoint (rootfs/opt/ha-panel/control.py).

The server shells out to the bundled ft-* helpers, so these tests point it at
fake helpers in a temporary directory and drive it over real HTTP. They cover
the parts that matter: input is whitelisted before a process is created, only
one job runs at a time, and a finished backtest is summarised from Freqtrade's
export format.

    python3 freqtrade_okx/tests/test_control_server.py
    python3 -m pytest freqtrade_okx/tests/test_control_server.py -q
"""

import importlib.util
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

# The server spawns POSIX helper scripts and signals process groups, neither of
# which exists on Windows. Those tests are skipped there (visibly, not silently)
# and run in full in CI, which is Linux.
POSIX = os.name == "posix"


class Skip(Exception):
    """Raised to report a test as skipped rather than passed."""


HERE = Path(__file__).resolve().parent
CONTROL_PY = HERE.parent / "rootfs" / "opt" / "ha-panel" / "control.py"

spec = importlib.util.spec_from_file_location("control", CONTROL_PY)
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)

TMP = Path(tempfile.mkdtemp(prefix="ft-control-"))
BIN = TMP / "bin"
BIN.mkdir()
control.LOG_FILE = str(TMP / "job.log")
control.BACKTEST_RESULTS = str(TMP / "backtest_results")
os.makedirs(control.BACKTEST_RESULTS, exist_ok=True)
os.environ["PATH"] = "%s%s%s" % (BIN, os.pathsep, os.environ["PATH"])

_server = ThreadingHTTPServer(("127.0.0.1", 0), control.Handler)
PORT = _server.server_address[1]
threading.Thread(target=_server.serve_forever, daemon=True).start()


def fake_helper(name: str, body: str) -> None:
    path = BIN / name
    path.write_text("#!/usr/bin/env bash\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def call(method: str, path: str, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (PORT, path), data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def wait_idle(timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, body = call("GET", "/status")
        if not body["running"]:
            return body
        time.sleep(0.05)
    raise AssertionError("job did not finish within %ss" % timeout)


# --------------------------------------------------------------------- tests --
def test_rejects_unknown_job():
    code, body = call("POST", "/run", {"job": "rm -rf /"})
    assert code == 400, code
    assert "unknown job" in body["error"], body


def test_rejects_bad_days():
    for days in ("240; rm -rf /", -1, 0, 5000, 12.5, True, None):
        code, body = call("POST", "/run", {"job": "download", "days": days})
        assert code == 400, "days=%r was accepted (%s)" % (days, body)


def test_rejects_bad_timerange_and_currency():
    code, _ = call("POST", "/run", {"job": "backtest", "timerange": "$(id)"})
    assert code == 400
    code, _ = call("POST", "/run", {"job": "backtest", "stake_currency": "BTC"})
    assert code == 400


def test_arguments_are_passed_through():
    if not POSIX:
        raise Skip("needs POSIX: executes a shebang script")
    fake_helper("ft-download-data", 'echo "args: $*"')
    code, body = call("POST", "/run", {"job": "download", "days": 30,
                                       "stake_currency": "USDC"})
    assert code == 202, body
    assert body["argv"] == ["ft-download-data", "30", "--stake-currency", "USDC"], body
    status = wait_idle()
    assert status["returncode"] == 0, status
    assert any("args: 30 --stake-currency USDC" in line for line in status["log"]), status["log"]


def test_one_job_at_a_time():
    if not POSIX:
        raise Skip("needs POSIX: process groups / shebang scripts")
    fake_helper("ft-backtest", "sleep 2")
    code, _ = call("POST", "/run", {"job": "backtest"})
    assert code == 202
    code, body = call("POST", "/run", {"job": "backtest"})
    assert code == 409, body
    assert "already running" in body["error"]
    code, body = call("POST", "/abort", {})
    assert code == 200 and body["ok"], body
    status = wait_idle()
    assert status["returncode"] != 0, "aborted job should not report success"


def test_failure_is_reported():
    if not POSIX:
        raise Skip("needs POSIX: executes a shebang script")
    fake_helper("ft-backtest", 'echo "No data found." >&2; exit 1')
    call("POST", "/run", {"job": "backtest"})
    status = wait_idle()
    assert status["returncode"] == 1, status
    assert any("No data found." in line for line in status["log"]), status["log"]


def test_screen_job_rejects_bad_options():
    """Rejection happens before any process is created, so this runs anywhere."""
    for bad in ({"max_pairs": 0}, {"max_pairs": 500}, {"max_pairs": "5; rm -rf /"},
                {"max_pairs": 2.5}, {"timeframes": "$(id)"}, {"timerange": "not-a-range"}):
        payload = {"job": "screen"}
        payload.update(bad)
        code, body = call("POST", "/run", payload)
        assert code == 400, "%r was accepted (%s)" % (bad, body)


def test_screen_job_arguments():
    if not POSIX:
        raise Skip("needs POSIX: executes a shebang script")
    fake_helper("ft-backtest-all", 'echo "args: $*"')
    code, body = call("POST", "/run", {"job": "screen", "max_pairs": 5,
                                       "timeframes": "15m 1h", "timerange": "20260201-"})
    assert code == 202, body
    assert body["argv"] == ["ft-backtest-all", "20260201-", "--max-pairs", "5",
                            "--timeframes", "15m 1h"], body
    status = wait_idle()
    assert status["returncode"] == 0, status


def test_child_output_is_unbuffered():
    """A file-backed stdout is block-buffered by default, which made the panel
    log lag minutes behind a running job. The child must see PYTHONUNBUFFERED."""
    if not POSIX:
        raise Skip("needs POSIX: executes a shebang script")
    fake_helper("ft-download-data", 'echo "unbuffered=$PYTHONUNBUFFERED"')
    call("POST", "/run", {"job": "download", "days": 5})
    status = wait_idle()
    assert any("unbuffered=1" in line for line in status["log"]), status["log"]


def test_backtest_result_is_summarised():
    results = Path(control.BACKTEST_RESULTS)
    (results / "backtest-2026-08-12.json").write_text(json.dumps({
        "strategy": {"ReboundStrategy": {
            "total_trades": 42, "wins": 25, "draws": 0, "losses": 17,
            "winrate": 0.5952, "profit_factor": 1.31, "profit_total": 0.0734,
            "profit_total_abs": 73.4, "max_drawdown_account": 0.0812,
            "backtest_days": 182, "trades_per_day": 0.23, "stake_currency": "USDT",
            "timeframe": "1h", "ignored": "should not be reported",
            "exit_reason_summary": [{"exit_reason": "roi", "trades": 25, "profit_total": 0.12}],
        }}
    }))
    (results / control.LAST_RESULT).write_text(
        json.dumps({"latest_backtest": "backtest-2026-08-12.json"}))

    _, status = call("GET", "/status")
    r = status["result"]
    assert r["strategy"] == "ReboundStrategy", r
    assert r["total_trades"] == 42 and r["wins"] == 25, r
    assert abs(r["winrate"] - 0.5952) < 1e-9, r
    assert abs(r["max_drawdown_account"] - 0.0812) < 1e-9, r
    assert "ignored" not in r, "only known keys should be surfaced"
    assert r["exit_reasons"][0]["reason"] == "roi", r


def test_missing_or_corrupt_result_is_not_fatal():
    results = Path(control.BACKTEST_RESULTS)
    (results / control.LAST_RESULT).write_text("{not json")
    _, status = call("GET", "/status")
    assert status["result"] is None, status
    (results / control.LAST_RESULT).unlink()
    _, status = call("GET", "/status")
    assert status["result"] is None, status


def test_unknown_paths_404():
    assert call("GET", "/nope")[0] == 404
    assert call("POST", "/nope", {})[0] == 404


def _main() -> int:
    failures = skipped = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS %s" % name)
            except Skip as exc:
                skipped += 1
                print("SKIP %s: %s" % (name, exc))
            except AssertionError as exc:
                failures += 1
                print("FAIL %s: %s" % (name, exc))
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print("ERROR %s: %r" % (name, exc))
    shutil.rmtree(TMP, ignore_errors=True)
    print("=" * 40)
    if failures:
        print("%d TEST(S) FAILED" % failures)
    else:
        print("ALL TESTS PASSED" + (" (%d skipped)" % skipped if skipped else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_main())
