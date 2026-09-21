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

部分程式碼修改自（Adapted in part from）<https://github.com/drpwchen/lecture-to-notes>（MIT）。
逐檔的衍生關係與上游 commit 會整理於 `ATTRIBUTION.md`。
