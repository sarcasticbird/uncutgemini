import json, os, re, sys, time, urllib.request, urllib.error

SEVERITY_RANK = {"HIGH": 0, "MEDIUM": 1, "NIT": 2}
SEVERITY_ICON = {"HIGH": "🔴", "MEDIUM": "🟡", "NIT": "🔵"}

FRAGMENT_FILES = [
    "/tmp/trivy-fragment.md",
    "/tmp/size-fragment.md",
    "/tmp/deps-fragment.md",
]


def read_fragments():
    sections = []
    for path in FRAGMENT_FILES:
        try:
            with open(path) as f:
                content = f.read().strip()
                if content:
                    sections.append(content)
        except FileNotFoundError:
            continue
    return sections


def parse_diff_ranges(diff_text):
    ranges = {}
    current_file = None
    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            match = re.search(r" b/(.+)$", line)
            if match:
                current_file = match.group(1)
                ranges.setdefault(current_file, [])
        elif line.startswith("@@") and current_file:
            hunk = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if hunk:
                start = int(hunk.group(1))
                count = int(hunk.group(2)) if hunk.group(2) else 1
                if count > 0:
                    ranges[current_file].append((start, start + count - 1))
    return ranges


def validate_findings(findings, valid_ranges):
    validated = []
    for f in findings:
        path = f.get("file", "")
        line = f.get("line")
        if not path or not isinstance(line, int):
            print(f"::warning::Dropping finding with missing file/line: {f.get('description', '')[:80]}")
            continue
        file_ranges = valid_ranges.get(path)
        if not file_ranges:
            print(f"::warning::Dropping finding for file not in diff: {path}")
            continue
        in_range = any(start <= line <= end for start, end in file_ranges)
        if not in_range:
            print(f"::warning::Dropping finding with line {line} outside diff ranges for {path}")
            continue
        start_line = f.get("start_line")
        if isinstance(start_line, int):
            if start_line > line:
                f["start_line"] = None
            elif not any(s <= start_line <= e and s <= line <= e for s, e in file_ranges):
                f["start_line"] = None
        validated.append(f)
    return validated


def format_comment_body(finding):
    sev = finding.get("severity", "NIT")
    icon = SEVERITY_ICON.get(sev, "⚪")
    body = f"**{icon} {sev}**\n\n{finding['description']}"
    fix = finding.get("suggested_fix")
    if fix:
        fix = re.sub(r"^```\w*\s*", "", fix.strip())
        fix = re.sub(r"\s*```$", "", fix)
        body += f"\n\n```suggestion\n{fix}\n```"
    return body


def build_review_payload(findings, summary, is_incremental, sha, fragments):
    body_parts = ["## Uncut Gemini", ""]

    if is_incremental:
        body_parts.append(f"*Incremental review — commits since `{sha[:7]}`*")
        body_parts.append("")

    for fragment in fragments:
        body_parts.append(fragment)
        body_parts.append("")

    if not findings:
        body_parts.append("### 🔍 Code Review — clean")
        body_parts.append("")
        body_parts.append(summary)
        return {
            "event": "APPROVE",
            "body": "\n".join(body_parts),
            "comments": []
        }

    high = sum(1 for f in findings if f.get("severity") == "HIGH")
    med = sum(1 for f in findings if f.get("severity") == "MEDIUM")
    nit = sum(1 for f in findings if f.get("severity") == "NIT")
    counts = ", ".join(p for p in [
        f"{high} high" if high else "",
        f"{med} medium" if med else "",
        f"{nit} nit" if nit else "",
    ] if p)

    noun = "finding" if len(findings) == 1 else "findings"
    body_parts.append(f"### 🔍 Code Review — {len(findings)} {noun} ({counts})")
    body_parts.append("")
    body_parts.append(summary)
    body_parts.append("")
    body_parts.append("See inline comments below for details.")

    comments = []
    for f in sorted(findings, key=lambda x: SEVERITY_RANK.get(x.get("severity", "NIT"), 3)):
        comment = {
            "path": f["file"],
            "line": f["line"],
            "side": "RIGHT",
            "body": format_comment_body(f)
        }
        start_line = f.get("start_line")
        if isinstance(start_line, int) and start_line < f["line"]:
            comment["start_line"] = start_line
            comment["start_side"] = "RIGHT"
        comments.append(comment)

    return {
        "event": "COMMENT",
        "body": "\n".join(body_parts),
        "comments": comments
    }


