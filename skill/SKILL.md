---
name: lecture2notes
description: 把講座影片整理成逐字稿、分段、投影片影格與可追溯的筆記，並產出時間同步 viewer 與課程首頁；transcription、segmentation、frames、notes、viewer、course hub 六類工作共用這一份路由。
---

# lecture2notes

把使用者指定的影片或課程資料夾，做到他要的那一段為止。只做被要求的產物；已有可用的
SRT／JSON 就從缺少的階段接續，不重跑已完成的階段。

所有機械階段都由 `l2n` 子命令完成，不要在對話裡自己重做一次。旗標一律放在子命令之後
（`l2n frames video.mp4 --mode scene`），不確定時先跑 `l2n <子命令> --help`。

## 路由表

| 本次工作 | 只讀這一份 |
|---|---|
| 轉錄成字幕（`l2n transcribe`）、套用錯字對照表、校正官方字幕時間偏移（`l2n calibrate-subs`） | [references/transcription.md](references/transcription.md) |
| 已有字幕，要切分段、建立或升級正規 JSON v2（`l2n scaffold`、`l2n migrate`） | [references/segmentation.md](references/segmentation.md) |
| 抓投影片影格（`l2n frames`）、對影格做 OCR（`l2n ocr`）、產骨架筆記（`l2n render`） | [references/frames-and-notes.md](references/frames-and-notes.md) |
| 把骨架筆記擴寫成完整筆記 | [references/note-writing.md](references/note-writing.md) |
| 產 viewer（`l2n viewer`）、PotPlayer 章節檔（`l2n pbf`）、課程首頁（`l2n hub`）、整份搬到輸出目錄（`l2n publish`）、批次整課，以及用 `l2n check` 驗收任一階段 | [references/outputs-and-batch.md](references/outputs-and-batch.md) |
| 選 profile、疊 overlay、改詞庫或輸出設定（`l2n profile`） | [references/profiles-and-overlay.md](references/profiles-and-overlay.md) |

只要字幕、不要筆記時，用 whisper-srt-zh；純文字筆記整理或新知查核，用 obsidian-v4-cleanup。

## HARD RULES

1. **轉錄前確認語言，沒有預設值。** `l2n transcribe --lang` 必填；使用者已指定就沿用，
   沒指定就問。猜錯時 ASR 不會報錯，而是把帶口音的英文幻覺成一份通順的中文逐字稿，
   讀起來完全正常，一路污染到分段 JSON 與筆記才會被發現。
2. **逐字稿不自動改寫。** 用對照表取代錯字時，必定留下 `*.raw.srt` 與
   `*.corrections.json`；語境層級的校正交給人或 LLM 判斷，不做整批自動套用。
3. **來源優先序：官方講義 > 人眼看過的投影片影格 > ASR 逐字稿 > OCR 文字。**
   ASR 會聽錯專有名詞、數字甚至因果關係；OCR 會把「分類與追蹤」讀成「分類興追蹦」。
   OCR 只用來決定「要不要打開那張影格」，**不可以抄進筆記**。
4. **抓圖階段完成後才做相依階段。** `l2n frames` 可以在背景跑，但要持續回報進度、
   等到 exit code、確認影格已併入 JSON 才往下走；不可以啟動後就當作完成。
5. **院內或病患相關素材一律本機轉錄**，不得送雲端 ASR；不確定就當作是。本機 ASR 不等於
   後續 LLM 擴寫也獲准接收這些內容，不要超出使用者授權的資料處理範圍。
6. **介面文字用繁體中文台灣用語，專有名詞保留英文原文**（Lung-RADS、BI-RADS、
   forced aligner 不翻）。程式碼與註解用英文；主控台輸出只用 ASCII 標記。

## 完成條件

- 每一個產出的產物都跑對應的 `l2n check`，並把結果回報給使用者：

  ```bash
  l2n check transcribe <stem>.srt     # 轉錄後
  l2n check frames <stem>.json        # 抓圖或 OCR 後
  l2n check json <stem>.json          # JSON 建立或改動後
  l2n check note <stem>.json --note <stem>.v4.md   # 筆記擴寫後
  ```

  exit code：0 全過、1 只有 warning、2 有 error。**有 error 就回頭修，不要交出去。**
- 保留原始字幕與錯字更動紀錄；背景程序追蹤到結束為止。
- 回報時明講哪些內容尚未覆核（`unverified_terms`、待確認的姓名與數字）。
