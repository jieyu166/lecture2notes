<!-- Encoding: UTF-8 (no BOM). Windows PowerShell 5.1: Get-Content -Encoding UTF8 <file> -->

# 參考：分段與正規 JSON（schema 2.1）

任務是「把字幕切成導航章節」或「建立／修正正規 JSON」時，讀這一份。

## 誰做什麼

| 工作 | 誰做 |
|---|---|
| 切音訊、跑 ASR、寫 SRT、抓影格、OCR、算 hash、渲染 HTML | `l2n` 子命令 |
| **段落邊界、段落標題、段落摘要、四條 takeaways** | **語言模型（你）** |
| 驗收 | `l2n check json` |

段落的語意內容只有語言模型做得出來，所以它由你產生，**寫成正規 JSON v2**；
其餘每一個機械階段都呼叫對應的 `l2n` 子命令，不要在對話裡用散文重做一次。

## 流程

```bash
l2n condense <stem>.srt                    # 1. 壓成每分鐘一行，一次讀完
l2n scaffold <stem>.srt --segments 6       # 2. 產形狀合法的 draft 骨架
#    3. 你（語言模型）填語意：段落邊界、標題、摘要、條列，並刪掉頂層 "draft"
l2n check json <stem>.json                 # 4. 驗收
```

1. **`l2n condense`**：把 SRT 壓成 `<stem>.condensed.txt`，每 `--window` 秒一行
   （預設 60）。三小時的逐字稿有九成是時間碼，壓完才讀得動。
2. **`l2n scaffold`**：產出 `<stem>.json`，等時間切 `--segments` 段、時間欄位算好、
   已抓到的影格併入、頂層寫上 `"draft": true`，每一個語意欄位填 `<!-- ai-draft -->`
   佔位字串，每段附一行 `transcript_condensed` 指到壓縮逐字稿的行號區間。
   **它只做形狀，不做語意。**
3. **你來填**：依主題轉折移動段界（等時間只是起始格線）、寫標題／摘要／條列，
   把每一個 `<!-- ai-draft -->` 換掉，最後**刪除頂層的 `"draft"`**。
4. **驗收**：`l2n check json`。

`"draft": true` 還在時，`check json` 會印一行
`warn draft ...: draft document: fill every ai-draft field, then remove "draft"`，
並把「內容長度類」規則（`overall_summary_zh` 字數、`takeaways_zh` 條數）降為 warning ——
骨架本來就還沒寫完，為此報 error 只會把真正該修的結構問題蓋掉。
刪掉 `draft` 之後這些規則恢復為 error。

舊版（1.x）與 2.0 文件都用 `l2n migrate <stem>.json` 原地升級到 2.1，不要手動改 `schema_version`。
`check json` 回報 `got '2.1'` 之類的版本錯誤，代表**這台的 `l2n` 太舊**：升級程式，不要改檔案。

## 最小範例

下面這段可以整份貼進 `<stem>.json`，`l2n check json` 會通過（影格檔案要真的存在）。

```json
{
  "schema_version": "2.1",
  "stem": "sample-talk",
  "title": "範例講座標題",
  "duration_sec": 180,
  "source": {
    "video": "sample-talk.mp4",
    "subtitle": {
      "path": "sample-talk.srt",
      "origin": "asr",
      "engine": "breeze_ct2",
      "lang": "zh",
      "offset_model": null
    }
  },
  "profile": "generic",
  "overall_summary_zh": "這場講座先說明為什麼這個主題值得花時間，再建立聽眾需要的前提知識，接著用兩個實例把主要判準講完，最後收束回開場的問題。講者在後半段明確指出哪些結論他自己也沒有把握，那一段照錄不改寫。全片約三分鐘，分成兩個段落，第二段沿用第一段的投影片。",
  "takeaways_zh": [
    "全片重點 1",
    "全片重點 2",
    "全片重點 3",
    "全片重點 4",
    "全片重點 5",
    "全片重點 6"
  ],
  "segments": [
    {
      "index": 1,
      "start_time": "00:00:00",
      "end_time": "00:01:30",
      "start_sec": 0,
      "end_sec": 90,
      "title": "這一段在講什麼",
      "summary_zh": "本段摘要，兩三句。",
      "bullets_zh": [
        {"text": "重點 1", "t": 5, "kind": "synthesis"},
        {"text": "重點 2", "t": null, "kind": "synthesis"},
        {"text": "講者原話重點", "t": 30, "kind": "quote"},
        {"text": "重點 4", "t": null, "kind": "synthesis"}
      ],
      "quotes_zh": [{"text": "講者原話", "t": 10}],
      "frame": "frames/sample-talk-0001.png",
      "frames": ["frames/sample-talk-0001.png"],
      "frame_ocr": [{"frame": "frames/sample-talk-0001.png", "text": "投影片文字"}],
      "editorial_notes_zh": []
    },
    {
      "index": 2,
      "start_time": "00:01:30",
      "end_time": "00:03:00",
      "start_sec": 90,
      "end_sec": 180,
      "title": "這一段沒有換投影片",
      "summary_zh": "本段摘要，兩三句。",
      "bullets_zh": [
        {"text": "重點 1", "t": 100, "kind": "synthesis"},
        {"text": "重點 2", "t": null, "kind": "synthesis"}
      ],
      "quotes_zh": [],
      "frame": "frames/sample-talk-0001.png",
      "frames": [],
      "frame_ocr": [],
      "editorial_notes_zh": []
    }
  ],
  "corrections": [],
  "unverified_terms": []
}
```

