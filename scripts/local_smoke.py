"""Verify the local CPU backend and prepare an OQTOPUS job without submission."""

import json
import platform
import subprocess
import sys
from pathlib import Path

import numpy as np
import qarp
import qarpx
from oqtopus_client import OqtopusJobSpec
from qarp.algorithms import Sampler, StateVector
from qarp.blocks import SimpleBlock
from qarp.emit import QASM3Emitter
from qarp.engines import QarpEngine
from qarp.operators import QubitOperator


ROOT = Path(__file__).resolve().parents[1]


def sample(block, shots=4000):
    primitive = Sampler(ket=block, n_shots=shots)
    engine = QarpEngine(seed=42)
    engine.build([primitive])
    return engine.run()[0]


def main():
    bell = SimpleBlock(2, name="bell")
    bell.h(0)
    bell.cx(0, 1)
    bell.build()

    distribution = sample(bell)
    assert set(distribution) == {(0, 0), (1, 1)}, distribution
    assert abs(distribution[(0, 0)] - 0.5) < 0.05, distribution
    assert np.isclose(sum(distribution.values()), 1.0)

    observable = QubitOperator("Z0 Z1") + 0.5 * QubitOperator("X0 X1")
    engine = QarpEngine(seed=42)
    engine.build([StateVector(ket=bell, operator=observable)])
    expectation = float(np.real(engine.run()[0]))
    assert np.isclose(expectation, 1.5), expectation

    asymmetric = {}
    for qubit, expected in ((0, (1, 0)), (1, (0, 1))):
        block = SimpleBlock(2, name=f"x{qubit}")
        block.x(qubit)
        block.build()
        result = sample(block, shots=128)
        assert dict(result) == {expected: 1.0}, result
        asymmetric[f"x{qubit}"] = {str(bits): float(p) for bits, p in result.items()}

    measured = SimpleBlock(2, name="measured_bell")
    measured.h(0)
    measured.cx(0, 1)
    measured.measure([(0, 0), (1, 1)])
    measured.build()
    measured_distribution = sample(measured)
    assert set(measured_distribution) == {(0, 0), (1, 1)}
    program = QASM3Emitter().emit(measured.flatten(), measured.n_qubits)
    assert "OPENQASM 3" in program and program.count("measure") == 2, program
    spec = OqtopusJobSpec.sampling(device_id="qulacs", program=program, shots=4000)
    assert spec.program == program and spec.shots == 4000
    spec.to_model()

    commits = {
        name: subprocess.check_output(
            ["git", "-C", str(ROOT / "ref" / name), "rev-parse", "HEAD"], text=True
        ).strip()
        for name in ("openqarp", "oqtopus-client")
    }
    report = {
        "python": sys.version,
        "platform": platform.platform(),
        "openqarp_version": qarp.__version__,
        "qarp_module": qarp.__file__,
        "qarpx_module": qarpx.__file__,
        "reference_commits": commits,
        "bell_probabilities": {str(bits): float(p) for bits, p in distribution.items()},
        "bell_expectation_ZZ_plus_half_XX": expectation,
        "asymmetric_probabilities": asymmetric,
        "qasm3": program,
        "oqtopus_job_spec_created": True,
        "oqtopus_job_submitted": False,
    }
    output = ROOT / "artifacts"
    output.mkdir(exist_ok=True)
    (output / "local-smoke.json").write_text(json.dumps(report, indent=2) + "\n")
    (output / "bell.qasm").write_text(program)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
