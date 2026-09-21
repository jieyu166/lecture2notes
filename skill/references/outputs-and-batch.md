<!-- Encoding: UTF-8 (no BOM). Windows PowerShell 5.1: Get-Content -Encoding UTF8 <file> -->

# 參考：輸出、批次與驗收

任務是「產 viewer」「產 PotPlayer 章節檔」「做課程首頁」「整課批次跑」或
「驗收某個階段」時，讀這一份。

## viewer

```bash
l2n viewer <stem>.json
l2n viewer <stem>.json --preflight
```

產出影片、逐字稿、筆記三層時間同步的單檔 HTML。`--preflight` 只列出將建立或覆蓋
哪些檔案，不寫入任何東西——覆蓋使用者既有檔案前先跑它。

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
