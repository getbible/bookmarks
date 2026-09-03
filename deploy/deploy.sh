#!/usr/bin/env bash
# Pull the published branch, verify the committed v1/ tree, and publish it as the
# current release. Safe to run repeatedly (systemd timer) and concurrently with the
# management API: both take the same flock inside the checkout's .git directory.
#
# Environment (see deploy/env.template): BOOKMARKS_REPO_DIR, BOOKMARKS_RELEASE_DIR,
# BOOKMARKS_GIT_REMOTE, BOOKMARKS_GIT_BRANCH, BOOKMARKS_BIN, GIT_SSH_COMMAND.
set -euo pipefail

REPO="${BOOKMARKS_REPO_DIR:-/srv/getbible-bookmarks/repo}"
WWW="${BOOKMARKS_RELEASE_DIR:-/srv/getbible-bookmarks/www}"
REMOTE="${BOOKMARKS_GIT_REMOTE:-origin}"
BRANCH="${BOOKMARKS_GIT_BRANCH:-main}"
BIN="${BOOKMARKS_BIN:-/srv/getbible-bookmarks/venv/bin/getbible-bookmarks}"
KEEP="${BOOKMARKS_KEEP_RELEASES:-3}"

log() { printf '[getbible-bookmarks] %s\n' "$*"; }

[ -d "$REPO/.git" ] || { log "no git checkout at $REPO"; exit 1; }
[ -x "$BIN" ] || { log "missing CLI at $BIN"; exit 1; }

exec 9>"$REPO/.git/bookmarks-publisher.lock"
flock -w 600 9 || { log "could not obtain the publisher lock"; exit 1; }

cd "$REPO"
current="$(git symbolic-ref --quiet --short HEAD || true)"
[ "$current" = "$BRANCH" ] || { log "checkout is on '$current', expected '$BRANCH'"; exit 1; }
if [ -n "$(git status --porcelain --untracked-files=all -- data v1)" ]; then
  log "uncommitted changes under data/ or v1/; refusing to deploy"
  exit 1
fi

if [ -n "$REMOTE" ]; then
  if git fetch --quiet "$REMOTE" "refs/heads/$BRANCH"; then
    local_head="$(git rev-parse --verify HEAD)"
    remote_head="$(git rev-parse --verify FETCH_HEAD)"
    if [ "$local_head" != "$remote_head" ]; then
      if git merge-base --is-ancestor "$local_head" "$remote_head"; then
        git merge --quiet --ff-only FETCH_HEAD
        log "fast-forwarded $BRANCH to $(git rev-parse --short HEAD)"
      elif git merge-base --is-ancestor "$remote_head" "$local_head"; then
        log "local branch is ahead of $REMOTE/$BRANCH (unpushed service commits); publishing local state"
      else
        log "local and remote history diverged; resolve $REPO manually"
        exit 1
      fi
    fi
  else
    log "fetch from $REMOTE failed; publishing the local checkout"
  fi
fi

"$BIN" --repo "$REPO" validate
"$BIN" --repo "$REPO" build --check
"$BIN" --repo "$REPO" publish --target "$WWW" --keep "$KEEP"
log "published $(git rev-parse --short HEAD) to $WWW/current"
