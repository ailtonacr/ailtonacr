#!/usr/bin/env python3
"""Generate compact, self-hosted GitHub profile charts from the GraphQL API."""

from __future__ import annotations

import html
import json
import os
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

API_URL = "https://api.github.com/graphql"
USERNAME = os.getenv("GITHUB_USERNAME", "ailtonacr")
OUTPUT_DIR = Path(os.getenv("METRICS_OUTPUT_DIR", "assets"))
TOKEN = os.getenv("GH_TOKEN", "")

QUERY = """
query ProfileCharts($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    login
    name
    repositories(
      first: 100
      ownerAffiliations: OWNER
      isFork: false
      orderBy: { field: UPDATED_AT, direction: DESC }
    ) {
      nodes {
        isPrivate
        languages(first: 20, orderBy: { field: SIZE, direction: DESC }) {
          edges {
            size
            node { name color }
          }
        }
      }
    }
    contributionsCollection(from: $from, to: $to) {
      restrictedContributionsCount
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays {
            contributionCount
            date
          }
        }
      }
    }
  }
}
"""

THEMES = {
    "light": {
        "text": "#1f2328",
        "muted": "#656d76",
        "grid": "#d8dee4",
        "track": "#eaeef2",
        "activity": "#0969da",
        "activity_fill": "#ddf4ff",
    },
    "dark": {
        "text": "#e6edf3",
        "muted": "#8b949e",
        "grid": "#30363d",
        "track": "#21262d",
        "activity": "#58a6ff",
        "activity_fill": "#16324f",
    },
}

FALLBACK_COLORS = ["#3572A5", "#f1e05a", "#00ADD8", "#3178c6", "#e34c26", "#663399"]
MONTH_NAMES = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]


