# Repository instructions for Codex

## Git identity

Always use `Codex <codex@openai.com>` as the author and committer of commits
you create for your own changes in this repository. This distinguishes Codex
changes from the repository owner's changes.

Set the identity for each command; do not change the user's global or local
Git identity configuration. For example:

```bash
GIT_AUTHOR_NAME=Codex GIT_AUTHOR_EMAIL=codex@openai.com \
GIT_COMMITTER_NAME=Codex GIT_COMMITTER_EMAIL=codex@openai.com \
git commit --author='Codex <codex@openai.com>' -m "Describe the change"
```

Verify the resulting author and committer with `git log -1 --format=fuller`.
