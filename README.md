# lecture2notes

把講座錄影變成可讀的筆記、時間同步的 viewer 與課程首頁。本機 ASR 轉錄（Breeze-ASR-25 /
faster-whisper / whisper.cpp / Qwen3-ASR）、投影片換頁抓圖、OCR、一份可被機器檢查的筆記撰寫
規範；可當 Claude Code / Codex 技能使用，也可只當純 CLI 用。

> 狀態：v0.1.0.dev0 骨架。子命令介面、進度輸出、相依檢查與語言旗標已就位；各階段的實作
> 依序補上，尚未實作的階段會印 `[<stage>] not implemented yet` 並以 exit code 4 結束。

## 安裝

需要 Python 3.10 以上，以及 PATH 上的 `ffmpeg` / `ffprobe`。

```bash
python -m venv .venv
.venv\Scripts\pip install -e .[dev]
l2n --help
```

選用套件（extras）：

| Extra | 用途 | 備註 |
|---|---|---|
| `breeze` | Breeze-ASR-25（預設引擎） | 需先跑 `l2n convert-model` 產生 CTranslate2 權重 |
| `qwen` | Qwen3-ASR 本機推論 | 開源權重，transformers 後端 |
| `whispercpp` | whisper.cpp | 無 pip 相依，需自備編譯好的二進位並以 `--whisper-cpp-bin` 指定 |
| `scene` | PySceneDetect 場景偵測抓圖 | 未安裝時退回 ffmpeg scene filter |
| `dev` | pytest / pytest-cov | 開發與測試 |

## 子命令

`transcribe`、`calibrate-subs`、`frames`、`ocr`、`scaffold`、`render`、`viewer`、`pbf`、
`hub`、`check`、`migrate`、`run`、`convert-model`、`profile`、`install-skill`。

Exit code 約定：0 成功、1 有警告、2 合約或參數錯誤、3 缺外部相依、4 該階段尚未實作。

## 隱私邊界 / Privacy boundary

本機 ASR 引擎在你自己的電腦上完成轉錄，音訊與影片不會上傳到任何伺服器（首次下載模型權重除外）。
若你使用 LLM 擴寫階段，逐字稿與骨架筆記會被送給你所使用的 agent 背後的模型供應商，請自行依課程、
院內或客戶的保密規定決定是否啟用。

## 平台

以 Windows 為主要支援平台（主控台輸出相容 cp950，只使用 ASCII 標記）。純 Python 階段在
Linux／macOS 可跑，GPU 引擎為盡力支援。

## 授權

MIT，見 [LICENSE](LICENSE)。

## 致謝

部分設計修改自（Adapted from）<https://github.com/drpwchen/lecture-to-notes>（MIT）。
經逐檔比對，本專案 `src/lecture2notes/` 下沒有任何檔案直接複製上游程式碼；上述措辭指的是
部分模組的流程階段設計參考自上游對應腳本（例如投影片抓圖後 OCR、逐字稿轉錄、筆記/JSON
機器審核、課程首頁），實作（函式名稱、資料流、範圍）皆為獨立撰寫。逐檔的衍生關係、對應
上游路徑與上游 commit hash 完整列於 [`ATTRIBUTION.md`](ATTRIBUTION.md)，上游 MIT LICENSE
全文收錄於 [`NOTICE`](NOTICE)。

兩者的主要差異：

- **profile／overlay 分層**：上游是單一整包腳本；本專案把設定拆成套件內建 `generic` 詞庫
  profile 與可疊加的 `radiology` overlay，兩者互不污染。
- **schema v2**：本專案的正規 lecture JSON 有版本化 schema（`schema_version`、`source`、
  `quotes_zh` 等欄位與驗證器），上游沒有對應的正規 schema 模組。
- **官方字幕偏移校正**：本專案有獨立的字幕/逐字稿時間偏移量測與線性校正流程
  （`engines/calibrate.py`），上游沒有這個階段。
- **撰寫規範與機器檢查**：本專案的筆記撰寫規則（`notes/rules.py`）與四階段機器檢查
  （`acceptance/check.py`、`acceptance/audit.py`）自成一套合約，檢查項目與上游的
  frontmatter／wikilink／citation 審核完全不同。
- **三家 skill 安裝器**：本專案可一鍵安裝為 Claude Code、Codex、OpenCode 三家 AI 代理共用的
  skill（`install-skill` 子命令），上游沒有這個發佈形式。
