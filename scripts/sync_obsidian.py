# /// script
# requires-python = ">=3.9"
# dependencies = ["pyyaml>=6"]
# ///
"""Sync publishable Obsidian notes into this Quarto site.

One-way: Obsidian (source of truth) -> posts/<slug>/index.qmd.

A note is published when its frontmatter has `publish: true` and
`status` is `ready` or `published`. Drafts are never copied.

Converts Obsidian-only syntax to Quarto:
  [[Note|alias]]   -> link to the post if Note is published, else plain text
  ![[image.png|400]] -> image copied next to the post, width kept
  > [!tip] Title   -> ::: {.callout-tip title="Title"}
  ==highlight==    -> [highlight]{.mark}
  [[@citekey]]     -> [@citekey]
  %% comment %%, Up:: / Tags:: lines, ^block-ids  -> removed
Code blocks are left untouched.

Usage:
  uv run scripts/sync_obsidian.py               # sync
  uv run scripts/sync_obsidian.py --dry-run     # show what would happen
  uv run scripts/sync_obsidian.py --mark-published   # also set status/url in Obsidian
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import shutil
import sys
import unicodedata
from pathlib import Path

import yaml

SITE = Path(__file__).resolve().parent.parent
CONFIG = SITE / "scripts" / "config.yml"
MARKER = ".from-obsidian"  # marks post folders managed by this script

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".avif"}
SKIP_DIRS = {".obsidian", ".git", ".npm", ".trash", ".claude", "node_modules", ".pandoc"}

CALLOUT_MAP = {
    "note": "note", "info": "note", "abstract": "note", "summary": "note", "tldr": "note",
    "todo": "note", "example": "note", "quote": "note", "cite": "note",
    "tip": "tip", "hint": "tip", "success": "tip", "check": "tip", "done": "tip",
    "important": "important",
    "warning": "warning", "attention": "warning", "question": "warning", "help": "warning", "faq": "warning",
    "caution": "caution", "danger": "caution", "error": "caution", "failure": "caution",
    "fail": "caution", "missing": "caution", "bug": "caution",
}
# Frontmatter keys passed through to Quarto
PASS_KEYS = {"title", "subtitle", "description", "date", "image", "categories", "draft",
             "bibliography", "toc", "format", "jupyter", "execute", "lang", "author", "aliases"}
OBSIDIAN_FIELD_LINE = re.compile(r"^(Up|Tags|Status|Priority|Links|Source|Date|Method|Related)::.*$\n?", re.M)


# ---------------------------------------------------------------- helpers
def load_config() -> dict:
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    cfg["vault"] = Path(cfg["vault"]).expanduser()
    return cfg


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[\s_-]+", "-", text)[:80] or "post"


def split_frontmatter(raw: str) -> tuple[dict, str]:
    if raw.startswith("---"):
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n?", raw, re.S)
        if m:
            try:
                fm = yaml.safe_load(m.group(1)) or {}
            except yaml.YAMLError as e:
                print(f"  ! bad YAML frontmatter: {e}", file=sys.stderr)
                fm = {}
            return (fm if isinstance(fm, dict) else {}), raw[m.end():]
    return {}, raw


def is_publishable(fm: dict) -> bool:
    return bool(fm.get("publish")) and str(fm.get("status", "")).lower() in {"ready", "published"}


def index_vault_images(vault: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for p in vault.rglob("*"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.is_file() and p.suffix.lower() in IMAGE_EXT:
            found.setdefault(p.name, p)
    return found


def safe_filename(name: str) -> str:
    stem, ext = Path(name).stem, Path(name).suffix.lower()
    return f"{slugify(stem)}{ext}"


# ---------------------------------------------------------------- converters
def convert_callouts(text: str) -> str:
    out, lines, i = [], text.split("\n"), 0
    head = re.compile(r"^>\s*\[!(\w+)\]([+-]?)\s*(.*)$")
    while i < len(lines):
        m = head.match(lines[i])
        if not m:
            out.append(lines[i]); i += 1; continue
        kind = CALLOUT_MAP.get(m.group(1).lower(), "note")
        fold, title = m.group(2), m.group(3).strip()
        attrs = [f".callout-{kind}"]
        if title:
            attrs.append('title="' + title.replace('"', "'") + '"')
        if fold:
            attrs.append(f'collapse="{"true" if fold == "-" else "false"}"')
        body, i = [], i + 1
        while i < len(lines) and lines[i].startswith(">"):
            body.append(re.sub(r"^>\s?", "", lines[i])); i += 1
        out += ["::: {" + " ".join(attrs) + "}", *body, ":::"]
    return "\n".join(out)


def convert_prose(text: str, ctx: dict) -> str:
    # comments, Obsidian-only inline fields, block ids
    text = re.sub(r"%%.*?%%", "", text, flags=re.S)
    text = OBSIDIAN_FIELD_LINE.sub("", text)
    text = re.sub(r"\s\^[A-Za-z0-9-]+$", "", text, flags=re.M)

    # image / note embeds
    def embed(m: re.Match) -> str:
        target, opt = m.group(1).strip().rstrip("\\"), (m.group(2) or "").strip()
        name = target.split("#")[0]
        if Path(name).suffix.lower() in IMAGE_EXT:
            src = ctx["images"].get(Path(name).name)
            if not src:
                ctx["warnings"].append(f"image not found: {name}")
                return ""
            dest = safe_filename(src.name)
            ctx["copy"].append((src, dest))
            width = f"{{width={opt}}}" if opt.isdigit() else ""
            alt = "" if opt.isdigit() else opt
            return f"![{alt}]({dest}){width}"
        ctx["warnings"].append(f"note embed dropped: ![[{target}]]")
        return ""
    text = re.sub(r"!\[\[([^\]|]+)(?:\|([^\]]*))?\]\]", embed, text)

    # citations [[@key]]
    text = re.sub(r"\[\[@([^\]|]+)(?:\|[^\]]*)?\]\]", r"[@\1]", text)

    # wikilinks
    def wikilink(m: re.Match) -> str:
        target, alias = m.group(1).strip().rstrip("\\"), (m.group(2) or "").strip()
        note = target.split("#")[0].strip()
        label = alias or note or target.lstrip("#")
        slug = ctx["published"].get(note.lower())
        if slug:
            return f"[{label}](../{slug}/index.qmd)"
        return label
    text = re.sub(r"\[\[([^\]|]+)(?:\|([^\]]*))?\]\]", wikilink, text)

    # highlights
    text = re.sub(r"==([^=\n]+)==", r"[\1]{.mark}", text)

    # Pandoc needs a blank line before a list; Obsidian doesn't
    text = re.sub(r"(?m)^((?![ \t]*(?:[-*+]|\d+[.)])[ \t])[ \t]*[^\s|#:>].*)\n([ \t]*(?:[-*+]|\d+[.)])[ \t])",
                  r"\1\n\n\2", text)
    return text


def convert_body(body: str, ctx: dict) -> str:
    # protect fenced code blocks
    parts = re.split(r"(^```.*?^```[ \t]*$|^~~~.*?^~~~[ \t]*$)", body, flags=re.M | re.S)
    out = []
    for idx, part in enumerate(parts):
        if idx % 2 == 1:
            out.append(part)
        else:
            out.append(convert_prose(convert_callouts(part), ctx))
    text = "".join(out)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def build_frontmatter(fm: dict, note: Path, cfg: dict) -> dict:
    # Skip empty template fields (e.g. `image:`); Quarto rejects null values
    q = {k: v for k, v in fm.items() if k in PASS_KEYS and v not in (None, "", [])}
    q.setdefault("title", note.stem)
    if "date" not in q:
        q["date"] = dt.date.fromtimestamp(note.stat().st_mtime).isoformat()
    if isinstance(q["date"], (dt.date, dt.datetime)):
        q["date"] = q["date"].isoformat()[:10]
    tags = fm.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in re.split(r"[,\s]+", tags) if t.strip()]
    drop = {t.lower() for t in cfg.get("drop_tags", [])}
    cats = list(q.get("categories") or []) + [str(t).lstrip("#") for t in tags]
    cats = [c for c in dict.fromkeys(cats) if c.lower() not in drop]
    if cats:
        q["categories"] = cats
    return q


def mark_published(note: Path, url: str) -> None:
    raw = note.read_text(encoding="utf-8")
    m = re.match(r"^---\s*\n(.*?)\n---", raw, re.S)
    if not m:
        return
    fm_text = m.group(1)
    new = re.sub(r"(?m)^status:.*$", "status: published", fm_text)
    if re.search(r"(?m)^url:", new):
        new = re.sub(r"(?m)^url:.*$", f"url: {url}", new)
    else:
        new += f"\nurl: {url}"
    if new != fm_text:
        note.write_text(raw[: m.start(1)] + new + raw[m.end(1):], encoding="utf-8")


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--mark-published", action="store_true",
                    help="set status: published and url: in the Obsidian note")
    args = ap.parse_args()

    cfg = load_config()
    vault = cfg["vault"]
    src_dir = vault / cfg.get("publish_dir", "04_Publish")
    if not src_dir.is_dir():
        print(f"Publish folder not found: {src_dir}", file=sys.stderr)
        return 1
    posts_dir = SITE / "posts"
    site_url = cfg.get("site_url", "").rstrip("/")

    notes = []
    for p in sorted(src_dir.rglob("*.md")):
        if p.name.startswith("_") or any(part.startswith("_") for part in p.relative_to(src_dir).parts[:-1]):
            continue  # _templates, _README etc.
        fm, body = split_frontmatter(p.read_text(encoding="utf-8"))
        if is_publishable(fm):
            slug = str(fm.get("slug") or slugify(str(fm.get("title") or p.stem)))
            notes.append((p, fm, body, slug))

    published = {}
    for p, fm, _, slug in notes:
        published[p.stem.lower()] = slug
        if fm.get("title"):
            published[str(fm["title"]).lower()] = slug

    images = index_vault_images(vault)
    kept = set()
    for p, fm, body, slug in notes:
        ctx = {"images": images, "published": published, "copy": [], "warnings": []}
        qfm = build_frontmatter(fm, p, cfg)
        qbody = convert_body(body, ctx)
        if qfm.get("image"):  # cover image for listings / social cards
            src = images.get(Path(str(qfm["image"]).strip("[]!")).name)
            if src:
                qfm["image"] = safe_filename(src.name)
                ctx["copy"].append((src, qfm["image"]))
            else:
                ctx["warnings"].append(f"cover image not found: {qfm['image']}")
                qfm.pop("image")
        out_dir = posts_dir / slug
        kept.add(slug)
        url = f"{site_url}/posts/{slug}/"
        print(f"✓ {p.name}  →  posts/{slug}/")
        for w in ctx["warnings"]:
            print(f"    ! {w}")
        if args.dry_run:
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / MARKER).write_text(str(p.relative_to(vault)), encoding="utf-8")
        header = yaml.safe_dump(qfm, sort_keys=False, allow_unicode=True).strip()
        (out_dir / "index.qmd").write_text(f"---\n{header}\n---\n\n{qbody}", encoding="utf-8")
        for src, dest in ctx["copy"]:
            shutil.copy2(src, out_dir / dest)
        if args.mark_published and site_url:
            mark_published(p, url)

    # prune posts that came from Obsidian but are no longer publishable
    for d in posts_dir.iterdir() if posts_dir.exists() else []:
        if d.is_dir() and (d / MARKER).exists() and d.name not in kept:
            print(f"✗ removing unpublished post: posts/{d.name}/")
            if not args.dry_run:
                shutil.rmtree(d)

    bib = cfg.get("bibtex_source")
    if bib and not args.dry_run:
        bib_path = Path(bib).expanduser()
        if bib_path.exists():
            shutil.copy2(bib_path, SITE / "references.bib")
            print("✓ references.bib updated from Zotero export")
        else:
            print(f"  ! bibtex_source not found: {bib_path}")

    print(f"\n{len(notes)} post(s) synced{' (dry run)' if args.dry_run else ''}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
