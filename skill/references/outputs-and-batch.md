<!-- Encoding: UTF-8 (no BOM). Windows PowerShell 5.1: Get-Content -Encoding UTF8 <file> -->

# 參考：輸出、批次與驗收

任務是「產 viewer」「產 PotPlayer 章節檔」「做課程首頁」「整課批次跑」或
「驗收某個階段」時，讀這一份。

## viewer

```bash
l2n viewer <stem>.json
l2n viewer <stem>.json --preflight
```

產出影片、逐字稿、筆記三層時間同步的單檔 HTML，寫在 JSON 與影片同一個資料夾
（`<stem>.viewer.html`）。頁面裡的路徑全是相對路徑，要絕對網址時（問 AI、擷取）由瀏覽器依
頁面自己的網址算出，所以整個資料夾放到哪台主機都能用，repo 與頁面都不寫死任何網址。
`--preflight` 只列出將建立或覆蓋哪些檔案，不寫入任何東西——覆蓋使用者既有檔案前先跑它。

逐字稿層優先用 JSON 的 `lines`（含 `zh`），沒有才讀字幕；字幕旁有 cue 完全相同的
`<stem>.zh.srt` 時也會帶上中文。投影片時間優先取 `<stem>.frames.json` 的 `timestamp_sec`。

頁面功能（2.1 欄位缺少時對應功能自動隱藏或顯示空狀態，舊 JSON 照常運作）：

- 章節時間軸：每段一塊，滑過顯示標題；`已知` 段淡色、必看段綠色。
- 字幕疊在影片上：雙語（中上原下）／中文／原文／關，`C` 循環。
- 逐字稿面板：目前句高亮並自動捲動，點句跳轉；全文搜尋（`/`）也搜中文。
- 續播：瀏覽器記住每支影片的位置，標題列顯示「上次 mm:ss」。
- 題目模式：剛進入一段時先出該段 `question` 並暫停，「看答案」跳到 `answer_sec`。
- 分流：結論、必看段、省下的分鐘數；「只播必看段」自動跳過其他段。
- 問 AI（`Q`）：複製影片網址 `#t=秒`、字幕（有 `.bi.srt` 就附上）、JSON 網址、目前段落與
  前後逐字稿（長度在設定裡選 30／60／120 秒或整段），最後一行是「我的問題：」。
- 快捷鍵：`J/K` 換段、左右鍵 ±5 秒、`A/B` 區段重播、`[ ]` 調速、`S` 擷取
  「[▶ mm:ss](影片網址#t=秒) 當句字幕」、`?` 說明。
- 手機（寬度 760px 以下）：影片固定在上方，下方分頁「章節／逐字稿／題目／摘要／分流」
  一次顯示一個；點擊目標至少 44px；雙擊影片左右半邊 ±10 秒。

## PotPlayer 章節檔

```bash
l2n pbf <stem>.json
```

`.pbf` 書籤檔，預設**關閉**，要在 profile 的 `outputs.toml` 打開才會產出。

## 課程首頁

```bash
l2n hub <課程資料夾>
l2n hub <課程資料夾> --title "課程名稱" --preflight
```

掃資料夾內所有講座 JSON，產出首頁與跨講座全文搜尋索引，搜尋結果可以直接跳到
`?t=<秒數>`。`--title` 不給就用資料夾名。

## 發佈到輸出目錄

```bash
l2n publish <stem> --dest <目標資料夾>
```

把一場講座的衍生產物**交易式**搬到目標資料夾：正規 JSON、`.srt`、`.frames.json`、
`.v4.md`、viewer，以及 profile 有開 pbf 時的 `.pbf`。要嘛全部落地、要嘛全部不動，
不會留下 viewer 是新的、章節檔是舊的這種半套狀態。

- `<stem>` 可以給講座 stem，也可以給它任何一個檔案路徑。
- `--dest` 必填，且必須與來源在**同一個檔案系統**（最後一步要靠 rename 保證原子性），
  也不可以就是來源資料夾本身；違反時 exit 2。
- 成功後目標資料夾會多一份 `<stem>.publish.json`，記錄這次發佈的每個檔案、sha256、
  是否覆蓋了舊版，以及可回滾的 backup_dir（沒覆蓋任何舊檔時為 null）。
- 覆蓋失敗會自動回滾；萬一回滾也沒做完，訊息會指出備份留在哪裡。

實際旗標以 `l2n publish --help` 為準。

## 批次整課

沒有獨立的 batch 子命令。一整門課的做法是：對每支影片跑 `l2n run`，最後對資料夾
跑一次 `l2n hub`。

```bash
l2n run <video> --lang zh
```

`run` 依序串接所有**機械**階段（旗標：`--lang` 必填、`--engine`、`--model`、
`--allow-cloud`）。它不會幫你產段落語意，也不會擴寫筆記——那兩件事是你的工作。

## 驗收合約

```bash
l2n check transcribe <stem>.srt
l2n check frames <stem>.json
l2n check json <stem>.json
l2n check note <stem>.json --note <stem>.v4.md [--style faithful|concise]
```

四個階段各有各的合約。輸出每行格式是
`<severity> <rule-or-field> <location>: <message>`。

exit code：**0 全過、1 只有 warning、2 有 error**。
每產出或改動一個產物就跑對應的 check，把 exit code 與每一條 warning 回報給使用者。
有 error 就回頭修，不要交出去，也不要手改產物把檢查騙過去。

共用旗標：`--quiet`（只留警告與錯誤）、`--json-progress`（進度改成每行一個 JSON
物件，給 agent 追蹤）、`--force`（忽略既有產物重跑本階段）。

## 不歸這裡管

- 筆記內容規範 -> [note-writing.md](note-writing.md)
- 哪些輸出預設開啟 -> [profiles-and-overlay.md](profiles-and-overlay.md)
