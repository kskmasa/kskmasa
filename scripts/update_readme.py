import os, json, re, sys, math
from datetime import datetime, timedelta, timezone
import requests
from dateutil import parser, tz

TOKEN = os.getenv("GITHUB_TOKEN")
USER  = os.getenv("GH_USER")
API_GQL = "https://api.github.com/graphql"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

ROOT = os.getcwd()
ASSETS_DIR = os.path.join(ROOT, "assets")
SVG_PATH = os.path.join(ASSETS_DIR, "monthly_activity.svg")
README_PATH = os.path.join(ROOT, "README.md")

# ---------- helpers ----------
def gql(query, variables=None):
    r = requests.post(API_GQL, json={"query": query, "variables": variables or {}}, headers=HEADERS, timeout=30)
    r.raise_for_status()
    j = r.json()
    if "errors" in j:
        raise RuntimeError(j["errors"])
    return j["data"]

def humanize(dt_iso):
    # 相対時間（例: "3d ago"）
    dt = parser.isoparse(dt_iso).replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    diff = now - dt
    s = diff.total_seconds()
    if s < 60: return f"{int(s)}s ago"
    if s < 3600: return f"{int(s//60)}m ago"
    if s < 86400: return f"{int(s//3600)}h ago"
    return f"{int(s//86400)}d ago"

def replace_between(text, start_marker, end_marker, new_body):
    pattern = re.compile(
        r"(?P<start><!--\s*"+re.escape(start_marker)+r"\s*-->)(?P<body>.*?)(?P<end><!--\s*"+re.escape(end_marker)+r"\s*-->)",
        re.DOTALL
    )
    return pattern.sub(lambda m: f"{m.group('start')}\n{new_body}\n{m.group('end')}", text)

# ---------- 1) 現在開発中プロジェクト ----------
QUERY_RECENT_REPOS = """
query($login:String!) {
  user(login:$login){
    repositories(privacy:PUBLIC, first: 20, orderBy:{field: PUSHED_AT, direction: DESC}, isFork:false) {
      nodes {
        name
        description
        stargazerCount
        primaryLanguage { name color }
        pushedAt
        url
      }
    }
  }
}
"""

def build_projects_section():
    data = gql(QUERY_RECENT_REPOS, {"login": USER})
    nodes = data["user"]["repositories"]["nodes"]
    # 最近更新TOP3（説明が空でもOK）
    top = nodes[:3]

    lines = ["### 🔄 Currently Building"]
    for n in top:
        lang = n["primaryLanguage"]["name"] if n["primaryLanguage"] else "-"
        color = n["primaryLanguage"]["color"] if n["primaryLanguage"] else "#cccccc"
        desc = (n["description"] or "No description").strip()
        pushed = humanize(n["pushedAt"])
        lines.append(
            f"- **[{n['name']}]({n['url']})** — {desc}  \n"
            f"  <sub><span style='display:inline-block;width:10px;height:10px;background:{color};border-radius:50%;vertical-align:middle;margin-right:6px'></span>{lang} • ⭐ {n['stargazerCount']} • updated {pushed}</sub>"
        )
    return "\n".join(lines)

# ---------- 2) 活動履歴（直近のコミット等） ----------
QUERY_EVENTS = """
query($login:String!, $from:DateTime!, $to:DateTime!) {
  user(login:$login) {
    contributionsCollection(from:$from, to:$to) {
      commitContributionsByRepository(maxRepositories: 10) {
        repository { name url }
        contributions(first: 5) {
          edges {
            node {
              occurredAt
              commitCount
            }
          }
        }
      }
      pullRequestContributions(first:10){
        edges{
          node{
            occurredAt
            pullRequest { title url }
          }
        }
      }
      issueContributions(first:10){
        edges{
          node{
            occurredAt
            issue { title url }
          }
        }
      }
    }
  }
}
"""

def build_activity_section():
    to = datetime.now(timezone.utc)
    frm = to - timedelta(days=14)
    data = gql(QUERY_EVENTS, {"login": USER, "from": frm.isoformat(), "to": to.isoformat()})
    cc = data["user"]["contributionsCollection"]

    events = []

    # commits
    for repoBlock in cc.get("commitContributionsByRepository", []):
        repo = repoBlock["repository"]
        for e in repoBlock["contributions"]["edges"]:
            node = e["node"]
            events.append({
                "type": "commit",
                "text": f"pushed **{node['commitCount']}** commit(s) to [{repo['name']}]({repo['url']})",
                "when": node["occurredAt"]
            })

    # PRs
    for e in cc.get("pullRequestContributions", {}).get("edges", []):
        node = e["node"]
        pr = node["pullRequest"]
        events.append({
            "type": "pr",
            "text": f"opened PR: [{pr['title']}]({pr['url']})",
            "when": node["occurredAt"]
        })

    # Issues
    for e in cc.get("issueContributions", {}).get("edges", []):
        node = e["node"]
        isu = node["issue"]
        events.append({
            "type": "issue",
            "text": f"opened Issue: [{isu['title']}]({isu['url']})",
            "when": node["occurredAt"]
        })

    # 直近でソートして上位5件
    events.sort(key=lambda x: x["when"], reverse=True)
    events = events[:5]

    if not events:
        return "_No recent public activity_"

    # ちょっと動く行頭アイコン（CSSアニメはSVG側が無難。ここは軽く記号だけ）
    out = ["### 🏃 Recent Activity"]
    for ev in events:
        out.append(f"- ⏺️ {ev['text']}  <sub>{humanize(ev['when'])}</sub>")
    return "\n".join(out)

