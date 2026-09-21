# generic profile

The default, and the bottom layer of the configuration stack: every key not set
by a higher layer is answered from here. Aimed at finance, productivity and
book-style lectures.

| File | What it sets |
|---|---|
| `outputs.toml` | `note.style = "concise"`, `pbf = false`, `hub = true`, `viewer = true`, `guideline.transcript_paste = "warning"`. |
| `note.frontmatter.yaml` | Four fields only: title, date, source, tags. |
| `note.template.md` | The six mandatory sections, in the mandated order. |
| `corrections.json` | General AI and software vocabulary the local ASR engines mishear, written for this project (`lecture2notes-generic`). |
| `privacy.toml` | Identifiers that are identifiers by construction: national ID, labelled chart number, labelled phone number, e-mail address. |

Two deliberate omissions:

- `pbf = false`. A `.pbf` beside a video changes what PotPlayer does with that
  video. The public default states the off answer rather than leaving it to an
  absent key.
- No personal-name patterns in `privacy.toml`. A public default that guesses at
  names produces false positives on ordinary words; the names of one course's
  attendees belong in that person's overlay.
