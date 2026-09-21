# overlay-minimal

One valid instance of each of the five overlay-able files, with placeholder
content. Copy the whole folder's *contents* into `~/.lecture2notes/`:

```bash
# Windows PowerShell
Copy-Item examples\overlay-minimal\* $HOME\.lecture2notes\ -Recurse

# macOS / Linux
mkdir -p ~/.lecture2notes && cp examples/overlay-minimal/*.{yaml,md,json,toml} ~/.lecture2notes/
```

Then check that it took:

```bash
l2n profile show
```

Every key these files define is now reported with source layer `user`. Delete a
file to hand that question back to the profile below; a layer that lacks a file
contributes nothing.

This README is documentation and is not itself an overlay file -- `l2n` ignores
anything in an overlay directory that is not one of the five names below.

| File | Merge rule |
|---|---|
| `note.frontmatter.yaml` | replaces the lower layer whole |
| `note.template.md` | replaces the lower layer whole |
| `outputs.toml` | merges key by key, this layer wins |
| `privacy.toml` | merges key by key, this layer wins |
| `corrections.json` | merges key by key, this layer wins |

For a project-only overlay, put the same files in `./.lecture2notes/` inside the
course folder instead; that layer outranks this one.