# ---------- 3) 今月のアクティビティ（SVGアニメ） ----------
QUERY_MONTH = """
query($login:String!, $from:DateTime!, $to:DateTime!) {
  user(login:$login){
    contributionsCollection(from:$from, to:$to){
      contributionCalendar{
        weeks{
          contributionDays{
            date
            contributionCount
          }
        }
      }
    }
  }
}
"""

def generate_month_svg():
    # 今月（直近30日）を対象
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=29)

    data = gql(QUERY_MONTH, {"login": USER, "from": start.isoformat()+"T00:00:00Z", "to": end.isoformat()+"T23:59:59Z"})
    weeks = data["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]

    # 日付 -> count
    counts = {}
    for w in weeks:
        for d in w["contributionDays"]:
            date = d["date"]
            if start.isoformat() <= date <= end.isoformat():
                counts[date] = d["contributionCount"]

    # 並び替え
    days = [(start + timedelta(days=i)).isoformat() for i in range(30)]
    vals = [counts.get(day, 0) for day in days]

    maxv = max(vals) if any(vals) else 1

    width = 800
    height = 240
    padding = 30
    chart_w = width - padding*2
    chart_h = height - padding*2
    bar_w = chart_w / len(vals)

    # SVG + CSS アニメ（バーの伸び、ホバーで値）
    bars = []
    for i, v in enumerate(vals):
        x = padding + i * bar_w
        h = 0 if maxv == 0 else (v / maxv) * chart_h
        y = height - padding - h
        delay = i * 0.03
        bars.append(f"""
        <g class="bar" transform="translate({x:.2f}, {y:.2f})">
          <title>{days[i]}: {v}</title>
          <rect x="0" y="0" width="{bar_w*0.8:.2f}" height="{h:.2f}" rx="6" ry="6"
                style="animation: grow 0.8s {delay:.2f}s ease-out forwards; transform-origin: bottom;">
          </rect>
        </g>
        """)

    svg = f"""<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">
  <style>
    .title {{ font: 700 16px 'Segoe UI', Roboto, Ubuntu, 'Helvetica Neue', Arial; }}
    .axis  {{ font: 12px 'Segoe UI', Roboto, Ubuntu, Arial; fill: #888; }}
    rect {{ fill: #00bcd4; opacity: 0.85; }}
    rect:hover {{ opacity: 1.0; }}
    @keyframes grow {{ from {{ transform: scaleY(0); }} to {{ transform: scaleY(1); }} }}
  </style>
  <rect x="0" y="0" width="{width}" height="{height}" fill="#0b1220" rx="16" />
  <text x="{padding}" y="{padding-8}" class="title" fill="#ffffff">This Month Activity (last 30 days)</text>
  <g>
    {"".join(bars)}
  </g>
  <!-- x-axis ticks (週目盛りだけ) -->
  <g class="axis">
    <text x="{padding}" y="{height - 8}">{days[0][5:]}</text>
    <text x="{padding + chart_w/2:.2f}" y="{height - 8}">{days[15][5:]}</text>
    <text x="{padding + chart_w - 36:.2f}" y="{height - 8}">{days[-1][5:]}</text>
  </g>
</svg>"""
    os.makedirs(ASSETS_DIR, exist_ok=True)
    with open(SVG_PATH, "w", encoding="utf-8") as f:
        f.write(svg)

def build_monthly_graph_section():
    # 生成されたSVGを参照（相対パス）
    return "### 🕓 This Month\n\n![Monthly Activity](assets/monthly_activity.svg)"

# ---------- main ----------
def main():
    if not TOKEN or not USER:
        print("Missing env GITHUB_TOKEN or GH_USER", file=sys.stderr); sys.exit(1)

    projects_md = build_projects_section()
    activity_md = build_activity_section()
    generate_month_svg()
    graph_md = build_monthly_graph_section()

    with open(README_PATH, "r", encoding="utf-8") as f:
        readme = f.read()

    readme = replace_between(readme, "PROJECTS:START", "PROJECTS:END", projects_md)
    readme = replace_between(readme, "ACTIVITY:START", "ACTIVITY:END", activity_md)
    readme = replace_between(readme, "MONTHLY_GRAPH:START", "MONTHLY_GRAPH:END", graph_md)

    with open(README_PATH, "w", encoding="utf-8") as f:
        f.write(readme)

if __name__ == "__main__":
    main()
