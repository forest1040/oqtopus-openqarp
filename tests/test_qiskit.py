"""Qiskit V2 primitive adapter tests with a fully offline Cloud client."""

from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("qiskit")

from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister  # noqa: E402
from qiskit.circuit import Parameter  # noqa: E402
from qiskit.primitives import BaseEstimatorV2, BaseSamplerV2  # noqa: E402
from qiskit.primitives.containers import SamplerPub  # noqa: E402
from qarp.errors import CapabilityError  # noqa: E402

from openqarp_oqtopus.qiskit import (  # noqa: E402
    OpenQARPOqtopusEstimatorV2,
    OpenQARPOqtopusSamplerV2,
)


class MockClient:
    """Each submitted Cloud spec consumes a supplied integer-keyed counts map."""

    def __init__(self, counts):
        self.counts = list(counts)
        self.submitted = []
        self.waited = []
        self.devices = []

    def get_device(self, device_id):
        self.devices.append(device_id)
        return SimpleNamespace(
            device_id=device_id,
            n_qubits=16,
            status="available",
            device_type="simulator",
            basis_gates=["sx", "x", "rz", "cx"],
            supported_instructions=["measure"],
        )

    def submit_job(self, spec):
        index = len(self.submitted)
        self.submitted.append(spec)
        return SimpleNamespace(job_id=f"job-{index}")

    def wait_for_job(self, job_id, *, timeout):
        self.waited.append((job_id, timeout))
        index = int(job_id.removeprefix("job-"))
        counts = self.counts[index]
        return SimpleNamespace(
            status="succeeded",
            counts_with_integer_keys=lambda: {"counts": counts},
        )


def sampler(counts, **kwargs):
    client = MockClient(counts)
    primitive = OpenQARPOqtopusSamplerV2(
        device_id="qulacs", client=client, **kwargs
    )
    return primitive, client


def test_sampler_v2_implements_qiskit_base_and_samples_x0():
    primitive, client = sampler([{1: 100}], n_shots=100, timeout=17)
    circuit = QuantumCircuit(1, 1, name="x0")
    circuit.x(0)
    circuit.measure(0, 0)

    result = primitive.run([circuit]).result()

    assert isinstance(primitive, BaseSamplerV2)
    assert result[0].data.c.get_counts() == {"1": 100}
    assert result[0].data.c.num_shots == 100
    assert client.devices == []
    assert client.submitted[0].shots == 100
    assert client.submitted[0].device_id == "qulacs"
    assert client.waited == [("job-0", 17)]
    assert result[0].metadata["shot_order"] == "unavailable_counts_expanded"
    assert result[0].metadata["job_ids"] == ["job-0"]


def test_sampler_v2_routes_classical_bits_and_splits_registers():
    primitive, client = sampler([{2: 5, 1: 3}], n_shots=8)
    qubits = QuantumRegister(2, "q")
    low = ClassicalRegister(1, "low")
    high = ClassicalRegister(1, "high")
    circuit = QuantumCircuit(qubits, low, high)
    # q0 is measured to high[0], q1 to low[0]; result integer bit 1 is set.
    circuit.x(qubits[0])
    circuit.measure(qubits[0], high[0])
    circuit.measure(qubits[1], low[0])

    pub_result = primitive.run([circuit]).result()[0]

    assert pub_result.data.low.get_counts() == {"0": 5, "1": 3}
    assert pub_result.data.high.get_counts() == {"1": 5, "0": 3}
    # The same expanded joint shot sequence is projected into both registers.
    low_samples = pub_result.data.low.get_bitstrings()
    high_samples = pub_result.data.high.get_bitstrings()
    assert len(low_samples) == len(high_samples) == 8
    assert sum(a == "1" and b == "1" for a, b in zip(low_samples, high_samples)) == 0
    assert len(client.submitted) == 1


def test_sampler_v2_binds_parameter_sweep_without_losing_pub_shape():
    primitive, client = sampler([{0: 16}, {1: 16}], n_shots=16)
    theta = Parameter("theta")
    circuit = QuantumCircuit(1, 1)
    circuit.ry(theta, 0)
    circuit.measure(0, 0)

    pub_result = primitive.run([(circuit, [[0.0], [np.pi]])]).result()[0]

    assert pub_result.data.c.shape == (2,)
    assert pub_result.data.c.get_counts(loc=0) == {"0": 16}
    assert pub_result.data.c.get_counts(loc=1) == {"1": 16}
    assert len(client.submitted) == 2
    assert all(spec.shots == 16 for spec in client.submitted)
    assert "theta" not in client.submitted[0].program
    assert "theta" not in client.submitted[1].program


def test_sampler_v2_run_shots_override_is_used_for_each_pub():
    primitive, client = sampler([{0: 37}], n_shots=10)
    circuit = QuantumCircuit(1, 1)
    circuit.measure(0, 0)

    primitive.run([circuit], shots=37).result()

    assert client.submitted[0].shots == 37


def test_sampler_v2_pub_shots_override_run_shots_in_pub_order():
    primitive, client = sampler([{0: 3}, {0: 5}], n_shots=2)
    circuit = QuantumCircuit(1, 1)
    circuit.measure(0, 0)

    results = primitive.run(
        [SamplerPub(circuit, shots=3), SamplerPub(circuit, shots=5)], shots=7
    ).result()

    assert [spec.shots for spec in client.submitted] == [3, 5]
    assert [pub.data.c.num_shots for pub in results] == [3, 5]


