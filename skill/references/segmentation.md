# 參考：分段與正規 JSON v2

任務是「把字幕切成導航章節」或「建立／修正正規 JSON」時，讀這一份。

## 誰做什麼

| 工作 | 誰做 |
|---|---|
| 切音訊、跑 ASR、寫 SRT、抓影格、OCR、算 hash、渲染 HTML | `l2n` 子命令 |
| **段落邊界、段落標題、段落摘要、四條 takeaways** | **語言模型（你）** |
| 驗收 | `l2n check json` |

段落的語意內容只有語言模型做得出來，所以它由你產生，**寫成正規 JSON v2**；
其餘每一個機械階段都呼叫對應的 `l2n` 子命令，不要在對話裡用散文重做一次。

## 產骨架

```bash
l2n scaffold <subs.srt>
```

此子命令目前仍是 stub（會印 `[scaffold] not implemented yet` 並以 exit code 4 結束）。
在它補上之前，**依下面的 schema 手寫 JSON**，再用 `l2n check json` 驗。
舊版（1.x）文件用 `l2n migrate <stem>.json` 原地升級到 v2，不要手動改 `schema_version`。

## 最小範例

```json
{
  "schema_version": "2.0",
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
  "overall_summary_zh": "全片在講什麼，兩三句。",
  "takeaways_zh": ["全片重點 1", "全片重點 2", "全片重點 3", "全片重點 4"],
  "segments": [
    {
      "index": 1,
      "start_time": "00:00:00",
      "end_time": "00:01:00",
      "start_sec": 0,
      "end_sec": 60,
      "title": "這一段在講什麼",
      "summary_zh": "本段摘要，兩三句。",
      "takeaways_zh": [
        {"text": "重點 1", "t": 5, "kind": "synthesis"},
        {"text": "重點 2", "t": null, "kind": "synthesis"},
        {"text": "重點 3", "t": 30, "kind": "synthesis"},
        {"text": "重點 4", "t": null, "kind": "synthesis"}
      ],
      "quotes_zh": [{"text": "講者原話", "t": 10}],
      "frames": ["frames/sample-talk-0001.png"],
      "frame_ocr": [{"frame": "frames/sample-talk-0001.png", "text": "投影片文字"}],
      "editorial_notes_zh": []
    }
  ],
  "corrections": [],
  "unverified_terms": []
}
```

規則：每段 `takeaways_zh` 恰好 4 條；每段掛 1 至 4 張影格；`start_sec` / `end_sec`
單調遞增且不重疊；`t` 是該條在影片中的秒數，找不到就寫 `null`，**不要亂填**。
`quotes_zh` 是講者原話（附時間），`kind: "synthesis"` 表示是你整理過的話。

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
寫進 `editorial_notes_zh`，不要自己補一段結論。

## 驗收

```bash
l2n check json <stem>.json
```

exit code 0 才算完成。時間欄位一旦寫定，內容階段不得更動——`l2n check` 會比對時間簽章。

## 不歸這裡管

- 影格怎麼抓、骨架筆記怎麼產 -> [frames-and-notes.md](frames-and-notes.md)
- 筆記怎麼擴寫 -> [note-writing.md](note-writing.md)
