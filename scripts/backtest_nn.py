# SPDX-License-Identifier: MIT
"""Backtest the BESSER-generated neural forecaster against the methods of /insights/forecast.

The protocol of docs/forecasting.md: at every quarter end from December 2009 (66 origins up to
March 2026), each method is fitted only on the months up to the origin, forecasts the next 12 and
is scored (MASE against the in-sample seasonal naive error, MAPE, 80% conformal coverage) on the
monthly totals of exports and imports against the world. The gradient boosting model and the
pooled networks are retrained once a year, on every HS2 chapter up to the December before.

    python scripts/backtest_nn.py                       # data/lux_trade.db, every method
    python scripts/backtest_nn.py --db PATH --methods snaive,ets,nn_mlp
    python scripts/backtest_nn.py --chapters 8 --origins 20   # the top chapters, last 20 quarter ends

It takes minutes (the networks and the gradient boosting models are trained once per December and
cached in FORECAST_CACHE_DIR). The table it prints is in docs/forecasting_nn.md.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app" / "backend"))

from forecasting import backtest, models, nn, pool  # noqa: E402

PROB = 0.8
FIRST_ORIGIN = "2009-12"
LABELS = {
    "snaive": "seasonal naive", "ets": "Holt-Winters", "gbm": "gradient boosting (pooled)",
    "ensemble": "ensemble (Holt-Winters + gradient boosting), today's default",
    "nn_mlp": "BESSER MLP, pooled", "nn_gru": "BESSER GRU, pooled",
    "nn_mlp_local": "BESSER MLP, this series only", "nn_gru_local": "BESSER GRU, this series only",
    "ensemble_nn": "Holt-Winters + gradient boosting + BESSER MLP",
}


def read(db: str, flow: str, product: str = "TOTAL") -> tuple[list[str], np.ndarray]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = con.execute("SELECT time_period, SUM(obs_value) FROM observation WHERE freq = 'M' AND reporter_id = 'LU' "
                       "AND partner_id = 'WORLD' AND flow = ? AND product_id = ? GROUP BY time_period "
                       "ORDER BY time_period", (flow, product)).fetchall()
    con.close()
    return pool.consecutive([r[0] for r in rows], [float(r[1]) for r in rows])


def chapters(db: str) -> dict[str, tuple[list[str], list[float]]]:
    """The pool: every HS2 chapter of exports and imports against the world, monthly."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = con.execute("SELECT o.flow, o.product_id, o.time_period, SUM(o.obs_value) FROM observation o "
                       "JOIN product p ON p.code = o.product_id WHERE o.freq = 'M' AND o.reporter_id = 'LU' "
                       "AND o.partner_id = 'WORLD' AND p.parent_id = 'TOTAL' "
                       "GROUP BY o.flow, o.product_id, o.time_period ORDER BY 1, 2, 3").fetchall()
    con.close()
    out: dict[str, tuple[list, list]] = {}
    for flow, code, period, value in rows:
        periods, values = out.setdefault(f"{flow}:{code}", ([], []))
        periods.append(period)
        values.append(float(value or 0))
    return out


def forecasts(method: str, z: np.ndarray, start: str, origins: list[int], known: dict) -> dict[int, np.ndarray]:
    if method in ("nn_mlp", "nn_gru"):
        return nn.run(z, start, origins, method[3:])
    if method in ("nn_mlp_local", "nn_gru_local"):
        loader = pool._loader
        pool.set_loader(None)
        try:
            return nn.run(z, start, origins, method[3:6])
        finally:
            pool.set_loader(loader)
    if method == "ensemble_nn":
        parts = [known.get(m) or forecasts(m, z, start, origins, known) for m in ("ets", "gbm", "nn_mlp")]
        return {o: np.mean([p[o] for p in parts], axis=0) for o in origins}
    if method == "ensemble":
        parts = [known.get(m) or forecasts(m, z, start, origins, known) for m in models.ENSEMBLE]
        return {o: np.mean([p[o] for p in parts], axis=0) for o in origins}
    return backtest.run(z, start, method, origins)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", default=str(ROOT / "data" / "lux_trade.db"))
    parser.add_argument("--methods", default="snaive,ets,gbm,ensemble,nn_mlp_local,nn_gru_local,nn_mlp,nn_gru,ensemble_nn")
    parser.add_argument("--chapters", type=int, default=0, help="score the top N chapters of each flow instead")
    parser.add_argument("--origins", type=int, default=0, help="only the last N quarter ends")
    args = parser.parse_args()
    methods = args.methods.split(",")

    pooled = chapters(args.db)
    pool.set_loader(lambda: pooled)
    targets = []
    for flow in ("EXPORT", "IMPORT"):
        if args.chapters:
            sizes = sorted(((sum(v[-12:]), k) for k, (p, v) in pooled.items() if k.startswith(flow + ":")), reverse=True)
            targets += [(k, *read(args.db, flow, k.split(":")[1])) for _, k in sizes[:args.chapters]]
        else:
            targets.append((f"{flow}:TOTAL", *read(args.db, flow)))

    table: dict[str, list[dict]] = {m: [] for m in methods}
    for name, periods, values in targets:
        z, log = models.transform(values)
        first = periods.index(FIRST_ORIGIN)
        origins = [i for i in range(first, len(periods) - 1) if int(periods[i][5:7]) % 3 == 0]
        if args.origins:
            origins = origins[-args.origins:]
        known: dict = {}
        for m in methods:
            t0 = time.time()
            known[m] = forecasts(m, z, periods[0], origins, known)
            s = backtest.score(values, periods, z, log, known[m], PROB)
            table[m].append(s)
            print(f"{name:14s} {m:14s} MASE h1 {s['mase_h1']:.2f} h12 {s['mase_h12']:.2f} avg {s['mase']:.2f} "
                  f"({time.time() - t0:.0f} s)", flush=True)

    def avg(m, k):
        xs = [s[k] for s in table[m] if s[k] is not None]
        return float(np.mean(xs)) if xs else float("nan")
    print(f"\n{len(targets)} series, {len(origins)} origins ({periods[origins[0]]} to {periods[origins[-1]]})\n")
    print("| method | MASE 1 month | 3 months | 6 months | 12 months | MASE average | MAPE 1 month | MAPE 12 months "
          "| 80% band coverage |")
    print("|---|---|---|---|---|---|---|---|---|")
    for m in methods:
        print(f"| {LABELS.get(m, m)} | {avg(m, 'mase_h1'):.2f} | {avg(m, 'mase_h3'):.2f} | {avg(m, 'mase_h6'):.2f} | "
              f"{avg(m, 'mase_h12'):.2f} | {avg(m, 'mase'):.2f} | {avg(m, 'mape_h1'):.1%} | {avg(m, 'mape_h12'):.1%} | "
              f"{avg(m, 'coverage'):.0%} |")


if __name__ == "__main__":
    main()
