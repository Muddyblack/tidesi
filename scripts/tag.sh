#!/usr/bin/env bash
# Start a release: bump the version on a release/vX.Y.Z branch and open a PR.
# Nothing is tagged or pushed to the default branch from here. Merging the PR
# is the release: .github/workflows/release.yml sees the new version on the
# default branch, creates the tag and the GitHub release, and builds the assets.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
METADATA_PATH="hosts/kde/metadata.json"
# The DMS plugin manifest repeats the version; metadata.json stays the source.
DMS_MANIFEST_PATH="hosts/quickshell/plugin/plugin.json"
# Rewrite the version in both files: bump_files OLD NEW
bump_files() {
    sed -i "s/\"Version\": \"$1\"/\"Version\": \"$2\"/" "$METADATA_PATH"
    sed -i "s/\"version\": \"$1\"/\"version\": \"$2\"/" "$DMS_MANIFEST_PATH"
}

BETA_FLAG=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --beta) BETA_FLAG=true; shift ;;
        *) echo "Usage: $0 [--beta]" >&2; exit 1 ;;
    esac
done

cd "$HERE"
command -v gh >/dev/null || { echo "Error: the GitHub CLI (gh) is required to open the PR." >&2; exit 1; }

DEFAULT_BRANCH="$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##' || true)"
DEFAULT_BRANCH="${DEFAULT_BRANCH:-$(gh repo view --json defaultBranchRef -q .defaultBranchRef.name)}"
START_BRANCH="$(git branch --show-current)"

echo "Fetching origin..."
git fetch --quiet --tags origin "$DEFAULT_BRANCH"

VERSION_FILES=("$METADATA_PATH" "$DMS_MANIFEST_PATH")

# ── version bump (relative to what is on the default branch) ─────────────────
CURRENT_VERSION="$(git show "origin/${DEFAULT_BRANCH}:${METADATA_PATH}" \
    | grep -oE '"Version":[[:space:]]*"[^"]+"' | head -1 | sed -E 's/.*"([^"]+)"$/\1/')"

# Split into numeric part and optional suffix (e.g. "0.0.2-beta" → "0.0.2" + "-beta")
NUMERIC="${CURRENT_VERSION%%-*}"
if [[ "$CURRENT_VERSION" == *-* ]]; then
    CURRENT_SUFFIX="-${CURRENT_VERSION#*-}"
else
    CURRENT_SUFFIX=""
fi
IFS='.' read -r MAJOR MINOR PATCH <<< "$NUMERIC"

echo "Current version on ${DEFAULT_BRANCH}: ${CURRENT_VERSION}"
echo ""
echo "Bump type:"
echo "  [p] patch  → ${MAJOR}.${MINOR}.$((PATCH + 1))${CURRENT_SUFFIX}"
echo "  [m] minor  → ${MAJOR}.$((MINOR + 1)).0${CURRENT_SUFFIX}"
echo "  [M] major  → $((MAJOR + 1)).0.0${CURRENT_SUFFIX}"
read -rp "Choice [p/m/M]: " bump_choice

case "$bump_choice" in
    m) NEW_NUMERIC="${MAJOR}.$((MINOR + 1)).0" ;;
    M) NEW_NUMERIC="$((MAJOR + 1)).0.0" ;;
    *) NEW_NUMERIC="${MAJOR}.${MINOR}.$((PATCH + 1))" ;;  # default: patch
esac

if [[ "$BETA_FLAG" == true ]]; then
    NEW_SUFFIX="-beta"
elif [[ -n "$CURRENT_SUFFIX" ]]; then
    read -rp "Remove beta suffix? [y/N]: " remove_beta
    [[ "$remove_beta" =~ ^[Yy]$ ]] && NEW_SUFFIX="" || NEW_SUFFIX="$CURRENT_SUFFIX"
else
    NEW_SUFFIX=""
fi

NEW_VERSION="${NEW_NUMERIC}${NEW_SUFFIX}"
TAG_NAME="v${NEW_VERSION}"
RELEASE_BRANCH="release/${TAG_NAME}"

if git rev-parse --verify --quiet "refs/tags/${TAG_NAME}" >/dev/null; then
    echo "Error: tag ${TAG_NAME} already exists." >&2
    exit 1
fi
if git ls-remote --exit-code --heads origin "$RELEASE_BRANCH" >/dev/null 2>&1; then
    echo "Error: ${RELEASE_BRANCH} already exists on origin (open PR?)." >&2
    exit 1
fi

# ── branch, bump, commit, push, PR ───────────────────────────────────────────
# Branch off the default branch's tip; unrelated local changes stay in the
# working tree and are not part of the release commit.
git switch -c "$RELEASE_BRANCH" "origin/${DEFAULT_BRANCH}"

back_out() {
    local rc=$?
    if (( rc != 0 )); then
        echo "" >&2
        echo "Release aborted; going back to ${START_BRANCH:-the previous branch}." >&2
        git checkout -- "${VERSION_FILES[@]}" 2>/dev/null || true
        [[ -n "$START_BRANCH" ]] && git switch "$START_BRANCH" 2>/dev/null || true
        git branch -D "$RELEASE_BRANCH" >/dev/null 2>&1 || true
    fi
}
trap back_out EXIT

bump_files "$CURRENT_VERSION" "$NEW_VERSION"
git add -- "${VERSION_FILES[@]}"

COMMIT_MSG="chore: release ${TAG_NAME}"
if ! git commit -m "$COMMIT_MSG" -- "${VERSION_FILES[@]}"; then
    # pre-commit hooks may have reformatted the version files: stage and retry once
    git add -- "${VERSION_FILES[@]}"
    git commit -m "$COMMIT_MSG" -- "${VERSION_FILES[@]}"
fi

git push -u origin "$RELEASE_BRANCH"

PR_URL="$(gh pr create --base "$DEFAULT_BRANCH" --head "$RELEASE_BRANCH" \
    --title "$COMMIT_MSG" \
    --body "Bumps the version to ${NEW_VERSION}. Merging this PR tags ${TAG_NAME} and publishes the release (see .github/workflows/release.yml).")"
echo "$PR_URL"

read -rp "Auto-merge once checks pass? [y/N]: " auto_merge
if [[ "$auto_merge" =~ ^[Yy]$ ]]; then
    gh pr merge "$PR_URL" --auto --squash || echo "Could not enable auto-merge; merge the PR by hand."
fi

trap - EXIT
[[ -n "$START_BRANCH" ]] && git switch "$START_BRANCH"

echo ""
echo "=== Release PR for ${TAG_NAME} is open. Merge it and CI tags, builds and publishes. ==="
