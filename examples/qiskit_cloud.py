"""Run Qiskit V2 primitives on OQTOPUS Cloud without local transpilation."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from oqtopus_client import OqtopusClient, OqtopusConfig
from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister
from qiskit.quantum_info import SparsePauliOp

from openqarp_oqtopus.qiskit import OpenQARPOqtopusEstimatorV2, OpenQARPOqtopusSamplerV2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--section", default="default")
    parser.add_argument("--device-id", default="qulacs")
    parser.add_argument("--output", type=Path, default=Path("artifacts/remote-qiskit.json"))
    args = parser.parse_args()
    client = OqtopusClient(OqtopusConfig.from_file(args.section, path=args.config))
    sampler = OpenQARPOqtopusSamplerV2(device_id=args.device_id, client=client, n_shots=1000)
    estimator = OpenQARPOqtopusEstimatorV2(device_id=args.device_id, client=client)
    values = {}
    try:
        bell = QuantumCircuit(2)
        bell.h(0)
        bell.cx(0, 1)
        measured = bell.copy()
        measured.measure_all()
        values["bell_counts"] = sampler.run([measured]).result()[0].data.meas.get_counts()
        assert set(values["bell_counts"]) == {"00", "11"}
        assert abs(values["bell_counts"]["00"] / 1000 - 0.5) < 0.1
        print(json.dumps(values), flush=True)

        qubits = QuantumRegister(2, "q")
        low, high = ClassicalRegister(1, "low"), ClassicalRegister(1, "high")
        swapped = QuantumCircuit(qubits, low, high)
        swapped.x(0)
        swapped.measure(0, high[0])
        swapped.measure(1, low[0])
        result = sampler.run([swapped]).result()[0]
        values["low_counts"] = result.data.low.get_counts()
        values["high_counts"] = result.data.high.get_counts()
        assert values["low_counts"] == {"0": 1000}
        assert values["high_counts"] == {"1": 1000}
        print(json.dumps(values), flush=True)

        observable = SparsePauliOp.from_list([("ZZ", 1.0), ("XX", 0.5)])
        result = estimator.run([(bell, observable)], precision=0.05).result()[0]
        values["expectation"] = float(result.data.evs)
        values["std"] = float(result.data.stds)
        assert abs(values["expectation"] - 1.5) < 1e-10
        assert values["std"] == 0.0
    except Exception as error:
        report = {"ok": False, "error_type": type(error).__name__}
    else:
        report = {"ok": True}
    report.update(
        device_id=args.device_id, values=values,
        sampler_jobs=[asdict(j) for j in sampler.jobs],
        estimator_jobs=[asdict(j) for j in estimator.jobs],
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
