"""Offline tests for the OpenQARP to OQTOPUS sampler adapter."""

from types import SimpleNamespace

import pytest
import qarp
from qarp.algorithms import Sampler, StateVector
from qarp.blocks import SimpleBlock
from qarp.errors import CapabilityError
from sympy import Symbol

from openqarp_oqtopus import OpenQARPOqtopusSampler


class MockClient:
    """Small SDK stand-in; each submitted job consumes one predetermined result."""

    def __init__(self, counts_by_job):
        self.counts_by_job = list(counts_by_job)
        self.devices = []
        self.submitted = []
        self.waited = []

    def get_device(self, device_id):
        self.devices.append(device_id)
        return SimpleNamespace(
            device_id=device_id,
            n_qubits=16,
            basis_gates=["sx", "x", "rz", "cx"],
            supported_instructions=["measure"],
            status="available",
            device_type="simulator",
        )

    def submit_job(self, spec):
        index = len(self.submitted)
        self.submitted.append(spec)
        return SimpleNamespace(job_id=f"job-{index}")

    def wait_for_job(self, job_id, *, timeout):
        self.waited.append((job_id, timeout))
        index = int(job_id.removeprefix("job-"))
        return SimpleNamespace(
            status="succeeded",
            counts_with_integer_keys=lambda: {"counts": self.counts_by_job[index]},
        )


def make_sampler(counts_by_job, **kwargs):
    client = MockClient(counts_by_job)
    sampler = OpenQARPOqtopusSampler(device_id="qulacs", client=client, **kwargs)
    return sampler, client


def bell_block():
    block = SimpleBlock(2, name="bell")
    block.h(0)
    block.cx(0, 1)
    return block


def test_sample_bell_counts_and_records_job():
    sampler, client = make_sampler([{0: 501, 3: 499}], n_shots=1000, timeout=12)
    block = bell_block()

    result = sampler.sample(ket=block)

    assert set(result) == {(0, 0), (1, 1)}
    assert result[(0, 0)] == pytest.approx(0.501)
    assert result[(1, 1)] == pytest.approx(0.499)
    assert sum(result.values()) == pytest.approx(1)
    assert client.devices == []
    assert client.submitted[0].shots == 1000
    assert client.submitted[0].device_id == "qulacs"
    assert client.waited == [("job-0", 12)]
    assert sampler.device_id == "qulacs"
    assert sampler.jobs
    assert isinstance(sampler.jobs, tuple)
    with pytest.raises((AttributeError, TypeError)):
        sampler.device_id = "other-device"


@pytest.mark.parametrize(
    ("qubit", "integer_key", "expected_bits"),
    [(0, 1, (1, 0)), (1, 2, (0, 1))],
)
def test_counts_are_decoded_in_openqarp_lsb_order(qubit, integer_key, expected_bits):
    sampler, _ = make_sampler([{integer_key: 32}], n_shots=32)
    block = SimpleBlock(2)
    block.x(qubit)

    assert dict(sampler.sample(ket=block)) == {expected_bits: 1.0}


def test_sample_binds_parameters_and_per_call_shots_override():
    theta = Symbol("theta")
    sampler, client = make_sampler([{1: 30, 0: 70}], n_shots=500)
    block = SimpleBlock(1)
    block.ry(0, theta)

    sampler.sample(ket=block, params={theta: 1.2}, n_shots=100)

    spec = client.submitted[0]
    assert spec.shots == 100
    assert "theta" not in spec.program
    assert "1.2" in spec.program


def test_sample_does_not_mutate_the_input_block():
    sampler, _ = make_sampler([{0: 4, 3: 4}], n_shots=8)
    block = bell_block()
    block.build()
    before = tuple(map(str, block.flatten()))

    sampler.sample(ket=block)

    assert tuple(map(str, block.flatten())) == before


def test_exact_sampling_and_statevector_primitive_are_rejected_before_submit():
    sampler, client = make_sampler([])
    block = bell_block()

    with pytest.raises((ValueError, CapabilityError, NotImplementedError)):
        sampler.sample(ket=block, n_shots=qarp.EXACT)
    with pytest.raises((ValueError, CapabilityError, NotImplementedError)):
        sampler.executor.build([StateVector(ket=block)])

    assert client.submitted == []


def test_mid_circuit_measurement_is_rejected_before_submit():
    sampler, client = make_sampler([])
    block = SimpleBlock(1)
    block.measure([(0, 0)])
    block.x(0)

    with pytest.raises((ValueError, CapabilityError, NotImplementedError)):
        sampler.sample(ket=block)

    assert client.submitted == []


@pytest.mark.parametrize("counts", [{}, {4: 8}, {0: -1, 1: 9}, {0: 8.0}, {0: 7}, {True: 8}])
def test_invalid_counts_preserve_job_and_raise(counts):
    from openqarp_oqtopus import OqtopusResultError

    sampler, client = make_sampler([counts], n_shots=8)
    with pytest.raises(OqtopusResultError) as caught:
        sampler.sample(ket=bell_block())
    assert caught.value.job_id == "job-0"
    assert sampler.jobs[-1].status == "invalid_result"
    assert len(client.submitted) == 1


