"""Run the separate programme from its committed public-data inputs."""
from pathlib import Path
import argparse
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true", help="Rebuild normalized inputs from parent repository's audited extraction caches")
    parser.add_argument("--collect-trades", action="store_true", help="Fetch the specified frozen 28-day public execution tape (resumable)")
    parser.add_argument("--report-only", action="store_true", help="Render charts/report from existing derived outputs")
    args = parser.parse_args()
    from midterm_monitor import attention, data, fundamentals, political, sensitivity, trades
    from midterm_monitor.reporting import build
    inputs, out = HERE / "inputs", HERE / "outputs"
    if args.prepare:
        data.prepare(HERE.parents[1], inputs)
    if args.collect_trades:
        trades.collect(inputs)
    if not args.report_only:
        for name, module, destination in [
            ("attention", attention, HERE / "report/tables/attention"),
            ("political", political, out), ("fundamentals", fundamentals, out),
            ("executions", trades, out), ("sensitivities", sensitivity, out)]:
            print(f"Running {name}...", flush=True)
            module.run(inputs, destination)
    print(build(HERE), flush=True)


if __name__ == "__main__":
    main()
