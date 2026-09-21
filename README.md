# lecture2notes

把講座錄影變成可讀的筆記、時間同步的 viewer 與課程首頁。本機 ASR 轉錄（Breeze-ASR-25 /
faster-whisper / whisper.cpp / Qwen3-ASR）、投影片換頁抓圖、OCR、一份可被機器檢查的筆記撰寫
規範；可當 Claude Code / Codex / OpenCode 的技能使用，也可只當純 CLI 用。

> 狀態：v0.1.0.dev0 骨架。子命令介面、進度輸出、相依檢查與語言旗標已就位；各階段的實作
> 依序補上，尚未實作的階段會印 `[<stage>] not implemented yet` 並以 exit code 4 結束。

## 它做什麼

一支講座錄影進去，出來的是：

| 產物 | 說明 |
|---|---|
| `<stem>.srt` | 本機 ASR 逐字稿；套過對照表時另有 `.raw.srt` 與 `.corrections.json` |
| `frames/` | 投影片換頁影格，附 manifest 與 OCR 快取 |
| `<stem>.json` | 正規 lecture 文件（schema v2）：分段、摘要、takeaways、影格、引用 |
| `<stem>.v4.md` | 骨架筆記，由 LLM 依撰寫規範擴寫，再由 `l2n check note` 驗收 |
| `<stem>.html` | 影片／逐字稿／筆記三層時間同步的單檔 viewer |
| `index.html` | 課程首頁與跨講座全文搜尋，搜尋結果可直接跳到 `?t=<秒數>` |

分段的語意內容（段落邊界、標題、摘要、四條 takeaways）與筆記擴寫由語言模型做；
其餘每一個機械階段都是一個可獨立重跑的 `l2n` 子命令。

## 安裝

需要 Python 3.10 以上，以及 PATH 上的 `ffmpeg` / `ffprobe`。

```bash
pip install lecture2notes
```

從原始碼安裝（開發或要用 `install.py` 部署 skill 時）：

```bash
git clone https://github.com/jieyu166/lecture2notes
cd lecture2notes
python -m venv .venv
.venv\Scripts\pip install -e .[dev]
l2n --help
```

選用套件（extras）：

```bash
pip install "lecture2notes[breeze]"    # 預設引擎
pip install "lecture2notes[qwen]"      # Qwen3-ASR
pip install "lecture2notes[scene]"     # PySceneDetect 抓圖
```

| Extra | 用途 | 備註 |
|---|---|---|
| `breeze` | Breeze-ASR-25（預設引擎） | 需先跑 `l2n convert-model` 產生 CTranslate2 權重 |
| `qwen` | Qwen3-ASR 本機推論 | 開源權重；時間戳需另載 forced aligner，見引擎相容表 |
| `whispercpp` | whisper.cpp | 無 pip 相依，需自備編譯好的二進位並以 `--whisper-cpp-bin` 指定 |
| `scene` | PySceneDetect 場景偵測抓圖 | 未安裝時退回 ffmpeg scene filter |
| `dev` | pytest / pytest-cov | 開發與測試 |

## 快速開始

```bash
# 1. 轉錄（--lang 必填，沒有預設值）
l2n transcribe lecture.mp4 --lang zh

# 2. 抓投影片影格，再對影格做 OCR
l2n frames lecture.mp4 --mode scene
l2n ocr lecture.json

# 3. 產骨架筆記，交給 LLM 擴寫
l2n render lecture.json --expand-prompt

# 4. 產 viewer 與課程首頁
l2n viewer lecture.json
l2n hub ./course-folder --title "課程名稱"

# 5. 每個產物都要驗收
l2n check note lecture.json --note lecture.v4.md
```

機械階段可以一次串起來：

```bash
l2n run lecture.mp4 --lang zh
```

`run` 不產生段落語意，也不擴寫筆記——那兩件事需要語言模型。

`--lang` 刻意沒有預設值。猜錯語言時 Whisper 家族不會報錯，而是把帶口音的英文
幻覺成一份通順的中文逐字稿，讀起來完全正常，一路污染到筆記才會被發現。

## 子命令

`transcribe`、`calibrate-subs`、`frames`、`ocr`、`scaffold`、`render`、`viewer`、`pbf`、
`hub`、`check`、`migrate`、`run`、`convert-model`、`profile`、`install-skill`。

每個子命令可獨立重跑且冪等（已有產物即跳過，`--force` 重做）。共用旗標：`--quiet`、
`--json-progress`（每行一個 JSON 物件，給 agent 追蹤進度）、`--force`。

Exit code 約定：0 成功、1 有警告、2 合約或參數錯誤、3 缺外部相依、4 該階段尚未實作。

## 隱私邊界 / Privacy boundary

