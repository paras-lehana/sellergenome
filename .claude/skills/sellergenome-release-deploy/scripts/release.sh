#!/usr/bin/env bash
# Promote the V5 development branch to the live container: fast-forward the production worktree,
# restart sellergenome-v4, wait for healthy, optionally push both remotes.
# Usage: release.sh [--push] [--docs-only]
#   --docs-only: fast-forward (and push) without restarting — for commits that change only docs/skills/logs.
#   Run only after: version bumped (config/settings.yaml + config/genome/settings.yaml), CHANGELOG entry,
#   tests green (sellergenome-testing), staged-diff scan clean, v5 commit pushed.
set -euo pipefail
ROOT=/opt/sellergenome/.worktrees
here="$(cd "$(dirname "$0")" && pwd)"
dev_branch=feat/v5-command-center; prod_branch=feat/production-control-room
cd "$ROOT/v5"
[ -z "$(git status --porcelain)" ] || { echo "v5 worktree has uncommitted changes — commit first" >&2; exit 1; }
version=$(python3 -c "import re;print(re.search(r'version:\s*(\S+)', open('config/settings.yaml').read()).group(1))")
echo "releasing $version ($(git log --oneline -1))"
cd "$ROOT/production"
git fetch -q origin
# Codex also commits to the production branch (locally or from elsewhere): refuse unless both the local and the remote
# production branch are ancestors of what we ship, so the merge is a fast-forward and the later push can't be rejected.
git merge-base --is-ancestor "origin/$prod_branch" "$dev_branch" \
  || { echo "origin/$prod_branch has commits not in $dev_branch — rebase v5 on it, re-test, then release" >&2; exit 1; }
docs_only=0
if [[ " $* " == *" --docs-only "* ]]; then
  # Decide BEFORE merging: the live container serves frontend files straight from this worktree.
  changed=$(git diff --name-only HEAD "$dev_branch" | grep -vE '^(docs/|\.claude/|AGENTS\.md|CLAUDE\.md|README\.md|CHANGELOG\.md|PRODUCTION_PLAN\.md)' || true)
  [ -z "$changed" ] || { echo "--docs-only refused (nothing merged): code/config changed — run without it:" >&2; echo "$changed" >&2; exit 1; }
  docs_only=1
fi
git merge --ff-only "$dev_branch"
git log --oneline -1
if [ "$docs_only" = 1 ]; then echo "docs-only: no restart"; else docker restart sellergenome-v4 >/dev/null; "$here/wait_healthy.sh"; fi
if [[ " $* " == *" --push "* ]]; then
  git -C "$ROOT/v5" push -q origin "$dev_branch"
  git push -q origin "$prod_branch"
  git push -q sellergenome "$prod_branch:main"
  echo "pushed: origin/$dev_branch, origin/$prod_branch, sellergenome/main → $(git rev-parse --short HEAD)"
fi
