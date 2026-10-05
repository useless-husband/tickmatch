#!/bin/bash
# ============================================================
#  跑跑看：在 Mac 上模擬「台股撮合引擎晶片」跑完一整個交易日
#
#  這個檔案在 Finder 裡雙擊就會打開「終端機」來執行。它會做四件事：
#    1. 檢查需要的工具（verilator、python3、c++、make）有沒有裝。
#    2. 用 Verilator 把 rtl/ 資料夾裡的電路（SystemVerilog）編譯成模擬程式
#       （第一次大約 10 秒，之後就不用再編）。
#    3. 產生一檔股票一整天的委託（約 12 萬筆，固定亂數種子，不是真實資料），
#       從 8:30 開盤前收單、9:00 集合競價開盤、盤中逐筆撮合、
#       瞬間價格穩定措施、13:25 收盤集合競價，一路送進模擬的晶片，
#       再把晶片的每一則回覆跟「軟體版規則模型」逐則比對，並檢查不變量。
#    4. 產生一個可以逐步重播的網頁（五檔、成交、競價、穩定措施），並自動打開。
#
#  注意：這全部是模擬。電路沒有燒進真的 FPGA 板子。
#  這是依照證交所公開規則做的教學模型，與證交所無關，不是投資工具。
#  不需要安裝任何 Python 套件。
# ============================================================

# 先切換到這個檔案所在的資料夾。資料夾名稱有空白和中文，
# 所以 "$(dirname "$0")" 一定要用雙引號包起來。
cd "$(dirname "$0")" || exit 1

fail() {
  echo ""
  echo "$1"
  read -r -p "按 Enter 關閉視窗..."
  exit 1
}

# 1. 檢查工具
for tool in verilator python3 make c++; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    case "$tool" in
      verilator) fail "找不到 verilator。請先在終端機執行：brew install verilator" ;;
      python3)   fail "找不到 python3。請到 https://www.python.org 下載安裝 Python 3。" ;;
      *)         fail "找不到 $tool。請在終端機執行：xcode-select --install（安裝 Xcode 命令列工具）" ;;
    esac
  fi
done
echo "工具都在：$(verilator --version | head -1)"

# 2. 編譯模擬程式
echo "編譯撮合引擎的模擬程式（第一次大約 10 秒，之後很快）..."
make -s build 2>&1 | grep -v "^- V" || true
[ -x build/sim_tm ] || fail "編譯失敗，上面的訊息會說明原因。"

# 3. 跑一整個交易日，並和規則模型逐則比對
echo ""
echo "模擬一個交易日：參考價 583.00 元，漲停 641、跌停 525，升降單位 1 元"
echo "------------------------------------------------------------"
mkdir -p build/day
python3 tools/gen.py --seed 2330 --profile day --ref 58300 --messages 120000 \
  --stim build/day/day.stim --exp build/day/day.exp > build/day/gen.txt \
  || fail "產生委託失敗，上面的訊息會說明原因。"
echo "已產生委託：$(sed 's/.*lines; //' build/day/gen.txt)"
echo "送進晶片模擬（RTL）..."
./build/sim_tm --stats build/day/day.stats < build/day/day.stim > build/day/day.out \
  || fail "模擬失敗，上面的訊息會說明原因。"
echo "逐則比對晶片的回覆和規則模型（golden model）："
python3 tools/compare.py build/day/day.exp build/day/day.out build/day/day.stim \
  || fail "晶片和規則模型的回覆不一樣！上面顯示第一個不同的地方。"
echo "檢查不變量（價格時間優先、成交價在漲跌停內、數量守恆、盤中不交叉…）："
python3 tools/invariants.py build/day/day.stim build/day/day.out \
  || fail "不變量檢查沒過，上面的訊息會說明原因。"
echo "晶片平均每則訊息花 $(awk '/^messages/{m=$2} /^cycles/{c=$2} END{printf "%.1f", c/m}' build/day/day.stats) 個時脈週期。"

# 4. 產生網頁
echo "------------------------------------------------------------"
python3 tools/make_report.py || fail "產生網頁失敗，上面的訊息會說明原因。"

echo ""
echo "完成！可重播的報告在 build/report.html，現在幫你打開。"
echo "（identical = 晶片和規則模型每一則回覆都相同；invariants hold = 不變量都成立）"
echo "想跑完整測試（官方範例、隨機等價、窮舉、突變測試）：make test、make mutants"
open build/report.html 2>/dev/null || echo "請自己用瀏覽器打開 build/report.html"
echo ""
read -r -p "按 Enter 關閉視窗..."
