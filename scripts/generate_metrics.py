#!/usr/bin/env python3
"""Generate self-hosted GitHub profile activity cards from the GraphQL API."""

from __future__ import annotations

import html
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

API_URL = "https://api.github.com/graphql"
USERNAME = os.getenv("GITHUB_USERNAME", "ailtonacr")
OUTPUT_DIR = Path(os.getenv("METRICS_OUTPUT_DIR", "assets"))
TOKEN = os.getenv("GH_TOKEN", "")

QUERY = """
query ProfileMetrics($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    login
    name
    url
    followers { totalCount }
    repositories(
      first: 100
      ownerAffiliations: OWNER
      privacy: PUBLIC
      isFork: false
      orderBy: { field: UPDATED_AT, direction: DESC }
    ) {
      totalCount
      nodes {
        nameWithOwner
        url
        stargazerCount
        forkCount
        updatedAt
        primaryLanguage { name color }
      }
    }
    contributionsCollection(from: $from, to: $to) {
      totalCommitContributions
      totalIssueContributions
      totalPullRequestContributions
      totalPullRequestReviewContributions
      restrictedContributionsCount
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays {
            contributionCount
            contributionLevel
            date
            weekday
          }
        }
      }
      commitContributionsByRepository(maxRepositories: 20) {
        repository { nameWithOwner url isPrivate owner { login } }
        contributions(first: 1) { totalCount }
      }
      pullRequestContributionsByRepository(maxRepositories: 20) {
        repository { nameWithOwner url isPrivate owner { login } }
        contributions(first: 1) { totalCount }
      }
    }
  }
}
"""

THEMES = {
    "light": {
        "background": "#ffffff",
        "border": "#d0d7de",
        "text": "#1f2328",
        "muted": "#656d76",
        "accent": "#0969da",
        "panel": "#f6f8fa",
        "levels": ["#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"],
    },
    "dark": {
        "background": "#0d1117",
        "border": "#30363d",
        "text": "#e6edf3",
        "muted": "#8b949e",
        "accent": "#58a6ff",
        "panel": "#161b22",
        "levels": ["#161b22", "#0e4429", "#006d32", "#26a641", "#39d353"],
    },
}

LEVEL_INDEX = {
    "NONE": 0,
    "FIRST_QUARTILE": 1,
    "SECOND_QUARTILE": 2,
    "THIRD_QUARTILE": 3,
    "FOURTH_QUARTILE": 4,
}


@dataclass(frozen=True)
class Day:
    value: date
    count: int
    level: int
    weekday: int