def test_canonical_terminal_measurements_are_not_duplicated():
    sampler, client = make_sampler([{0: 8}], n_shots=8)
    block = SimpleBlock(2)
    block.measure([(0, 0), (1, 1)])
    sampler.sample(ket=block)
    assert client.submitted[0].program.count("measure") == 2


@pytest.mark.parametrize("mapping", [[(0, 0)], [(0, 1), (1, 0)]])
def test_partial_or_permuted_measurements_are_rejected(mapping):
    sampler, client = make_sampler([])
    block = SimpleBlock(2)
    block.measure(mapping)
    with pytest.raises(CapabilityError):
        sampler.sample(ket=block)
    assert not client.submitted


def test_marginalization_respects_requested_order():
    sampler, _ = make_sampler([{1: 3, 2: 5}], n_shots=8)
    result = sampler.sample(ket=bell_block(), measured_qubits=[1, 0])
    assert dict(result) == {(0, 1): 0.375, (1, 0): 0.625}


@pytest.mark.parametrize("params", [{}, {"theta": float("nan")}, {"theta": float("inf")}])
def test_missing_or_nonfinite_parameters_are_not_submitted(params):
    sampler, client = make_sampler([])
    block = SimpleBlock(1)
    block.ry(0, Symbol("theta"))
    with pytest.raises(CapabilityError):
        sampler.sample(ket=block, params=params)
    assert not client.submitted


def test_sweep_preserves_set_and_primitive_order_and_shot_priority():
    sampler, client = make_sampler([{0: 4}, {1: 7}, {1: 4}, {2: 7}], n_shots=2)
    theta = Symbol("theta")
    first = SimpleBlock(1)
    first.ry(0, theta)
    primitives = [Sampler(ket=first, n_shots=4), Sampler(ket=bell_block(), n_shots=7)]
    results = sampler.executor.batch_run(primitives, [{theta: 0}, {theta: 3.14159}])
    assert [[dict(result) for result in row] for row in results] == [
        [{(0,): 1.0}, {(1, 0): 1.0}], [{(1,): 1.0}, {(0, 1): 1.0}],
    ]
    assert [job.shots for job in client.submitted] == [4, 7, 4, 7]
    client.counts_by_job.extend([{0: 8}, {0: 8}])
    sampler.executor.batch_run(primitives, [{theta: 0}], n_shots=8, rebuild=False)
    assert [job.shots for job in client.submitted][-2:] == [8, 8]


def test_sweep_preflights_all_parameter_sets_before_sending():
    sampler, client = make_sampler([])
    theta = Symbol("theta")
    block = SimpleBlock(1)
    block.ry(0, theta)
    with pytest.raises(CapabilityError):
        sampler.executor.batch_run([Sampler(ket=block)], [{theta: 0.2}, {}])
    assert not client.submitted


def test_runtime_shot_mutations_are_checked():
    sampler, client = make_sampler([])
    primitive = Sampler(ket=bell_block())
    sampler.executor.build([primitive])
    primitive.n_shots = qarp.EXACT
    with pytest.raises(CapabilityError):
        sampler.executor.run()
    assert not client.submitted


def test_device_capacity_is_left_to_the_server():
    sampler, client = make_sampler([{0: 8}], n_shots=8)
    sampler.sample(ket=SimpleBlock(17))
    assert len(client.submitted) == 1
    assert not client.devices


def test_gate_stream_is_emitted_without_local_transpilation(monkeypatch):
    import qarpx as qx

    def forbidden(*args, **kwargs):
        raise AssertionError("Local transpilation must not be invoked")

    monkeypatch.setattr(qx, "Transpiler", forbidden)
    sampler, client = make_sampler([{0: 8}], n_shots=8)
    block = SimpleBlock(2)
    block.cz(0, 1)
    sampler.sample(ket=block)
    assert "cz q[0], q[1]" in client.submitted[0].program
    assert not client.devices


@pytest.mark.parametrize("shots", [10_001, 0, -1, True, 1.5])
def test_shot_limits_are_enforced_before_submission(shots):
    sampler, client = make_sampler([])
    with pytest.raises(CapabilityError):
        sampler.sample(ket=SimpleBlock(1), n_shots=shots)
    assert not client.submitted


def test_device_selection_is_independent_between_instances():
    client = MockClient([{0: 8}, {0: 8}])
    for device_id in ("qulacs", "another-simulator"):
        sampler = OpenQARPOqtopusSampler(device_id=device_id, client=client, n_shots=8)
        sampler.sample(ket=SimpleBlock(1))
    assert [job.device_id for job in client.submitted] == ["qulacs", "another-simulator"]


def test_submission_failure_is_not_retried():
    sampler, client = make_sampler([])
    attempts = []

    def fail(spec):
        attempts.append(spec)
        raise ConnectionError("submission interrupted")

    client.submit_job = fail
    with pytest.raises(ConnectionError):
        sampler.sample(ket=SimpleBlock(1))
    assert len(attempts) == 1
    assert not sampler.jobs