欄位規則（與 `l2n check json` 一致，不是另一套說法）：

| 欄位 | 規則 |
| ---- | ---- |
| `overall_summary_zh` | 100 至 500 字 |
| `takeaways_zh`（頂層） | 6 至 12 條字串 |
| `segments[].bullets_zh` | **物件**陣列，每個物件 `text`／`t`／`kind`；`kind` 只能是 `synthesis` 或 `quote`。條數至少 2（少於 2 會報 warning），目標 4 條 |
| `segments[].frame` | 必填鍵，字串或 `null`；這一段要顯示的那一張 |
| `segments[].frames` | 字串陣列，可以是空陣列（見下） |
| `start_sec` / `end_sec` | 單調遞增、前一段的 `end_sec` 等於下一段的 `start_sec`，最後一段的 `end_sec` 等於 `floor(duration_sec)` |
| `start_time` / `end_time` | `HH:MM:SS`，必須與對應的 `_sec` 相符 |
| `t` | 該條在影片中的秒數，找不到就寫 `null`，**不要亂填** |

`quotes_zh` 是講者原話（附時間），`kind: "synthesis"` 表示是你整理過的話。

## 寫 takeaways、摘要與條列時就要對的三件事

`takeaways_zh` 會被筆記的 Summary **逐字引用**（規範 7.1），`summary_zh` 與 `bullets_zh` 是每一段的草稿。
錯在這裡，就會錯在筆記裡，而筆記那一層不准改寫它們——所以在 JSON 階段就要對：

1. **語氣詞照講者**：講者說「通常／可能／大多／少數／我覺得」，就寫「通常／可能／大多／少數」，
   不改成「一定／只有／絕不」；「約一成」這類數量詞不可漏（規範 2.5）。
2. **縮寫展開看讀者層級**：`note.reader` 是 `self`（預設）時，專有名詞與縮寫保留英文原文；
   不是 `self` 時，首見寫「中文（English, 縮寫）」，分級系統名（BI-RADS 等）例外（規範 0.10）。
3. **台灣譯名**：thrombosis＝血栓（不是栓塞）、arachnoid＝蜘蛛膜、palate＝顎、distant metastasis＝遠處轉移……
   完整對照見規範附錄 C。radiology profile 下，`l2n check json` 會對 `takeaways_zh`、各段 `summary_zh`、
   `bullets_zh[].text` 跑同一份清單（warning `forbidden_term`）。

## 影格數不決定段數

**段數由主題轉折決定，不由抓到幾張圖決定。** 一個區間裡沒有換投影片時，
沿用前一張：`frame` 填上一段那一張，`frames` 留空陣列。這是合法的，
`l2n check json` 不會報錯（上面的範例第 2 段就是這樣寫的）。

scene 模式抓到的張數少於你預計的段數時，**改用 interval**，不要為了湊圖去改分段：

```bash
l2n frames <video> --mode interval --every 45
```

## 分段怎麼切

依主題轉折切，不是依固定長度。一段通常 3 到 10 分鐘；投影片換頁是線索不是規則。
標題寫「這一段在講什麼」，不是「第三部分」。

## 跨段題目 `questions_zh`

頂層選填欄位。寫得出來就寫，因為 `l2n render` 會拿它當題目節的草稿；沒有這個欄位時骨架自己從段落標題生，品質差一截。

```json
"questions_zh": [
  {"text": "若某個案例具備 A 但缺少 B，這篇的結論還成立嗎？", "segments": [1, 3]},
  {"text": "第 2 段的判準為什麼不能直接套到第 4 段的情境？", "segments": [2, 4]}
]
```

