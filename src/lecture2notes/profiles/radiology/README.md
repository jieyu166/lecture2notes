# radiology profile

Built in, never on by default. Select it with `l2n --profile radiology ...`, or
by putting `profile = "radiology"` in an overlay's `outputs.toml`.

What it adds on top of `generic`:

| File | What it changes |
|---|---|
| `note.template.md` | Adds a 閱片 section with `> [!reading-case]`, `> [!reading-pearl]` and `> [!differential]` callouts, between the Note section and References. The six mandatory sections keep their mandated order. |
| `corrections.json` | Radiology terminology the local ASR engines mishear (`Lung-RADS`, `BI-RADS`, `tomosynthesis`, `顯影劑`, ...). Section `radiology`, which `engines/corrections.py` applies automatically. |
| `privacy.toml` | The generic patterns plus three that only matter clinically: a bare eight-digit chart number, a national ID, and a name following 病患／病人. |

What it does **not** ship, on purpose:

- No `outputs.toml`. Nothing about reading radiology changes whether a `.pbf`
  or a hub should be written, and a layer that lacks a file contributes
  nothing -- so the generic answers stand.
- No `note.frontmatter.yaml`. The four generic fields are the four fields, and
  a vault's own frontmatter convention belongs in that person's overlay, not in
  a profile shipped to everybody.

Provenance of the corrections table: every pair was selected as radiology
terminology and carries the source label `lecture2notes-radiology`. Nothing
naming a person, a hospital or a department's paperwork was carried over.
