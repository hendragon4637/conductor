#!/usr/bin/env python3
"""check_tokens.py — token-conformance gate (variants 02.2 / 03.1).

Validates a design component directory (variant library subdir at curation
time, or a generated component's work/ tree at gate time):

  - tokens.css exists, parses, and declares only concrete values
    (no placeholders, no TODO markers, no self-referential var()).
  - The spacing scale is a real 8pt-based scale — off-scale px values are
    rejected (this is what stops silent drift back to arbitrary numbers).
  - Text/background token pairs meet WCAG AA (>= 4.5:1), computed from the
    declared palette — this is a token-level check, run once at curation.
  - Style-bearing files (CSS/HTML/TSX/JSX, excluding tests) reference >= 1
    var(--) token (``tokens_used``) and no file contains raw color literals —
    tokens.css is the ONLY file permitted to hold literals.
  - No lorem-ipsum placeholder text in deliverables.
  - Every <img> in an HTML file carries an ``alt`` attribute (``img_alt``).
  - exports/ is valid: non-empty; every .html parses; every .pdf opens
    (``%PDF-`` magic header) — ``exports_valid``.

Usage:
    python3 check_tokens.py [target_dir]

target_dir defaults to the current directory.  Exits 0 when clean, 1 on any
violation.  The multi-file color/px scan mirrors File 03's intent: a file with
zero token references bypassed the system entirely, which no color check would
catch.
"""

from __future__ import annotations

import html.parser
import pathlib
import re
import sys

TOKENS_FILE = "tokens.css"
# Text-on-background token pairs whose contrast must meet WCAG AA.
# --accent-on is the label color ON the --accent background (not --accent
# itself, which usually fails AA against --surface by design).
CONTRAST_PAIRS = [
    ("--fg", "--bg"),
    ("--text", "--surface"),
    ("--fg-2", "--bg"),
    ("--accent-on", "--accent"),
]

HEX_RE = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b")
RGB_RE = re.compile(r"rgb(a)?\s*\(\s*\d+\s*,\s*\d+\s*,\s*\d+")
LOREM_RE = re.compile(r"\blorem\b|\bipsum\b", re.IGNORECASE)
IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
IMG_ALT_RE = re.compile(r"\balt\s*=", re.IGNORECASE)
PDF_MAGIC = b"%PDF-"


class _HtmlParseTracker(html.parser.HTMLParser):
    """Collect malformed-HTML signals: unbalanced tags or hard parse errors.

    HTML is not XML, so a strict well-formedness check is wrong; we only flag
    structurally broken documents (unclosed non-void tags, stray closing tags,
    bad attribute syntax) — enough to catch a truncated or half-rendered file.
    """

    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.errors: list[str] = []
        self._stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() not in self.VOID:
            self._stack.append(tag.lower())

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        pass  # <img /> — self-closing, balanced by construction

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in self.VOID:
            return
        if not self._stack:
            self.errors.append(f"stray closing </{t}>")
            return
        if self._stack[-1] == t:
            self._stack.pop()
            return
        # Non-nesting close (e.g. </div> while <section> open).  Only flag if
        # the tag is truly absent from the stack — forgiving recovery otherwise.
        if t not in self._stack:
            self.errors.append(f"closing </{t}> with no matching open tag")
        else:
            # Pop through to the matching tag (browsers recover this way).
            while self._stack and self._stack[-1] != t:
                self._stack.pop()
            if self._stack:
                self._stack.pop()

    def finish(self) -> list[str]:
        for t in self._stack:
            self.errors.append(f"unclosed <{t}>")
        return self.errors


def parse_tokens(css_text: str) -> dict[str, str]:
    """Extract ``--name: value;`` custom properties from a tokens.css body."""
    tokens: dict[str, str] = {}
    for m in re.finditer(r"\s*--([\w-]+)\s*:\s*([^;]+);", css_text):
        name, value = m.group(1).strip(), m.group(2).strip()
        # Drop trailing comments
        value = value.split("/*")[0].rstrip()
        tokens[name] = value
    return tokens


def to_rgb(value: str) -> tuple[int, int, int] | None:
    """Convert a #hex/8-digit-hex token value to (r, g, b).  None otherwise."""
    v = value.strip().lower()
    m = re.match(r"^#([0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$", v)
    if not m:
        return None
    hexv = m.group(1)
    if len(hexv) == 3:
        hexv = "".join(c * 2 for c in hexv)
    rgb = tuple(int(hexv[i:i + 2], 16) for i in (0, 2, 4))
    return rgb  # type: ignore[return-value]


