#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# seed_all.sh — Unified seed orchestrator for the Conductor project.
#
# Discovers all seed_*.py and seed_*.sql files under scripts/, orders them by
# dependency (canonical order), and executes each via `uv run python` or `psql`.
#
# Usage:
#   ./scripts/seed_all.sh                  # run all seeds
#   ./scripts/seed_all.sh --dry-run        # print plan, don't execute
#   ./scripts/seed_all.sh --from seed_agent_configs.py   # start at step N
#   ./scripts/seed_all.sh --only seed_rubrics.py          # run one step
#
# Flags:
#   --dry-run        Print execution plan without running anything.
#   --from STEP      Start execution from the named step (inclusive).
#   --only STEP      Execute only the named step.
#
# Environment:
#   DATABASE_URL     Required. PostgreSQL connection string.
#   CONDUCTOR_ENV    Optional. If set, sources .env.$CONDUCTOR_ENV first.
#
# Exit code: 0 if all seeds pass, 1 if any fail.
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPTS_DIR="$PROJECT_ROOT/scripts"

# ─── Coloured output helpers ─────────────────────────────────────────────────
GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'
NC='\033[0m'
pass() { echo -e "  ${GREEN}PASS${NC}  $1"; }
fail() { echo -e "  ${RED}FAIL${NC}  $1"; }
warn() { echo -e "  ${YELLOW}WARN${NC}  $1"; }

# ─── Load environment ────────────────────────────────────────────────────────
if [ -n "${CONDUCTOR_ENV:-}" ] && [ -f "$PROJECT_ROOT/.env.$CONDUCTOR_ENV" ]; then
  set -a
  # shellcheck source=/dev/null
  source "$PROJECT_ROOT/.env.$CONDUCTOR_ENV"
  set +a
elif [ -f "$PROJECT_ROOT/.env" ]; then
  set -a
  # shellcheck source=/dev/null
  source "$PROJECT_ROOT/.env"
  set +a
fi

if [ -z "${DATABASE_URL:-}" ]; then
  echo "ERROR: DATABASE_URL is not set. Export it or create a .env file." >&2
  exit 1
fi

# ─── Canonical seed order (dependency-aware) ─────────────────────────────────
# Scripts earlier in this list may be required by later ones.
declare -A SEED_LABELS
SEED_LABELS["seed_capabilities.py"]="Capability registry"
SEED_LABELS["seed_agent_configs.py"]="Agent configurations"
SEED_LABELS["seed_default_checks.py"]="Default L1/L2 checks"
SEED_LABELS["seed_domain_profiles.py"]="Domain profiles"
SEED_LABELS["seed_rubrics.py"]="Rubric registry + check templates"
SEED_LABELS["seed_golden_example.py"]="L3 golden examples"
SEED_LABELS["seed_l4_golden_example.py"]="L4 golden examples"
SEED_LABELS["seed_l4_golden_cases.py"]="L4 golden discrimination cases"

CANONICAL_ORDER=(
  seed_capabilities.py
  seed_agent_configs.py
  seed_default_checks.py
  seed_domain_profiles.py
  seed_rubrics.py
  seed_golden_example.py
  seed_l4_golden_example.py
  seed_l4_golden_cases.py
)

# ─── Argument parsing ────────────────────────────────────────────────────────
DRY_RUN=false
FROM_STEP=""
ONLY_STEP=""

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=true; shift ;;
    --from) FROM_STEP="$2"; shift 2 ;;
    --only) ONLY_STEP="$2"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

# ─── Discover seed files ─────────────────────────────────────────────────────
declare -a DISCOVERED=()
while IFS= read -r -d '' f; do
  DISCOVERED+=("$(basename "$f")")
done < <(find "$SCRIPTS_DIR" -maxdepth 1 \( -name 'seed_*.py' -o -name 'seed_*.sql' \) -print0 | sort -z)

if [ "${#DISCOVERED[@]}" -eq 0 ]; then
  echo "No seed_*.py or seed_*.sql files found in $SCRIPTS_DIR"
  exit 0
fi

# ─── Build execution plan ────────────────────────────────────────────────────
# Start with canonical entries that exist on disk, then append unknown extras.
declare -a PLAN=()
declare -A ADDED=()

for f in "${CANONICAL_ORDER[@]}"; do
  if [ -f "$SCRIPTS_DIR/$f" ]; then
    PLAN+=("$f")
    ADDED["$f"]=1
  fi
done

for f in "${DISCOVERED[@]}"; do
  if [ -z "${ADDED[$f]:-}" ]; then
    warn "Unknown seed script (not in canonical order): $f"
    PLAN+=("$f")
  fi
done

# ─── Filter: --from STEP ─────────────────────────────────────────────────────
if [ -n "$FROM_STEP" ]; then
  found=false
  declare -a FILTERED=()
  for f in "${PLAN[@]}"; do
    if [ "$f" = "$FROM_STEP" ]; then
      found=true
    fi
    if $found; then
      FILTERED+=("$f")
    fi
  done
  if ! $found; then
    echo "ERROR: --from step '$FROM_STEP' not found in execution plan" >&2
    exit 1
  fi
  PLAN=("${FILTERED[@]}")
fi

# ─── Filter: --only STEP ─────────────────────────────────────────────────────
if [ -n "$ONLY_STEP" ]; then
  found=false
  for f in "${PLAN[@]}"; do
    if [ "$f" = "$ONLY_STEP" ]; then
      found=true
      break
    fi
  done
  if ! $found; then
    echo "ERROR: --only step '$ONLY_STEP' not found in execution plan" >&2
    exit 1
  fi
  PLAN=("$ONLY_STEP")
fi

# ─── Print plan ──────────────────────────────────────────────────────────────
# Mask password in DATABASE_URL for display
DB_DISPLAY="${DATABASE_URL%%@*}:****@${DATABASE_URL##*@}"

echo "═══════════════════════════════════════════════════════════════"
echo "  Conductor Seed Orchestrator"
echo "  Project : $PROJECT_ROOT"
echo "  Database: $DB_DISPLAY"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "Execution plan (${#PLAN[@]} steps):"
for f in "${PLAN[@]}"; do
  label="${SEED_LABELS[$f]:-}"
  printf "  %s  %s\n" "$f" "${label:+($label)}"
done
echo ""

if $DRY_RUN; then
  echo "Dry-run mode. No scripts were executed."
  exit 0
fi

# ─── Execute each seed ───────────────────────────────────────────────────────
PASSED=0
FAILED=0
declare -a FAILED_SCRIPTS=()

for f in "${PLAN[@]}"; do
  label="${SEED_LABELS[$f]:-}"
  echo "─── $f ${label:+($label)} ───"

  case "$f" in
    *.sql)
      if psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$SCRIPTS_DIR/$f" 2>&1; then
        pass "$f"
        ((PASSED++))
      else
        fail "$f"
        ((FAILED++))
        FAILED_SCRIPTS+=("$f")
      fi
      ;;
    *.py)
      if (cd "$PROJECT_ROOT" && uv run python "$SCRIPTS_DIR/$f"); then
        pass "$f"
        ((PASSED++))
      else
        fail "$f"
        ((FAILED++))
        FAILED_SCRIPTS+=("$f")
      fi
      ;;
  esac
done

# ─── Summary ─────────────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  Results: $PASSED passed, $FAILED failed"
echo "═══════════════════════════════════════════════════════════════"
echo ""

if [ "$FAILED" -gt 0 ]; then
  echo "Failed scripts:"
  for s in "${FAILED_SCRIPTS[@]}"; do
    echo "  - $s"
  done
  exit 1
fi

exit 0
