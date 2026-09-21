# lecture2notes E2E Test Fixture — CopyrightX Lecture 12.1 (clip)

**Title:** William Fisher, CopyrightX, Lecture 12.1, "Remedies: Equitable Relief" (25s excerpt)
**Speaker:** William Fisher (Harvard Law School, CopyrightX / Berkman Klein Center for Internet & Society)

## Source

- Commons page: https://commons.wikimedia.org/wiki/File:William_Fisher_CopyrightX_Lecture_12_1.webm
- Direct file URL: https://upload.wikimedia.org/wikipedia/commons/8/8b/William_Fisher_CopyrightX_Lecture_12_1.webm
- Upstream filename: `William_Fisher_CopyrightX_Lecture_12_1.webm`
- Original YouTube source (per Commons page): "William Fisher, CopyrightX, Lecture 12.1, Remedies: Equitable Relief", https://www.youtube.com/watch?v=kYUloapzo5U

## License

**CC BY 3.0 Unported** — https://creativecommons.org/licenses/by/3.0/

License statement as it appears on the Commons file page (re-checked on download date via WebFetch):

> "This file is licensed under the Creative Commons Attribution 3.0 Unported license. You are free: to share – to copy, distribute and transmit the work; to remix – to adapt the work. Under the following conditions: attribution – You must give appropriate credit, provide a link to the license, and indicate if changes were made."

Attribution requirement stated on the page:

> "You must provide a link (URL) to the original file and the authorship information if available."

Author / uploader field on the Commons page: **BerkmanCenter**

Additional note on the page: the lecture was prepared for a Harvard Law School course on Copyright Law and the CopyrightX course offered under the auspices of HarvardX, with terms also available at http://copyx.org/permission.

**This clip is redistributed under CC BY 3.0; attribution above. It is used only as a test fixture.**

## Download

- Download date: 2026-09-21
- Original file size: 39,886,070 bytes (≈38.0 MB)
- Original file SHA-256: `097b014ad159c6a7464aac7244506db1ae10206dcba2847e473bb020e18329cb`

## Crop / Transcode

- Crop window: `00:00:00`–`00:00:25` (`-ss 0 -t 25`)
- ffmpeg parameters: `-c:v libx264 -crf 28 -preset slow -vf scale=640:-2 -c:a aac -b:a 64k -movflags +faststart`
- Output file: `copyrightx-12-1-clip.mp4`

## Output clip

- File: `tests/fixtures/media/copyrightx-12-1-clip.mp4`
- Size: 209,674 bytes (≈205 KB) — well under the 3 MB target, no need to raise crf/lower scale
- Duration (ffprobe): 25.025 s (within 25±0.5 s)
- Streams (ffprobe): 1 video (h264, 640x360), 1 audio (aac) — confirmed present
- Cropped file SHA-256: `4efd6eb1f07ac23dce1c3f3255a43fd645511351ea1de28f2a7ba8ecc55bd565`

## Scene-change check

Extracted frames at 0s / 8s / 14s / 20s (`probe-0s.png`, `probe-8s.png`, `probe-14s.png`, `probe-20s.png` — working files, deliberately not committed: this directory holds one video and this README, nothing else) plus extra spot checks at 9–12s and 15–18s to pinpoint transitions:

- 0s: black frame (leading black)
- 8–10s: white "credits" card (names Fisher, HarvardX team, copyx.org)
- **~10–11s: cut to black "LECTURE 12: PART I" title card** — main confirmed scene change within the 25s window
- ~15–16s: second cut, title card → speaker (William Fisher) on dark background, talking to camera
- 20s: speaker mid-lecture on dark background

Confirmed: within the 25-second clip there is at least one clean, unambiguous scene cut, at approximately **10–11 seconds** (white credits card → black title card), with a second cut around **15–16 seconds** (title card → speaker) also present.

---

此片段依 CC BY 3.0 授權（William Fisher / Harvard CopyrightX，上傳者 BerkmanCenter）再散布，僅作為 lecture2notes 的測試 fixture 使用，並附上述具名資訊。

## Why this clip is in the repository

`tests/test_e2e_fixture.py` needs one real recording with real speech, a real
audio track and a real scene cut to run the pipeline end to end; a synthesised
video cannot exercise ASR. It is kept at 205 KB and it is the **only** video
file the repository is allowed to contain (`.gitignore` names it explicitly
rather than allowing a wildcard). `tests/test_fixture_media.py` re-checks the
sha256 above and the licence wording on every test run, so the file cannot be
swapped for a differently-licensed recording without a test failure.
