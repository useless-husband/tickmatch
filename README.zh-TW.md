# tickmatch：照台股規則撮合的硬體撮合引擎

用 SystemVerilog 寫的證券撮合引擎，遵循**臺灣證券交易所（證交所）的交易規則**，而不是一般常見的美式逐筆訂單簿：
開盤與收盤集合競價（依證交所的成交價決定原則）、依價格分段的升降單位與 ±10% 漲跌停、各時段允許的委託種類
（限價／市價 × ROD／IOC／FOK）、未成交市價單以「轉換參考價」優先留在委託簿，以及盤中瞬間價格穩定措施
（暫緩撮合兩分鐘後以集合競價恢復）。一個引擎負責一檔股票的一個交易日。

驗證分三方面：用證交所自己公布的範例檢查；和另一套軟體規則模型在三百多萬則固定亂數種子產生的隨機與刁鑽訊息上逐則比對
（每一則輸出訊息依序比較）；再用獨立的不變量檢查器檢查。突變測試在 RTL 中埋入 42 個錯誤，全部被抓到。

> **請先讀這段。** tickmatch 是依照公開規則做的教學模型，與證交所無關，也未經證交所認可，不是經過認證的實作。
> 規則以 **2026-10-06**（[`docs/RULES.md`](docs/RULES.md) 中資料的抓取日期）為準。全專案沒有使用任何真實委託資料
> （證交所的逐筆委託資料並未免費公開），所有委託流都由固定亂數種子產生。這不是投資軟體。
> 全部在模擬中執行：沒有 FPGA 板子，下面的時脈數字是合成後的估計值。

English: [README.md](README.md) · 初學者導讀：[docs/導讀.zh-TW.md](docs/導讀.zh-TW.md)

## 示範

`./跑跑看.command`（或 `make report`）會產生一檔參考價 583.00 元股票的一整個交易日，送進 Verilator 模擬的 RTL，
把每一則回覆和規則模型比對、檢查不變量，然後打開一個可重播的靜態網頁：

```
$ make report
python3 tools/gen.py --seed 2332 --profile day --ref 58300 --messages 200000 --stim build/day/day.stim --exp build/day/day.exp
seed 2332 profile day: 123571 lines; acks 72131, rejects 2007, trades 38922, cancels 32084, interruptions 3, auctions 5, snapshots 2161
./build/sim_tm --stats build/day/day.stats < build/day/day.stim > build/day/day.out
python3 tools/compare.py build/day/day.exp build/day/day.out build/day/day.stim
identical: 123571 input messages, 171029 output messages
python3 tools/invariants.py build/day/day.stim build/day/day.out
invariants hold: 1 days, 123570 messages
python3 tools/make_report.py
build/report.html: 628 frames, 196 KB; day had 123570 messages, 38922 trades, 3 interruptions
report: build/report.html
```

網頁（`build/report.html`，單一檔案、不需網路、不用 WebAssembly，Safari 鎖定模式也能開）可以逐則重播：最佳五檔、
每則輸入與它造成的所有輸出、開盤競價撮合、瞬間價格穩定措施啟動與之後的競價、收盤集合競價、每分鐘價格圖、延遲分布與合成數字。

## 實作了哪些規則

每條規則的原文和出處都在 [`docs/RULES.md`](docs/RULES.md)，下表的編號對應那份文件。

| 規則 | |
| --- | --- |
| R1 交易時段 | 08:30 開盤前收單 → 09:00 開盤集合競價 → 盤中逐筆交易 → 13:25 收盤前集合 → 13:30 收盤競價，由 TIME 訊息推動 |
| R2、R3 價格表 | 證交所股票升降單位表；漲跌停為參考價 ±10% 向內取到合法價位；最小升降單位但書 |
| R4 委託種類 | 逐筆時段六種委託都可；集合競價時段只收限價 ROD；進入集合競價時刪除留存的市價單；取消；減量（保留排隊順位） |
| R5 優先順序 | 市價優先，再來價格優先、時間優先 |
| R6 集合競價 | 成交量最大、較佳價格全部成交、成交價位至少一方全部成交、多個價位符合時取最接近最近成交價（或開盤競價基準） |
| R7 逐筆交易 | 以委託簿中既有委託的價格成交；市價單轉換參考價（包含留在簿上的市價單） |
| R8 瞬間價格穩定措施 | 參考價：開盤後 5 分鐘用開盤價、之後用前 5 分鐘成交量加權平均價、措施後 5 分鐘用該次競價成交價；超過 3.5% 時限價 ROD 暫緩 2 分鐘後競價、IOC 與市價單取消超出部分、FOK 整筆取消；開盤競價基準低於 1 元不適用 |
| R9 行情 | 依要求提供買賣各五檔；集合競價時段提供模擬成交價、量與撮合後五檔 |

