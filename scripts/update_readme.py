"""Update only the public activity block; keep the profile editorially curated."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
START = "<!-- ACTIVITY:START -->"
END = "<!-- ACTIVITY:END -->"


def build_activity(events, user):
    lines = []
    for event in events:
        if event.get("public") is not True:
            continue
        repo = event.get("repo", {}).get("name", "")
        if not repo or repo.lower() == f"{user}/{user}".lower():
            continue
        payload = event.get("payload", {})
        kind = event.get("type")
        # Never render arbitrary event titles or HTML from API data.
        if kind == "PushEvent":
            action = "コードを更新"
        elif kind == "PullRequestEvent" and payload.get("action") == "opened":
            action = "Pull Request を作成"
        elif kind == "IssuesEvent" and payload.get("action") == "opened":
            action = "Issue を作成"
        elif kind == "ReleaseEvent" and payload.get("action") == "published":
            action = "リリースを公開"
        else:
            continue
        # GitHub owner/repository names may contain only these characters.
        if any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._/" for c in repo):
            continue
        date = datetime.fromisoformat(event["created_at"].replace("Z", "+00:00")).date()
        line = f"- {date} · [{repo}](https://github.com/{repo}) — {action}"
        if line not in lines:
            lines.append(line)
        if len(lines) == 5:
            break
    return "\n".join(lines) or f"[最近の公開アクティビティを見る ↗](https://github.com/{user}?tab=overview)"


def replace_activity(text, body):
    if text.count(START) != 1 or text.count(END) != 1:
        raise ValueError("Expected exactly one activity marker pair")
    before, rest = text.split(START)
    _, after = rest.split(END)
    return f"{before}{START}\n{body}\n{END}{after}"


def main():
    user = os.environ["GH_USER"]
    if any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-" for c in user) or not user:
        raise ValueError("Invalid GitHub login")
    request = Request(
        f"https://api.github.com/users/{user}/events/public?per_page=100",
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": "github-profile-updater",
                 "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}"},
    )
    with urlopen(request, timeout=30) as response:
        events = json.load(response)
    if not isinstance(events, list):
        raise ValueError("Expected a public event list")
    path = ROOT / "README.md"
    text = path.read_text(encoding="utf-8")
    updated = replace_activity(text, build_activity(events, user))
    if updated != text:
        path.write_text(updated, encoding="utf-8")


if __name__ == "__main__":
    main()
