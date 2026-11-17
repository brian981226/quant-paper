"""
所有策略都寫在這裡。

每個策略是一個函式：
  輸入  close —— 收盤價表（每一列是一天、每一欄是一檔股票）
  輸出  一張同樣形狀的「目標權重表」

第 t 天那一列的意思是：看完第 t 天收盤後，希望「隔天開盤」時
每檔股票占總資產多少比例（0 代表不持有）。

規則：只能用 rolling、shift、ewm 這種「往回看」的計算，不能偷看未來。
引擎會自動在隔天開盤照權重下單，也就是我們學過的 t+1 規則。

想加新策略：照下面的格式寫一個函式，再加進最下面的 STRATEGIES 清單。
"""
import numpy as np
import pandas as pd


# ---------- 小工具 ----------

def _equal(hold, close):
    """把 True/False 的持有表變成平均分配的權重：每檔股票分到 1/N 的資金。"""
    return hold.astype(float) / close.shape[1]


def _enter_exit(close, enter, exit_):
    """enter 成立就持有、exit_ 成立就賣出，兩個都不成立就維持前一天的狀態。"""
    sig = pd.DataFrame(np.nan, index=close.index, columns=close.columns)
    sig = sig.mask(enter, 1.0)
    sig = sig.mask(exit_, 0.0)
    return sig.ffill().fillna(0.0)


def _monthly_pick(close, score, top, require_positive=False):
    """每個月第一個交易日收盤後，挑 score 最高的 top 檔，平均持有到下個月。"""
    month = pd.Series(close.index.to_period("M"), index=close.index)
    is_first_day = month != month.shift(1)
    w = pd.DataFrame(np.nan, index=close.index, columns=close.columns)
    for day in close.index[is_first_day.values]:
        s = score.loc[day].dropna()
        if require_positive:
            s = s[s > 0]
        winners = s.nlargest(top).index
        row = pd.Series(0.0, index=close.columns)
        row[winners] = 1.0 / top
        w.loc[day] = row
    return w.ffill().fillna(0.0)


def _inverse_vol(close, lookback=60):
    """每個月第一個交易日，依過去 lookback 天的波動度分配資金：波動越大分越少，總和為 1。"""
    vol = close.pct_change().rolling(lookback).std()
    month = pd.Series(close.index.to_period("M"), index=close.index)
    is_first_day = month != month.shift(1)
    w = pd.DataFrame(np.nan, index=close.index, columns=close.columns)
    for day in close.index[is_first_day.values]:
        inv = (1 / vol.loc[day]).replace([np.inf, -np.inf], np.nan).dropna()
        row = pd.Series(0.0, index=close.columns)
        if len(inv):
            row[inv.index] = inv / inv.sum()
        w.loc[day] = row
    return w.ffill().fillna(0.0)


def _market_on(close, n=200, ref="0050.TW"):
    """大盤在 n 日均線上面為 True。"""
    base = close[ref] if ref in close else close.div(close.iloc[0]).mean(axis=1)
    return base > base.rolling(n).mean()


# ---------- 基準 ----------

def buy_and_hold(close):
    """第一天平均買進每一檔，之後什麼都不做。"""
    return pd.DataFrame(1.0 / close.shape[1], index=close.index, columns=close.columns)


# ---------- 趨勢追蹤 ----------

def ma_cross(close, short=5, long=20):
    """短均線在長均線上面就持有，否則空手。"""
    return _equal(close.rolling(short).mean() > close.rolling(long).mean(), close)


def ma_band(close, short=5, long=20, k=0.02):
    """差距超過 k 才進出，中間地帶維持前一天的狀態。"""
    ma_s, ma_l = close.rolling(short).mean(), close.rolling(long).mean()
    return _equal(_enter_exit(close, ma_s > ma_l * (1 + k), ma_s < ma_l * (1 - k)), close)


def golden_cross(close, short=50, long=200):
    """長期版均線交叉：50 日線在 200 日線上面就持有。"""
    return _equal(close.rolling(short).mean() > close.rolling(long).mean(), close)