不在範圍內：零股、盤後定價、鉅額交易、權證／ETF／債券、新上市無漲跌幅期間、處置股票、平盤下不得放空等限制、暫緩開收盤（R1.1）。

## 運作方式

台股規則讓一檔股票一天內可能的成交價成為一個很小的有限集合，硬體就是圍繞這點設計的。細節和捨棄的做法見
[`docs/DESIGN.md`](docs/DESIGN.md)（英文）。

- **價位是直接索引的陣列，不是排序結構。** 當天每個合法價格有一個編號（1000 元以下的股票最多 182 個；256 格可涵蓋參考價
  6,395 元以下的所有股票）。買賣各一個位元圖標示哪些價位有委託，優先編碼器一步找出最佳買賣價。一天的範圍內最多只跨一個
  升降單位交界（已對所有價格檢查），所以兩種跳檔大小加一個分界點就能描述；`rtl/tm_daycfg.sv` 在每天開始時從參考價算出這些值。
  （FPGA 訂單簿用固定跳檔陣列並不新，He 等人 FPL 2017 就用過；這裡特別的是從證交所的漲跌停與升降單位規則推導出來，包括交界。）
- **同價位的排隊是 block RAM 中的雙向鏈結串列**，以委託編號為位址；從隊伍中間取消也是固定時間，減量保留順位。
- **集合競價只掃一遍。** 滿足前兩條原則的價格是一段連續的價位，所以答案是 `clamp(anchor, lo, hi)`。硬體一次由低到高的掃描找出
  `lo`、`hi`；規則模型則照字面逐條計算；`make prove` 在全部 19,683 種小委託簿上比較兩者。
- **五分鐘加權平均價用 300 個一秒的桶子**加上滾動總和；3.5% 的判斷用交叉相乘，不需要除法、沒有四捨五入。
- **介面：** 一條 valid/ready 輸入（NEW、CANCEL、REDUCE、TIME、SNAP）、一條 valid/ready 輸出（ACK、附原因的 REJ、TRADE、
  附原因的 CXL、RED、VI、AUC、PHASE、五檔）。一次處理一則訊息。

## 結果

以下數字都在 Apple M5（與其他工作共用）上量測，Verilator 5.052、Yosys 0.69、Python 3.13。

### 證交所範例

在證交所資料中找到 35 個範例（集合競價表、逐筆交易表、ROD/IOC/FOK 比較、市價委託轉換參考價投影片、漲跌停與開盤競價基準範例、
加權平均價範例、十一張瞬間價格穩定措施投影片），清單與出處在 `docs/RULES.md` 最後。

- 規則模型完全重現 **35 個中的 34 個**（`make unit`）。第 35 個是原始資料本身的不一致，記錄為比對不符而不是通過：
  證交所網頁寫參考價 97 元的漲停價是 106.7 元，但依同一頁的升降單位表這不是合法價格，規則算出來是 106.50 元（R3.2）。
- RTL 在 **31 個**屬於訊息序列的範例上，產生的訊息和規則模型完全相同（`make vectors`）；另外 4 個漲跌停／參考價範例由
  `make prove` 涵蓋，它檢查 RTL 對 7,000 元以下每一個參考價算出的漲跌停。
- 資料中找不到範例、只依條文測試的：收盤競價、最接近成交價的取捨、減量保留順位、最小升降單位但書。

### 等價與不變量