def test_sampler_v2_rejects_more_than_ten_thousand_shots_before_submission():
    primitive, client = sampler([{0: 10_001}], n_shots=10_000)
    circuit = QuantumCircuit(1, 1)
    circuit.measure(0, 0)

    with pytest.raises(CapabilityError):
        primitive.run([circuit], shots=10_001).result()

    assert client.submitted == []


def test_estimator_v2_returns_expectation_and_standard_deviation():
    client = MockClient([{0: 4, 3: 4}])
    circuit = QuantumCircuit(2)
    circuit.h(0)
    circuit.cx(0, 1)

    result = OpenQARPOqtopusEstimatorV2(
        device_id="qulacs", client=client, n_shots=8
    ).run([(circuit, "ZZ")]).result()

    assert isinstance(result[0].data.evs, np.ndarray)
    assert result[0].data.evs.item() == pytest.approx(1.0)
    assert result[0].data.stds.item() == pytest.approx(0.0)
    assert client.submitted[0].shots == 8
    assert "OPENQASM 3" in client.submitted[0].program


def test_estimator_v2_precision_overrides_default_shots():
    client = MockClient([{0: 16}])
    primitive = OpenQARPOqtopusEstimatorV2(
        device_id="qulacs", client=client, n_shots=16
    )
    circuit = QuantumCircuit(1)

    result = primitive.run([(circuit, "Z")]).result()

    assert isinstance(primitive, BaseEstimatorV2)
    assert primitive.default_precision == pytest.approx(0.25)
    assert client.devices == []
    assert client.submitted[0].shots == 16
    assert result[0].data.evs.item() == pytest.approx(1)


def test_estimator_v2_broadcasts_observable_grid_over_parameter_sweep():
    client = MockClient([
        {0: 4}, {1: 4}, {0: 2, 1: 2}, {0: 2, 1: 2},
    ])
    primitive = OpenQARPOqtopusEstimatorV2(
        device_id="qulacs", client=client, n_shots=4
    )
    theta = Parameter("theta")
    circuit = QuantumCircuit(1)
    circuit.ry(theta, 0)

    result = primitive.run(
        [(circuit, np.array([["Z"], ["X"]]), [[0.0], [np.pi]])], precision=0.5
    ).result()[0]

    assert result.data.evs.shape == (2, 2)
    np.testing.assert_allclose(result.data.evs, [[1, -1], [0, 0]])
    assert result.data.stds[1, 0] == pytest.approx(np.sqrt(1 / 3))
    assert len(client.submitted) == 4
    assert all(spec.shots == 4 for spec in client.submitted)


@pytest.mark.parametrize(
    ("gate", "observable", "expected"),
    [("h", "X", 1.0), ("y_plus", "Y", 1.0)],
)
def test_estimator_v2_uses_qiskit_x_and_y_measurement_bases(gate, observable, expected):
    client = MockClient([{0: 8}])
    primitive = OpenQARPOqtopusEstimatorV2(device_id="qulacs", client=client, n_shots=8)
    circuit = QuantumCircuit(1)
    if gate == "h":
        circuit.h(0)
    else:
        circuit.h(0)
        circuit.s(0)

    result = primitive.run([(circuit, observable)]).result()[0]

    assert result.data.evs.item() == pytest.approx(expected)
    if observable == "Y":
        assert "sdg" in client.submitted[0].program.lower()


def test_estimator_v2_identity_only_returns_constant_without_jobs():
    client = MockClient([])
    primitive = OpenQARPOqtopusEstimatorV2(device_id="qulacs", client=client)

    result = primitive.run([(QuantumCircuit(1), "I")]).result()[0]

    assert result.data.evs.item() == pytest.approx(1)
    assert result.metadata["shots_per_term"] == 0
    assert client.submitted == []


@pytest.mark.parametrize("precision", [0, -0.1, float("nan")])
def test_estimator_v2_rejects_invalid_precision(precision):
    client = MockClient([])
    primitive = OpenQARPOqtopusEstimatorV2(device_id="qulacs", client=client)

    with pytest.raises(ValueError):
        primitive.run([(QuantumCircuit(1), "Z")], precision=precision)

    assert client.submitted == []


def test_estimator_v2_rejects_precision_requiring_over_ten_thousand_shots():
    client = MockClient([])
    primitive = OpenQARPOqtopusEstimatorV2(device_id="qulacs", client=client)

    with pytest.raises(CapabilityError):
        primitive.run([(QuantumCircuit(1), "Z")], precision=0.0099)

    assert client.submitted == []


def test_qiskit_custom_gate_is_serialized_without_local_transpilation():
    client = MockClient([{0: 8}])
    primitive = OpenQARPOqtopusSamplerV2(device_id="qulacs", client=client, n_shots=8)
    definition = QuantumCircuit(1)
    definition.h(0)
    custom_gate = definition.to_gate(label="custom_h")
    circuit = QuantumCircuit(1, 1)
    circuit.append(custom_gate, [0])
    circuit.measure(0, 0)

    primitive.run([circuit]).result()

    program = client.submitted[0].program
    assert "gate " in program
    assert "h _gate_q_0;" in program
    assert "h q[0];" not in program


def test_estimator_large_finite_coefficients_do_not_overflow_variance():
    from qiskit.quantum_info import SparsePauliOp

    client = MockClient([{0: 2}])
    primitive = OpenQARPOqtopusEstimatorV2(device_id="qulacs", client=client)
    observable = SparsePauliOp.from_list([("Z", 1e200)])
    result = primitive.run(
        [(QuantumCircuit(1), observable)], precision=1e200 / np.sqrt(2)
    ).result()[0]
    assert result.data.evs.item() == pytest.approx(1e200)
    assert result.data.stds.item() == 0
