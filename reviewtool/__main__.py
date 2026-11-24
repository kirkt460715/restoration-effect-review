"""命令行入口：python3 -m reviewtool run --data data --out review_out [--as-of 日期]"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .core import run_review
from .model import Config, load_dataset
from .report import render_markdown, run_to_json


def _summary_lines(run) -> list[str]:
    lines = []
    for window, ids in run.impact["现可排续建组合"]:
        lines.append(f"{window} 可签：{'、'.join(ids)}")
    for rid, _ in run.impact["需补证合同"]:
        lines.append(f"合同 {rid} 待补证")
    for rid in run.impact["已验收维持但需人工复议"]:
        lines.append(f"已验收 {rid} 维持原结论，需人工复议")
    for item in run.unsignable:
        lines.append(f"不能签出：{item}")
    return lines


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="reviewtool", description="生态修复成效复核与续建核定（季度复用）")
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run", help="执行季度复核并输出报告")
    run.add_argument("--data", default="data", help="资料目录（默认 data）")
    run.add_argument("--out", default="review_out", help="输出目录（默认 review_out）")
    run.add_argument("--as-of", default=None, help="复核截止日 YYYY-MM-DD（默认今天）")
    run.add_argument("--min-effect", type=float, default=4.0,
                     help="续建适用最低相对成效（百分点，默认 4.0）")
    args = parser.parse_args(argv)
    if args.command in (None, "run"):
        as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
        dataset = load_dataset(args.data, as_of=as_of)
        config = Config(renewal_min_effect_pp=args.min_effect)
        result = run_review(dataset, config)
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "report.md").write_text(render_markdown(result), encoding="utf-8")
        (out_dir / "report.json").write_text(
            json.dumps(run_to_json(result), ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"复核截止 {as_of}：报告已写入 {out_dir}/report.md 与 report.json")
        for line in _summary_lines(result):
            print(" -", line)
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
