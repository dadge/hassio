#!/usr/bin/env python3
"""Engine tests for the harvester, against a stubbed exchange.

No network: the dev host blocks the exchange domains, and a unit test that
needs a live venue is a test that fails for reasons unrelated to the code. The
stub serves deterministic price paths, which also lets the accounting be
asserted exactly rather than approximately.

Run:  python tests/test_harvest_bot.py
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
import tempfile
from pathlib import Path

ADDON = Path(__file__).resolve().parent.parent
BOT_PY = ADDON / "rootfs" / "opt" / "harvest" / "bot.py"

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        failures.append(name)


# ------------------------------------------------------------------- stub ---
class FakeExchange:
    """Minimal ccxt surface: markets, tickers, OHLCV, balances, orders."""

    def __init__(self, prices: dict[str, float], series: dict[str, list[float]] | None = None):
        self.prices = dict(prices)
        self.series = series or {}
        self.orders: list[tuple[str, str, float]] = []
        self.hostname = ""

    def load_markets(self):
        return {s: {"spot": True, "active": True, "quote": "USDT",
                    "base": s.split("/")[0]} for s in self.prices}

    def fetch_tickers(self, symbols=None):
        syms = symbols or list(self.prices)
        return {s: {"last": self.prices[s], "quoteVolume": 50_000_000.0}
                for s in syms if s in self.prices}

    def fetch_ohlcv(self, symbol, timeframe="4h", limit=300):
        closes = self.series.get(symbol) or [self.prices[symbol]] * limit
        return [[0, c, c, c, c, 1.0] for c in closes[-limit:]]

    def fetch_balance(self):
        return {}

    def create_order(self, symbol, type_, side, amount):
        self.orders.append((symbol, side, amount))
        return {"id": str(len(self.orders))}


def load_bot(tmp: Path):
    """Import bot.py with its /data paths redirected into a temp dir."""
    spec = importlib.util.spec_from_file_location("harvest_bot", BOT_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["harvest_bot"] = mod
    spec.loader.exec_module(mod)
    mod.STATE = tmp / "state.json"
    mod.RUNTIME = tmp / "runtime.json"
    mod.PANEL = ADDON / "rootfs" / "opt" / "ha-panel" / "index.html"
    return mod


def base_cfg(**over):
    cfg = {"mode": "dry-run", "okx_environment": "okx", "okx_api_key": "",
           "okx_api_secret": "", "okx_api_passphrase": "", "basket_size": 4,
           "target_exposure_pct": 50.0, "rebalance_band_pct": 1.0,
           "volatility_lookback_days": 30, "reselect_days": 30,
           "min_volume_usdt": 0.0, "min_order_usdt": 5.0,
           "paper_wallet_usdt": 1000.0, "live_max_deployed_usdt": 100.0,
           "check_interval_minutes": 15, "notifications_enabled": False,
           "notify_service": "notify.x", "log_level": "info"}
    cfg.update(over)
    return cfg


def new_bot(mod, cfg, prices, series=None, cash=1000.0):
    state = mod.State()
    state.data["cash"] = cash
    state.data["wallet_start"] = cash
    state.data["mode"] = cfg["mode"]
    bot = mod.Harvester.__new__(mod.Harvester)
    bot.cfg, bot.state = cfg, state
    bot.live = cfg["mode"] == "live"
    bot.quote, bot.fee = "USDT", 0.001
    bot.exchange = FakeExchange(prices, series)
    import threading
    bot.force_rebalance = threading.Event()
    return bot, state


# ------------------------------------------------------------------ tests ---
def test_selection_ranks_by_volatility(mod):
    print("\n[selection] ranks on volatility, ignores past return")
    # QUIET rises 3x with tiny wobble; WILD ends flat but swings hard. A ranker
    # that looked at returns would take QUIET; the harvest needs WILD.
    n = 200
    quiet = [100 * (1 + 2 * i / n) + (0.05 if i % 2 else -0.05) for i in range(n)]
    wild = [100 * math.exp(0.9 * math.sin(i / 3.0)) for i in range(n)]
    mid = [100 * math.exp(0.3 * math.sin(i / 3.0)) for i in range(n)]
    prices = {"QUIET/USDT": quiet[-1], "WILD/USDT": wild[-1], "MID/USDT": mid[-1]}
    series = {"QUIET/USDT": quiet, "WILD/USDT": wild, "MID/USDT": mid}
    cfg = base_cfg(basket_size=2)
    bot, _ = new_bot(mod, cfg, prices, series)
    picks = bot.select_basket()
    check("WILD selected first", picks and picks[0] == "WILD/USDT", f"got {picks}")
    check("QUIET (best return, lowest vol) excluded", "QUIET/USDT" not in picks, f"got {picks}")


def test_rebalance_hits_target(mod):
    print("\n[rebalance] every leg lands on its target weight")
    prices = {f"A{i}/USDT": 10.0 for i in range(4)}
    cfg = base_cfg(basket_size=4, target_exposure_pct=50.0)
    bot, state = new_bot(mod, cfg, prices)
    state.data["basket"] = list(prices)
    bot.rebalance(prices, "initial")

    total = bot.equity(prices)
    want = 0.5 / 4
    weights = [state.data["holdings"][s] * prices[s] / total for s in prices]
    check("4 legs opened", len(state.data["holdings"]) == 4, str(state.data["holdings"]))
    check("each leg within 0.1pp of target",
          all(abs(w - want) < 0.001 for w in weights),
          f"weights={[round(w, 4) for w in weights]} target={want}")
    invested = sum(state.data["holdings"][s] * prices[s] for s in prices)
    check("exposure ~50% of equity", abs(invested / total - 0.5) < 0.002,
          f"invested={invested:.2f} total={total:.2f}")


def test_accounting_conserves_value(mod):
    print("\n[accounting] equity only changes by fees, never by bookkeeping")
    prices = {f"A{i}/USDT": 10.0 for i in range(4)}
    cfg = base_cfg(basket_size=4)
    bot, state = new_bot(mod, cfg, prices)
    state.data["basket"] = list(prices)
    before = bot.equity(prices)
    bot.rebalance(prices, "initial")
    after = bot.equity(prices)
    fees = state.data["fees_paid"]
    check("equity drops by exactly the fees paid", abs((before - after) - fees) < 1e-9,
          f"before={before:.6f} after={after:.6f} fees={fees:.6f}")
    check("fees are non-zero and small", 0 < fees < before * 0.01, f"fees={fees}")
    check("cash never negative", state.data["cash"] >= -1e-9, str(state.data["cash"]))


def test_band_controls_trading(mod):
    print("\n[band] no trade inside the band, trade outside it")
    prices = {f"A{i}/USDT": 10.0 for i in range(4)}
    cfg = base_cfg(basket_size=4, rebalance_band_pct=1.0)
    bot, state = new_bot(mod, cfg, prices)
    state.data["basket"] = list(prices)
    bot.rebalance(prices, "initial")
    n_after_init = state.data["rebalances"]

    bot.exchange.prices["A0/USDT"] = 10.2      # +2% on one leg -> ~0.25pp drift
    bot.tick()
    check("small move does not trigger", state.data["rebalances"] == n_after_init,
          f"rebalances={state.data['rebalances']}")

    bot.exchange.prices["A0/USDT"] = 20.0      # +100% -> far outside the band
    bot.tick()
    check("large move triggers", state.data["rebalances"] > n_after_init,
          f"rebalances={state.data['rebalances']}")

    prices2 = bot.exchange.prices
    total = bot.equity(prices2)
    w = state.data["holdings"]["A0/USDT"] * prices2["A0/USDT"] / total
    check("winner trimmed back to target", abs(w - 0.125) < 0.002, f"weight={w:.4f}")


def test_min_order_respected(mod):
    print("\n[min order] dust legs are not sent to the exchange")
    prices = {f"A{i}/USDT": 10.0 for i in range(4)}
    cfg = base_cfg(basket_size=4, min_order_usdt=1e9)   # nothing can qualify
    bot, state = new_bot(mod, cfg, prices)
    state.data["basket"] = list(prices)
    bot.rebalance(prices, "initial")
    check("no orders when every leg is below the minimum",
          not state.data["holdings"], str(state.data["holdings"]))


def test_live_cap_and_orders(mod):
    print("\n[live] deployment cap binds and real orders are sent")
    prices = {f"A{i}/USDT": 10.0 for i in range(4)}
    cfg = base_cfg(mode="live", basket_size=4, target_exposure_pct=50.0,
                   live_max_deployed_usdt=100.0)
    bot, state = new_bot(mod, cfg, prices, cash=10_000.0)
    state.data["basket"] = list(prices)
    bot.sync_live_balances = lambda: None      # FakeExchange returns no balances
    bot.rebalance(prices, "initial")

    invested = sum(state.data["holdings"][s] * prices[s] for s in prices)
    check("deployed capped at 100 USDT, not 50% of 10k",
          abs(invested - 100.0) < 1.0, f"invested={invested:.2f}")
    check("orders actually placed on the exchange",
          len(bot.exchange.orders) == 4, str(bot.exchange.orders))
    check("all orders are buys", all(o[1] == "buy" for o in bot.exchange.orders),
          str(bot.exchange.orders))

    # With the cap binding, the drift check must agree with the capped target,
    # otherwise the book rebalances on every single tick forever.
    n = state.data["rebalances"]
    bot.tick()
    check("capped book does not re-trade on the next tick",
          state.data["rebalances"] == n, f"rebalances went {n} -> {state.data['rebalances']}")


def test_harvest_beats_static(mod):
    print("\n[harvest] rebalancing beats the same basket left alone")
    # Two anti-correlated oscillators, zero net drift: the textbook case the
    # whole strategy is built on. Rebalancing must end ahead of buy-and-hold.
    n = 400
    a = [100 * math.exp(0.35 * math.sin(i / 8.0)) for i in range(n)]
    b = [100 * math.exp(-0.35 * math.sin(i / 8.0)) for i in range(n)]
    cfg = base_cfg(basket_size=2, target_exposure_pct=100.0, rebalance_band_pct=1.0)
    prices = {"A/USDT": a[0], "B/USDT": b[0]}
    bot, state = new_bot(mod, cfg, prices)
    state.data["basket"] = ["A/USDT", "B/USDT"]
    bot.rebalance(prices, "initial")

    for i in range(1, n):
        bot.exchange.prices = {"A/USDT": a[i], "B/USDT": b[i]}
        bot.tick()

    final_prices = {"A/USDT": a[-1], "B/USDT": b[-1]}
    rebalanced = bot.equity(final_prices)
    static = 500 * a[-1] / a[0] + 500 * b[-1] / b[0]     # same 50/50 start, untouched
    check("rebalanced ends above static hold", rebalanced > static,
          f"rebalanced={rebalanced:.2f} static={static:.2f}")
    print(f"        rebalanced {rebalanced:.2f} vs static {static:.2f} "
          f"(+{(rebalanced / static - 1) * 100:.2f}%), "
          f"{state.data['rebalances']} rebalances, {state.data['fees_paid']:.2f} fees")


def test_state_roundtrip(mod, tmp: Path):
    print("\n[state] survives a restart")
    prices = {f"A{i}/USDT": 10.0 for i in range(4)}
    cfg = base_cfg(basket_size=4)
    bot, state = new_bot(mod, cfg, prices)
    state.data["basket"] = list(prices)
    bot.rebalance(prices, "initial")
    state.save()

    restored = mod.State()
    restored.load()
    check("holdings restored", restored.data["holdings"] == state.data["holdings"],
          f"{restored.data['holdings']} != {state.data['holdings']}")
    check("cash restored", abs(restored.data["cash"] - state.data["cash"]) < 1e-9)
    check("state file is valid json", json.loads(mod.STATE.read_text())["basket"] == list(prices))


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        mod = load_bot(tmp)
        test_selection_ranks_by_volatility(mod)
        test_rebalance_hits_target(mod)
        test_accounting_conserves_value(mod)
        test_band_controls_trading(mod)
        test_min_order_respected(mod)
        test_live_cap_and_orders(mod)
        test_harvest_beats_static(mod)
        test_state_roundtrip(mod, tmp)

    print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