def breakout(close, enter_n=20, exit_n=10):
    """海龜突破：收盤創 20 日新高就買，跌破 10 日新低就賣。"""
    high = close.rolling(enter_n).max().shift(1)  # shift(1)：跟「今天以前」的最高比
    low = close.rolling(exit_n).min().shift(1)
    return _equal(_enter_exit(close, close > high, close < low), close)


def macd(close, fast=12, slow=26, signal=9):
    """MACD 線在訊號線上面就持有。"""
    line = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    sig = line.ewm(span=signal, adjust=False).mean()
    return _equal(line > sig, close)


def market_filter(close, n=200, ref="0050.TW"):
    """大盤濾網：0050 在 200 日均線上面時，平均持有所有股票；跌破就全部換成現金。"""
    on = _market_on(close, n, ref)
    hold = pd.DataFrame({c: on for c in close.columns}) & close.notna()
    return _equal(hold, close)


# ---------- 均值回歸 ----------

def bollinger(close, window=20, z=2.0):
    """跌破均線下方 z 個標準差時買進，回到均線就賣出。"""
    ma, sd = close.rolling(window).mean(), close.rolling(window).std()
    return _equal(_enter_exit(close, close < ma - z * sd, close >= ma), close)


def rsi_reversal(close, n=14, low=30, exit_=55):
    """RSI 低於 30（超賣）時買進，回到 55 以上就賣出。"""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rsi = 100 - 100 / (1 + gain / loss)
    return _equal(_enter_exit(close, rsi < low, rsi > exit_), close)


# ---------- 因子投資（多檔互相比較） ----------

def momentum(close, lookback=120, top=5):
    """每月挑過去 120 天漲最多的 5 檔（必須是上漲的）。"""
    return _monthly_pick(close, close / close.shift(lookback) - 1, top, require_positive=True)


def low_volatility(close, lookback=60, top=5):
    """每月挑過去 60 天波動最小的 5 檔。"""
    vol = close.pct_change().rolling(lookback).std()
    return _monthly_pick(close, -vol, top)


# ---------- 進階：部位大小與組合 ----------

def inverse_vol_hold(close):
    """一直持有全部股票，但波動大的少買、波動小的多買，每月調整一次。"""
    return _inverse_vol(close)


def momentum_filtered(close):
    """動能輪動 + 大盤濾網：平常持有最強的 5 檔，大盤跌破 200 日線就全部換現金。"""
    on = _market_on(close)
    return momentum(close).mul(on.astype(float), axis=0)


def ensemble(close):
    """多策略組合：把四個不同流派的策略各分 1/4 的資金同時跑。"""
    parts = [ma_band(close), bollinger(close), momentum(close), low_volatility(close)]
    return sum(parts) / len(parts)


def ensemble_inverse_vol(close):
    """多策略組合，但每檔股票的金額再按波動度調整（波動大的少買）。"""
    base = ensemble(close)
    iv = _inverse_vol(close) * close.shape[1]  # 平均是 1，波動小的 > 1、波動大的 < 1
    w = base * iv
    total = w.sum(axis=1)
    scale = (base.sum(axis=1) / total).where(total > 0, 0.0)  # 總持股比例維持跟原組合一樣
    return w.mul(scale, axis=0)


