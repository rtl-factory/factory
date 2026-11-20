# Squash issue branches; merge-commit epic branches into main

Every issue branch is squash-merged, whether into `main` or into an epic branch, so each issue is one commit. An epic branch is merged into `main` with a merge commit, never squashed, so the epic's issues stay as separate commits on `main` and each can be read, bisected or reverted on its own.
