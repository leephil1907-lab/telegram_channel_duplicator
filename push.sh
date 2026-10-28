#!/usr/bin/env bash
# Push this checkout to leephil1907-lab/telegram_channel_duplicator.
#
# Sandbox/CI usage (token never appears in the process argument list):
#   GITHUB_TOKEN=ghp_xxx ./push.sh
#
# The token needs the `repo` scope (or a fine-grained token with Contents: write)
# for that repository.  The remote URL is set automatically.
set -euo pipefail

cd "$(dirname "$0")"
REPO="leephil1907-lab/telegram_channel_duplicator"
URL="https://github.com/${REPO}.git"
BRANCH="${BRANCH:-main}"

: "${GITHUB_TOKEN:?Set GITHUB_TOKEN to a GitHub token with write access to ${REPO}}"

if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "$URL"
else
  git remote add origin "$URL"
fi

git config user.name "${GIT_AUTHOR_NAME:-Arena Agent}"
git config user.email "${GIT_AUTHOR_EMAIL:-agent@arena.ai}"

if [ -n "$(git status --porcelain)" ]; then
  git add -A
  git commit -m "${COMMIT_MESSAGE:-Production-only release: remove demo/simulation/samples and require explicit approval}"
fi

echo "==> Pushing $(git rev-parse --short HEAD) to ${REPO} (${BRANCH})"
AUTH="$(printf 'x-access-token:%s' "$GITHUB_TOKEN" | base64 | tr -d '\n')"
GIT_CONFIG_COUNT=1 \
GIT_CONFIG_KEY_0="http.extraheader" \
GIT_CONFIG_VALUE_0="AUTHORIZATION: basic ${AUTH}" \
GIT_TERMINAL_PROMPT=0 \
  git push origin "HEAD:${BRANCH}"
echo "==> Pushed. https://github.com/${REPO}"