| 檢查 | 結果 | 指令 |
| --- | --- | --- |
| RTL 對規則模型，隨機與刁鑽委託流（12 個種子；奇數種子在兩個握手介面上加隨機壅塞） | **3,017,660 則輸入、3,892,706 則輸出，全部相同** | `make equiv-long` |
| 同上，CI 規模（4 個種子） | 約 20 萬則 | `make equiv` |
| 用 RTL 自己的輸出檢查不變量（價格時間優先、漲跌停與升降單位、數量守恆、已取消委託不會成交、盤中委託簿不交叉、競價條件、五檔、收盤後無成交） | 以上每次都成立 | `tools/invariants.py` |
| 針對邊界的情境（3.5% 邊緣、固定參考價和暫緩的最後一秒、剛好 13:25:00 結束的暫緩、FOK 剛好全部成交） | 相同 | `make vectors` |
| 窮舉：7,000 元以下全部 4,800 個參考價的漲跌停與格數；260 個參考價的價格↔格子對應；19,683 種委託簿的競價價格 | 全部符合規則 | `make prove` |
| 埋入 RTL 的錯誤 | **42 個全部抓到** | `make mutants` |
| RTL 中的組合迴路 | 無（`yosys check -assert`） | `make loopcheck` |

### 延遲與吞吐量

延遲以時脈數計：從接受一則訊息到可以接下一則，輸出端一直就緒，包含送出所有輸出訊息（每個時脈一則）。`make bench`，一般交易日：

| 訊息 | 中位數 | p99 | 最大 |
| --- | --- | --- | --- |
| 取消／減量 | 4 | 4 | 4 |
| 新限價 ROD，未成交而留簿 | 6 | 6 | 6 |
| 新限價 ROD，1 筆成交 | 7 | 9 | 9 |
| 新限價 ROD，2–4 筆成交 | 10 | 16 | 18 |
| 新限價 ROD，5 筆以上 | 22 | 40 | 52 |
| 新市價單，1 筆成交 | 6 | 11 | 11 |
| 新 FOK，被取消 | 7 | 10 | 13 |
| 被拒絕的訊息 | 4 | 4 | 4 |
| TIME（時鐘、均價視窗；競價在這裡執行） | 5 | 9 | 5,693 |
| 五檔查詢（集合競價時段包含一次競價掃描） | 19 | 256 | 256 |

平均每則訊息時脈數：6.9（一般交易日）、12.4（大單掃價）、16.1（刁鑽混合）。

以下面**估計**的 50 MHz 時脈換算，一般交易日約等於每秒 720 萬則訊息（單一股票），取消或單筆成交的委託約 80–140 ns。
作為對照，He 等人（FPL 2017）在 FPGA 上做**訂單簿更新**，實測 132–288 ns、每秒 120–150 萬則（含 10 Gb/s 網路）。
兩者不能直接比：他們包含網路 I/O 且在板子上量測；本引擎做的是撮合而非建簿、不含 I/O，時脈是佈局繞線前的估計。只能當數量級參考。

### 合成估計

`make synth`：Yosys `synth_xilinx -family xc7`，預設參數（4,096 個委託編號、256 價位、12 位元數量）。完整報告：
[`synth/report.md`](synth/report.md)。

| LUT | LUT-RAM | 正反器 | RAMB36 | RAMB18 | DSP48 |
| --- | --- | --- | --- | --- | --- |
| 11,050 | 208 | 2,495 | 3 | 9 | 34 |

最長暫存器到暫存器路徑：30 層 LUT／多工器加 10 級 CARRY4，從買賣位元圖經優先編碼器、市價單轉換參考價、格子到價格的乘法。
**時序估計：約 20 ns、50 MHz**（1.0 ns + 每層 LUT 0.6 ns + 每級進位 0.1 ns）。沒有做佈局繞線，真正的數字需要做。
早期的合成曾印出數千個「Detected loop」警告，那是量測路徑時經過正反器和 LUT-RAM 元件造成的假象，`docs/DESIGN.md` 第 9 節有說明；
`yosys check` 在 RTL 中找不到任何迴路。

## 限制

