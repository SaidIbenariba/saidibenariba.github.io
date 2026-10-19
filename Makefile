# Obsidian → Quarto → GitHub Pages
# Requires: quarto, uv (or python3 + pyyaml), git
PY := $(shell command -v uv >/dev/null 2>&1 && echo "uv run" || echo "python3")

.PHONY: help sync check preview publish

help:
	@echo "make sync     - copy ready notes from Obsidian into posts/"
	@echo "make check    - dry run: show what would be published + warnings"
	@echo "make preview  - sync, then live preview at http://localhost:4200"
	@echo "make publish  - sync, render, mark notes published, commit and push"

sync:
	$(PY) scripts/sync_obsidian.py

check:
	$(PY) scripts/sync_obsidian.py --dry-run

preview: sync
	quarto preview --port 4200

publish:
	$(PY) scripts/sync_obsidian.py --mark-published
	quarto render
	git add -A
	git commit -m "publish: $$(date +%F)" || echo "Nothing new to commit"
	git push
	@echo "Pushed. GitHub Actions will deploy in ~1-2 min."
