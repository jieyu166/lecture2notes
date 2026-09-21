<!-- Encoding: UTF-8 (no BOM). Windows PowerShell 5.1: Get-Content -Encoding UTF8 <file> -->

# 參考：轉錄與字幕校正

任務是「把影片轉成字幕」或「把官方字幕的時間對回影片」時，讀這一份。

## 先問語言

`--lang` 沒有預設值（`zh` / `en` / `ja` / `auto`）。使用者指定過就沿用，沒指定就問，
不要猜。猜錯不會報錯，只會得到一份看起來很正常、內容卻是幻覺的逐字稿。

## 轉錄

```bash
l2n transcribe <video> --lang zh
l2n transcribe <video> --lang zh --engine faster_whisper --model large-v3
l2n transcribe --list-engines
```

常用旗標：`--engine`、`--model`、`--model-dir`、`--corrections <對照表.json>`、
`--no-s2t`（預設會簡轉繁）、`--chunk-sec`、`--allow-cloud`（預設拒絕非本機引擎）。
`whisper_cpp` 另需 `--whisper-cpp-bin` 與 `--whisper-cpp-model`；`qwen3_asr` 的時間戳
來自另一份權重，用 `--aligner-dir` 指定，後端用 `--backend` 選。

引擎選擇與相依見 README 的引擎相容表。院內或病患素材只能用 local 引擎，
**不要**加 `--allow-cloud`。

## 錯字對照表

`--corrections` 會先寫出未修改的 `<stem>.raw.srt`，再寫修正後的 `<stem>.srt`，
並留下 `<stem>.corrections.json` 記錄每一筆取代。這三個檔一個都不能刪：
沒有 raw 就無法回頭驗證改了什麼。

只做「一定錯」的字面取代（專有名詞、機構名、藥名）。語境層級的判斷交給人；
不要為了讓逐字稿好讀而整批改寫。

## 官方字幕偏移校正

課程平台匯出的字幕常整體早／晚幾秒，甚至有線性漂移。

```bash
l2n calibrate-subs <video> <subs.vtt> --lang zh --probes 4
```

`--probes` 可給數量（至少 3）或明確秒數（`300,1800,4200`）；`--out-dir` 可改輸出位置。
它會在數個探針位置用 ASR 轉一小段、量測偏移、擬合線性模型後寫出校正過的字幕，
並把 `offset_model` 記進 JSON 的 `source.subtitle`。

## 驗收

```bash
l2n check transcribe <stem>.srt
```

會檢查 cue 時間單調遞增、無重疊、無 Whisper 家族的重複幻覺迴圈。有 error 就重跑，
不要手改 SRT 把檢查騙過去。

## 不歸這裡管

- 切分段、建立 JSON -> [segmentation.md](segmentation.md)
- 詞庫與 profile -> [profiles-and-overlay.md](profiles-and-overlay.md)
