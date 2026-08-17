#!/usr/bin/env python3
"""Engine tests for the harvester, against a stubbed exchange.

No network: the dev host blocks the exchange domains, and a unit test that
needs a live venue is a test that fails for reasons unrelated to the code. The
stub serves deterministic price paths, which also lets the accounting be
asserted exactly rather than approximately.

Run:  python harvest_okx/tests/test_bot.py
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
import tempfile
from datetime import datetime, timezone
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
        # Quote derived from the symbol, not hardcoded: the pair filter under
        # test reads exactly this field.
        return {s: {"spot": True, "active": True,
                    "base": s.split("/")[0], "quote": s.split("/")[1]}
                for s in self.prices}

    def fetch_tickers(self, symbols=None):
        syms = symbols or list(self.prices)
        return {s: {"last": self.prices[s], "quoteVolume": 50_000_000.0}
                for s in syms if s in self.prices}

    def fetch_ohlcv(self, symbol, timeframe="4h", limit=300):
        closes = self.series.get(symbol) or [self.prices[symbol]] * limit
        return [[0, c, c, c, c, 1.0] for c in closes[-limit:]]

    def fetch_order_book(self, symbol, limit=50):
        """A book with a spread and finite depth at each level.

        Deliberately shallow: the point of the slippage model is that a large
        order eats through levels, so a stub with one infinite level would let
        a broken implementation pass.
        """
        px = self.prices[symbol]
        spread = getattr(self, "spread", 0.002)          # 20 bps each side
        depth = getattr(self, "depth", 1.0)              # units per level
        asks = [(px * (1 + spread) * (1 + 0.001 * i), depth) for i in range(20)]
        bids = [(px * (1 - spread) * (1 - 0.001 * i), depth) for i in range(20)]
        return {"asks": asks, "bids": bids}

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
           "notify_service": "notify.x", "log_level": "info",
           # Not the shipped default, which is "orderbook". These fixtures assert
           # exact weights and exact fee arithmetic, and a spread is a second
           # variable in those sums; the slippage test opts back in explicitly.
           "paper_slippage_model": "none", "paper_slippage_pct": 0.0}
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
    # Mirror __init__, which this bypasses via __new__.
    bot.quote = cfg.get("quote_currency") or "USDT"
    bot.fee = 0.001
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


def test_quote_currency_is_honoured(mod):
    print("\n[quote] the configured quote currency selects the pairs")
    # Mixed book. Selecting USDC must ignore the USDT pairs entirely, and the
    # setting most easily confused for this one -- okx_environment, which only
    # picks an API hostname -- must not affect it.
    n = 200
    series, prices = {}, {}
    for sym in ("AAA/USDT", "BBB/USDT", "AAA/USDC", "BBB/USDC"):
        s = [100 * math.exp(0.4 * math.sin(i / 5.0)) for i in range(n)]
        series[sym], prices[sym] = s, s[-1]

    for quote in ("USDT", "USDC"):
        bot, _ = new_bot(mod, base_cfg(basket_size=2, quote_currency=quote), prices, series)
        picks = bot.select_basket()
        check(f"quote_currency={quote} selects only {quote} pairs",
              bool(picks) and all(p.endswith("/" + quote) for p in picks), f"got {picks}")

    bot, _ = new_bot(mod, base_cfg(okx_environment="myokx"), prices, series)
    check("myokx alone does not change the quote currency", bot.quote == "USDT", bot.quote)

    # Switching the currency on a running book must take effect immediately.
    # Waiting for the scheduled re-selection leaves the panel reporting the new
    # currency over a book still held in the old one -- exactly what happened on
    # a live instance after this option was added.
    bot, state = new_bot(mod, base_cfg(basket_size=2, quote_currency="USDC"), prices, series)
    state.data["basket"] = ["AAA/USDT", "BBB/USDT"]     # selected under USDT
    # Selected just now, so the scheduled re-selection is NOT due: without the
    # currency check the basket would survive untouched. An old timestamp would
    # make this pass on the schedule alone and test nothing.
    state.data["selected_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    bot.rebalance({s: prices[s] for s in state.data["basket"]}, "seed")
    bot.tick()
    check("switching quote currency re-selects at once",
          bool(state.data["basket"])
          and all(s.endswith("/USDC") for s in state.data["basket"]),
          f"basket={state.data['basket']}")
    check("the old quote's legs are liquidated",
          not any(s.endswith("/USDT") for s in state.data["holdings"]),
          f"holdings={list(state.data['holdings'])}")

    # Same trap, different setting: a change to any input of the selection must
    # re-select now, while a change that only affects trading must not.
    def settled(**over):
        b, st = new_bot(mod, base_cfg(basket_size=2, **over), prices, series)
        b.tick()                                   # selects and stores the key
        st.data["selected_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return b, st

    # 15 days, not 60: a 60-day lookback wants 300 bars and these fixtures are
    # 200 long, so every candidate would be skipped for want of history and
    # nothing would be selected to compare.
    for option, value in (("min_volume_usdt", 1.0), ("basket_size", 3),
                          ("volatility_lookback_days", 15)):
        _, st = settled()
        key_before = st.data.get("selection_key")
        over = {"basket_size": 2, option: value}   # option may BE basket_size
        b2, _ = new_bot(mod, base_cfg(**over), prices, series)
        b2.state.data.update(st.data)              # same book, one option changed
        b2.tick()
        check(f"changing {option} re-selects immediately",
              b2.state.data.get("selection_key") not in (None, key_before),
              f"key stayed {key_before}")

    _, st = settled()
    key_before = st.data.get("selection_key")
    b2, _ = new_bot(mod, base_cfg(basket_size=2, rebalance_band_pct=5.0), prices, series)
    b2.state.data.update(st.data)
    b2.tick()
    check("changing the band alone does not re-select",
          b2.state.data.get("selection_key") == key_before,
          f"{key_before} -> {b2.state.data.get('selection_key')}")


def test_paper_slippage(mod):
    print("\n[slippage] paper fills pay the spread and their own impact")
    prices = {"A/USDT": 100.0}
    cfg = base_cfg(basket_size=1, paper_slippage_model="orderbook")
    bot, state = new_bot(mod, cfg, prices)

    buy = bot.paper_fill_price("A/USDT", 0.5, 100.0)     # inside the first level
    sell = bot.paper_fill_price("A/USDT", -0.5, 100.0)
    check("a buy fills above last", buy > 100.0, f"{buy}")
    check("a sell fills below last", sell < 100.0, f"{sell}")

    # 10 units against 1 unit per level must walk the book and cost more than
    # a small order, which is the impact term a flat percentage cannot express.
    big = bot.paper_fill_price("A/USDT", 10.0, 100.0)
    check("a larger order fills worse than a small one", big > buy, f"{big} vs {buy}")

    cfg2 = base_cfg(basket_size=1, paper_slippage_model="fixed", paper_slippage_pct=1.0)
    b2, _ = new_bot(mod, cfg2, prices)
    check("fixed model charges the configured percentage",
          abs(b2.paper_fill_price("A/USDT", 1.0, 100.0) - 101.0) < 1e-9,
          str(b2.paper_fill_price("A/USDT", 1.0, 100.0)))

    cfg3 = base_cfg(basket_size=1, paper_slippage_model="none")
    b3, _ = new_bot(mod, cfg3, prices)
    check("none restores last-price fills",
          b3.paper_fill_price("A/USDT", 1.0, 100.0) == 100.0, "")

    # And it must actually be charged, not merely computed.
    state.data["basket"] = ["A/USDT"]
    before = bot.equity(prices)
    bot.rebalance(prices, "initial")
    after = bot.equity(prices)
    slip = state.data["slippage_paid"]
    fees = state.data["fees_paid"]
    check("slippage is recorded", slip > 0, f"{slip}")
    check("equity falls by fees plus slippage",
          abs((before - after) - (fees + slip)) < 1e-6,
          f"drop={(before - after):.6f} fees={fees:.6f} slip={slip:.6f}")


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
        test_quote_currency_is_honoured(mod)
        test_paper_slippage(mod)
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