- `text`：題目本身，非空字串。
- `segments`：這題要跨過的段落，填 `segments[].index`（1 起算），至少一個。

**一題只掛一段就失去意義**：翻到那一節就答得出來，那是重讀不是回看。出題請跨段（交錯），
並至少寫一題問邊界的推論題（「若有 A 但沒有 B，結論還成立嗎」），不要寫「X 的定義是什麼」。

## schema 2.1 的四個選填欄位

全部選填；舊文件沒有這些欄位照常有效，viewer 也照常運作。寫了就要對，`l2n check json` 會驗。

```bash
l2n lines <stem>.json                        # 1. 把字幕逐句寫進 lines（機械，不要手寫）
l2n triage-prompt <stem>.json --vault <vault> # 2. 印分流交辦書（只在本機讀 vault）
#    3. 你（語言模型）寫每段 question、triage 與頂層 triage_overall
l2n check json <stem>.json                   # 4. 驗收
```

| 欄位 | 形狀與規則 |
| ---- | ---- |
| `lines` | `[{"t": 秒, "end": 秒, "text": "...", "zh": "..."}]`，依 `t` 排序；`zh` 選填。由 `l2n lines` 產生（重複與捲動字幕已合併），`--force` 重建時保留未變動句的 `zh`。結尾早於最後一段開頭是 error（字幕被截斷）；頭尾空白超過 60 秒是 warning；字幕改過而 `lines` 沒重建是 warning `lines_stale` |
| `segments[].question` | `{"q": "...", "a": "...", "answer_sec": 秒}`，一段一題。答案只能出自這一段講者說的話；`answer_sec` 是講者講解答案的地方，必須落在本段範圍內，附近 5 秒內要有字幕句。有一段寫了就每段都要寫 |
| `segments[].triage` | `{"novelty": "新知"\|"已知"\|"複習", "reason": "...", "related_notes": ["[[筆記]]"]}`。`已知` 必須附上涵蓋它的筆記連結；候選來自 `triage-prompt`，**打開筆記確認內容真的涵蓋**才算，同名不算 |
| `triage_overall` | `{"verdict": "全看"\|"只看新知段"\|"跳過", "must_watch": [段號], "est_minutes": 分鐘, "reason": "..."}`。`est_minutes` 是必看段時長加總。`reason` 要寫出哪幾段必看（「第 N 段」，跳過時免）、省下或需要幾分鐘（全看時免）、為什麼；泛泛的「內容實用」會被 `triage_vague` 擋下 |

```json
"triage_overall": {
  "verdict": "只看新知段", "must_watch": [2], "est_minutes": 1,
  "reason": "只需看第 2 段（共 1 分鐘，省下 2 分鐘）：第 2 段的判準 vault 沒有，第 1 段已由筆記涵蓋。"
}
```

不得編造：題目、答案、分流理由都只能根據字幕與你打開的筆記；不確定的術語保留原文並加註「（可能為XXX）」。

## 四段弧檢查（寫 `overall_summary_zh` 時做）

一場講座的完整結構是**四段弧**：

| 段 | 這一段在做什麼 |
| ---- | ---- |
| 坡道 | 開頭 5 至 8%：為什麼值得講、前人卡在哪 |
| 背景 | 建立聽眾需要的前提 |
| 正文 | 主要的判準、數值、做法 |
| 昇華 | 收束回開場的問題，給一個帶得走的結論 |

`overall_summary_zh` 寫完之後，逐段指認一次：四段各對應到哪幾個 `segments`。

**缺哪一段就照實寫，不要補。** 最常缺的是昇華，通常表示結尾被 Q&A 吃掉了——那是事實，
寫進**頂層**的 `editorial_notes_zh`，不要自己補一段結論。

頂層 `editorial_notes_zh` 是選填的字串陣列，放關於整場講座的編輯備註（四段弧的指認結果就寫這裡）；
`l2n check json` 接受它，`l2n render` 會把它印在 Summary 的摺疊 callout 裡。各段自己的
`segments[].editorial_notes_zh` 照舊是那一段的備註。

```json
"editorial_notes_zh": ["四段弧：坡道 段 1、背景 段 2、正文 段 3-5；缺昇華，結尾被 Q&A 吃掉"]
```

## 驗收

```bash
l2n check json <stem>.json
```

exit code 0 才算完成。時間欄位一旦寫定，內容階段不得更動——`l2n check` 會比對時間簽章。

## 不歸這裡管

- 影格怎麼抓、骨架筆記怎麼產 -> [frames-and-notes.md](frames-and-notes.md)
- 筆記怎麼擴寫 -> [note-writing.md](note-writing.md)
