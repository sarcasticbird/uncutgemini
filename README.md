# Uncut Gemini

AI code review + dependency scanning in one GitHub Action. One `uses:` line gets you Gemini inline review comments with suggestion fixes and Trivy vulnerability scanning.

Zero dependencies beyond Python stdlib and `gh` CLI. One auditable Python file. No SDK, no Docker, no npm.

## Quick Start

```yaml
# .github/workflows/review.yml
name: Code Review
on:
  pull_request:
    types: [opened, synchronize]

permissions:
  contents: read
  pull-requests: write

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: sarcasticbird/uncutgemini@v1
        with:
          google-api-key: ${{ secrets.GOOGLE_API_KEY }}
```

That's it. Every PR gets a single unified review comment covering:

## Manual Trigger

Add `workflow_dispatch` to re-run reviews on demand (useful when Gemini hits rate limits or you want a fresh review):

```yaml
name: Code Review
on:
  pull_request:
    types: [opened, synchronize]
  workflow_dispatch:
    inputs:
      pr-number:
        description: 'PR number to review'
        required: true

permissions:
  contents: read
  pull-requests: write

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: sarcasticbird/uncutgemini@v1
        with:
          google-api-key: ${{ secrets.GOOGLE_API_KEY }}
          pr-number: ${{ github.event.inputs.pr-number }}
```

Trigger from the GitHub UI (Actions → Code Review → Run workflow) or the CLI:

```bash
gh workflow run review.yml -f pr-number=109
```

Every PR gets a single unified review comment covering:
- **Trivy** dependency scan — vulnerability table, blocks merge on CRITICAL
- **PR size** warning when changes exceed threshold
- **Dependency diff** summary when lockfiles change
- **Gemini** code review — inline comments on specific lines with one-click `suggestion` fixes

## How It Works

1. **Trivy scan** — scans for known vulnerabilities, writes a summary table
2. **PR stats** — calculates PR size, detects lockfile changes, summarizes dependency diffs
3. **Detect review scope** — checks for prior Gemini reviews; if found, fetches only the incremental diff
4. **Gemini review** — sends the diff to Gemini with a structured prompt requesting line-targeted findings
5. **Validate** — checks Gemini's line numbers against the actual diff ranges, drops invalid findings
6. **Post review** — submits one unified GitHub PR review combining all checks, with inline comments and `suggestion` blocks

## Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `google-api-key` | Yes | — | Google AI API key for Gemini |
| `model` | No | `gemini-3.1-pro-preview` | Gemini model to use |
| `guidelines-file` | No | `.github/review-guidelines.md` | Path to repo-specific review guidelines |
| `min-severity` | No | `MEDIUM` | Minimum review severity: `HIGH`, `MEDIUM`, or `NIT` |
| `extra-instructions` | No | — | Additional instructions appended to the review prompt |
| `exclude-paths` | No | docs, markdown, lockfiles | Comma-separated path patterns excluded from review. Set to `none` to review everything |
| `custom-prompt` | No | — | Full replacement prompt (`{diff}`, `{guidelines}`, `{schema}` placeholders) |
| `trivy` | No | `true` | Run Trivy dependency scan |
| `trivy-severity` | No | `CRITICAL,HIGH` | Trivy severity threshold |
| `trivy-block-on` | No | `CRITICAL` | Block merge at this level (`CRITICAL`, `HIGH`, or `NONE`) |
| `size-warning` | No | `500` | Warn when PR exceeds this many changed lines (0 to disable) |
| `pr-number` | No | *(auto-detected)* | PR number to review (for manual/`workflow_dispatch` triggers) |

## Review Guidelines

Create `.github/review-guidelines.md` in your repo with project-specific conventions:

```markdown
## Conventions
- All API responses use camelCase keys
- Database queries must use parameterized placeholders ($1, $2)
- Frontend components must not import from `src/server/`
```

## Tuning the Prompt

**Light touch** — append instructions without replacing the built-in prompt:

```yaml
- uses: sarcasticbird/uncutgemini@v1
  with:
    google-api-key: ${{ secrets.GOOGLE_API_KEY }}
    extra-instructions: 'Focus on SQL injection. Ignore test files.'
```

**Full control** — replace the entire prompt (must include `{diff}` and `{schema}`):

```yaml
- uses: sarcasticbird/uncutgemini@v1
  with:
    google-api-key: ${{ secrets.GOOGLE_API_KEY }}
    custom-prompt: |
      You are a security auditor. Only flag OWASP Top 10 issues.
      {schema}
      <diff>{diff}</diff>
```

## Unified Review Comment

All checks roll up into a single PR review comment:

```
## Uncut Gemini

### 🛡️ Dependencies — 1 critical, 2 high
| Severity | Package | Installed | Fixed | CVE |
| ...

### 📏 Size — 847 lines across 12 files
PRs over 500 lines are harder to review thoroughly.

### 📦 Dependencies Changed
**package-lock.json:** +42 / -18 lines

### 🔍 Code Review — 2 finding(s) (1 high, 1 medium)
Changes look solid but there are two security concerns.
See inline comments below for details.
```

Gemini findings appear as inline comments on the specific lines they reference, with `suggestion` blocks for one-click apply.

## Review Behavior

- **Unified comment** — Trivy vulns, PR size, dependency changes, and code review all in one place
- **Inline suggestions** — code findings appear on specific lines with one-click apply
- **Incremental** — subsequent pushes only review new changes, skipping already-reviewed code
- **Auto-approve** — clean PRs get an approval; PRs with findings get a non-blocking `COMMENT` review
- **Graceful degradation** — invalid line numbers are dropped, Gemini API errors are warnings (never block merge)

## Migrating from Standalone review.py

If you're running a cloned `review.py` + `review.yml` workflow, migration is:

1. Delete `.github/scripts/review.py`
2. Replace your `review.yml` with the [quick start](#quick-start) workflow above
3. Keep `.github/review-guidelines.md` as-is (uncutgemini reads it automatically)
4. Add `GOOGLE_API_KEY` as a repository secret (if not already set)

Your Trivy job is now built in — delete that too.

## Examples

| Example | Use case |
|---------|----------|
| [`reusable-action.yml`](examples/reusable-action.yml) | Minimal — full Trivy + Gemini in one line |
| [`customized.yml`](examples/customized.yml) | Stricter Trivy, NIT-level reviews, extra prompt instructions |
| [`gemini-only.yml`](examples/gemini-only.yml) | Trivy disabled (already running separately) |
| [`standalone.yml`](examples/standalone.yml) | Inline steps for orgs that can't reference external actions |

## Requirements

- GitHub Actions runner with Python 3.8+
- `gh` CLI (pre-installed on all GitHub-hosted runners)
- A [Google AI API key](https://aistudio.google.com/apikey)

## License

MIT
