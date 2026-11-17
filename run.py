"""
每天執行一次：抓股價 → 跑每個策略 → 產生網站 docs/index.html 和交易紀錄。

本機執行：python run.py
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from engine import metrics, simulate
from strategies import STRATEGIES

# ===== 設定：想改股票或日期就改這裡 =====
TICKERS = {
    # 電子
    "2330.TW": "台積電",
    "2317.TW": "鴻海",
    "2454.TW": "聯發科",
    "2308.TW": "台達電",
    "2382.TW": "廣達",
    "2303.TW": "聯電",
    "3711.TW": "日月光投控",
    # 金融
    "2882.TW": "國泰金",
    "2881.TW": "富邦金",
    "2886.TW": "兆豐金",
    # 傳產、民生
    "1301.TW": "台塑",
    "2002.TW": "中鋼",
    "1101.TW": "台泥",
    "1216.TW": "統一",
    "2912.TW": "統一超",
    "2412.TW": "中華電",
    # 航運
    "2603.TW": "長榮",
    "2609.TW": "陽明",
    # ETF
    "0050.TW": "元大台灣50",
    "00878.TW": "國泰永續高股息",
}
DATA_START = "2019-01-01"      # 多抓一年，讓 120 日動能在 2020 年初就算得出來
BACKTEST_START = "2020-01-02"  # 歷史回測起點
PAPER_START = "2026-11-18"     # 模擬實盤起點（這天開盤第一次下單）
CAPITAL = 1_000_000            # 每個策略的虛擬資金

# 時光機：從這些日期開始，五個策略各拿 CAPITAL 重新操作，網頁上可以一天一天回放
# 想回放別段時間就加一行，日期要在 BACKTEST_START 之後
REPLAYS = [
    {"name": "2020 疫情崩盤", "start": "2020-01-02"},
    {"name": "2022 空頭市場", "start": "2022-01-03"},
    {"name": "2024 AI 多頭", "start": "2024-01-02"},
]
# =========================================

ROOT = Path(__file__).parent
TPE = ZoneInfo("Asia/Taipei")


def download_one(ticker, retries=4):
    import yfinance as yf

    for attempt in range(retries):
        try:
            df = yf.Ticker(ticker).history(start=DATA_START, auto_adjust=False, actions=True)
            if df is not None and not df.empty:
                if df.index.tz is not None:
                    df.index = df.index.tz_localize(None)
                df.index = df.index.normalize()
                df = df[~df.index.duplicated(keep="last")]
                if "Dividends" not in df:
                    df["Dividends"] = 0.0
                df = df[["Open", "Close", "Dividends"]].dropna(subset=["Open", "Close"])
                return df[(df["Open"] > 0) & (df["Close"] > 0)]
        except Exception as e:  # 網路錯誤或被限流
            print(f"  {ticker} 第 {attempt + 1} 次失敗：{e}")
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"抓不到 {ticker} 的資料")


def load_prices():
    frames = {}
    for t in TICKERS:
        print(f"下載 {t} {TICKERS[t]}")
        frames[t] = download_one(t)
    prices = {
        "open": pd.DataFrame({t: f["Open"] for t, f in frames.items()}).sort_index(),
        "close": pd.DataFrame({t: f["Close"] for t, f in frames.items()}).sort_index(),
        "div": pd.DataFrame({t: f["Dividends"] for t, f in frames.items()}).sort_index(),
    }
    return prices


def series_to_lists(eq, digits=0):
    return [d.strftime("%Y-%m-%d") for d in eq.index], [round(v, digits) for v in eq.values]


def build(prices):
    close = prices["close"]
    close_ff = close.ffill()
    last_date = close.index[-1]
    names = TICKERS

    stocks = []
    for t in close.columns:
        s = close[t].dropna()
        chg = float(s.iloc[-1] / s.iloc[-2] - 1) if len(s) >= 2 else 0.0
        stocks.append({"ticker": t, "name": names[t], "close": float(s.iloc[-1]), "change": chg,
                       "date": s.index[-1].strftime("%Y-%m-%d")})

    out_strats = []
    paper_trades_all, paper_equity_all = [], {}
    weights = {}
    for st in STRATEGIES:
        w = st["func"](close_ff)
        weights[st["id"]] = w
        bt = simulate(prices, w, BACKTEST_START, CAPITAL)
        pp = simulate(prices, w, PAPER_START, CAPITAL)

        bt_dates, bt_vals = series_to_lists(bt["equity"])
        pp_dates, pp_vals = series_to_lists(pp["equity"])
        paper_equity_all[st["name"]] = pp["equity"]

        trades = pp["trades"].copy()
        if not trades.empty:
            trades.insert(1, "strategy", st["name"])
            trades.insert(3, "name", trades["ticker"].map(names))
            paper_trades_all.append(trades)

        recent = []
        for r in pp["trades"].tail(20).iloc[::-1].itertuples():
            recent.append({"date": r.date.strftime("%Y-%m-%d"), "ticker": r.ticker, "name": names[r.ticker],
                           "action": r.action, "shares": int(r.shares), "price": float(r.price),
                           "amount": float(r.amount), "cost": float(r.cost)})

        out_strats.append({
            "id": st["id"], "name": st["name"], "style": st["style"], "desc": st["desc"],
            "backtest": {"dates": bt_dates, "equity": bt_vals, "metrics": metrics(bt)},
            "paper": {"dates": pp_dates, "equity": pp_vals, "metrics": metrics(pp),
                      "cash": pp["cash"],
                      "holdings": [{**h, "name": names[h["ticker"]]} for h in pp["holdings"]],
                      "pending": [{**p, "name": names[p["ticker"]]} for p in pp["pending"]],
                      "recent": recent},
        })

    # 交易紀錄存成 CSV（每天 commit 一次，GitHub 上就有完整歷史）
    data_dir = ROOT / "data"
    data_dir.mkdir(exist_ok=True)
    cols = ["date", "strategy", "ticker", "name", "action", "shares", "price", "amount", "cost"]
    if paper_trades_all:
        all_trades = pd.concat(paper_trades_all).sort_values(["date", "strategy"])
        all_trades["date"] = all_trades["date"].dt.strftime("%Y-%m-%d")
        all_trades[cols].round(2).to_csv(data_dir / "paper_trades.csv", index=False, encoding="utf-8-sig")
    else:
        pd.DataFrame(columns=cols).to_csv(data_dir / "paper_trades.csv", index=False, encoding="utf-8-sig")
    eq_df = pd.DataFrame(paper_equity_all)
    eq_df.index.name = "date"
    eq_df.round(0).to_csv(data_dir / "paper_equity.csv", encoding="utf-8-sig")

    replay = build_replay(prices, weights)

    return {
        "replay": replay,
        "updated": datetime.now(TPE).strftime("%Y-%m-%d %H:%M"),
        "data_date": last_date.strftime("%Y-%m-%d"),
        "paper_start": PAPER_START,
        "backtest_start": BACKTEST_START,
        "capital": CAPITAL,
        "n_stocks": len(TICKERS),
        "stocks": stocks,
        "strategies": out_strats,
    }


def build_replay(prices, weights):
    """時光機用的資料：共用的日期表、每檔股票每天的收盤價、每段回放每個策略的資產和交易。"""
    close_ff = prices["close"].ffill()
    cal = close_ff.index[close_ff.index >= pd.Timestamp(BACKTEST_START)]
    tickers = list(close_ff.columns)
    t_idx = {t: i for i, t in enumerate(tickers)}
    d_idx = {d: i for i, d in enumerate(cal)}
    act_code = {"買進": 0, "賣出": 1, "配息": 2}

    scenarios = []
    for rp in REPLAYS:
        start_i = int(cal.searchsorted(pd.Timestamp(rp["start"])))
        if start_i >= len(cal):
            continue
        strats = []
        for st in STRATEGIES:
            res = simulate(prices, weights[st["id"]], cal[start_i], CAPITAL)
            eq = res["equity"].reindex(cal[start_i:]).ffill().fillna(CAPITAL)
            trades = [
                [d_idx[r.date], t_idx[r.ticker], act_code[r.action], int(r.shares), round(float(r.price), 2), round(float(r.cost))]
                for r in res["trades"].itertuples()
            ]
            strats.append({"id": st["id"], "equity": [round(v) for v in eq.values], "trades": trades})
        scenarios.append({"name": rp["name"], "start": start_i, "strategies": strats})

    return {
        "dates": [d.strftime("%Y-%m-%d") for d in cal],
        "tickers": tickers,
        "closes": [[round(float(v), 2) if pd.notna(v) else 0 for v in close_ff.loc[cal, t].values] for t in tickers],
        "scenarios": scenarios,
    }


def write_site(payload):
    template = (ROOT / "site_template.html").read_text(encoding="utf-8")
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = template.replace("__APP_DATA__", data, 1)
    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "index.html").write_text(html, encoding="utf-8")
    (docs / ".nojekyll").write_text("", encoding="utf-8")
    print(f"網站已產生：{docs / 'index.html'}")


def main():
    prices = load_prices()
    payload = build(prices)
    write_site(payload)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"執行失敗：{e}")
        sys.exit(1)
