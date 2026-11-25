"""Run one upstream AuctionNet market episode and record its actual dimensions."""

import argparse
import csv
import importlib
import importlib.metadata
import inspect
import json
import math
import os
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parent.parent
UPSTREAM = ROOT / "external" / "AuctionNet"


def json_value(value):
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    if hasattr(value, "tolist"):
        return json_value(value.tolist())
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Non-finite result: %r" % value)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pv-num", type=int, default=10000)
    parser.add_argument("--check-model", action="store_true")
    parser.add_argument("--model-pv-num", type=int, default=4096)
    parser.add_argument("--output", type=Path, default=ROOT / "work/auctionnet/market-smoke.json")
    args = parser.parse_args()
    if args.pv_num < 1000 or not 2 <= args.model_pv_num <= 105000:
        parser.error("Use at least 1000 market opportunities and 2..105000 model samples")

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    sys.path.insert(0, str(UPSTREAM))
    sys.path.append(str(UPSTREAM / "strategy_train_env"))
    os.chdir(UPSTREAM)

    import gin
    import numpy as np
    import torch
    upstream_runner = importlib.import_module("run.run_test")
    from simul_bidding_env.strategy.iql_bidding_strategy import IqlBiddingStrategy
    from simul_bidding_env.Tracker.PlayerAnalysis import PlayerAnalysis
    from simul_bidding_env.strategy.base_bidding_strategy import BaseBiddingStrategy
    from simul_bidding_env.Environment.BiddingEnv import BiddingEnv

    torch.set_num_threads(2)
    torch.manual_seed(1)
    np.random.seed(1)
    gin.parse_config_file(str(UPSTREAM / "config/test.gin"))
    gin.bind_parameter("run_test.num_episode", 1)
    gin.bind_parameter("Controller.pv_num", args.pv_num)

    # The default player entry point requires a separately trained saved_model.
    # Reuse the bundled market IQL policy for this environment-only startup probe.
    upstream_runner.initialize_player_agent = IqlBiddingStrategy

    ticks = []
    original_log = PlayerAnalysis.logging_player_tick
    fields = list(inspect.signature(original_log).parameters)[1:]

    def record_tick(self, *values, **kwargs):
        record = dict(zip(fields, values))
        record.update(kwargs)
        ticks.append(json_value(record))
        return original_log(self, *values, **kwargs)

    PlayerAnalysis.logging_player_tick = record_tick
    shapes = set()
    original_auction = BiddingEnv.simulate_ad_bidding

    def record_auction(self, values, sigmas, bids):
        shapes.add((tuple(values.shape), tuple(sigmas.shape), tuple(bids.shape)))
        return original_auction(self, values, sigmas, bids)

    BiddingEnv.simulate_ad_bidding = record_auction
    started = time.monotonic()
    result = upstream_runner.run_test(player_index=0)
    elapsed = time.monotonic() - started
    actual_opportunities = sum(row["tick_compete_pv"] for row in ticks)

    if len(ticks) != 48 or [row["tick"] for row in ticks] != list(range(48)):
        raise RuntimeError("Expected exactly 48 consecutive market ticks")
    if any(row["tick_cost"] < 0 or row["tick_value"] < 0 for row in ticks):
        raise RuntimeError("Unexpected negative market outcome")
    if float(result["allCost"]) > float(result["budget"]) + 1e-6:
        raise RuntimeError("Player exceeded its period budget")

    source = json.loads((ROOT / "external/auctionnet-source.json").read_text())
    data_csv = UPSTREAM / "simul_bidding_env/PvGenerator/model_utils/data/generated_105K_pv_data.csv"
    with data_csv.open(encoding="utf-8-sig", newline="") as handle:
        traffic_columns = next(csv.reader(handle))
    with (ROOT / "pkg/history/day_01.jsonl").open() as handle:
        fixture_fields = list(json.loads(next(handle)))

    report = {
        "status": "completed",
        "scope": "market startup check; no LLM, Harbor, or cross-day business layer",
        "upstream_commit": source["archive_metadata"]["comment"],
        "python": sys.version,
        "packages": {name: importlib.metadata.version(name) for name in (
            "numpy", "pandas", "torch", "scipy", "gin-config", "einops", "func-timeout")},
        "configuration": {
            "generator": "neuripsPvGen", "episodes": 1, "ticks_per_episode": 48,
            "advertisers": 48, "categories": 6, "advertisers_per_category": 8,
            "requested_opportunities": args.pv_num, "cpu_threads": 2,
            "actual_opportunities": actual_opportunities,
            "device": "cpu", "elapsed_seconds": elapsed,
            "reduced_traffic_smoke_run": actual_opportunities <= args.pv_num < 500000,
            "upstream_reset_caveat": "NeurIPSPvGen.reset calls __init__(episode) and restores default traffic parameters; requested pv_num may not survive reset",
            "player_policy": "bundled official IQL; environment probe only",
            "player_entry_adaptation": "Use bundled policy instead of absent user-trained saved_model; upstream files unchanged",
        },
        "result": result,
        "ticks": ticks,
        "auction_array_shapes": [list(shape) for shape in sorted(shapes)],
        "decision_interface": str(inspect.signature(BaseBiddingStrategy.bidding)),
        "auction_output_fields": [
            "xi", "slot", "cost", "is_exposed", "conversion_action",
            "least_winning_cost", "market_prices"],
        "traffic_csv_columns": traffic_columns,
        "current_ipinyou_fixture_fields": fixture_fields,
        "model_generator_check": {"status": "not_requested"},
    }
    # Preserve the successful market result even if the optional learned generator fails.
    output.write_text(json.dumps(json_value(report), ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"market": "completed", "ticks": len(ticks),
                      "reward": json_value(result["reward"]),
                      "cost": json_value(result["allCost"]),
                      "seconds": round(elapsed, 3)}, ensure_ascii=False), flush=True)

    if args.check_model:
        from simul_bidding_env.PvGenerator.ModelPvGen import ModelPvGenerator
        started = time.monotonic()
        try:
            generator = ModelPvGenerator(num_tick=48, num_agent_category=8,
                                         select_category=[1, 2, 3, 4, 5, 6],
                                         pv_num=args.model_pv_num, batch_size=1024)
            count = sum(len(values) for values in generator.pv_values)
            for values, sigmas in zip(generator.pv_values, generator.pValueSigmas):
                if values.shape != sigmas.shape or not np.isfinite(values).all():
                    raise RuntimeError("Invalid generated market values")
            model_report = {
                "status": "completed", "opportunities": count,
                "ticks": len(generator.pv_values),
                "feature_shapes": [list(value.shape) for value in generator.pv_features],
                "value_shapes": [list(value.shape) for value in generator.pv_values],
                "sigma_shapes": [list(value.shape) for value in generator.pValueSigmas],
                "feature_fields": generator.predictor.feature_info_dict,
                "model_input_dimension": generator.predictor.all_feature_dim,
                "device": generator.predictor.device,
                "elapsed_seconds": time.monotonic() - started,
            }
        except Exception as error:
            model_report = {"status": "failed", "error_type": type(error).__name__,
                            "error": str(error), "elapsed_seconds": time.monotonic() - started}
            raise
        finally:
            report["model_generator_check"] = model_report
            output.write_text(json.dumps(json_value(report), ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"model_generator": "completed", "opportunities": count,
                          "seconds": round(model_report["elapsed_seconds"], 3)},
                         ensure_ascii=False), flush=True)
    print("Result: %s" % output, flush=True)


if __name__ == "__main__":
    main()
