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
| `qwen` | Qwen3-ASR 本機推論 | 開源權重；時間戳需另載 forced aligner，見引擎相容表 |
| `whispercpp` | whisper.cpp | 無 pip 相依，需自備編譯好的二進位並以 `--whisper-cpp-bin` 指定 |
| `scene` | PySceneDetect 場景偵測抓圖 | 未安裝時退回 ffmpeg scene filter |
| `dev` | pytest / pytest-cov | 開發與測試 |

## 轉錄引擎相容表

`l2n transcribe --list-engines` 會依本機現況印出同樣四行並標示相依是否滿足。

| 引擎 | local | needs_gpu | native_timestamps | 預設模型 | 相依 | 實測狀態 |
|---|---|---|---|---|---|---|
| `breeze_ct2`（預設） | yes | yes | yes | MediaTek-Research/Breeze-ASR-25 | `faster-whisper` + 自行轉檔的 CT2 權重 | 已在 Windows + RTX 4060 使用 |
| `faster_whisper` | yes | no | yes | large-v3 | `faster-whisper` | CPU 可跑，有 CUDA 更快 |
| `whisper_cpp` | yes | no | yes | ggml-large-v3-turbo.bin | 自備二進位與 ggml 模型 | 以 `--whisper-cpp-bin` / `--whisper-cpp-model` 或 `WHISPER_SRT_BIN` / `WHISPER_SRT_MODEL` 指定 |
| `qwen3_asr` | yes | yes | yes（需對齊器） | Qwen/Qwen3-ASR-0.6B | `pip install lecture2notes[qwen]` | **待實測**（見下） |

### Qwen3-ASR 的三個實作前提

程式碼依 2026-09-21 查證的上游原始碼撰寫，以下三點與一般預期不同：

1. **ASR 模型本身不輸出時間戳。** `Qwen3ASRModel.transcribe()` 只回 `language` 與
   `text`；cue 時間來自另一份第一方權重 `Qwen/Qwen3-ForcedAligner-0.6B`（以
   `forced_aligner=` 掛上，**不需要**外部 ctc-forced-aligner）。因此 `--model-dir`
   之外另有 `--aligner-dir`。
2. **對齊結果是逐 token 的**（中文為逐字），本套件在 `cues_from_alignment()` 依句末
   標點、靜默間隔、字數上限與時長上限重新組成 cue。時間戳單位是**秒**（上游型別標註
   寫 `int`，但回傳前已除以 1000）。
3. **對齊器文件標示上限約 5 分鐘語音**，所以本引擎宣告 `chunk_sec = 240`，轉錄階段會
   自動切窗並把每窗的 cue 位移回整片的時間軸；`--chunk-sec` 可覆寫。

權重大小：0.6B 約 1.88 GB、1.7B 約 4.7 GB，對齊器另計 1.84 GB。`qwen-asr` 0.0.6 把
`transformers` 鎖在 `==4.57.6`，且**未宣告 torch**，torch 需自行依本機 CUDA 安裝；官方
文件所有範例皆假設 CUDA。vLLM 後端以 `pip install "qwen-asr[vllm]"` 安裝，並以
`--backend vllm` 選用（上游是換一個 constructor，不是加參數）。

Hugging Face 上另有 `-hf` 後綴的 repo（例如 `Qwen/Qwen3-ASR-0.6B-hf`），那是原生
transformers 路線、需要 `transformers>=5.13`，與 `qwen-asr` 套件路線**互斥**；`--model-dir`
指向哪一份權重決定能用哪一套 API。本套件走 `qwen-asr` 套件路線。

DashScope 的 `qwen3-asr-flash` 是**雲端付費 API**，與上述開源權重是不同東西；本套件不接它，
也不會有 local=false 的內建引擎。