STRATEGIES = [
    {"id": "buy_hold", "name": "買進持有", "style": "基準", "func": buy_and_hold,
     "desc": "第一天把錢平均分給每一檔股票，之後什麼都不做。這是所有策略的對照組：打不贏它，再聰明的策略都沒有意義。"},

    {"id": "ma_cross", "name": "均線交叉 5/20", "style": "趨勢追蹤", "func": ma_cross,
     "desc": "你親手寫過的那個。每檔股票各自判斷：5 日線在 20 日線上面就持有，掉到下面就賣掉。反應快，但盤整時常被巴來巴去。"},
    {"id": "ma_band", "name": "均線 + 2% 門檻", "style": "趨勢追蹤", "func": ma_band,
     "desc": "你的改良版。5 日線要高出 20 日線 2% 以上才買、低於 2% 以上才賣，中間地帶不動作，用來減少假訊號。"},
    {"id": "golden_cross", "name": "黃金交叉 50/200", "style": "趨勢追蹤", "func": golden_cross,
     "desc": "最有名的長期趨勢訊號：50 日線在 200 日線上面就持有。交易很少、反應很慢，目標是只抓大趨勢、躲過大空頭。"},
    {"id": "breakout", "name": "海龜突破", "style": "趨勢追蹤", "func": breakout,
     "desc": "來自 1980 年代著名的「海龜交易員」實驗：收盤價突破過去 20 天最高價就買進，跌破過去 10 天最低價就賣出。"},
    {"id": "macd", "name": "MACD", "style": "趨勢追蹤", "func": macd,
     "desc": "用 12 日和 26 日指數移動平均的差（MACD 線），跟它自己的 9 日平均（訊號線）比較。MACD 線在上面就持有。看盤軟體幾乎都內建這個指標。"},
    {"id": "market_filter", "name": "大盤濾網", "style": "風險控管", "func": market_filter,
     "desc": "不看個股，只看大盤：0050 在 200 日均線上面時平均持有全部股票，跌破就全部換成現金。目的是躲開大空頭，代價是偶爾被假跌破騙出場。"},

    {"id": "bollinger", "name": "布林通道", "style": "均值回歸", "func": bollinger,
     "desc": "跟趨勢追蹤相反：收盤價跌破 20 日均線下方 2 個標準差，代表跌太多了，買進等反彈；回到均線就賣出。盤整時賺，一路下跌時會接刀子。"},
    {"id": "rsi", "name": "RSI 超賣反彈", "style": "均值回歸", "func": rsi_reversal,
     "desc": "RSI 衡量最近漲跌的力道，介於 0 到 100。低於 30 代表「超賣」，買進等反彈；回到 55 以上就賣出。"},

    {"id": "momentum", "name": "動能輪動", "style": "因子投資", "func": momentum,
     "desc": "每個月第一個交易日收盤後，比較所有股票過去 120 天的漲幅，挑最強的 5 檔（必須是上漲的）持有一個月。強者恆強的概念。"},
    {"id": "low_vol", "name": "低波動輪動", "style": "因子投資", "func": low_volatility,
     "desc": "每個月挑過去 60 天波動最小的 5 檔持有。學術研究發現，低波動股票長期的風險調整後報酬常常不輸高波動股票，這叫「低波動異象」。"},

    {"id": "inv_vol", "name": "波動加權持有", "style": "部位大小", "func": inverse_vol_hold,
     "desc": "跟買進持有一樣一直持有全部股票，差別只在分配：波動大的（例如航運股）少買，波動小的（例如中華電）多買，每月調整。拿來跟買進持有比，就能看出「部位大小」單獨的影響。"},
    {"id": "mom_filter", "name": "動能 + 大盤濾網", "style": "組合", "func": momentum_filtered,
     "desc": "動能輪動加上大盤濾網：平常每月挑最強的 5 檔，但 0050 跌破 200 日均線時全部換成現金。想保留動能的報酬，又躲開大空頭。"},
    {"id": "ensemble", "name": "多策略組合", "style": "組合", "func": ensemble,
     "desc": "把均線門檻（趨勢）、布林通道（均值回歸）、動能、低波動四個流派的策略各分 1/4 資金同時跑。單一策略會有爛年，但它們不會同時爛，組合起來的曲線通常比較平穩。"},
    {"id": "ensemble_iv", "name": "多策略組合 + 波動加權", "style": "組合", "func": ensemble_inverse_vol,
     "desc": "多策略組合，再把每檔股票的金額按波動度調整。同時用上「組合」和「部位大小」兩個改進。"},
]