def fail(message: str) -> None:
    print(f"metrics: {message}", file=sys.stderr)
    raise SystemExit(1)


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
        data=json.dumps({"query": QUERY, "variables": variables}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": f"{USERNAME}-profile-metrics",
            "X-Github-Next-Global-ID": "1",
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


def parse_days(calendar: dict[str, Any]) -> list[Day]:
    days: list[Day] = []
    for week in calendar.get("weeks", []):
        for item in week.get("contributionDays", []):
            days.append(
                Day(
                    value=date.fromisoformat(item["date"]),
                    count=int(item["contributionCount"]),
                    level=LEVEL_INDEX.get(item["contributionLevel"], 0),
                    weekday=int(item["weekday"]),
                )
            )
    return sorted(days, key=lambda item: item.value)


def streaks(days: list[Day]) -> tuple[int, int]:
    longest = 0
    running = 0
    for day in days:
        if day.count:
            running += 1
            longest = max(longest, running)
        else:
            running = 0

    current = 0
    meaningful_days = list(days)
    if meaningful_days and meaningful_days[-1].value == datetime.now(timezone.utc).date():
        meaningful_days = meaningful_days[:-1]
    for day in reversed(meaningful_days):
        if not day.count:
            break
        current += 1
    return current, longest


def public_external_repositories(collection: dict[str, Any]) -> list[tuple[str, str, int]]:
    repositories: dict[str, tuple[str, int]] = {}
    groups = (
        collection.get("commitContributionsByRepository", []),
        collection.get("pullRequestContributionsByRepository", []),
    )
    for group in groups:
        for item in group:
            repository = item.get("repository") or {}
            owner = (repository.get("owner") or {}).get("login", "")
            if repository.get("isPrivate") or owner.lower() == USERNAME.lower():
                continue
            name = repository.get("nameWithOwner")
            url = repository.get("url")
            if not name or not url:
                continue
            total = int((item.get("contributions") or {}).get("totalCount", 0))
            previous_url, previous_total = repositories.get(name, (url, 0))
            repositories[name] = (previous_url, previous_total + total)
    return sorted(
        ((name, url, total) for name, (url, total) in repositories.items()),
        key=lambda item: (-item[2], item[0].lower()),
    )


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def metric_card(x: int, y: int, width: int, label: str, value: object, theme: dict[str, str]) -> str:
    return f"""
    <g transform="translate({x} {y})">
      <rect width="{width}" height="82" rx="8" fill="{theme['panel']}" stroke="{theme['border']}"/>
      <text x="18" y="32" class="label">{esc(label)}</text>
      <text x="18" y="62" class="value">{esc(value)}</text>
    </g>"""


def render_svg(user: dict[str, Any], mode: str) -> str:
    theme = THEMES[mode]
    collection = user["contributionsCollection"]
    calendar = collection["contributionCalendar"]
    days = parse_days(calendar)
    current_streak, longest_streak = streaks(days)
    active_days = sum(day.count > 0 for day in days)
    busiest = max(days, key=lambda day: day.count, default=Day(date.today(), 0, 0, 0))
    external = public_external_repositories(collection)

    total = int(calendar["totalContributions"])
    private = int(collection.get("restrictedContributionsCount", 0))
    commits = int(collection["totalCommitContributions"])
    pull_requests = int(collection["totalPullRequestContributions"])
    reviews = int(collection["totalPullRequestReviewContributions"])
    issues = int(collection["totalIssueContributions"])
    public_repositories = int(user["repositories"]["totalCount"])

    width = 980
    height = 590 if external else 548
    cards = [
        ("Contribuições", total),
        ("Commits", commits),
        ("Pull requests", pull_requests),
        ("Reviews", reviews),
        ("Issues", issues),
        ("Contribuições privadas", private),
    ]

    card_width = 142
    card_gap = 14
    card_start = 28
    card_markup = "".join(
        metric_card(card_start + index * (card_width + card_gap), 92, card_width, label, value, theme)
        for index, (label, value) in enumerate(cards)
    )

    square = 11
    gap = 3
    calendar_x = 142
    calendar_y = 236
    calendar_markup: list[str] = []
    weeks = calendar.get("weeks", [])[-53:]
    for week_index, week in enumerate(weeks):
        for item in week.get("contributionDays", []):
            level = LEVEL_INDEX.get(item["contributionLevel"], 0)
            x = calendar_x + week_index * (square + gap)
            y = calendar_y + int(item["weekday"]) * (square + gap)
            title = f"{item['date']}: {item['contributionCount']} contribuições"
            calendar_markup.append(
                f'<rect x="{x}" y="{y}" width="{square}" height="{square}" rx="2" '
                f'fill="{theme["levels"][level]}"><title>{esc(title)}</title></rect>'
            )

    months_markup: list[str] = []
    seen_months: set[tuple[int, int]] = set()
    for week_index, week in enumerate(weeks):
        contribution_days = week.get("contributionDays", [])
        if not contribution_days:
            continue
        first = date.fromisoformat(contribution_days[0]["date"])
        key = (first.year, first.month)
        if key in seen_months:
            continue
        seen_months.add(key)
        label = ["", "Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"][first.month]
        x = calendar_x + week_index * (square + gap)
        months_markup.append(f'<text x="{x}" y="218" class="month">{label}</text>')

    detail_y = 362
    detail_items = [
        ("Sequência atual", f"{current_streak} dias"),
        ("Maior sequência", f"{longest_streak} dias"),
        ("Dias ativos", f"{active_days} / {len(days)}"),
        ("Dia mais ativo", f"{busiest.count} em {busiest.value.strftime('%d/%m/%Y')}"),
        ("Repositórios públicos", public_repositories),
    ]
    detail_width = 172
    detail_gap = 13
    detail_markup = "".join(
        metric_card(28 + index * (detail_width + detail_gap), detail_y, detail_width, label, value, theme)
        for index, (label, value) in enumerate(detail_items)
    )

    external_markup = ""
    if external:
        items = []
        for name, url, count in external[:3]:
            items.append(
                f'<a href="{esc(url)}"><text class="repo" '
                f'x="{28 + len(items) * 300}" y="512">{esc(name)} · {count}</text></a>'
            )
        external_markup = (
            '<text x="28" y="482" class="section">Contribuições em outros projetos</text>'
            + "".join(items)
        )

    generated_at = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    subtitle = f"Últimos 12 meses · dados oficiais da API do GitHub · atualizado em {generated_at}"

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">
  <title id="title">Atividade de {esc(user.get('name') or user['login'])} no GitHub</title>
  <desc id="desc">Resumo personalizado de contribuições, commits, pull requests, revisões, issues e calendário de atividade.</desc>
  <style>
    text {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; fill: {theme['text']}; }}
    .heading {{ font-size: 24px; font-weight: 650; }}
    .subtitle, .month {{ font-size: 12px; fill: {theme['muted']}; }}
    .label {{ font-size: 12px; fill: {theme['muted']}; }}
    .value {{ font-size: 22px; font-weight: 650; fill: {theme['accent']}; }}
    .section {{ font-size: 15px; font-weight: 600; }}
    .repo {{ font-size: 13px; fill: {theme['accent']}; }}
    a:hover .repo {{ text-decoration: underline; }}
  </style>
  <rect x="0.5" y="0.5" width="979" height="{height - 1}" rx="10" fill="{theme['background']}" stroke="{theme['border']}"/>
  <text x="28" y="42" class="heading">Atividade no GitHub</text>
  <text x="28" y="66" class="subtitle">{esc(subtitle)}</text>
  {card_markup}
  <text x="28" y="218" class="section">Calendário de contribuições</text>
  <text x="28" y="251" class="label">Dom</text>
  <text x="28" y="279" class="label">Ter</text>
  <text x="28" y="307" class="label">Qui</text>
  <text x="28" y="335" class="label">Sáb</text>
  {''.join(months_markup)}
  {''.join(calendar_markup)}
  {detail_markup}
  {external_markup}
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
