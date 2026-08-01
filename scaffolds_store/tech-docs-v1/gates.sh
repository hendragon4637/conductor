#!/usr/bin/env bash
# boot-verify gate for tech-docs-v1
set -euo pipefail

echo "[gate] markdownlint"
npx --yes markdownlint-cli2 'docs/**/*.md'

echo "[gate] front matter"
python3 - <<'PY'
import glob, re, sys
bad = []
for f in glob.glob("docs/**/*.md", recursive=True):
    head = open(f, encoding="utf-8").read()
    if not re.match(r"^---\n(?=(?:.*\btitle:))(?=(?:.*\bdescription:))(?=(?:.*\blast_updated:)).*?---", head, re.S):
        bad.append(f)
print(bad)
sys.exit(1 if bad else 0)
PY

echo "[gate] links resolve"
python3 - <<'PY'
import glob, os, re, sys
bad = []
for f in glob.glob("docs/**/*.md", recursive=True):
    text = open(f, encoding="utf-8").read()
    for m in re.findall(r"\]\((?!https?:|#)([^)#]+)", text):
        p = os.path.normpath(os.path.join(os.path.dirname(f), m))
        if not os.path.exists(p):
            bad.append((f, m))
print(bad)
sys.exit(1 if bad else 0)
PY

echo "[gate] code fences tagged"
python3 - <<'PY'
import glob, re, sys
bad = [f for f in glob.glob("docs/**/*.md", recursive=True)
       if re.search(r"^```\s*$", open(f, encoding="utf-8").read(), re.M)]
print(bad)
sys.exit(1 if bad else 0)
PY
