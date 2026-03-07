#!/usr/bin/env python3
"""Merge per-tier YAML files into a single queries.yaml."""
from pathlib import Path
import yaml

EVAL_DIR = Path(__file__).resolve().parent.parent
DATASET_DIR = EVAL_DIR / "datasets" / "intent-extraction"

TIER_FILES = ["tier_t1.yaml", "tier_t2.yaml", "tier_t3.yaml", "tier_t4.yaml", "tier_t5.yaml"]

def load_tier(path: Path) -> list[dict]:
    """Load a tier file, handling both raw list and queries-wrapped formats."""
    text = path.read_text()
    data = yaml.safe_load(text)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "queries" in data:
        return data["queries"]
    raise ValueError(f"Unexpected format in {path}")


def main():
    all_queries = []

    for tier_file in TIER_FILES:
        path = DATASET_DIR / tier_file
        if not path.exists():
            print(f"WARNING: {path} not found, skipping")
            continue
        queries = load_tier(path)
        print(f"  {tier_file}: {len(queries)} queries")
        all_queries.extend(queries)

    output = {"queries": all_queries}
    output_path = DATASET_DIR / "queries.yaml"
    with open(output_path, "w") as f:
        yaml.dump(output, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    print(f"\nMerged {len(all_queries)} queries -> {output_path}")

    # Summary by tier
    from collections import Counter
    tiers = Counter(q.get("tier", "?") for q in all_queries)
    for tier, count in sorted(tiers.items()):
        print(f"  {tier}: {count}")


if __name__ == "__main__":
    main()