- 只有模擬。沒有板子、沒有佈局繞線、沒有網路介面。
- 一個引擎一檔股票；時間以秒為單位；一次處理一則訊息。
- 沒有模擬暫緩開盤／收盤（R1.1）。開盤前的時間優先用到達順序；證交所是電腦隨機排列，所以改由產生器打亂（R5.1）。
  改價以「取消＋新增」表示，證交所也說改價就是這兩個動作的合併。
- 容量：預設 4,096 張有效委託、256 個價位（參考價 6,395 元以下）、每張 4,095 張（lots）；需要更多價位的日子會被整天拒絕，
  不會被截斷。以上都是參數。
- 沒有帳戶、風控、自我成交防止、放空限制。
- 最長路徑沒有再切管線（`docs/DESIGN.md` 第 9 節）。
- CI 使用 Ubuntu 較舊的 Verilator 和 Yosys；設計刻意只用保守的 SystemVerilog 子集。

## 相關作品

開源的訂單簿和撮合引擎很多，FPGA 上的也不少，FPGA 交易系統更是大學課程常見的期末專題。找到最接近的有：

- [yibo-hou/fpga-limit-order-book](https://github.com/yibo-hou/fpga-limit-order-book)：Artix-7 上的 SystemVerilog 價格時間優先訂單簿，
  有 Python 規則模型、UDP/乙太網路與 UVM 驗證。只有逐筆撮合。
- [mjfalz16/FPGA-Trading](https://github.com/mjfalz16/FPGA-Trading)：Zynq-7000 上的 SystemVerilog 訂單簿與撮合引擎，有 Python 規則模型和隨機自我檢查測試。
- [adilsondias-engineer/08-fpga-order-book](https://github.com/adilsondias-engineer/08-fpga-order-book)：以 BRAM 實作、256 價位、追蹤最佳買賣價的訂單簿（建簿，不是撮合）。
- C. He、H. Fu、W. Luk、W. Li、G. Yang，〈Exploring the Potential of Reconfigurable Platforms for Order Book Update〉，FPL 2017
  （[pdf](https://www.doc.ic.ac.uk/~wl/papers/17/fpl17ch.pdf)）：FPGA 上的固定跳檔訂單簿結構。
- 課程專題：MIT 6.111「HFT Accelerator」（2019）、MIT 6.205「High Frequency Trading on FPGA」（2022）、Cornell ECE 5760
  「High Frequency Trader」（2024）、Columbia 4840「HFT Book Builder」（2024）。都採用美式行情或逐筆撮合。
- 軟體：例如 [philipperemy/order-book-matching-engine](https://github.com/philipperemy/order-book-matching-engine)。

本專案的不同之處：實作一個真實市場的完整規則——證交所的集合競價決價原則、分段升降單位與漲跌停、依時段限制的委託種類、
以轉換參考價留簿的市價單、瞬間價格穩定措施——用該交易所自己的範例檢查，並利用這些規則來設計硬體。
就我所知沒有公開的 RTL 實作證交所規則；這只是說我找不到，並不是宣稱這些技術是新的，它們都是標準做法。

## 建置與測試

需要：Verilator（5.020 以上）、C++17 編譯器、Python 3.10 以上（只用標準函式庫）、GNU make；`make synth` 與 `make loopcheck` 需要 Yosys。
路徑可以有空白和中文。

```
make lint          # verilator -Wall
make unit          # 規則模型：證交所範例、規則測試、不變量檢查器自我測試
make vectors       # RTL 對規則模型：證交所範例與邊界情境
make equiv         # RTL 對規則模型：隨機／刁鑽委託流（約 20 萬則）
make prove         # 小規模窮舉檢查（約 30 秒）
make test          # 以上全部
make equiv-long    # 300 萬則（約 1.5 分鐘）
make mutants       # 42 個埋入的錯誤（4 個工作約 2.5 分鐘）
make bench         # 各類訊息延遲、吞吐量
make loopcheck     # yosys check：沒有組合迴路
make synth         # Yosys 7 系列估計（約 6 分鐘）
make report        # 一個模擬交易日 -> build/report.html
```

## 授權

MIT，見 [LICENSE](LICENSE)。
