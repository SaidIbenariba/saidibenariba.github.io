# saidibenariba.github.io

Personal site and blog, built with [Quarto](https://quarto.org) and published to GitHub Pages.

**Posts are written in Obsidian** (`~/Obsidian/04_Publish/`) and synced here by
`scripts/sync_obsidian.py`. Don't edit `posts/<slug>/index.qmd` for synced posts by hand:
the next sync overwrites them. Edit the Obsidian note instead.

## Commands

| Command | What it does |
|---|---|
| `make check` | Dry run: lists which notes would be published, with warnings (missing images, etc.) |
| `make preview` | Sync, then live preview at http://localhost:4200 (auto-reloads) |
| `make publish` | Sync, render, mark notes `published` in Obsidian, commit and push → GitHub Actions deploys |

## Layout

```
_quarto.yml          site config (navbar, theme, bibliography)
index.qmd            About / home (latest posts listed automatically)
blog.qmd             post listing + RSS (blog.xml)
projects.qmd  publications.qmd  cv.qmd
posts/<slug>/        one folder per post (synced from Obsidian; has .from-obsidian marker)
posts/_metadata.yml  defaults for every post (author, license, citation)
references.bib       citations used with [@key]
scripts/             sync_obsidian.py + config.yml
.github/workflows/   render + deploy to gh-pages on every push to main
```

Posts written directly here (folders **without** `.from-obsidian`) are never touched by the sync.

## Requirements

`quarto`, `git`, and `uv` (or `python3` + `pyyaml`).
