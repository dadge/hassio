#!/usr/bin/env python3
"""Constant-weight rebalancing bot for OKX spot -- the volatility harvester.

What it earns, and why it is not a forecasting strategy
------------------------------------------------------
For an asset with log-drift nu and volatility sigma, a portfolio continuously
rebalanced to a constant fraction w in the asset and 1-w in cash grows at

    g(w) = w*(nu + sigma^2/2) - w^2*sigma^2/2

The second term is the harvest. With nu = 0 it leaves (sigma^2/2)*w*(1-w),
maximised at w = 1/2 giving sigma^2/8 > 0 -- growth out of volatility alone,
predicting nothing. That matters because a screen of 51 community strategies
over the same market produced no forecasting edge that survived validation,
while this harvest was positive in every configuration, every split date, both
market regimes and up to 2%/side in fees.

The design follows from the formula:

  * Assets are ranked on trailing VOLATILITY, never on past returns. Measured
    on this market, sigma persists across time (rank correlation +0.88 between
    adjacent halves) while drift does not (+0.16). Ranking on past returns
    would be hindsight dressed up as a rule.
  * Equal weight across N assets dilutes any single asset's unpredictable
    drift, and earns the spread between mean asset variance and portfolio
    variance.
  * A cash leg caps exposure to the common market drift, which was deeply
    negative over the test window. Harvest is quadratic in w, drift exposure
    linear, so trimming w costs little harvest and removes a lot of risk.
  * A no-trade band, because rebalancing continuously would pay fees forever.

The honest limit: the harvest is RELATIVE. Over the test data it turned -14.0%
into -10.6% in the falling half and +13.5% into +24.2% in the rising half. It
reliably beats holding the same basket. It does not make a falling market
profitable, and no amount of rebalancing will.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from statistics import stdev
from typing import Any

import ccxt

RUNTIME = Path("/data/.addon/runtime.json")
STATE = Path("/data/harvest_state.json")
PANEL = Path("/opt/ha-panel/index.html")
# Home Assistant's ingress gateway. The panel is reachable only through it, so
# HA's own login is the authentication boundary -- same approach as the
# freqtrade add-on's nginx `allow 172.30.32.2`.
INGRESS_IP = "172.30.32.2"
YEAR_DAYS = 365.25

log = logging.getLogger("harvest")


# --------------------------------------------------------------------- state --
class State:
    """Portfolio state, persisted so a restart does not lose the book.

    Guarded by a lock: the rebalance loop mutates it while HTTP handlers read
    it, and a half-written book rendered in the panel would be alarming.
    """

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.data: dict[str, Any] = {
            "mode": "dry-run",
            "cash": 0.0,
            "holdings": {},          # symbol -> units
            "basket": [],            # symbols currently targeted
            "selected_at": None,
            "started_at": None,
            "paused": False,
            "wallet_start": 0.0,
            "equity": [],            # [[iso8601, value], ...]
            "events": [],            # newest first
            "rebalances": 0,
            "fees_paid": 0.0,
            "last_check": None,
            "last_error": None,
            "prices": {},
        }

    def load(self) -> None:
        if STATE.exists():
            try:
                self.data.update(json.loads(STATE.read_text()))
                log.info("Restored state: %d holdings, cash %.2f",
                         len(self.data["holdings"]), self.data["cash"])
            except (OSError, ValueError) as exc:
                log.warning("Could not read %s (%s); starting fresh", STATE, exc)

    def save(self) -> None:
        with self.lock:
            # Bounded history: this file is rewritten on every check and lives
            # on the Pi's SD card.
            self.data["equity"] = self.data["equity"][-2000:]
            self.data["events"] = self.data["events"][:200]
            payload = json.dumps(self.data, indent=1)
        tmp = STATE.with_suffix(".tmp")
        tmp.write_text(payload)
        tmp.replace(STATE)      # atomic: a crash mid-write must not truncate the book

    def event(self, kind: str, message: str) -> None:
        with self.lock:
            self.data["events"].insert(0, {
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "kind": kind, "message": message})
        log.info("%s: %s", kind, message)


# ------------------------------------------------------------------ helpers --
def notify(cfg: dict, title: str, message: str) -> None:
    """Fire-and-forget HA notification. Never let this break the trading loop."""
    if not cfg.get("notifications_enabled"):
        return
    service = cfg.get("notify_service", "")
    if "." not in service:
        return
    domain, name = service.split(".", 1)
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    req = urllib.request.Request(
        f"http://supervisor/core/api/services/{domain}/{name}",
        data=json.dumps({"title": title, "message": message}).encode(),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        method="POST")
    try:
        urllib.request.urlopen(req, timeout=10).close()
    except (urllib.error.URLError, OSError) as exc:
        log.warning("HA notification failed: %s", exc)


def annualised_sigma(closes: list[float], bars_per_day: float) -> float:
    """Realised volatility from log returns, annualised."""
    if len(closes) < 30:
        return 0.0
    rets = [math.log(closes[i] / closes[i - 1])
            for i in range(1, len(closes))
            if closes[i - 1] > 0 and closes[i] > 0]
    if len(rets) < 30:
        return 0.0
    return stdev(rets) * (bars_per_day * YEAR_DAYS) ** 0.5


# --------------------------------------------------------------------- bot ---
class Harvester:
    def __init__(self, cfg: dict, state: State) -> None:
        self.cfg = cfg
        self.state = state
        self.live = cfg["mode"] == "live"
        # The currency the book is denominated in and the cash leg is held in.
        # Note this is independent of okx_environment, which only selects the
        # API hostname (the EEA entity) and has no bearing on which pairs exist.
        self.quote = cfg.get("quote_currency") or "USDT"
        self.fee = 0.001                     # OKX spot taker, worst tier
        self.exchange = self._build_exchange()
        self.force_rebalance = threading.Event()

    def _build_exchange(self) -> ccxt.Exchange:
        params: dict[str, Any] = {"enableRateLimit": True, "options": {"defaultType": "spot"}}
        if self.live:
            params.update(apiKey=self.cfg["okx_api_key"],
                          secret=self.cfg["okx_api_secret"],
                          password=self.cfg["okx_api_passphrase"])
        ex = ccxt.okx(params)
        if self.cfg.get("okx_environment") == "myokx":
            # The EEA-regulated entity; ccxt reaches it via the same API with a
            # different hostname.
            ex.hostname = "my.okx.com"
        return ex

    # ------------------------------------------------------------ universe --
    def select_basket(self) -> list[str]:
        """Rank liquid spot pairs by trailing volatility and take the top N.

        Volatility only -- see the module docstring on why past returns must
        not enter this decision.
        """
        markets = self.exchange.load_markets()
        tickers = self.exchange.fetch_tickers()
        candidates = []
        quoted = 0
        no_volume = 0
        for sym, m in markets.items():
            if not (m.get("spot") and m.get("active")):
                continue
            if m.get("quote") != self.quote:
                continue
            quoted += 1
            t = tickers.get(sym) or {}
            vol = t.get("quoteVolume")
            if vol is None:
                # Counted separately: a pair the exchange reports no volume for
                # is indistinguishable here from a genuinely illiquid one, and
                # if this number is large the filter is discarding the universe
                # rather than screening it.
                no_volume += 1
                vol = 0.0
            if vol < self.cfg["min_volume_usdt"]:
                continue
            candidates.append(sym)

        log.info("Universe: %d active %s spot pairs, %d above %.0f 24h volume"
                 "%s", quoted, self.quote, len(candidates),
                 self.cfg["min_volume_usdt"],
                 f" ({no_volume} reported no volume)" if no_volume else "")

        lookback = int(self.cfg["volatility_lookback_days"])
        bars = min(int(lookback * 6), 300)          # 4h candles -> 6/day
        scored: list[tuple[float, str]] = []
        for sym in candidates:
            try:
                ohlcv = self.exchange.fetch_ohlcv(sym, timeframe="4h", limit=bars)
            except ccxt.BaseError as exc:
                log.debug("skip %s: %s", sym, exc)
                continue
            closes = [c[4] for c in ohlcv if c[4]]
            if len(closes) < bars * 0.8:            # too short a history to rank
                continue
            sigma = annualised_sigma(closes, bars_per_day=6)
            if sigma > 0:
                scored.append((sigma, sym))

        scored.sort(reverse=True)
        picks = [s for _, s in scored[: int(self.cfg["basket_size"])]]
        if scored:
            log.info("Top sigma: %s", ", ".join(
                f"{s}={v:.2f}" for v, s in scored[: len(picks)]))
        return picks

    # ------------------------------------------------------------- pricing --
    def prices_for(self, symbols: list[str]) -> dict[str, float]:
        if not symbols:
            return {}
        out: dict[str, float] = {}
        tickers = self.exchange.fetch_tickers(symbols)
        for sym in symbols:
            t = tickers.get(sym) or {}
            px = t.get("last") or t.get("close")
            if px:
                out[sym] = float(px)
        return out

    def equity(self, prices: dict[str, float]) -> float:
        with self.state.lock:
            d = self.state.data
            held = sum(units * prices.get(sym, 0.0)
                       for sym, units in d["holdings"].items())
            return held + d["cash"]

    # ----------------------------------------------------------- rebalance --
    def target_leg_value(self, total: float, n_legs: int) -> float:
        """Target notional for one leg, after the live deployment cap.

        Both the rebalance and the drift check go through this. If they
        disagreed -- e.g. drift measured against the uncapped target while
        orders were sized against the capped one -- a capped live book would
        read as permanently off-target and rebalance on every single check.
        """
        if n_legs <= 0 or total <= 0:
            return 0.0
        w = self.cfg["target_exposure_pct"] / 100.0
        deployed = total * w
        if self.live:
            deployed = min(deployed, float(self.cfg["live_max_deployed_usdt"]))
        return deployed / n_legs

    def rebalance(self, prices: dict[str, float], reason: str) -> None:
        """Move every leg back to its target weight.

        Sells run before buys: in live mode the quote balance has to exist
        before it can be spent, and a buy-first ordering would fail the first
        order of every rebalance on a fully-invested book.
        """
        with self.state.lock:
            d = self.state.data
            basket = list(d["basket"])
            total = self.equity(prices)
            if total <= 0 or not basket:
                return
            w = self.cfg["target_exposure_pct"] / 100.0
            per_leg = w / len(basket)
            min_order = self.cfg["min_order_usdt"]

            leg_value = self.target_leg_value(total, len(basket))

            orders: list[tuple[str, float, float]] = []   # (symbol, delta_units, price)
            for sym in basket:
                px = prices.get(sym)
                if not px:
                    continue
                want_units = leg_value / px
                have_units = d["holdings"].get(sym, 0.0)
                delta = want_units - have_units
                if abs(delta) * px >= min_order:
                    orders.append((sym, delta, px))

            # Legs no longer in the basket are liquidated regardless of size.
            for sym, units in list(d["holdings"].items()):
                if sym not in basket and units > 0:
                    px = prices.get(sym)
                    if px:
                        orders.append((sym, -units, px))

        if not orders:
            return

        orders.sort(key=lambda o: o[1])          # sells (negative) first
        executed = 0
        for sym, delta, px in orders:
            if self._execute(sym, delta, px):
                executed += 1

        if executed:
            with self.state.lock:
                self.state.data["rebalances"] += 1
            self.state.event("rebalance", f"{reason}: {executed} order(s) executed")
            notify(self.cfg, f"Harvester — rebalanced [{self.cfg['mode'].upper()}]",
                   f"{reason}: {executed} order(s). "
                   f"Equity {self.equity(self.prices_for(list(prices))):.2f} {self.quote}.")

    def _execute(self, symbol: str, delta_units: float, price: float) -> bool:
        """Apply one leg. Paper adjusts the book; live sends a market order."""
        notional = abs(delta_units) * price
        side = "buy" if delta_units > 0 else "sell"

        if self.live:
            try:
                self.exchange.create_order(symbol, "market", side, abs(delta_units))
            except ccxt.BaseError as exc:
                # One failed leg must not abort the rest of the rebalance: a
                # partially rebalanced book is still closer to target than none.
                self.state.event("error", f"{side} {symbol} failed: {exc}")
                return False

        with self.state.lock:
            d = self.state.data
            fee = notional * self.fee
            d["holdings"][symbol] = d["holdings"].get(symbol, 0.0) + delta_units
            d["cash"] -= delta_units * price + fee
            d["fees_paid"] += fee
            if d["holdings"][symbol] <= 1e-12:
                d["holdings"].pop(symbol, None)
        log.info("%s %s %.8f @ %.8f (%.2f %s)", self.cfg["mode"], side,
                 abs(delta_units), price, notional, self.quote)
        return True

    def sync_live_balances(self) -> None:
        """Adopt the exchange's balances as truth in live mode.

        Fills are never exactly the requested size (fees in base currency,
        lot-size rounding, partial fills), so the internal book would drift
        away from reality over time if it were trusted.
        """
        if not self.live:
            return
        try:
            bal = self.exchange.fetch_balance()
        except ccxt.BaseError as exc:
            self.state.event("error", f"balance sync failed: {exc}")
            return
        with self.state.lock:
            d = self.state.data
            d["cash"] = float((bal.get(self.quote) or {}).get("free") or 0.0)
            for sym in list(d["basket"]) + list(d["holdings"]):
                base = sym.split("/")[0]
                amount = float((bal.get(base) or {}).get("total") or 0.0)
                if amount > 0:
                    d["holdings"][sym] = amount
                else:
                    d["holdings"].pop(sym, None)

    # ---------------------------------------------------------------- loop --
    def tick(self) -> None:
        d = self.state.data
        now = datetime.now(timezone.utc)

        due = True
        if d["selected_at"]:
            age = (now - datetime.fromisoformat(d["selected_at"])).days
            due = age >= int(self.cfg["reselect_days"])

        # A basket quoted in something other than the configured currency has to
        # go now, not at the next scheduled re-selection. Otherwise changing
        # quote_currency appears to do nothing for up to reselect_days while the
        # panel cheerfully reports the new currency over the old book.
        stale_quote = [s for s in d["basket"] if not s.endswith("/" + self.quote)]
        if stale_quote:
            log.info("Re-selecting: %d leg(s) not quoted in %s (%s)",
                     len(stale_quote), self.quote,
                     ", ".join(s.split("/")[0] for s in stale_quote[:5]))
            self.state.event("select", f"quote currency is now {self.quote}; "
                                       f"re-selecting the basket")
            due = True

        if due or not d["basket"]:
            picks = self.select_basket()
            if picks:
                with self.state.lock:
                    d["basket"] = picks
                    d["selected_at"] = now.isoformat(timespec="seconds")
                self.state.event("select", f"basket: {', '.join(s.split('/')[0] for s in picks)}")

        symbols = sorted(set(d["basket"]) | set(d["holdings"]))
        prices = self.prices_for(symbols)
        if not prices:
            raise RuntimeError("no prices returned by the exchange")

        self.sync_live_balances()

        total = self.equity(prices)
        with self.state.lock:
            d["prices"] = prices
            d["last_check"] = now.isoformat(timespec="seconds")
            d["equity"].append([now.isoformat(timespec="seconds"), round(total, 4)])

        if d["paused"]:
            return

        band = self.cfg["rebalance_band_pct"] / 100.0
        leg_value = self.target_leg_value(total, len(d["basket"]))
        drift = 0.0
        for sym in d["basket"]:
            px = prices.get(sym)
            if not px or total <= 0:
                continue
            # Drift as a fraction of the whole book, so the band means the same
            # thing regardless of basket size.
            drift = max(drift, abs(d["holdings"].get(sym, 0.0) * px - leg_value) / total)

        forced = self.force_rebalance.is_set()
        if forced:
            self.force_rebalance.clear()
        if forced or drift > band:
            self.rebalance(prices, "manual" if forced else f"drift {drift:.2%} > band {band:.2%}")

    def run(self) -> None:
        interval = int(self.cfg["check_interval_minutes"]) * 60
        while True:
            try:
                self.tick()
                with self.state.lock:
                    self.state.data["last_error"] = None
            except Exception as exc:                      # noqa: BLE001
                # The loop must outlive any single failure -- an exchange
                # hiccup should not take the add-on down and leave a live book
                # unattended.
                log.exception("tick failed")
                with self.state.lock:
                    self.state.data["last_error"] = str(exc)
                self.state.event("error", str(exc))
            self.state.save()
            time.sleep(interval)


# -------------------------------------------------------------------- http --
def make_handler(bot: Harvester, state: State, cfg: dict):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _guard(self) -> bool:
            # Ingress-only. Home Assistant already authenticated the user
            # before proxying, so no second login here -- but nothing else on
            # the network may reach it.
            if self.client_address[0] != INGRESS_IP:
                self.send_error(403, "ingress only")
                return False
            return True

        def _json(self, payload: dict, code: int = 200) -> None:
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args) -> None:
            log.debug("http %s", fmt % args)

        def do_GET(self) -> None:                          # noqa: N802
            if not self._guard():
                return
            path = self.path.split("?")[0].rstrip("/") or "/"
            if path == "/":
                body = PANEL.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/api/status":
                with state.lock:
                    d = dict(state.data)
                    prices = d.get("prices", {})
                    total = bot.equity(prices)
                    leg_value = bot.target_leg_value(total, len(d["basket"]))
                    legs = []
                    for sym in d["basket"]:
                        px = prices.get(sym, 0.0)
                        val = d["holdings"].get(sym, 0.0) * px
                        legs.append({
                            "symbol": sym, "price": px, "units": d["holdings"].get(sym, 0.0),
                            "value": val,
                            "weight": (val / total) if total else 0.0,
                            "target": (leg_value / total) if total else 0.0,
                        })
                    start = d["wallet_start"] or 1.0
                    self._json({
                        "mode": d["mode"], "paused": d["paused"],
                        "quote": bot.quote,
                        "equity": total, "cash": d["cash"],
                        "wallet_start": d["wallet_start"],
                        "pnl_pct": (total / start - 1) * 100 if start else 0.0,
                        "invested_pct": ((total - d["cash"]) / total * 100) if total else 0.0,
                        "target_exposure_pct": cfg["target_exposure_pct"],
                        "band_pct": cfg["rebalance_band_pct"],
                        "legs": legs, "history": d["equity"][-500:],
                        "events": d["events"][:40], "rebalances": d["rebalances"],
                        "fees_paid": d["fees_paid"], "last_check": d["last_check"],
                        "selected_at": d["selected_at"], "last_error": d["last_error"],
                        "version": os.environ.get("ADDON_VERSION", "dev"),
                    })
                return
            self.send_error(404)

        def do_POST(self) -> None:                         # noqa: N802
            if not self._guard():
                return
            path = self.path.split("?")[0].rstrip("/")
            if path == "/api/pause":
                with state.lock:
                    state.data["paused"] = True
                state.event("control", "paused by user")
                return self._json({"ok": True, "paused": True})
            if path == "/api/resume":
                with state.lock:
                    state.data["paused"] = False
                state.event("control", "resumed by user")
                return self._json({"ok": True, "paused": False})
            if path == "/api/rebalance":
                bot.force_rebalance.set()
                state.event("control", "manual rebalance requested")
                return self._json({"ok": True})
            if path == "/api/reset":
                # Paper only: resetting a live book would mean forgetting real
                # positions that still exist on the exchange.
                if bot.live:
                    return self._json({"ok": False, "error": "not available in live mode"}, 400)
                with state.lock:
                    state.data.update(cash=cfg["paper_wallet_usdt"], holdings={},
                                      equity=[], rebalances=0, fees_paid=0.0,
                                      wallet_start=cfg["paper_wallet_usdt"])
                state.event("control", "paper portfolio reset")
                return self._json({"ok": True})
            self.send_error(404)

    return Handler


# -------------------------------------------------------------------- main --
def main() -> None:
    cfg = json.loads(RUNTIME.read_text())
    logging.basicConfig(
        level=logging.DEBUG if cfg.get("log_level") == "debug" else logging.INFO,
        format="%(asctime)s [%(levelname)-5s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    state = State()
    state.load()
    with state.lock:
        d = state.data
        d["mode"] = cfg["mode"]
        d["started_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if cfg["mode"] == "dry-run" and not d["wallet_start"]:
            d["cash"] = cfg["paper_wallet_usdt"]
            d["wallet_start"] = cfg["paper_wallet_usdt"]

    bot = Harvester(cfg, state)

    if cfg["mode"] == "live":
        bot.sync_live_balances()
        with state.lock:
            if not state.data["wallet_start"]:
                state.data["wallet_start"] = bot.equity(state.data.get("prices", {}))

    state.event("startup", f"started in {cfg['mode']} mode")
    notify(cfg, f"Harvester started [{cfg['mode'].upper()}]",
           f"Basket {cfg['basket_size']} assets, exposure {cfg['target_exposure_pct']:.0f}%, "
           f"band {cfg['rebalance_band_pct']:.1f}%.")

    threading.Thread(target=bot.run, daemon=True, name="harvest-loop").start()

    server = ThreadingHTTPServer(("0.0.0.0", 8099), make_handler(bot, state, cfg))
    log.info("Ingress panel listening on :8099")
    server.serve_forever()


if __name__ == "__main__":
    main()
