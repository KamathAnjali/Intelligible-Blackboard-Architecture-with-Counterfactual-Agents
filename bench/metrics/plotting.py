"""
Benchmark Plotting & Chart Generation — Student 4 (UI & Benchmarking)
bench/metrics/plotting.py

Generates evaluation charts from benchmark result CSV files.

Rule Enforced:
  CSVs are the single source of truth. Charts are saved to:
  bench/results/<phase>/charts/<chart_name>.png
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure repository root is on sys.path for direct script execution
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bench-plotting")


def load_results_csv(csv_path: str | Path) -> List[Dict[str, Any]]:
    """Load and type-cast a benchmark results CSV."""
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Result CSV not found: {path}")

    rows: List[Dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for r in reader:
            rows.append({
                "task_id": r["task_id"],
                "benchmark_source": r["benchmark_source"],
                "config_pct": int(r["config_pct"]),
                "outcome": r["outcome"],
                "iterations": int(r["iterations"]),
                "deadlock_count": int(r["deadlock_count"]),
                "intelligibility_classification": r["intelligibility_classification"],
                "total_tokens": int(r["total_tokens"]),
                "elapsed_time_s": float(r["elapsed_time_s"]),
                "dry_run": r.get("dry_run", "").lower() == "true",
                "timestamp": r["timestamp"],
                "notes": r.get("notes", ""),
            })
    return rows


def generate_charts_from_csv(
    csv_path: str | Path,
    output_charts_dir: Optional[str | Path] = None,
) -> List[Path]:
    """
    Generate standard benchmark evaluation charts from a result CSV file.
    """
    csv_p = Path(csv_path)
    rows = load_results_csv(csv_p)
    if not rows:
        logger.warning("No rows found in %s", csv_p)
        return []

    # Determine charts output dir (default: ../charts relative to csv)
    if output_charts_dir:
        charts_dir = Path(output_charts_dir)
    else:
        charts_dir = csv_p.parent / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)

    generated_charts: List[Path] = []

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        # Chart 1: Outcome & Intelligibility Distribution
        chart1_path = charts_dir / "outcome_distribution.png"
        outcomes = [r["outcome"] for r in rows]
        outcome_counts: Dict[str, int] = {}
        for o in outcomes:
            outcome_counts[o] = outcome_counts.get(o, 0) + 1

        plt.figure(figsize=(6, 4), dpi=150)
        plt.bar(list(outcome_counts.keys()), list(outcome_counts.values()), color=["#10b981", "#f59e0b", "#ef4444"])
        plt.title(f"Task Outcomes ({csv_p.stem})")
        plt.xlabel("Outcome")
        plt.ylabel("Task Count")
        plt.grid(axis="y", linestyle="--", alpha=0.5)
        plt.tight_layout()
        plt.savefig(chart1_path)
        plt.close()
        generated_charts.append(chart1_path)

        # Chart 2: Token Consumption vs Iterations
        chart2_path = charts_dir / "tokens_vs_iterations.png"
        iters = [r["iterations"] for r in rows]
        toks = [r["total_tokens"] for r in rows]

        plt.figure(figsize=(6, 4), dpi=150)
        plt.scatter(iters, toks, color="#6366f1", s=60, alpha=0.8, edgecolors="none")
        plt.title(f"Token Consumption vs. Iterations")
        plt.xlabel("Iterations (Board Entries)")
        plt.ylabel("Total Tokens")
        plt.grid(True, linestyle="--", alpha=0.5)
        plt.tight_layout()
        plt.savefig(chart2_path)
        plt.close()
        generated_charts.append(chart2_path)

        logger.info("✓ Generated %d charts in %s", len(generated_charts), charts_dir)

    except ImportError:
        logger.warning("matplotlib not installed — skipped PNG chart generation.")

    return generated_charts


def _run_cli(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Generate benchmark charts from result CSV.")
    parser.add_argument("--csv", "-c", required=True, help="Path to input results CSV file.")
    parser.add_argument("--out-dir", "-o", default=None, help="Output directory for charts.")
    args = parser.parse_args(argv)

    charts = generate_charts_from_csv(args.csv, output_charts_dir=args.out_dir)
    print(f"\n✅  Successfully generated {len(charts)} chart(s):")
    for c in charts:
        print(f"  📊  {c}")


if __name__ == "__main__":
    import sys
    _run_cli(sys.argv[1:])
