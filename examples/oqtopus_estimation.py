"""Validate Cloud estimation and parameter sweeps against analytic values."""

import argparse
import json
from dataclasses import asdict
from math import pi
from pathlib import Path

from oqtopus_client import OqtopusClient, OqtopusConfig
from qarp.algorithms import Sampler
from qarp.blocks import SimpleBlock
from qarp.operators import QubitOperator
from sympy import Symbol

from openqarp_oqtopus import OpenQARPOqtopusEstimator, OpenQARPOqtopusSampler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--section", default="default")
    parser.add_argument("--device-id", default="qulacs")
    parser.add_argument("--shots", type=int, default=1000)
    parser.add_argument("--output", type=Path, default=Path("artifacts/remote-estimation.json"))
    args = parser.parse_args()
    sampler = OpenQARPOqtopusSampler(
        device_id=args.device_id,
        client=OqtopusClient(OqtopusConfig.from_file(args.section, path=args.config)),
        n_shots=args.shots,
    )
    estimator = OpenQARPOqtopusEstimator(sampler=sampler)
    values = {}
    try:
        bell = SimpleBlock(2)
        bell.h(0)
        bell.cx(0, 1)
        value = estimator.estimate(
            ket=bell, operator=QubitOperator("Z0 Z1") + 0.5 * QubitOperator("X0 X1")
        )
        values["bell_ZZ_plus_half_XX"] = value
        assert abs(value - 1.5) < 1e-10
        print(json.dumps(values), flush=True)

        asymmetric = SimpleBlock(2)
        asymmetric.x(0)
        value = estimator.estimate(
            ket=asymmetric, operator=QubitOperator("Z0") - 2 * QubitOperator("Z1")
        )
        values["x0_Z0_minus_twice_Z1"] = value
        assert abs(value + 3.0) < 1e-10
        before = len(sampler.jobs)
        values["constant"] = estimator.estimate(ket=asymmetric, operator=QubitOperator("", 2.5))
        assert values["constant"] == 2.5 and len(sampler.jobs) == before
        print(json.dumps(values), flush=True)

        theta = Symbol("theta")
        circuit = SimpleBlock(1)
        circuit.ry(0, theta)
        results = sampler.executor.batch_run(
            [Sampler(ket=circuit)], [{theta: angle} for angle in (0.0, pi / 3, pi)]
        )
        values["ry_sweep_p1"] = [row[0].get((1,), 0.0) for row in results]
        assert values["ry_sweep_p1"][0] == 0.0
        assert abs(values["ry_sweep_p1"][1] - 0.25) < 0.075
        assert values["ry_sweep_p1"][2] == 1.0
    except Exception as error:
        report = {"ok": False, "error_type": type(error).__name__}
    else:
        report = {"ok": True}
    report.update(device_id=args.device_id, values=values, jobs=[asdict(j) for j in sampler.jobs])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
