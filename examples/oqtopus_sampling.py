"""Submit X0, X1 and Bell circuits to an explicitly configured Cloud account."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from oqtopus_client import OqtopusClient, OqtopusConfig
from qarp.blocks import SimpleBlock

from openqarp_oqtopus import OpenQARPOqtopusSampler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--section", default="default")
    parser.add_argument("--device-id", default="qulacs")
    parser.add_argument("--shots", type=int, default=1000)
    parser.add_argument("--output", type=Path, default=Path("artifacts/remote-sampling.json"))
    args = parser.parse_args()
    sampler = OpenQARPOqtopusSampler(
        device_id=args.device_id,
        client=OqtopusClient(OqtopusConfig.from_file(args.section, path=args.config)),
        n_shots=args.shots,
    )
    distributions = {}
    try:
        for qubit, expected in ((0, (1, 0)), (1, (0, 1))):
            block = SimpleBlock(2)
            block.x(qubit)
            result = sampler.sample(ket=block)
            distributions[f"x{qubit}"] = {str(k): float(v) for k, v in result.items()}
            assert dict(result) == {expected: 1.0}, "Unexpected bit order or counts"
            print(json.dumps({f"x{qubit}": distributions[f"x{qubit}"]}), flush=True)
        bell = SimpleBlock(2)
        bell.h(0)
        bell.cx(0, 1)
        result = sampler.sample(ket=bell)
        distributions["bell"] = {str(k): float(v) for k, v in result.items()}
        assert set(result) == {(0, 0), (1, 1)}
        assert abs(result[(0, 0)] - 0.5) < 0.1
    except Exception as error:
        report = {"ok": False, "error_type": type(error).__name__}
    else:
        report = {"ok": True}
    report.update(
        device_id=args.device_id,
        distributions=distributions,
        jobs=[asdict(job) for job in sampler.jobs],
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
