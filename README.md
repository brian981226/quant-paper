# 量化模擬交易場

十五種常見的量化策略，各拿 100 萬虛擬資金，同時操作同一組台股。
GitHub Actions 每個交易日收盤後自動抓股價、照策略「下單」、更新網站。全程不用真錢。

## 檔案

| 檔案 | 做什麼 |
|---|---|
| `strategies.py` | 十五個策略的規則。想加新策略就改這裡 |
| `engine.py` | 模擬下單：t+1 開盤成交、手續費、交易稅、配息 |
| `run.py` | 抓股價 → 跑策略 → 產生網站。股票清單和日期在最上面 |
| `site_template.html` | 網站版面 |
| `docs/index.html` | 產生出來的網站（GitHub Pages 讀這個） |
| `data/paper_trades.csv` | 模擬實盤的完整交易紀錄 |
| `data/paper_equity.csv` | 每個策略每天的資產 |
| `.github/workflows/daily.yml` | 每天自動執行的排程 |

## 先在自己電腦試跑

```
conda activate quant
cd 這個資料夾
python run.py
```

跑完用瀏覽器打開 `docs/index.html` 就能看到網站。

## 放上 GitHub

1. 在 GitHub 建一個新的 **Public** repository（例如 `quant-paper`），不要勾選加 README
2. 進入 repository → **Add file → Upload files**，把這個資料夾裡**所有東西**拖進去
   （包含 `.github` 資料夾；如果拖不進去，改用 GitHub Desktop 或 git 指令上傳）
3. **Settings → Pages**：Source 選 **Deploy from a branch**，Branch 選 `main`、資料夾選 `/docs`，按 Save
4. **Actions** 分頁 → 左邊點 **daily-update** → 右邊 **Run workflow**，手動跑第一次
5. 等一兩分鐘，網站會出現在 `https://你的帳號.github.io/quant-paper/`

之後每個交易日 14:23 左右會自動更新（GitHub 的排程有時會延遲幾十分鐘）。

## 規則細節

- 第 t 天收盤後算訊號，第 t+1 天**開盤價**成交
- 買進手續費 0.1425%；賣出手續費 0.1425% + 證交稅（股票 0.3%、ETF 0.1%）
- 股數取整數（可買零股），先賣後買
- 現金股利在除息日入帳
- 模擬實盤每天都從起點重新計算一次，所以哪天漏跑也沒關係，補跑結果一樣

## 網站版面

`site_template.html` 是從 `site-src/`（React + Tailwind）打包出來的單一檔案，`run.py` 會把資料塞進裡面的 `__APP_DATA__`。
平常不需要動它；想改版面時在 `site-src/` 裡 `pnpm install`，改完用 Parcel 打包成單一 HTML，再覆蓋 `site_template.html`。
