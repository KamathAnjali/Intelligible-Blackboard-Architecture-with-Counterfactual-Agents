"""
Benchmark Plotting & Ablation Analytics Toolkit — Student 4 (UI & Benchmarking)
bench/metrics/plotting.py

W3 Day 3 / Day 5 Implementation:
- Ingests single or multi-configuration benchmark result CSVs.
- Generates publication-ready evaluation charts:
  1. Accuracy (Agreement / Convergence Rate) vs Counterfactual Density (0%, 33%, 66%, 100%)
  2. Intelligibility Classification Depth vs Counterfactual Density
  3. Token Cost & Overhead vs Counterfactual Density
- Generates an interactive standalone HTML dashboard (`ablation_summary_report.html`).
"""

from __future__ import annotations

import argparse
import csv
import logging
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
    """Load and type-cast a benchmark results CSV."""
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Result CSV not found: {path}")

    rows: List[Dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for r in reader:
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
                "elapsed_time_s": float(r["elapsed_time_s"]),
                "dry_run": r.get("dry_run", "").lower() == "true",
                "timestamp": r["timestamp"],
                "notes": r.get("notes", ""),
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

    target_csvs = pilot_csvs if pilot_csvs else (dryrun_csvs if dryrun_csvs else (example_csvs if example_csvs else all_other_csvs))

    by_config: Dict[int, List[Dict[str, Any]]] = {}
    for f in target_csvs:
        rows = load_results_csv(f)
        if rows:
            cfg = rows[0]["config_pct"]
            by_config[cfg] = rows

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

        # 1. Accuracy / Agreement Rate vs Density
        p1 = charts_dir / "accuracy_vs_density.png"
        acc_rates = []
        for c in sorted_configs:
            rows = by_config[c]
            agreed = sum(1 for r in rows if r["outcome"] == "agreement")
            acc_rates.append((agreed / len(rows)) * 100 if rows else 0)

        plt.figure(figsize=(6.5, 4.2), dpi=160)
        plt.plot(sorted_configs, acc_rates, marker="o", linewidth=2.5, color="#10b981", markersize=8)
        plt.title("Convergence / Agreement Rate vs. Counterfactual Density", fontsize=12, fontweight="bold", pad=12)
        plt.xlabel("Counterfactual Participation Density (%)", fontsize=10)
        plt.ylabel("Agreement Rate (%)", fontsize=10)
        plt.xticks(sorted_configs, [f"{c}%" for c in sorted_configs])
        plt.ylim(0, 105)
        plt.grid(True, linestyle="--", alpha=0.5)
        for x, y in zip(sorted_configs, acc_rates):
            plt.text(x, y + 3, f"{y:.1f}%", ha="center", fontsize=9, fontweight="bold", color="#065f46")
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

        plt.title("Intelligibility Classification vs. Counterfactual Density", fontsize=12, fontweight="bold", pad=12)
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
        plt.title("Mean Token Consumption vs. Counterfactual Density", fontsize=12, fontweight="bold", pad=12)
        plt.xlabel("Counterfactual Participation Density (%)", fontsize=10)
        plt.ylabel("Mean Total Tokens / Task", fontsize=10)
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
        agreed = sum(1 for r in rows if r["outcome"] == "agreement")
        deadlocked = sum(1 for r in rows if r["outcome"] == "deadlock")
        ultra = sum(1 for r in rows if r["intelligibility_classification"] == "ULTRA_STRONG")
        avg_tok = sum(r["total_tokens"] for r in rows) / max(1, total_tasks)
        avg_time = sum(r["elapsed_time_s"] for r in rows) / max(1, total_tasks)

        summary_rows.append({
            "config": f"{c}%",
            "total_tasks": total_tasks,
            "accuracy": f"{(agreed / total_tasks) * 100:.1f}%",
            "deadlock_rate": f"{(deadlocked / total_tasks) * 100:.1f}%",
            "ultra_strong_rate": f"{(ultra / total_tasks) * 100:.1f}%",
            "avg_tokens": f"{avg_tok:.0f}",
            "avg_time": f"{avg_time:.3f}s",
        })

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Week 3 Pilot Ablation Study — Intelligible Blackboard</title>
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
  <h1>📊 Week 3 Pilot Study — Counterfactual Participation Ablation</h1>
  <p class="subtitle">Evaluated on KramaBench symbolic reasoning tasks across 0%, 33%, 66%, and 100% density configurations.</p>
  
  <div class="card" style="margin-bottom: 24px;">
    <h3>Ablation Summary Table</h3>
    <table>
      <thead>
        <tr>
          <th>CF Density</th>
          <th>Sample N</th>
          <th>Agreement / Accuracy</th>
          <th>Deadlock Rate</th>
          <th>ULTRA_STRONG Intelligibility</th>
          <th>Mean Tokens / Task</th>
          <th>Avg Latency</th>
        </tr>
      </thead>
      <tbody>
        {"".join(f"<tr><td><strong>{r['config']}</strong></td><td>{r['total_tasks']}</td><td><span class='tag-pill tag-green'>{r['accuracy']}</span></td><td>{r['deadlock_rate']}</td><td>{r['ultra_strong_rate']}</td><td>{r['avg_tokens']}t</td><td>{r['avg_time']}</td></tr>" for r in summary_rows)}
      </tbody>
    </table>
  </div>

  <div class="grid">
    <div class="card">
      <h3>Accuracy vs. Density</h3>
      <img src="accuracy_vs_density.png" alt="Accuracy vs Density">
    </div>
    <div class="card">
      <h3>Intelligibility Classification</h3>
      <img src="intelligibility_vs_density.png" alt="Intelligibility vs Density">
    </div>
    <div class="card">
      <h3>Token Consumption Overhead</h3>
      <img src="tokens_vs_density.png" alt="Tokens vs Density">
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
        rows = load_results_csv(args.csv)
        cfg = rows[0]["config_pct"] if rows else 100
        by_config = {cfg: rows}
    else:
        by_config = load_all_phase_csvs(phase_dir)

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
