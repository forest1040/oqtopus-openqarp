"""Estimator behavior using an in-process stand-in for the cloud transport."""

from types import SimpleNamespace

import pytest
import qarpx as qx

from qarp.blocks import SimpleBlock
from qarp.operators import QubitOperator
from openqarp_oqtopus import OpenQARPOqtopusEstimator, OpenQARPOqtopusSampler


class SimulatedClient:
    def __init__(self):
        self.jobs = []

    def get_device(self, device_id):
        return SimpleNamespace(
            device_id=device_id, n_qubits=32, status="available",
            basis_gates=["x", "sx", "rz", "cx"], supported_instructions=[],
        )

    def submit_job(self, spec):
        self.jobs.append(spec)
        return SimpleNamespace(job_id=str(len(self.jobs) - 1))

    def wait_for_job(self, job_id, **kwargs):
        spec = self.jobs[int(job_id)]
        source = spec.program if isinstance(spec.program, str) else spec.program[0]
        commands, width, _, symbols = qx.QASM3Absorber().absorb(source)
        assert not symbols
        result = qx.QarpSimulator().run(commands, width, spec.shots, 17)
        return SimpleNamespace(
            status="succeeded",
            counts_with_integer_keys=lambda: {
                "counts": result.counts, "divided_counts": {},
            },
        )


def estimator():
    client = SimulatedClient()
    sampler = OpenQARPOqtopusSampler(device_id="qulacs", client=client)
    return OpenQARPOqtopusEstimator(sampler=sampler), client


def test_bell_state_expectation_has_correct_basis_rotations():
    est, client = estimator()
    ket = SimpleBlock(2)
    ket.h(0)
    ket.cx(0, 1)
    operator = QubitOperator("Z0 Z1") + QubitOperator("X0 X1", 0.5)
    value = est.estimate(ket=ket, operator=operator, n_shots=128)
    assert isinstance(value, float)
    assert value == pytest.approx(1.5)
    assert client.jobs


def test_asymmetric_qubit_expectations():
    est, _ = estimator()
    ket = SimpleBlock(2)
    ket.x(0)
    assert est.estimate(ket=ket, operator=QubitOperator("Z0"), n_shots=64) == pytest.approx(-1)
    assert est.estimate(ket=ket, operator=QubitOperator("Z1"), n_shots=64) == pytest.approx(1)


def test_identity_only_requires_no_cloud_job():
    est, client = estimator()
    ket = SimpleBlock(2)
    value = est.estimate(ket=ket, operator=QubitOperator("", 2.5), n_shots=64)
    assert value == pytest.approx(2.5)
    assert client.jobs == []


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_failed_cloud_job_preserves_status_and_job_id(status):
    from openqarp_oqtopus import OqtopusJobError

    est, client = estimator()
    client.wait_for_job = lambda job_id, **kwargs: SimpleNamespace(status=status)
    with pytest.raises(OqtopusJobError) as caught:
        est.estimate(ket=SimpleBlock(1), operator=QubitOperator("Z0"), n_shots=64)
    assert caught.value.job_id == "0"
    assert caught.value.status == status
    assert est._sampler.jobs[-1].status == status


def test_timeout_preserves_job_for_later_recovery():
    from openqarp_oqtopus import OqtopusJobError

    est, client = estimator()

    def timeout(job_id, **kwargs):
        raise TimeoutError("deadline")

    client.wait_for_job = timeout
    with pytest.raises(OqtopusJobError) as caught:
        est.estimate(ket=SimpleBlock(1), operator=QubitOperator("Z0"), n_shots=64)
    assert caught.value.job_id == "0"
    assert caught.value.status == "timeout"
    assert est._sampler.jobs[-1].status == "timeout"


@pytest.mark.parametrize("coefficient", [1j, float("inf"), float("nan")])
def test_invalid_observable_is_rejected_without_submission(coefficient):
    from qarp.errors import CapabilityError

    est, client = estimator()
    with pytest.raises(CapabilityError):
        est.estimate(ket=SimpleBlock(1), operator=QubitOperator("Z0", coefficient))
    assert not client.jobs


def test_overflowing_coefficient_sum_is_rejected_without_submission():
    from qarp.errors import CapabilityError

    est, client = estimator()
    operator = QubitOperator("Z0", 1e308) + QubitOperator("Z1", 1e308)
    with pytest.raises(CapabilityError):
        est.estimate(ket=SimpleBlock(2), operator=operator)
    assert not client.jobs


def test_identity_only_checks_sdk_shot_limit():
    from qarp.errors import CapabilityError

    est, client = estimator()
    with pytest.raises(CapabilityError):
        est.estimate(ket=SimpleBlock(1), operator=QubitOperator("", 1), n_shots=10_000_001)
    assert not client.jobs


def test_noncommuting_groups_are_aggregated_against_analytic_value():
    est, client = estimator()
    operator = QubitOperator("Z0") + QubitOperator("X0", 0.5)
    value = est.estimate(ket=SimpleBlock(1), operator=operator, n_shots=4096)
    assert len(client.jobs) == 2
    assert value == pytest.approx(1.0, abs=0.03)
