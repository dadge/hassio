# Community strategies (vendored)

68 example strategies copied verbatim from
[freqtrade/freqtrade-strategies](https://github.com/freqtrade/freqtrade-strategies)
at commit `eff78d3ce3456b52c68a4e9a33cc055a56b801ff` (2026-08-12).

**These files are GPL-3.0** — see `LICENSE` in this directory. The add-on's own
code is MIT; these remain under their original licence and copyright. They are
plugin files loaded at runtime by Freqtrade, not linked into the add-on.

They are deployed to `/data/user_data/strategies/` on start, so any of them can
be selected with the add-on's `strategy` option (use the class name, which
matches the filename in every case).

## Read this before selecting one

Nothing here has been reviewed, tested or endorsed. Upstream ships them as
*examples*, and two whole directories are unsuitable for this add-on:

| Directory | Files | Status |
| --- | --- | --- |
| `.` (top level) | 27 | Spot-compatible examples. Untested here. |
| `berlinguyinca/` | 30 | Spot-compatible examples. Untested here. |
| `futures/` | 7 | **Will not work.** They short and use leverage; this add-on is spot-only (`trading_mode: spot`, no margin). The add-on refuses to start with one selected. |
| `lookahead_bias/` | 4 | **Deliberately broken.** Upstream's own readme: *"Strategies in this folder do have a lookahead bias"* — they are exercises in spotting the mistake. They produce excellent backtests and lose money live, because they peek at future candles. The add-on refuses live mode with one selected and warns loudly in dry-run. |

A strategy that backtests well here means very little on its own — see the
add-on documentation's backtesting section. Two strategies were measured on
real OKX data over 181 days and both lost money; assume the same of these
until you have your own numbers.

## Dependencies

`GodStra`, `Heracles` and `lookahead_bias/Zeus` import the `ta` package, which
Freqtrade does not ship; the add-on image installs it. `technical`,
`pandas_ta`, `scipy` and `scikit-learn` come with Freqtrade itself.

## Updating

Re-copy from upstream and update the commit hash above. Do not edit these files
in place — local edits belong in your own strategy file.
