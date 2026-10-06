from __future__ import annotations

import argparse

from protocol_optimization_agent.agents.pipeline import ProtocolOptimizationAgent
from protocol_optimization_agent.runtime.config import ProtocolOptimizationConfig


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Pancreas ProtocolOptimizationAgent pipeline.")
    parser.add_argument("--config", required=True, help="Path to ProtocolOptimizationAgent JSON config.")
    args = parser.parse_args()

    config = ProtocolOptimizationConfig.from_file(args.config)
    state = ProtocolOptimizationAgent(config).run()

    print(f"ProtocolOptimizationAgent run_id={state.run_id}")
    print(f"output_dir={state.config.output_dir}")
    if state.missing_inputs:
        print(f"missing_inputs={','.join(state.missing_inputs)}")
    if state.warnings:
        print(f"warnings={'; '.join(state.warnings)}")


if __name__ == "__main__":
    main()
