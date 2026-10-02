"""
Benchmark Plotting & Ablation Analytics Toolkit — Student 4 (UI & Benchmarking)
bench/metrics/plotting.py

W3 Day 3 / Day 5 Implementation:
- Ingests single or multi-configuration benchmark result CSVs.
- Generates evaluation charts, separating evaluator scores from internal diagnostics:
  1. Official benchmark score vs counterfactual density
  2. Intelligibility Classification Depth vs Counterfactual Density
  3. Token Cost & Overhead vs Counterfactual Density
- Generates an interactive standalone HTML dashboard (`ablation_summary_report.html`).
"""

from __future__ import annotations

import argparse
import csv
import logging
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure repository root is on sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
logger = logging.getLogger("bench-plotting")


# ─────────────────────────────────────────────────────────────────────────────
# Ingestion & Aggregation
# ─────────────────────────────────────────────────────────────────────────────

def load_results_csv(csv_path: str | Path) -> List[Dict[str, Any]]:
    """Load only results explicitly produced by an official benchmark evaluator."""
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Result CSV not found: {path}")

    rows: List[Dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        required = {"result_kind", "official_metric", "official_score", "token_source", "timing_scope", "ground_truth_used"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path} has no evaluation provenance ({', '.join(sorted(missing))}); refusing legacy/simulation CSV")
        for line_number, r in enumerate(reader, start=2):
            if r.get("result_kind") != "official_evaluation" or not r.get("official_metric") or r.get("official_score") in (None, ""):
                raise ValueError(f"{path}:{line_number} is not an official evaluator result; refusing to plot it")
            if r.get("ground_truth_used", "").strip().lower() != "false":
                raise ValueError(f"{path}:{line_number} does not confirm that reference answers were kept out of agent input")
            if not r.get("token_source", "").strip() or not r.get("timing_scope", "").strip():
                raise ValueError(f"{path}:{line_number} is missing token or timing provenance")
            try:
                official_score = float(r["official_score"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{path}:{line_number} has a nonnumeric official_score") from exc
            if not math.isfinite(official_score):
                raise ValueError(f"{path}:{line_number} has a non-finite official_score")
            rows.append({
                "mode": r.get("mode", "unknown"),
                "task_id": r["task_id"],
                "benchmark_source": r["benchmark_source"],
                "config_pct": int(r["config_pct"]),
                "outcome": r["outcome"],
                "iterations": int(r["iterations"]),
                "deadlock_count": int(r["deadlock_count"]),
                "intelligibility_classification": r["intelligibility_classification"],
                "total_tokens": int(r["total_tokens"]),
                "mainline_tokens": int(r.get("mainline_tokens") or 0),
                "simulation_tokens": int(r.get("simulation_tokens") or 0),
                "elapsed_time_s": float(r["elapsed_time_s"]),
                "dry_run": r.get("dry_run", "").lower() == "true",
                "timestamp": r["timestamp"],
                "notes": r.get("notes", ""),
                "result_kind": r["result_kind"],
                "official_metric": r["official_metric"],
                "official_score": official_score,
                "token_source": r["token_source"],
                "timing_scope": r["timing_scope"],
                "ground_truth_used": r["ground_truth_used"].lower() == "true",
            })
    return rows


def load_all_phase_csvs(phase_dir: str | Path) -> Dict[int, List[Dict[str, Any]]]:
    """Scan a phase directory and group results by config_pct (0, 33, 66, 100)."""
    p_dir = Path(phase_dir)
    # Match mode-prefixed CSV files
    pilot_csvs = sorted(p_dir.glob("pilot_run_*_config-*pct.csv"))
    dryrun_csvs = sorted(p_dir.glob("dryrun_run_*_config-*pct.csv"))
    example_csvs = sorted(p_dir.glob("example_run_*_config-*pct.csv"))
    all_other_csvs = sorted(p_dir.glob("*run_*_config-*pct.csv"))

    target_csvs = sorted(set(pilot_csvs + dryrun_csvs + example_csvs + all_other_csvs))

    by_config: Dict[int, List[Dict[str, Any]]] = {}
    for f in target_csvs:
        try:
            rows = load_results_csv(f)
        except ValueError as exc:
            logger.warning("Skipping unverified results file: %s", exc)
            continue
        if rows:
            cfg = rows[0]["config_pct"]
            by_config.setdefault(cfg, []).extend(rows)

    return by_config


# ─────────────────────────────────────────────────────────────────────────────
# Matplotlib Chart Generation
# ─────────────────────────────────────────────────────────────────────────────

def generate_ablation_charts(
    by_config: Dict[int, List[Dict[str, Any]]],
    charts_dir: Path,
) -> List[Path]:
    """Generate all standard ablation PNG charts."""
    charts_dir.mkdir(parents=True, exist_ok=True)
    generated: List[Path] = []

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        sorted_configs = sorted(by_config.keys())
        if not sorted_configs:
            return []

        metric_names = {r["official_metric"] for rows in by_config.values() for r in rows}
        if len(metric_names) != 1:
            raise ValueError(f"Cannot compare different official metrics in one chart: {sorted(metric_names)}")
        metric_name = next(iter(metric_names))

        # 1. Arithmetic mean of evaluator-provided row scores. This is not an
        # official workload aggregate; KramaBench weighting is handled upstream.
        p1 = charts_dir / "mean_evaluator_score_vs_density.png"
        scores = []
        for c in sorted_configs:
            rows = by_config[c]
            scores.append(sum(r["official_score"] for r in rows) / len(rows) if rows else 0)

        plt.figure(figsize=(6.5, 4.2), dpi=160)
        plt.plot(sorted_configs, scores, marker="o", linewidth=2.5, color="#10b981", markersize=8)
        plt.title(f"Mean Per-Row {metric_name} vs. Counterfactual Density", fontsize=12, fontweight="bold", pad=12)
        plt.xlabel("Counterfactual Participation Density (%)", fontsize=10)
        plt.ylabel(f"Mean {metric_name}", fontsize=10)
        plt.xticks(sorted_configs, [f"{c}%" for c in sorted_configs])
        plt.grid(True, linestyle="--", alpha=0.5)
        for x, y in zip(sorted_configs, scores):
            plt.text(x, y, f"{y:.3g}", ha="center", va="bottom", fontsize=9, fontweight="bold", color="#065f46")
        plt.tight_layout()
        plt.savefig(p1)
        plt.close()
        generated.append(p1)

        # 2. Intelligibility Classification Distribution vs Density
        p2 = charts_dir / "intelligibility_vs_density.png"
        ultra_strong = []
        strong = []
        unresolved = []
        for c in sorted_configs:
            rows = by_config[c]
            total = max(1, len(rows))
            ultra_strong.append((sum(1 for r in rows if r["intelligibility_classification"] == "ULTRA_STRONG") / total) * 100)
            strong.append((sum(1 for r in rows if r["intelligibility_classification"] == "STRONG") / total) * 100)
            unresolved.append((sum(1 for r in rows if r["intelligibility_classification"] == "UNRESOLVED") / total) * 100)

        plt.figure(figsize=(7, 4.2), dpi=160)
        bar_width = 0.55
        x_indices = range(len(sorted_configs))
        plt.bar(x_indices, ultra_strong, width=bar_width, label="ULTRA_STRONG", color="#10b981", edgecolor="none")
        plt.bar(x_indices, strong, width=bar_width, bottom=ultra_strong, label="STRONG", color="#3b82f6", edgecolor="none")
        bottom_unres = [u + s for u, s in zip(ultra_strong, strong)]
        plt.bar(x_indices, unresolved, width=bar_width, bottom=bottom_unres, label="UNRESOLVED", color="#ef4444", edgecolor="none")

        plt.title("Internal Blackboard Classification vs. Counterfactual Density", fontsize=12, fontweight="bold", pad=12)
        plt.xlabel("Counterfactual Participation Density", fontsize=10)
        plt.ylabel("Proportion of Tasks (%)", fontsize=10)
        plt.xticks(x_indices, [f"{c}%" for c in sorted_configs])
        plt.ylim(0, 100)
        plt.legend(frameon=True, loc="upper left", fontsize=9)
        plt.tight_layout()
        plt.savefig(p2)
        plt.close()
        generated.append(p2)

        # 3. Token Cost vs Density
        p3 = charts_dir / "tokens_vs_density.png"
        avg_tokens = []
        for c in sorted_configs:
            rows = by_config[c]
            avg_tok = sum(r["total_tokens"] for r in rows) / max(1, len(rows))
            avg_tokens.append(avg_tok)

        plt.figure(figsize=(6.5, 4.2), dpi=160)
        plt.plot(sorted_configs, avg_tokens, marker="s", linewidth=2.5, color="#6366f1", markersize=8)
        plt.title("Board-Entry Text Token Estimate vs. Counterfactual Density", fontsize=12, fontweight="bold", pad=12)
        plt.xlabel("Counterfactual Participation Density (%)", fontsize=10)
        plt.ylabel("Mean Entry-Text Tokens / Task (see CSV provenance)", fontsize=10)
        plt.xticks(sorted_configs, [f"{c}%" for c in sorted_configs])
        plt.grid(True, linestyle="--", alpha=0.5)
        for x, y in zip(sorted_configs, avg_tokens):
            plt.text(x, y + 2, f"{y:.0f}t", ha="center", fontsize=9, fontweight="bold", color="#3730a3")
        plt.tight_layout()
        plt.savefig(p3)
        plt.close()
        generated.append(p3)

    except ImportError:
        logger.warning("matplotlib not found — skipping PNG generation.")

    return generated


# ─────────────────────────────────────────────────────────────────────────────
# Interactive HTML Report Generator
# ─────────────────────────────────────────────────────────────────────────────

def generate_interactive_html_report(
    by_config: Dict[int, List[Dict[str, Any]]],
    output_html_path: Path,
) -> Path:
    """Generate a responsive HTML dashboard report summarizing ablation results."""
    sorted_configs = sorted(by_config.keys())

    summary_rows = []
    for c in sorted_configs:
        rows = by_config[c]
        total_tasks = len(rows)
        deadlocked = sum(1 for r in rows if r["outcome"] == "deadlock")
        ultra = sum(1 for r in rows if r["intelligibility_classification"] == "ULTRA_STRONG")
        avg_tok = sum(r["total_tokens"] for r in rows) / max(1, total_tasks)
        avg_time = sum(r["elapsed_time_s"] for r in rows) / max(1, total_tasks)

        summary_rows.append({
            "config": f"{c}%",
            "total_tasks": total_tasks,
            "official_score": f"{sum(r['official_score'] for r in rows) / total_tasks:.4g}",
            "deadlock_rate": f"{(deadlocked / total_tasks) * 100:.1f}%",
            "ultra_strong_rate": f"{(ultra / total_tasks) * 100:.1f}%",
            "avg_tokens": f"{avg_tok:.0f}",
            "avg_time": f"{avg_time:.3f}s",
        })

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Official Benchmark Results — Intelligible Blackboard</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0b0f19; color: #f8fafc; padding: 32px; }}
    h1 {{ font-size: 24px; color: #f1f5f9; margin-bottom: 8px; }}
    p.subtitle {{ color: #94a3b8; font-size: 14px; margin-bottom: 24px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 20px; margin-bottom: 32px; }}
    .card {{ background: rgba(15, 23, 42, 0.9); border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 12px; padding: 20px; }}
    .card img {{ width: 100%; height: auto; border-radius: 8px; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 16px; font-size: 13px; }}
    th, td {{ padding: 10px 14px; text-align: left; border-bottom: 1px solid rgba(255, 255, 255, 0.08); }}
    th {{ background: rgba(255, 255, 255, 0.04); color: #cbd5e1; font-weight: 600; }}
    .tag-pill {{ padding: 2px 8px; border-radius: 4px; font-weight: 700; font-size: 11px; }}
    .tag-green {{ background: rgba(16, 185, 129, 0.2); color: #10b981; }}
  </style>
</head>
<body>
  <h1>Official Benchmark Results — Counterfactual Participation Ablation</h1>
  <p class="subtitle">The first column is the arithmetic mean of evaluator-provided row scores, not KramaBench's workload aggregate. Deadlock and intelligibility are internal diagnostics. Token counts cover board-entry text only; check token and timing provenance.</p>
  
  <div class="card" style="margin-bottom: 24px;">
    <h3>Ablation Summary Table</h3>
    <table>
      <thead>
        <tr>
          <th>CF Density</th>
          <th>Sample N</th>
          <th>Mean Per-Row Evaluator Score</th>
          <th>Deadlock Rate</th>
          <th>Internal ULTRA_STRONG Classification</th>
          <th>Mean Entry-Text Tokens (see provenance)</th>
          <th>Elapsed Time (see scope)</th>
        </tr>
      </thead>
      <tbody>
        {"".join(f"<tr><td><strong>{r['config']}</strong></td><td>{r['total_tasks']}</td><td><span class='tag-pill tag-green'>{r['official_score']}</span></td><td>{r['deadlock_rate']}</td><td>{r['ultra_strong_rate']}</td><td>{r['avg_tokens']}t</td><td>{r['avg_time']}</td></tr>" for r in summary_rows)}
      </tbody>
    </table>
  </div>

  <div class="grid">
    <div class="card">
      <h3>Mean Per-Row Evaluator Score</h3>
      <img src="mean_evaluator_score_vs_density.png" alt="Mean evaluator-provided row score vs Density">
    </div>
    <div class="card">
      <h3>Internal Blackboard Classification</h3>
      <img src="intelligibility_vs_density.png" alt="Intelligibility vs Density">
    </div>
    <div class="card">
      <h3>Board-Entry Text Token Estimate</h3>
      <img src="tokens_vs_density.png" alt="Estimated board-entry text tokens vs Density">
    </div>
  </div>
</body>
</html>"""

    output_html_path.write_text(html_content, encoding="utf-8")
    logger.info("✓ Wrote interactive HTML report to %s", output_html_path)
    return output_html_path


# ─────────────────────────────────────────────────────────────────────────────
# CLI Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def _run_cli(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Benchmark Ablation Plotting Toolkit")
    parser.add_argument("--csv", "-c", default=None, help="Path to single result CSV.")
    parser.add_argument("--phase-dir", "-p", default="bench/results/week3_pilot", help="Path to phase directory containing result CSVs.")
    parser.add_argument("--out-dir", "-o", default=None, help="Output directory for charts.")
    args = parser.parse_args(argv)

    phase_dir = Path(args.phase_dir)
    charts_dir = Path(args.out_dir) if args.out_dir else (phase_dir / "charts")

    if args.csv:
        try:
            rows = load_results_csv(args.csv)
        except ValueError as exc:
            parser.error(str(exc))
        cfg = rows[0]["config_pct"] if rows else 100
        by_config = {cfg: rows}
    else:
        by_config = load_all_phase_csvs(phase_dir)

    if not by_config:
        parser.error(f"No verified official evaluation CSVs found in {phase_dir}; simulation/legacy results are not plotted.")
    charts = generate_ablation_charts(by_config, charts_dir)
    html_report = generate_interactive_html_report(by_config, charts_dir / "ablation_summary_report.html")

    print("\n═════════════════════════════════════════════════════════════════════")
    print(f"✅  Ablation Plotting Complete! Generated {len(charts)} chart(s) + 1 HTML report:")
    for c in charts:
        print(f"  📊  {c}")
    print(f"  🌐  {html_report}")
    print("═════════════════════════════════════════════════════════════════════")


if __name__ == "__main__":
    _run_cli(sys.argv[1:])
