#!/usr/bin/env python3
"""Analyze date coverage and interruptions for prediction model CSV outputs."""

from __future__ import annotations

import argparse
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path


FILE_PATTERN = re.compile(r"^(\d{8})_(.+)\.csv$")


@dataclass(frozen=True)
class Gap:
    start: date
    end: date
    days: int


@dataclass(frozen=True)
class ModelCoverage:
    model: str
    start: date
    end: date
    file_count: int
    gap_count: int
    missing_days_total: int
    gaps: list[Gap]


def parse_data_dir(data_dir: Path) -> dict[str, list[date]]:
    model_dates: dict[str, list[date]] = defaultdict(list)
    for path in data_dir.iterdir():
        if not path.is_file():
            continue
        match = FILE_PATTERN.match(path.name)
        if not match:
            continue
        d = datetime.strptime(match.group(1), "%Y%m%d").date()
        model = match.group(2)
        model_dates[model].append(d)
    return model_dates


def summarize_model(model: str, dates: list[date]) -> ModelCoverage:
    unique_dates = sorted(set(dates))
    start = unique_dates[0]
    end = unique_dates[-1]
    gaps: list[Gap] = []

    for current, nxt in zip(unique_dates, unique_dates[1:]):
        delta = (nxt - current).days
        if delta > 1:
            gap_start = current + timedelta(days=1)
            gap_end = nxt - timedelta(days=1)
            gaps.append(Gap(start=gap_start, end=gap_end, days=delta - 1))

    return ModelCoverage(
        model=model,
        start=start,
        end=end,
        file_count=len(unique_dates),
        gap_count=len(gaps),
        missing_days_total=sum(g.days for g in gaps),
        gaps=gaps,
    )


def render_markdown(summary: list[ModelCoverage], data_dir: Path) -> str:
    today = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines: list[str] = []
    lines.append("# Prediction Model Data Coverage")
    lines.append("")
    lines.append(f"- Source directory: `{data_dir}`")
    lines.append(f"- Generated at: `{today}`")
    lines.append("")
    lines.append("## Coverage by model")
    lines.append("")
    lines.append("| Model | Data start | Data end | Days with files | Gap count | Missing days in gaps |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for item in summary:
        lines.append(
            f"| {item.model} | {item.start:%Y-%m-%d} | {item.end:%Y-%m-%d} | "
            f"{item.file_count} | {item.gap_count} | {item.missing_days_total} |"
        )

    lines.append("")
    lines.append("## Gap details")
    lines.append("")

    for item in summary:
        lines.append(f"### {item.model}")
        if not item.gaps:
            lines.append("- No interruptions (continuous daily coverage).")
            lines.append("")
            continue

        lines.append("| Gap start | Gap end | Missing days |")
        lines.append("|---:|---:|---:|")
        for gap in item.gaps:
            lines.append(f"| {gap.start:%Y-%m-%d} | {gap.end:%Y-%m-%d} | {gap.days} |")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        default="rice_blast_prediction/data",
        type=Path,
        help="Directory containing model prediction CSV files.",
    )
    parser.add_argument(
        "--output",
        default="docs/model_data_coverage.md",
        type=Path,
        help="Path to write markdown report.",
    )
    args = parser.parse_args()

    model_dates = parse_data_dir(args.data_dir)
    coverage = [summarize_model(model, dates) for model, dates in sorted(model_dates.items())]

    output_text = render_markdown(coverage, args.data_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(output_text, encoding="utf-8")

    print(f"Wrote report: {args.output}")
    print(f"Models analyzed: {len(coverage)}")


if __name__ == "__main__":
    main()