SCHEMA_INSTRUCTIONS = """Response schema:
{{
  "verdict": "clean | findings",
  "summary": "one sentence overall assessment",
  "findings": [
    {{
      "severity": "HIGH | MEDIUM | NIT",
      "file": "relative/path",
      "line": 42,
      "start_line": 38,
      "description": "what and why",
      "suggested_fix": "the corrected replacement code or null"
    }}
  ]
}}

Line number rules:
- "line" is REQUIRED — the line number in the NEW version of the file
- Count from diff hunk headers: @@ -old,count +new_start,count @@
- Lines prefixed with "+" or " " (space) are in the new file — count those from new_start
- Lines prefixed with "-" are old-file only — do not count them
- For multi-line findings, set "start_line" to the first line and "line" to the last line
- Omit "start_line" for single-line findings

Suggested fix rules:
- "suggested_fix" is the corrected code that REPLACES lines from start_line to line
- Provide raw code only — no markdown fences, no surrounding context
- Must be syntactically complete for the replaced range
- Set to null if no concrete fix exists or if the fix spans multiple locations"""


def main():
    api_key = os.environ["GOOGLE_API_KEY"]
    guidelines = os.environ.get("REVIEW_GUIDELINES", "")
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-pro")
    min_severity = os.environ.get("MIN_SEVERITY", "MEDIUM").upper()
    custom_prompt = os.environ.get("CUSTOM_PROMPT", "").strip()
    extra_instructions = os.environ.get("EXTRA_INSTRUCTIONS", "").strip()
    incremental = os.environ.get("INCREMENTAL", "false") == "true"
    last_reviewed_sha = os.environ.get("LAST_REVIEWED_SHA", "")
    min_rank = SEVERITY_RANK.get(min_severity, 1)

    with open("/tmp/pr.diff") as f:
        diff = f.read()

    if not diff.strip():
        print("Empty diff — nothing to review")
        sys.exit(0)

    valid_ranges = parse_diff_ranges(diff)
    fragments = read_fragments()

    if custom_prompt:
        prompt = custom_prompt.replace("{diff}", diff).replace("{guidelines}", guidelines)
        prompt = prompt.replace("{schema}", SCHEMA_INSTRUCTIONS)
    else:
        preamble = "You are a code reviewer. Review this pull request diff for security vulnerabilities, stability risks, and convention compliance."
        if incremental:
            preamble += f"\n\nThis is an incremental review of commits since {last_reviewed_sha[:7]}. Focus exclusively on new and modified code."

        prompt = f"""{preamble}

RULES:
- Only review changed lines (+ prefixed in the diff) — do not flag pre-existing issues
- Return ONLY a JSON object — no markdown fences, no explanation
- If no issues found, return: {{"verdict": "clean", "summary": "one sentence", "findings": []}}

{SCHEMA_INSTRUCTIONS}

Severity guide:
- HIGH: Security vulnerability, data loss risk, breaking change
- MEDIUM: Stability concern, missing safety check, convention violation
- NIT: Minor improvement — only include if truly worth mentioning

<conventions>
{guidelines}
</conventions>
"""
        if extra_instructions:
            prompt += f"""
<extra-instructions>
{extra_instructions}
</extra-instructions>
"""
        prompt += f"""
<diff>
{diff}
</diff>"""

    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1}
    })

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    data = None
    for attempt in range(2):
        req = urllib.request.Request(url, data=payload.encode(), headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        })
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
            break
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            if e.code in (429, 503) and attempt == 0:
                print(f"Gemini returned {e.code}, retrying in 30s...")
                time.sleep(30)
                continue
            print(f"::warning::Gemini API error {e.code}: {body[:200]}")
            sys.exit(1)
        except Exception as e:
            print(f"::warning::Gemini request failed: {e}")
            sys.exit(1)

    if "error" in data:
        print(f"::warning::Gemini error: {data['error'].get('message', 'unknown')}")
        sys.exit(1)

    candidates = data.get("candidates", [])
    if not candidates:
        reason = data.get("promptFeedback", {}).get("blockReason", "no candidates")
        print(f"::warning::Gemini blocked: {reason}")
        sys.exit(1)

    try:
        parts = candidates[0]["content"]["parts"]
        text = next(
            (p["text"] for p in parts if "text" in p and not p.get("thought")),
            parts[-1].get("text", "")
        )
        text = text.strip()
    except (KeyError, IndexError, StopIteration, TypeError):
        print("::warning::Gemini response had unexpected structure")
        sys.exit(1)

    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        if custom_prompt:
            print("::warning::Custom prompt did not produce valid JSON. Ensure your prompt requests the expected response schema.")
        else:
            print(f"::warning::Gemini returned non-JSON: {text[:200]}")
        sys.exit(1)

    findings = [f for f in result.get("findings", [])
                if SEVERITY_RANK.get(f.get("severity", "NIT"), 2) <= min_rank]

    findings = validate_findings(findings, valid_ranges)

    summary = result.get("summary", "No issues found.")

    review_payload = build_review_payload(
        findings, summary, incremental, last_reviewed_sha, fragments
    )

    with open("/tmp/review-payload.json", "w") as f:
        json.dump(review_payload, f, indent=2)

    with open("/tmp/review-summary.md", "w") as f:
        f.write(review_payload["body"])

    if findings:
        print(f"Found {len(findings)} finding(s)")
    else:
        print(f"Clean: {summary}")


if __name__ == "__main__":
    main()
