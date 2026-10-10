#!/usr/bin/env bash
# Scan what is about to be committed for secrets and private data. Run in the worktree after `git add`.
# Patterns were checked against the real key shapes in /opt/secrets/env_files (counts only, never values):
# Sarvam keys look like sk_<seg>_<24> or sk_samvaad_<seg>_<24>; employee AKs are JWTs (eyJ…).
# Embedded screenshots (data:image/... base64) are skipped: random base64 matches token patterns.
# Exit 1 on any finding. Usage: scan_staged.sh [worktree]
set -uo pipefail
cd "${1:-.}"
status=0
paths=$(git diff --cached --name-only)
bad_paths=$(echo "$paths" | grep -E '(^|/)(private|dataset|runtime|artifacts)/|\.(sqlite3?|db|env|wav|mp3|webm|ogg)$|venv_' | grep -v '^\.env\.example$' || true)
[ -n "$bad_paths" ] && { echo "PRIVATE PATHS STAGED:"; echo "$bad_paths"; status=1; }
check() {  # label regex [allow-regex]
  local hits
  hits=$(git diff --cached -U0 | awk '/^\+\+\+ b\//{f=substr($0,7)} /^\+[^+]/{print f": "substr($0,2)}' \
         | grep -v 'data:image/' | grep -E "$2" | { if [ -n "${3:-}" ]; then grep -vE "$3"; else cat; fi; } | head -5)
  [ -n "$hits" ] && { echo "== $1"; echo "$hits" | cut -c1-180; status=1; }
}
check "Sarvam/API keys"   '(^|[^A-Za-z0-9_])sk_([A-Za-z0-9]+_){1,2}[A-Za-z0-9]{16,}|(^|[^A-Za-z0-9_])sk_[A-Za-z0-9]{20,}'
check "JWT / employee AK" 'eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}'
check "ngrok gateway URL" '[a-z0-9-]+\.ngrok(-free)?\.(app|dev|io)'
check "env assignments"   '(OPERATOR_API_KEY|SARVAM_[A-Z0-9_]*KEY[A-Z0-9_]*|COMPANY_TOKEN|LEADS_TOKEN|IM_EMPLOYEE_AK|USERCTX_API_KEY|LLM_INTERNAL_KEY|PASSWORD|SECRET)=[^[:space:]$<{"'"'"')]'
# Mobile numbers: hex-safe boundaries (sha256 digests are not phones); the documented test numbers are allowed.
check "Indian mobile numbers" '(^|[^0-9A-Fa-f])[6-9][0-9]{9}([^0-9A-Fa-f]|$)' '9876543210|9999999999'
check "personal emails"   '[A-Za-z0-9._%+-]+@(gmail|yahoo|hotmail|rediffmail|indiamart)\.[a-z.]+'
[ "$status" = 0 ] && echo "staged diff clean ($(echo "$paths" | grep -c . ) files)"
exit "$status"
