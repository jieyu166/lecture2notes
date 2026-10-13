<!-- Encoding: UTF-8 (no BOM). Windows PowerShell 5.1: Get-Content -Encoding UTF8 <file> -->

# 參考：影格、OCR 與骨架筆記

任務是「抓投影片」「跑 OCR」或「產出骨架筆記」時，讀這一份。

## 抓影格

```bash
l2n frames <video> --mode scene
l2n frames <video> --mode interval --every 45
```

旗標：`--mode scene|interval`、`--every`（interval 取樣間隔秒）、`--diff-min`（相鄰
去重門檻）、`--width`（影格寬度像素）、`--stage`（候選先寫進 `staging/`）、
`--curate`（策展 staging 內的候選）、`--max-per-segment`（每段最多晉升幾張）。

建議流程：先 `--stage` 產候選，再 `--curate --max-per-segment 4` 晉升。
使用者已經自己截好圖時**沿用他的圖**，不要重抓。只有音檔時整個階段跳過。

**先 `l2n frames`，再用眼睛挑影格。** `l2n frames` 對已有的 `<stem>.json` 重新併入時，
只填 `frame` 是 `null` 的段落，已經有值的 `frame`（人或模型挑過的）一律保留；
`frames` 陣列照樣更新。要讓每一段的 `frame` 回到抓圖結果，才加 `--force`。

### 抓到幾張，不決定切幾段

**scene 抓到的張數少於你預計的段數時，改用 interval，不要為了湊圖去改分段。**
20 分鐘的講座在 scene 模式下只抓到 9 張是正常的（講者一張投影片講很久），
那不表示這場只能切 9 段。

```bash
l2n frames <video> --mode interval --every 45
```

一個區間裡沒有換投影片時，該段**沿用前一張**：`frame` 填前一段那一張、
`frames` 留空陣列。`l2n frames` 的合併邏輯本來就會這樣寫，`l2n check json`
也不會報錯。段數由主題轉折決定 -> [segmentation.md](segmentation.md)。

**抓圖沒跑完就不要做下一步。** 背景執行可以，但要回報進度、等 exit code、
確認影格已經併入 JSON。這是 HARD RULE 4。

## OCR

```bash
l2n ocr <stem>.json          # 建議寫法
l2n ocr <stem>.frames.json   # 還沒有 <stem>.json 時；只 OCR 這場講座的影格
l2n ocr <frames 資料夾>       # 只有在同層剛好一場講座時才可用
```

旗標：`--min-conf`（低於此信心值直接丟棄）、`--no-s2t`（預設會簡轉繁）、
`--skip-frames <glob 或時間範圍>`（略過不做 OCR，可重複給）。

**畫面含參與者面板或聊天室的影格，OCR 前先排除**，例如
`l2n ocr <stem>.json --skip-frames 00:52:00-00:58:30 --skip-frames "frames/<stem>-01*.png"`；
那些畫面上是與會者的真名與電子郵件，OCR 會把它們抄進 `frame_ocr`。已經做過 OCR 的，
用同樣的 `--skip-frames` 再跑一次：被略過的影格會從 `frame_ocr` 與快取
`<stem>.frames_ocr.json` 一起刪掉（只清 JSON 不夠，下次 scaffold 會從快取併回來）。
`l2n check json` 看到 `frame_ocr` 有電子郵件樣式會報 warning（`frame_ocr_email`）。

傳資料夾時，stem 取自**同層唯一**的 `<stem>.json` 或 `<stem>.frames.json`；
找不到或不只一個就 exit 2 並說明，不會自己拿資料夾名當 stem。
（舊行為會寫出 `frames.frames_ocr.json` 這種沒人會再讀的孤兒快取，
而 `<stem>.json` 一個字的 OCR 都拿不到。）

同一個課程資料夾放好幾場講座、共用一個 `frames/` 是常態，這時請傳 `<stem>.json`
或 `<stem>.frames.json`。傳 `<stem>.frames.json` 時快取寫到 `<stem>.frames_ocr.json`，
同層若已有 `<stem>.json` 也會一併寫入 `frame_ocr`；manifest 本身不會被改寫。
`l2n run` 已經知道 stem，會自己依序選 `<stem>.json` -> `<stem>.frames.json` ->
只取 `frames/<stem>-*` 的影格，不會因為旁邊有別場講座而 exit 2。
傳入的 JSON 如果不是物件（例如把 manifest 清單改名成 `x.json`），會 exit 2 並說明。

`l2n run` 的 OCR 在正式 JSON 寫出之前就跑完，文字先只在快取裡；之後
`l2n scaffold <stem>.srt` 建骨架時會自動把 `<stem>.frames_ocr.json` 併進每段的
`frame_ocr`，不必再手動補跑 `l2n ocr <stem>.json`。

