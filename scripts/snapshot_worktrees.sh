#!/bin/bash
# Snapshot every linked worktree of this repository to GitHub, so work
# survives a container that is restarted or reclaimed while a session is
# paused (usage limits, token limits, a long idle).
#
#   scripts/snapshot_worktrees.sh             once
#   scripts/snapshot_worktrees.sh --every 600 every ten minutes, until killed
#
# Each worktree <name> goes to the branch claude/erp-wip-<name> as one
# commit on top of its HEAD, holding every file as it stands: committed,
# uncommitted and untracked (what .gitignore ignores is left out). The
# worktree itself, its index and its HEAD are never touched. A worktree
# unchanged since its last snapshot is not pushed again.
#
# Restore one on a new machine:
#   git fetch origin claude/erp-wip-<name>
#   git worktree add --detach <path> FETCH_HEAD
#   git -C <path> reset HEAD~1     # the snapshot becomes uncommitted edits again
set -u
REPO="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
snap() {
  git -C "$REPO" worktree list --porcelain | awk '/^worktree /{print $2}' | tail -n +2 |
  while read -r w; do
    [ -d "$w" ] || continue
    name=$(basename "$w")
    idx=$(mktemp -u)
    if ! tree=$(export GIT_INDEX_FILE="$idx"; git -C "$w" read-tree HEAD && git -C "$w" add -A && git -C "$w" write-tree); then
      rm -f "$idx"; echo "$(date -u +%T) $name: could not read the worktree" >&2; continue
    fi
    rm -f "$idx"
    head=$(git -C "$w" rev-parse HEAD)
    last=$(git -C "$REPO" rev-parse -q --verify "refs/snapshots/$name" 2>/dev/null || true)
    if [ -n "$last" ] && [ "$(git -C "$REPO" rev-parse "$last^{tree}")" = "$tree" ] \
       && [ "$(git -C "$REPO" rev-parse "$last^")" = "$head" ]; then
      continue
    fi
    c=$(git -C "$w" -c user.name=Claude -c user.email=noreply@anthropic.com commit-tree "$tree" -p "$head" \
        -m "Snapshot of worktree $name at $(date -u +%FT%TZ): its HEAD plus every uncommitted and untracked file. Not reviewed." \
        -m "Restore: git worktree add --detach <path> <this commit>, then git -C <path> reset HEAD~1")
    if git -C "$REPO" push -q -f origin "$c:refs/heads/claude/erp-wip-$name" 2>/dev/null; then
      git -C "$REPO" update-ref "refs/snapshots/$name" "$c"
      echo "$(date -u +%T) $name -> ${c:0:7}"
    else
      echo "$(date -u +%T) $name: push failed; will try again next round" >&2
    fi
  done
}
if [ "${1:-}" = "--every" ]; then
  while true; do snap; sleep "${2:-600}"; done
else
  snap
fi