**本機 ASR 不上傳任何東西。** 四個轉錄引擎都在你自己的電腦上跑；音訊與影片不會送到任何
伺服器（首次下載模型權重除外）。非本機引擎預設被拒絕，要加 `--allow-cloud` 才會放行；
本套件目前沒有內建任何 `local = false` 的引擎。

**LLM 擴寫在本機邊界之外。** 分段語意與筆記擴寫由語言模型完成，這一步會把逐字稿文字與
骨架筆記送給你所使用的 agent 背後的**模型供應商**。這是離開本機的那一步，請自行依課程、
院內或客戶的保密規定決定要不要啟用；只要骨架、不要擴寫也是完整可用的流程。

院內或病患相關素材一律本機轉錄，且「本機 ASR」不等於後續 LLM 步驟也獲准接收同一批內容。
`privacy.toml` 提供個資樣式比對作為機器兜底（`l2n check note` 會執行），但沒抓到不代表安全。

## 平台

**以 Windows 為主要支援平台**：主控台輸出相容 cp950，只使用 ASCII 標記，路徑處理以
Windows 為準，CI 與實測都在 Windows 上進行。

Linux 與 macOS 為**盡力支援**：純 Python 階段可跑，GPU 引擎與外部二進位（ffmpeg、
whisper.cpp）需自行確認。回報問題時請附上平台。

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

## Overlay 設定

<!-- overlay-howto -->

設定分兩層：**profile** 是套件內建的一整組設定（`generic` 預設，`radiology` 內建但不預設
啟用），**overlay** 是你自己的檔案，疊在 profile 之上。程式碼裡沒有「某位使用者專屬」的
分支，所有個人化都走 overlay。

解析順序由高到低，先命中的贏：

```
CLI 參數 > 專案內 .lecture2notes/ > 家目錄 ~/.lecture2notes/ > profiles/<name>/ > 套件內建
```

可覆寫的檔名固定五個，某一層沒有那個檔就跳過該層：

| 檔名 | 管什麼 | 疊加方式 |
|---|---|---|
| `note.frontmatter.yaml` | 筆記 frontmatter 的欄位與順序 | 整檔覆寫 |
| `note.template.md` | 骨架筆記的章節模板 | 整檔覆寫 |
| `corrections.json` | ASR 錯字對照表 | 合併，高層蓋掉同一筆 `heard` |
| `outputs.toml` | pbf／hub／viewer 開關與 `note.style` 預設 | 整檔覆寫 |
| `privacy.toml` | 個資比對樣式 | 整檔覆寫 |

最小範例——把筆記預設風格改成 `faithful` 並打開 PotPlayer 章節檔輸出：

```bash
mkdir .lecture2notes
```

`.lecture2notes/outputs.toml`：

```toml
[note]
style = "faithful"

[pbf]
enabled = true
```

`.lecture2notes/corrections.json`：

```json
[
  { "heard": "轉職式學習", "correct": "轉移式學習", "source": "my-overlay" }
]
```

改完一定要確認有生效——檔名拼錯不會報錯，只會安靜地不作用：

```bash
l2n profile show
```

它會印出每一項的最終值與它來自哪一層（cli／project／user／profile／builtin）。

## 當成 agent skill 安裝

`skill/` 是一份路由式技能：`SKILL.md` 只有一張「本次工作 -> 讀哪一份 reference」的表、
六條 HARD RULES 與完成條件，細節在 `skill/references/` 的六份文件裡。

```bash
l2n install-skill --all                  # 三家一次
l2n install-skill --target claude        # 只裝一家
l2n install-skill --dest D:/somewhere    # 自訂目錄
l2n install-skill --check --all          # 比對內容 hash，有 drift 時 exit 2
```

沒安裝套件時，從 repo 根目錄直接跑 `python install.py --all` 也可以。

| `--target` | 目標目錄 |
|---|---|
| `claude` | `~/.claude/skills/lecture2notes` |
| `codex` | `~/.agents/skills/lecture2notes` |
| `opencode` | `~/.config/opencode/skills/lecture2notes` |

安裝是**複製**不是 symlink，並且**不會覆蓋目標目錄裡既有的那五個 overlay 檔名**，
所以更新 skill 不會弄丟你的設定。每個目標會寫一份 `.installed.json`（`version`、
`source_sha256`、`installed_at`、`target`）；`--check` 會從目標的實際檔案重算 hash 比對，
不是讀這份紀錄，所以手改過的目標一定抓得到。

## 開發與測試

```bash
.venv\Scripts\python -m pytest
```

測試不需要網路，也不需要模型權重；需要 CUDA 的測試以 `@pytest.mark.gpu` 標記，
沒有 GPU 時自動跳過。

## 授權

MIT，見 [LICENSE](LICENSE)。上游與第三方的授權聲明收錄於 [`NOTICE`](NOTICE)，
逐檔的衍生關係列於 [`ATTRIBUTION.md`](ATTRIBUTION.md)。

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