### OCR 文字住在兩個地方

| 檔案 | 是什麼 | 擴寫時讀哪一個 |
| ---- | ---- | ---- |
| `<stem>.frames_ocr.json` | `l2n ocr` 的**快取**（鍵是影格路徑，用大小＋mtime 判斷要不要重跑） | **不讀** |
| `<stem>.json` 每段的 `frame_ocr` | 快取**併進正式 JSON** 之後的結果，已經按段落分好 | **讀這個** |

快取只是為了重跑時不必再付一次 OCR 的錢；筆記階段從正式 JSON 的 `frame_ocr` 取用。
兩者都只用來決定**要不要打開那一張影格**——辨識結果會把字讀錯，
**OCR 文字不可以抄進筆記**。這是 HARD RULE 3。

## 來源優先序

官方講義 > 你用眼睛看過的影格 > ASR 逐字稿 > OCR 文字。

有官方講義（PDF／PPT）時，它就是主力來源，影格只用來對齊時間點。

## 骨架筆記

```bash
l2n render <stem>.json
l2n render <stem>.json --style faithful
l2n render <stem>.json --expand-prompt
```

`--style faithful|concise` 決定引用密度（不給就取 profile 的設定）。
`--expand-prompt` 只印出擴寫指令包後結束，不寫檔。

`render` 產出的是**骨架**：frontmatter 由 profile 的模板決定、章節順序固定、
內容留空待擴寫。不要手寫 frontmatter，也不要改章節順序。

### schema 2.1 文件的骨架

- **時間碼連結**：給了影片所在資料夾的網址（`--media-base-url <網址>`，或環境變數
  `L2N_MEDIA_BASE_URL`、overlay `outputs.toml` 的 `[note] media_base_url`），每段時間、
  有 `t` 的條列與原話、能明確對到某一段的 Summary 重點，都變成
  `[▶ mm:ss](<網址>/<影片>#t=秒)`，點了從那一秒播放。沒給網址就照舊印純時間。網址只放在
  使用者自己的設定裡，不要寫進 repo 或 profile。`check note` 把連結讀成它的時間標籤，
  有沒有連結驗收結果相同。
- **`## 題目`**：每段的 `question` 依序列出，答案已填、附「講解處」連結；不足 8 題時接著
  用跨段的 `questions_zh` 補，再不夠才用草稿題（帶 ai-draft 標記），並保證有一題 `[推論]`。
- **`## 分流`**：放在 `# Note (layer 1-3)` 之前：結論、必看段（附連結）、省下的分鐘數、
  理由，以及每段的判斷表。
- **舊版筆記（早期 v4.md）**：`l2n render <stem>.json --upgrade` 補上缺的必要章節（講者骨架、
  References 與錯字對照表、題目、學習驗證）、`## 分流` 與風格標記，放在規定的位置，既有內容
  一字不動，舊檔留 `.bak`。沒標過風格的舊筆記標 concise（它不是照 faithful 每段一句引文寫的）；
  要照 faithful 驗收就加 `--style faithful`。補進來的章節帶 `<!-- ai-draft -->`，擴寫完再跑
  `check note`。順序錯的章節只回報不搬動；早期 `Q:`／`A:` 題目不改寫，R9 會提醒改成編號加摺疊答案。
- **筆記不放在講座資料夾（vault）**：`render --embed-names`（或 overlay `[note] embed_names = true`）
  讓嵌圖只寫檔名 `![[<stem>-HHMM.png]]`，Obsidian 會在整個 vault 依檔名找；影格要另外複製進 vault。
- **JSON 改了以後**：`l2n render <stem>.json --refresh` 只替換 Summary 重點、`## 分流`、References
  這三塊（骨架裡用 `<!-- l2n:begin ... -->` 標記），擴寫內容不動，舊檔留 `.bak`。不要用 `--force`，
  那會把整份擴寫蓋回骨架。
- **`已知` 段**：本文只有一行「已知，不重寫：見 [[筆記]]」。擴寫時**不要**補寫內容；
  R10 不會把它算成未擴寫。

## 驗收

```bash
l2n check frames <stem>.json
l2n check json <stem>.json
```

`check frames` 會確認 JSON 裡每一個影格路徑在磁碟上真的存在、時間落在所屬段落內。

## 不歸這裡管

- 骨架怎麼擴寫成完整筆記 -> [note-writing.md](note-writing.md)
- viewer、章節檔、課程首頁 -> [outputs-and-batch.md](outputs-and-batch.md)
- frontmatter 模板從哪裡來 -> [profiles-and-overlay.md](profiles-and-overlay.md)