def fail(message: str) -> None:
    print(f"metrics: {message}", file=sys.stderr)
    raise SystemExit(1)


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def fetch_metrics() -> dict[str, Any]:
    if not TOKEN:
        fail("GH_TOKEN is required")

    now = datetime.now(timezone.utc)
    variables = {
        "login": USERNAME,
        "from": (now - timedelta(days=365)).isoformat(),
        "to": now.isoformat(),
    }
    request = urllib.request.Request(
        API_URL,
        data=json.dumps({"query": QUERY, "variables": variables}).encode(),
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": f"{USERNAME}-profile-charts",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        fail(f"GitHub API returned HTTP {error.code}: {detail}")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        fail(f"could not query GitHub API: {error}")

    if payload.get("errors"):
        fail("GraphQL error: " + json.dumps(payload["errors"], ensure_ascii=False))

    user = payload.get("data", {}).get("user")
    if not user:
        fail(f"GitHub user '{USERNAME}' was not found")
    return user


def aggregate_languages(user: dict[str, Any]) -> tuple[list[dict[str, Any]], int, int]:
    totals: dict[str, int] = defaultdict(int)
    repository_counts: dict[str, int] = defaultdict(int)
    colors: dict[str, str] = {}
    repositories_analyzed = 0
    private_repositories = 0

    for repository in user.get("repositories", {}).get("nodes", []):
        edges = (repository.get("languages") or {}).get("edges", [])
        if not edges:
            continue
        repositories_analyzed += 1
        private_repositories += int(bool(repository.get("isPrivate")))
        seen: set[str] = set()
        for edge in edges:
            language = edge.get("node") or {}
            name = language.get("name")
            size = int(edge.get("size") or 0)
            if not name or size <= 0:
                continue
            totals[name] += size
            colors[name] = language.get("color") or colors.get(name, "")
            if name not in seen:
                repository_counts[name] += 1
                seen.add(name)

    total_bytes = sum(totals.values())
    if not total_bytes:
        return [], repositories_analyzed, private_repositories

    languages = [
        {
            "name": name,
            "bytes": size,
            "percentage": size / total_bytes * 100,
            "repositories": repository_counts[name],
            "color": colors.get(name),
        }
        for name, size in totals.items()
    ]
    languages.sort(key=lambda item: (-item["bytes"], -item["repositories"], item["name"]))
    return languages[:6], repositories_analyzed, private_repositories


def monthly_activity(user: dict[str, Any]) -> tuple[list[dict[str, Any]], int, int]:
    calendar = user["contributionsCollection"]["contributionCalendar"]
    now = datetime.now(timezone.utc).date()
    months: list[tuple[int, int]] = []
    year, month = now.year, now.month
    for offset in range(11, -1, -1):
        absolute = year * 12 + month - 1 - offset
        months.append((absolute // 12, absolute % 12 + 1))

    counts = {key: 0 for key in months}
    for week in calendar.get("weeks", []):
        for item in week.get("contributionDays", []):
            day = date.fromisoformat(item["date"])
            key = (day.year, day.month)
            if key in counts:
                counts[key] += int(item["contributionCount"])

    result = [
        {"label": MONTH_NAMES[key[1] - 1], "year": key[0], "count": counts[key]}
        for key in months
    ]
    return (
        result,
        int(calendar.get("totalContributions", 0)),
        int(user["contributionsCollection"].get("restrictedContributionsCount", 0)),
    )


def language_chart(languages: list[dict[str, Any]], theme: dict[str, str]) -> str:
    if not languages:
        return '<text x="28" y="135" class="muted">Nenhuma linguagem disponível para análise.</text>'

    markup: list[str] = []
    x = 28
    track_x = 172
    track_width = 260
    start_y = 92
    row_height = 37

    for index, language in enumerate(languages):
        y = start_y + index * row_height
        percentage = language["percentage"]
        width = max(3, track_width * percentage / 100)
        color = language["color"] or FALLBACK_COLORS[index % len(FALLBACK_COLORS)]
        detail = f"{percentage:.1f}% · {language['repositories']} repo"
        if language["repositories"] != 1:
            detail += "s"
        markup.append(
            f'<circle cx="{x + 5}" cy="{y - 4}" r="5" fill="{esc(color)}"/>'
            f'<text x="{x + 18}" y="{y}" class="label">{esc(language["name"])}</text>'
            f'<rect x="{track_x}" y="{y - 12}" width="{track_width}" height="10" rx="5" fill="{theme["track"]}"/>'
            f'<rect x="{track_x}" y="{y - 12}" width="{width:.1f}" height="10" rx="5" fill="{esc(color)}"/>'
            f'<text x="{track_x + track_width}" y="{y + 15}" text-anchor="end" class="small">{esc(detail)}</text>'
        )
    return "".join(markup)


def activity_chart(months: list[dict[str, Any]], theme: dict[str, str]) -> str:
    chart_x = 526
    chart_y = 94
    chart_width = 420
    chart_height = 180
    max_count = max((month["count"] for month in months), default=0) or 1
    gap = 8
    bar_width = (chart_width - gap * (len(months) - 1)) / len(months)
    markup: list[str] = []

    for grid_index in range(4):
        y = chart_y + grid_index * chart_height / 3
        markup.append(
            f'<line x1="{chart_x}" y1="{y:.1f}" x2="{chart_x + chart_width}" y2="{y:.1f}" '
            f'stroke="{theme["grid"]}" stroke-width="1" opacity="0.65"/>'
        )

    for index, month in enumerate(months):
        x = chart_x + index * (bar_width + gap)
        height = chart_height * month["count"] / max_count
        y = chart_y + chart_height - height
        opacity = 1 if month["count"] else 0.28
        markup.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{max(2, height):.1f}" '
            f'rx="4" fill="{theme["activity"]}" opacity="{opacity}">'
            f'<title>{esc(month["label"])} {month["year"]}: {month["count"]} contribuições</title></rect>'
            f'<text x="{x + bar_width / 2:.1f}" y="{chart_y + chart_height + 22}" '
            f'text-anchor="middle" class="small">{esc(month["label"])}</text>'
        )
        if month["count"]:
            markup.append(
                f'<text x="{x + bar_width / 2:.1f}" y="{max(chart_y + 13, y - 6):.1f}" '
                f'text-anchor="middle" class="count">{month["count"]}</text>'
            )
    return "".join(markup)


def render_svg(user: dict[str, Any], mode: str) -> str:
    theme = THEMES[mode]
    languages, repositories_analyzed, private_repositories = aggregate_languages(user)
    months, total_contributions, private_contributions = monthly_activity(user)
    generated = datetime.now(timezone.utc).strftime("%d/%m/%Y")
    access_note = f"{repositories_analyzed} repositórios analisados"
    if private_repositories:
        access_note += f" · {private_repositories} privados"
    activity_note = f"{total_contributions} contribuições em 12 meses"
    if private_contributions:
        activity_note += f" · {private_contributions} privadas"

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="980" height="330" viewBox="0 0 980 330" role="img" aria-labelledby="title desc">
  <title id="title">Linguagens e atividade de {esc(user.get('name') or user['login'])} no GitHub</title>
  <desc id="desc">Distribuição das linguagens nos repositórios acessíveis e contribuições mensais dos últimos doze meses.</desc>
  <style>
    text {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; fill: {theme['text']}; }}
    .heading {{ font-size: 19px; font-weight: 650; }}
    .label {{ font-size: 13px; font-weight: 550; }}
    .muted, .small {{ font-size: 11px; fill: {theme['muted']}; }}
    .count {{ font-size: 10px; font-weight: 600; fill: {theme['muted']}; }}
  </style>
  <text x="28" y="32" class="heading">Linguagens mais usadas</text>
  <text x="28" y="53" class="muted">Por volume de código · {esc(access_note)}</text>
  {language_chart(languages, theme)}
  <line x1="486" y1="22" x2="486" y2="306" stroke="{theme['grid']}"/>
  <text x="526" y="32" class="heading">Atividade mensal</text>
  <text x="526" y="53" class="muted">{esc(activity_note)}</text>
  {activity_chart(months, theme)}
  <text x="28" y="318" class="muted">Dados da API oficial do GitHub · linguagens indicam distribuição de código, não nível de habilidade · atualizado em {generated}</text>
</svg>
"""


def main() -> None:
    user = fetch_metrics()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for mode in THEMES:
        destination = OUTPUT_DIR / f"github-activity-{mode}.svg"
        destination.write_text(render_svg(user, mode), encoding="utf-8")
        print(f"generated {destination}")


if __name__ == "__main__":
    main()