來源（查證日 2026-09-21）：
[QwenLM/Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR)（README news：2026-01-29 發布、2026-06-26 原生 transformers 支援）、
[qwen_asr/inference/qwen3_asr.py](https://github.com/QwenLM/Qwen3-ASR/blob/main/qwen_asr/inference/qwen3_asr.py)、
[qwen_asr/inference/qwen3_forced_aligner.py](https://github.com/QwenLM/Qwen3-ASR/blob/main/qwen_asr/inference/qwen3_forced_aligner.py)、
[PyPI qwen-asr 0.0.6](https://pypi.org/project/qwen-asr/)（0.0.6 上傳於 2026-01-30）、
[HF Qwen/Qwen3-ASR-0.6B](https://huggingface.co/Qwen/Qwen3-ASR-0.6B)、
[HF Qwen/Qwen3-ASR-1.7B](https://huggingface.co/Qwen/Qwen3-ASR-1.7B)、
[HF Qwen/Qwen3-ForcedAligner-0.6B](https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B)。

**待實測欄位**：實際 torch 版本、0.6B 對 fixture 的 WER 與速度、`language="Japanese"`
是否為上游接受的字串（文件只逐字出現 Chinese / English / Cantonese）。權重尚未下載，
因此 `tests/test_engines.py` 的 `@pytest.mark.gpu` 測試目前一律 skip。

## 子命令

`transcribe`、`calibrate-subs`、`frames`、`ocr`、`scaffold`、`render`、`viewer`、`pbf`、
`hub`、`check`、`migrate`、`run`、`convert-model`、`profile`、`install-skill`。

Exit code 約定：0 成功、1 有警告、2 合約或參數錯誤、3 缺外部相依、4 該階段尚未實作。

## Profile 與 overlay

設定由五層決定，由高到低：命令列參數 > 專案 `./.lecture2notes/` > 使用者 `~/.lecture2notes/` >
`profiles/<name>/` > 套件內建預設。可覆寫的檔案只有五個：

| 檔案 | 合併方式 |
|---|---|
| `note.frontmatter.yaml` | 整檔取代下層 |
| `note.template.md` | 整檔取代下層 |
| `outputs.toml` | 逐鍵合併，高層勝 |
| `privacy.toml` | 逐鍵合併，高層勝 |
| `corrections.json` | 逐鍵合併，高層勝 |

某一層沒有某個檔案，就是對那個檔案沒有意見——不會把值重設回預設。內建 profile 有兩個：
`generic`（預設）與 `radiology`（閱片 callout 模板、放射術語表、病患識別模式，不預設啟用；
用 `--profile radiology`，或在 overlay 的 `outputs.toml` 寫 `profile = "radiology"` 才會生效）。

把自己的設定變成 overlay，最快的方式是複製範例到家目錄：

```bash
# macOS / Linux
mkdir -p ~/.lecture2notes
cp examples/overlay-minimal/*.yaml examples/overlay-minimal/*.md \
   examples/overlay-minimal/*.json examples/overlay-minimal/*.toml ~/.lecture2notes/
```

```powershell
# Windows PowerShell
New-Item -ItemType Directory -Force $HOME\.lecture2notes | Out-Null
Copy-Item examples\overlay-minimal\note.frontmatter.yaml, examples\overlay-minimal\note.template.md, examples\overlay-minimal\outputs.toml, examples\overlay-minimal\privacy.toml, examples\overlay-minimal\corrections.json $HOME\.lecture2notes\
```

`examples/overlay-minimal/` 是五個檔案各一份的合法佔位範例（含 `消化層級` 這種自訂
frontmatter 欄位，註解說明 0–3 各代表什麼）。複製完後跑：

```bash
l2n profile show          # 每個生效的鍵，值與來源層
l2n profile show --json   # 同樣內容，每鍵含 value 與 source
```

範例裡定義的每一個鍵，來源層都會顯示 `user`。只想套用在某一門課，就把同樣的檔案放進課程
資料夾的 `./.lecture2notes/`，那一層比家目錄更高。設定檔壞掉時不會默默用預設值：會印出檔案
路徑與 `line N`、以 exit code 2 結束，而且那個檔案一個鍵都不套用。

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
