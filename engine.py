"""
模擬交易引擎：照策略給的目標權重，一天一天模擬真實下單。

規則（跟我們回測時學的一樣）：
  - 第 t 天收盤後決定權重，第 t+1 天「開盤價」成交
  - 買進：手續費 0.1425%
  - 賣出：手續費 0.1425% + 證交稅（股票 0.3%、ETF 0.1%）
  - 先賣後買，股數取整數（台股可以買零股）
  - 除息日當天，前一天收盤持有的股票會拿到現金股利
"""
import math

import numpy as np
import pandas as pd

FEE = 0.001425
TOL = 0.2  # 目標權重變化超過 20% 才調整部位，避免每天為了一點點差距交易


def tax_rate(ticker):
    return 0.001 if ticker.startswith("00") else 0.003


def simulate(prices, weights, start, capital=1_000_000):
    """
    prices  : dict，含 "open"、"close"、"div" 三張表（日期 × 股票）
    weights : 策略輸出的目標權重表
    start   : 模擬第一天（這天開盤開始下單）
    """
    dates = prices["close"].index
    tickers = list(prices["close"].columns)
    open_ = prices["open"].to_numpy()
    close_ff = prices["close"].ffill().to_numpy()
    div = prices["div"].fillna(0.0).to_numpy()
    w = weights.reindex(index=dates, columns=tickers).fillna(0.0).to_numpy()

    taxes = np.array([tax_rate(t) for t in tickers])
    cash = float(capital)
    shares = np.zeros(len(tickers), dtype=np.int64)
    cur_w = np.zeros(len(tickers))  # 目前「打算」持有的權重
    equity_prev = float(capital)

    equity_dates, equity_values, trades = [], [], []
    total_cost = 0.0
    total_div = 0.0

    start_idx = int(dates.searchsorted(pd.Timestamp(start)))
    for i in range(max(start_idx, 1), len(dates)):
        day = dates[i]
        target = w[i - 1]  # 昨天收盤決定的權重，今天開盤執行

        # 1. 配息：昨天收盤還持有的才拿得到
        for j in np.nonzero((shares > 0) & (div[i] > 0))[0]:
            amount = shares[j] * div[i, j]
            cash += amount
            total_div += amount
            trades.append([day, tickers[j], "配息", int(shares[j]), div[i, j], amount, 0.0])

        # 2. 先賣：目標降到 0 就全賣；目標明顯變小（少 TOL 以上）就賣掉一部分
        for j in range(len(tickers)):
            if cur_w[j] > 0 and target[j] < cur_w[j] * (1 - TOL):
                if np.isnan(open_[i, j]):
                    continue  # 今天沒開盤價（停牌），明天再試
                if target[j] == 0:
                    n = shares[j]
                else:
                    n = shares[j] - math.floor(equity_prev * target[j] / open_[i, j])
                if n > 0:
                    gross = n * open_[i, j]
                    cost = gross * (FEE + taxes[j])
                    cash += gross - cost
                    total_cost += cost
                    trades.append([day, tickers[j], "賣出", int(n), open_[i, j], gross, cost])
                    shares[j] -= n
                cur_w[j] = target[j]

        # 3. 再買：從 0 變成有目標就買進；目標明顯變大（多 TOL 以上）就加碼
        for j in range(len(tickers)):
            if target[j] > 0 and (cur_w[j] == 0 or target[j] > cur_w[j] * (1 + TOL)):
                if np.isnan(open_[i, j]):
                    continue
                budget = min(equity_prev * target[j] - shares[j] * open_[i, j], cash)
                n = math.floor(max(budget, 0) / (open_[i, j] * (1 + FEE)))
                if n > 0:
                    gross = n * open_[i, j]
                    cost = gross * FEE
                    cash -= gross + cost
                    total_cost += cost
                    shares[j] += n
                    trades.append([day, tickers[j], "買進", n, open_[i, j], gross, cost])
                cur_w[j] = target[j]

        # 4. 收盤結算
        value = np.where(np.isnan(close_ff[i]), 0.0, close_ff[i])
        equity_prev = cash + float((shares * value).sum())
        equity_dates.append(day)
        equity_values.append(equity_prev)

    equity = pd.Series(equity_values, index=pd.DatetimeIndex(equity_dates), dtype=float)
    trades_df = pd.DataFrame(trades, columns=["date", "ticker", "action", "shares", "price", "amount", "cost"])

    # 明天開盤會發生什麼：拿最後一天收盤的權重跟目前狀態比
    last_target = w[-1]
    pending = []
    for j, t in enumerate(tickers):
        if last_target[j] == 0 and cur_w[j] > 0 and shares[j] > 0:
            pending.append({"ticker": t, "action": "賣出", "shares": int(shares[j])})
        elif last_target[j] > 0 and cur_w[j] == 0:
            pending.append({"ticker": t, "action": "買進", "weight": float(last_target[j])})
        elif cur_w[j] > 0 and last_target[j] > cur_w[j] * (1 + TOL):
            pending.append({"ticker": t, "action": "加碼", "weight": float(last_target[j])})
        elif cur_w[j] > 0 and 0 < last_target[j] < cur_w[j] * (1 - TOL):
            pending.append({"ticker": t, "action": "減碼", "weight": float(last_target[j])})

    last_close = np.where(np.isnan(close_ff[-1]), 0.0, close_ff[-1])
    holdings = [
        {"ticker": t, "shares": int(shares[j]), "value": float(shares[j] * last_close[j])}
        for j, t in enumerate(tickers)
        if shares[j] > 0
    ]

    return {
        "equity": equity,
        "trades": trades_df,
        "cash": cash,
        "holdings": holdings,
        "pending": pending,
        "total_cost": total_cost,
        "total_div": total_div,
        "capital": capital,
    }


def metrics(result):
    eq = result["equity"]
    if eq.empty:
        return None
    t = result["trades"]
    n_trades = int((t["action"] != "配息").sum()) if not t.empty else 0
    dd = eq / eq.cummax() - 1
    daily = pd.concat([pd.Series([result["capital"]]), eq.reset_index(drop=True)]).pct_change().dropna()
    years = len(eq) / 252
    cagr = (eq.iloc[-1] / result["capital"]) ** (1 / years) - 1 if years > 0 else 0.0
    sharpe = float(daily.mean() / daily.std() * np.sqrt(252)) if daily.std() > 0 else 0.0
    return {
        "cagr": float(cagr),
        "sharpe": sharpe,
        "final": float(eq.iloc[-1]),
        "return": float(eq.iloc[-1] / result["capital"] - 1),
        "mdd": float(dd.min()),
        "trades": n_trades,
        "cost": float(result["total_cost"]),
        "dividends": float(result["total_div"]),
        "days": int(len(eq)),
    }
