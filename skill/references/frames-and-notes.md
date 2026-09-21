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

**抓圖沒跑完就不要做下一步。** 背景執行可以，但要回報進度、等 exit code、
確認影格已經併入 JSON。這是 HARD RULE 4。

## OCR

```bash
l2n ocr <stem>.json          # 建議寫法
l2n ocr <frames 資料夾>       # 只有在同層剛好一場講座時才可用
```

旗標：`--min-conf`（低於此信心值直接丟棄）、`--no-s2t`（預設會簡轉繁）。

傳資料夾時，stem 取自**同層唯一**的 `<stem>.json` 或 `<stem>.frames.json`；
找不到或不只一個就 exit 2 並說明，不會自己拿資料夾名當 stem。
（舊行為會寫出 `frames.frames_ocr.json` 這種沒人會再讀的孤兒快取，
而 `<stem>.json` 一個字的 OCR 都拿不到。）

OCR 結果只寫進 JSON 的 `frame_ocr`，作用是**讓你決定要打開哪一張影格**。
辨識結果會把字讀錯，所以 **OCR 文字不可以抄進筆記**。這是 HARD RULE 3。

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