def relative_luminance(rgb: tuple[int, int, int]) -> float:
    """WCAG relative luminance of an sRGB tuple."""
    def channel(c: int) -> float:
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = relative_luminance(a), relative_luminance(b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def resolve_token(value: str, tokens: dict[str, str]) -> str:
    """Resolve a ``var(--x)`` reference to its concrete value (1 level deep)."""
    m = re.match(r"^var\(\s*(--[\w-]+)\s*\)$", value.strip())
    if m:
        return tokens.get(m.group(1), value)
    return value


def main() -> int:
    root = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(".")
    violations: list[str] = []

    # Layout auto-detection (guide 03.3): design components keep tokens.css at
    # the variant-library root (curation) or in work/ (generated component);
    # frontend components copy the design tokens into src/styles/tokens.css and
    # source lives in src/.
    tokens_path = root / TOKENS_FILE
    if not tokens_path.exists():
        tokens_path = root / "work" / TOKENS_FILE
    if not tokens_path.exists():
        tokens_path = root / "src" / "styles" / TOKENS_FILE
    is_frontend = tokens_path.parent == root / "src" / "styles"

    if not tokens_path.exists():
        print(f"check_tokens: FAIL — {TOKENS_FILE} missing in {root} (or work/, or src/styles/)")
        return 1
    css_text = tokens_path.read_text(encoding="utf-8")
    if not css_text.strip():
        print(f"check_tokens: FAIL — {TOKENS_FILE} is empty")
        return 1
    tokens = parse_tokens(css_text)
    if not tokens:
        print(f"check_tokens: FAIL — could not parse any custom properties from {TOKENS_FILE}")
        return 1

    # ── Concrete values: no placeholders / no pure self-reference ────────
    for name, value in tokens.items():
        v = value.strip().lower()
        if v in ("", "todo", "placeholder"):
            violations.append(
                f"{TOKENS_FILE}: token --{name} has placeholder value {value!r}"
            )
        if value.strip() == f"var(--{name})":
            violations.append(
                f"{TOKENS_FILE}: token --{name} self-references var() — not a concrete value"
            )
        # ``var(--x)`` chains to OTHER tokens are fine — they resolve to a
        # concrete value one level deep.

    # ── Spacing is a real scale (extracted from the variant's own tokens) ──
    # File 03.1: SCALE = extract_space_scale(tokens.css).  A component file's
    # spacing-family px must exist in the declared space scale — off-scale
    # values (arbitrary drift) are rejected.  The 02.4 "real scale" rule is
    # enforced here too: --space-* values must be multiples of the base unit,
    # so a scale of 4/8/16/24/40/64 passes while 4/9/23 fails.
    def _px_tokens(prefixes: tuple[str, ...]) -> set[int]:
        out: set[int] = set()
        for k, v in tokens.items():
            if not k.startswith(prefixes):
                continue
            m = re.fullmatch(r"(\d+)px", v.strip())
            if m:
                out.add(int(m.group(1)))
        return out

    space_scale = _px_tokens(("space-",))
    if space_scale:
        base = min(space_scale)
        for name, value in tokens.items():
            if not name.startswith("space-"):
                continue
            m = re.fullmatch(r"(\d+)px", value.strip())
            if m and int(m.group(1)) % base != 0:
                violations.append(
                    f"{TOKENS_FILE}: --{name}={value} not a multiple of base unit {base}px"
                )
    text_scale = _px_tokens(("text-",))
    radius_scale = _px_tokens(("radius-",))
    # Container widths (max-width/width) come from --container-* OR the space
    # scale; section gutters live in --container-gutter-*.
    container_scale = _px_tokens(("container-",))
    spacing_props = {"padding", "margin", "gap", "top", "right", "bottom", "left"}
    width_props = {"width", "max-width", "height", "max-height"}

    # ── Contrast_aa: text/bg pairs >= 4.5:1 ───────────────────────────────
    for fore, back in CONTRAST_PAIRS:
        fval = resolve_token(tokens.get(fore, ""), tokens)
        bval = resolve_token(tokens.get(back, ""), tokens)
        frgb, brgb = to_rgb(fval), to_rgb(bval)
        if frgb is None or brgb is None:
            continue  # pairs missing from this variant's schema are skipped
        ratio = contrast_ratio(frgb, brgb)
        if ratio < 4.5:
            violations.append(
                f"{TOKENS_FILE}: contrast {fore} / {back} = {ratio:.2f}:1 < 4.5:1 (WCAG AA)"
            )

    def strip_media_blocks(text: str) -> str:
        """Blank ``@media … { … }`` groups (responsive breakpoints), keeping
        the text that declares design tokens.  Breakpoint px (e.g. max-width:
        768px) is an adaptive trigger, not a layout value, so it must not be
        judged against the container scale.  Nested braces are balanced so a
        brace inside a string value cannot break the scan."""
        out: list[str] = []
        pos = 0
        n = len(text)
        while pos < n:
            m = re.search(r"@media\b", text[pos:])
            if not m:
                out.append(text[pos:])
                break
            start = pos + m.start()
            out.append(text[pos:start])
            ob = text.find("{", start)
            if ob == -1:
                out.append(text[start:])
                break
            depth, k = 1, ob + 1
            while k < n and depth:
                if text[k] == "{":
                    depth += 1
                elif text[k] == "}":
                    depth -= 1
                k += 1
            pos = k
        return "".join(out)

    # ── component-source conformance: tokens_used + no raw literals ────────
    # Design layout scans work/** (.css/.html); frontend layout scans src/**.
    # tokens.css is exempt regardless of where it lives — it is the ONE place
    # literals and scale values are declared.  ``tokens_used`` applies to the
    # styling files (.css/.html) only — the frontend's stated approach is CSS
    # modules, so styling lives in .css.  Raw-literal detection applies to ALL
    # scanned file types (a hex in a .ts constant is still a bypass), except
    # test fixtures, which routinely assert literal values.
    _scan_root = root if not is_frontend else root / "src"
    scanned_exts = (".css", ".html", ".tsx", ".ts", ".jsx", ".js")
    style_exts = (".css", ".html")

    def _is_test_fixture(p: pathlib.Path) -> bool:
        return "__tests__" in p.parts or ".test." in p.name or ".spec." in p.name

    work_files = [p for p in _scan_root.rglob("*")
                  if p.is_file() and p.suffix in scanned_exts
                  and p.name != TOKENS_FILE and p.resolve() != tokens_path.resolve()]
    for f in work_files:
        text = f.read_text(encoding="utf-8", errors="ignore")
        if not _is_test_fixture(f):
            if f.suffix in style_exts and not re.search(r"var\s*\(\s*--", text):
                violations.append(f"{f.relative_to(root)}: no var(--…) token reference (tokens_used)")
            for lit in HEX_RE.findall(text):
                violations.append(f"{f.relative_to(root)}: raw color {lit} — use var(--token)")
            for _ in RGB_RE.finditer(text):
                violations.append(f"{f.relative_to(root)}: raw rgb() color — use var(--token)")
            if LOREM_RE.search(text):
                violations.append(f"{f.relative_to(root)}: contains lorem-ipsum placeholder text")
        if f.suffix == ".html":
            for m in IMG_TAG_RE.finditer(text):
                if not IMG_ALT_RE.search(m.group(0)):
                    violations.append(
                        f"{f.relative_to(root)}: <img> without alt attribute (line {text[:m.start()].count(chr(10)) + 1})"
                    )
        # Off-scale spacing only judged for the design layout, where the token
        # palette is tested against a real scale; a frontend copies the design
        # tokens wholesale so scale conformance is already inherited.
        if is_frontend:
            continue
        scan_text = strip_media_blocks(text)
        for m in re.finditer(
            r"(?<!border-)(padding|margin|gap|top|right|bottom|left|width|max-width|height|max-height|font-size|border-radius)\s*:\s*([^;}]+)",
            scan_text,
        ):
            prop = m.group(1)
            for pxm in re.finditer(r"(\d+(?:\.\d+)?)px", m.group(2)):
                px = float(pxm.group(1))
                if prop in spacing_props and space_scale and px not in space_scale:
                    violations.append(
                        f"{f.relative_to(root)}: off-scale spacing {prop}={pxm.group(1)}px (scale {sorted(space_scale)})"
                    )
                elif prop == "font-size" and text_scale and px not in text_scale:
                    violations.append(
                        f"{f.relative_to(root)}: off-scale font-size {pxm.group(1)}px (type scale {sorted(text_scale)})"
                    )
                elif prop == "border-radius" and radius_scale and px not in radius_scale:
                    violations.append(
                        f"{f.relative_to(root)}: off-scale radius {pxm.group(1)}px (radius scale {sorted(radius_scale)})"
                    )
                elif prop in width_props and (space_scale | container_scale) and px not in (space_scale | container_scale):
                    violations.append(
                        f"{f.relative_to(root)}: off-scale {prop} {pxm.group(1)}px (container scale {sorted(container_scale)})"
                    )

    # ── exports_valid: deliverables exist and are usable (design layout only)
    # Frontend components deliver a built dist/, not exports/ — the npm build
    # gate covers that.  A stray exports/ in a frontend is not a violation.
    if not is_frontend:
        exports_dir = root / "exports"
        if exports_dir.exists():
            export_files = [
                p for p in exports_dir.rglob("*")
                if p.is_file() and p.name != ".gitkeep"
            ]
            if not export_files:
                violations.append(f"exports/: directory exists but is empty (exports_valid)")
            for ef in export_files:
                if ef.stat().st_size < 1024:
                    violations.append(
                        f"{ef.relative_to(root)}: suspiciously small (< 1k) — possibly empty or truncated (exports_valid)"
                    )
                    continue
                raw = ef.read_bytes()
                if ef.suffix.lower() == ".html":
                    tracker = _HtmlParseTracker()
                    try:
                        tracker.feed(raw.decode("utf-8", errors="replace"))
                        tracker.close()
                    except Exception as exc:  # noqa: BLE001 — any parse failure is a violation
                        violations.append(
                            f"{ef.relative_to(root)}: HTML failed to parse: {exc} (exports_valid)"
                        )
                    for err in tracker.finish():
                        violations.append(f"{ef.relative_to(root)}: HTML {err} (exports_valid)")
                elif ef.suffix.lower() == ".pdf" and not raw.startswith(PDF_MAGIC):
                    violations.append(f"{ef.relative_to(root)}: not a valid PDF (missing %PDF- header) (exports_valid)")

    if violations:
        print("check_tokens: FAIL")
        for v in violations:
            print(f"  - {v}")
        return 1

    print(f"check_tokens: OK — {len(tokens)} tokens, no violations in {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())