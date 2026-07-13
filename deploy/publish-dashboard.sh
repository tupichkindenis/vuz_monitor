#!/usr/bin/env bash
# Publish docs/index.html to the gh-pages branch as a SINGLE force-pushed commit
# (the branch never grows an hourly history). Meant to run right after
# `vuz_monitor run`; a failure here is logged and NON-FATAL to the hourly job.
#
# Credentials: a fine-grained GitHub PAT (contents: read+write on this repo) in a
# gitignored `.gh-token` (chmod 600). It is passed to git via GIT_ASKPASS, so the
# token never appears in the remote URL, process args (`ps`), or the log.
#
# Usage:
#   deploy/publish-dashboard.sh            # hourly: refresh + force-push (self-bootstraps)
#   deploy/publish-dashboard.sh bootstrap  # explicit one-time orphan-branch creation
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

BRANCH="gh-pages"
OUT="docs/index.html"
WT="$ROOT/.gh-pages-wt"
TOKEN_FILE="$ROOT/.gh-token"
LOG="$ROOT/publish.log"
LOCK="$ROOT/.publish.lock"

log()  { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"$LOG"; }
fail() { log "ERROR: $*"; exit 1; }

# --- single-instance lock (two overlapping runs must not fight the worktree) --
exec 9>"$LOCK"
if command -v flock >/dev/null 2>&1; then
  flock -n 9 || { log "another publish is running; skip"; exit 0; }
fi

[ -f "$OUT" ] || { log "no $OUT yet; nothing to publish"; exit 0; }

REMOTE_URL="$(git remote get-url origin 2>/dev/null)" \
  || fail "no 'origin' remote — create the GitHub repo first"
case "$REMOTE_URL" in
  https://github.com/*) ;;
  *) fail "origin must be an https github url (got: $REMOTE_URL)" ;;
esac
OWNER="$(printf '%s' "$REMOTE_URL" | sed -E 's#https://github.com/([^/]+)/.*#\1#')"
[ -s "$TOKEN_FILE" ] || fail "missing $TOKEN_FILE (fine-grained PAT, contents:write; chmod 600)"

# --- token via GIT_ASKPASS, never on the command line or in the URL ----------
ASKPASS="$(mktemp)"
cat >"$ASKPASS" <<'EOF'
#!/bin/sh
# git calls this with the prompt as $1: "Username for ..." / "Password for ..."
case "$1" in
  Username*) printf '%s' "$GH_OWNER" ;;
  *)         cat "$GH_TOKEN_FILE" ;;
esac
EOF
chmod 700 "$ASKPASS"
export GIT_ASKPASS="$ASKPASS" GH_OWNER="$OWNER" GH_TOKEN_FILE="$TOKEN_FILE" GIT_TERMINAL_PROMPT=0
# Disable any configured credential helper (e.g. osxkeychain) for THIS process only,
# so git always uses our GIT_ASKPASS token instead of a stale keychain entry.
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=credential.helper GIT_CONFIG_VALUE_0=
trap 'rm -f "$ASKPASS"' EXIT

bootstrap_orphan() {
  log "bootstrapping orphan $BRANCH"
  rm -rf "$WT"
  git worktree prune
  git branch -D "$BRANCH" >/dev/null 2>&1 || true  # drop any half-made local branch
  git worktree add --detach "$WT" >/dev/null 2>&1 || fail "worktree add (detach) failed"
  git -C "$WT" checkout --orphan "$BRANCH" >/dev/null 2>&1 || fail "orphan checkout failed"
  git -C "$WT" rm -rf . >/dev/null 2>&1 || true   # drop main's tree from index+disk
}

# --- ensure a worktree checked out on gh-pages -------------------------------
if git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
  # remote branch exists — make sure we have a local worktree tracking it
  if [ ! -d "$WT/.git" ] && [ ! -f "$WT/.git" ]; then
    git fetch origin "$BRANCH" >/dev/null 2>&1 || fail "fetch $BRANCH failed"
    rm -rf "$WT"; git worktree prune
    git worktree add "$WT" "FETCH_HEAD" >/dev/null 2>&1 \
      || git worktree add -B "$BRANCH" "$WT" "origin/$BRANCH" >/dev/null 2>&1 \
      || fail "worktree add ($BRANCH) failed"
  fi
elif [ "${1:-}" = "bootstrap" ] || [ ! -d "$WT" ]; then
  bootstrap_orphan
fi

[ -d "$WT" ] || fail "worktree $WT missing after setup"

# --- stage the fresh page + Pages niceties -----------------------------------
cp "$OUT" "$WT/index.html"
[ -f docs/table.html ] && cp docs/table.html "$WT/table.html"   # desktop summary table
[ -f docs/mirea-scores.html ] && cp docs/mirea-scores.html "$WT/mirea-scores.html"  # score-loading tracker
: >"$WT/.nojekyll"                    # serve index.html verbatim, skip Jekyll
git -C "$WT" add -A

# nothing changed and a commit already exists → skip the push (save bandwidth)
if git -C "$WT" rev-parse HEAD >/dev/null 2>&1 && git -C "$WT" diff --cached --quiet; then
  log "dashboard unchanged; skip push"
  exit 0
fi

# keep exactly ONE commit on the branch
if git -C "$WT" rev-parse HEAD >/dev/null 2>&1; then
  git -C "$WT" commit --amend --no-edit -q || fail "amend failed"
else
  git -C "$WT" commit -q -m "dashboard" || fail "initial commit failed"
fi

git -C "$WT" push -f origin "HEAD:$BRANCH" >/dev/null 2>&1 \
  || fail "push failed (check PAT scope 'contents:write' and network)"
log "published $BRANCH ($(git -C "$WT" rev-parse --short HEAD))"
