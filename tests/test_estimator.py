"""Native OQTOPUS estimation jobs using an offline SDK client stub."""

from types import SimpleNamespace

import pytest

from qarp.blocks import SimpleBlock
from qarp.errors import CapabilityError
from qarp.operators import QubitOperator
from oqtopus_client import OqtopusEstimationOperator

from openqarp_oqtopus import (
    OpenQARPOqtopusEstimator,
    OpenQARPOqtopusSampler,
    OqtopusJobError,
    OqtopusResultError,
)


class MockClient:
    def __init__(self, results):
        self.results = list(results)
        self.jobs = []
        self.waited = []

    def get_device(self, device_id):
        raise AssertionError("device metadata is delegated to the server")

    def submit_job(self, spec):
        job_id = f"job-{len(self.jobs)}"
        self.jobs.append(spec)
        return SimpleNamespace(job_id=job_id)

    def wait_for_job(self, job_id, *, timeout):
        self.waited.append((job_id, timeout))
        return self.results[int(job_id.removeprefix("job-"))]


def make_estimator(results, *, n_shots=64, timeout=300):
    client = MockClient(results)
    sampler = OpenQARPOqtopusSampler(
        device_id="qulacs", client=client, n_shots=n_shots, timeout=timeout
    )
    return OpenQARPOqtopusEstimator(sampler=sampler), sampler, client


def success(exp_value, stds=0.0, status="succeeded"):
    return SimpleNamespace(status=status, exp_value=exp_value, stds=stds)


def test_bell_expectation_uses_one_native_estimation_job_for_full_observable():
    estimator, sampler, client = make_estimator([success(1.5, 0.125)])
    ket = SimpleBlock(2)
    ket.h(0)
    ket.cx(0, 1)
    operator = QubitOperator("Z0 Z1") + QubitOperator("X0 X1", 0.5)

    value = estimator.estimate(ket=ket, operator=operator, n_shots=128)

    assert value == pytest.approx(1.5)
    assert len(client.jobs) == 1
    spec = client.jobs[0]
    assert getattr(spec.job_type, "value", spec.job_type) == "estimation"
    assert spec.shots == 128
    assert isinstance(spec.program, str)
    assert "OPENQASM 3" in spec.program
    assert "measure" not in spec.program.lower()
    assert [isinstance(term, OqtopusEstimationOperator) for term in spec.operator] == [True, True]
    assert [(term.pauli, term.coeff) for term in spec.operator] == [
        ("Z 0 Z 1", 1.0),
        ("X 0 X 1", 0.5),
    ]
    assert sampler.jobs[0].job_id == "job-0"
    assert sampler.jobs[0].n_shots == 128


def test_native_estimator_preserves_server_value_and_standard_deviation():
    estimator, _, client = make_estimator([success(-0.3125, 0.047)])
    ket = SimpleBlock(1)

    value = estimator.estimate(ket=ket, operator=QubitOperator("Z0"), n_shots=32)

    assert value == pytest.approx(-0.3125)
    assert client.jobs[0].operator[0].pauli == "Z 0"
    assert client.jobs[0].operator[0].coeff == 1.0


def test_identity_and_zero_observables_are_submitted_as_i0_terms():
    estimator, _, client = make_estimator([success(2.5), success(0.0)])

    identity_value = estimator.estimate(
        ket=SimpleBlock(2), operator=QubitOperator("", 2.5), n_shots=64
    )
    zero_value = estimator.estimate(
        ket=SimpleBlock(1), operator=QubitOperator(), n_shots=64
    )

    assert identity_value == pytest.approx(2.5)
    assert zero_value == pytest.approx(0.0)
    assert len(client.jobs) == 2
    assert [job.operator[0].pauli for job in client.jobs] == ["I 0", "I 0"]
    assert [job.operator[0].coeff for job in client.jobs] == [2.5, 0.0]
    assert all(getattr(job.job_type, "value", job.job_type) == "estimation" for job in client.jobs)


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_failed_native_estimation_preserves_status_and_job_id(status):
    estimator, sampler, client = make_estimator([success(None, None, status)])

    with pytest.raises(OqtopusJobError) as caught:
        estimator.estimate(ket=SimpleBlock(1), operator=QubitOperator("Z0"))

    assert caught.value.job_id == "job-0"
    assert caught.value.status == status
    assert sampler.jobs[-1].status == status
    assert len(client.jobs) == 1


def test_timeout_keeps_submitted_job_record():
    estimator, sampler, client = make_estimator([])

    def timeout(job_id, *, timeout):
        raise TimeoutError("deadline")

    client.wait_for_job = timeout
    with pytest.raises(OqtopusJobError) as caught:
        estimator.estimate(ket=SimpleBlock(1), operator=QubitOperator("Z0"))

    assert caught.value.job_id == "job-0"
    assert caught.value.status == "timeout"
    assert sampler.jobs[-1].status == "timeout"
    assert len(client.jobs) == 1


@pytest.mark.parametrize(
    "result",
    [
        SimpleNamespace(status="succeeded"),
        success(None, 0.1),
        success(1.0, None),
        success(float("nan"), 0.1),
        success(1.0, float("inf")),
    ],
)
def test_missing_or_nonfinite_estimation_result_is_rejected(result):
    estimator, sampler, client = make_estimator([result])

    with pytest.raises(OqtopusResultError) as caught:
        estimator.estimate(ket=SimpleBlock(1), operator=QubitOperator("Z0"))

    assert caught.value.job_id == "job-0"
    assert sampler.jobs[-1].status == "invalid_result"
    assert len(client.jobs) == 1


@pytest.mark.parametrize("coefficient", [1j, float("inf"), float("nan")])
def test_invalid_observable_is_rejected_without_submission(coefficient):
    estimator, _, client = make_estimator([])

    with pytest.raises(CapabilityError):
        estimator.estimate(
            ket=SimpleBlock(1), operator=QubitOperator("Z0", coefficient)
        )

    assert client.jobs == []


def test_non_none_grouping_is_rejected_before_submission():
    estimator, _, client = make_estimator([])

    with pytest.raises(CapabilityError):
        estimator.estimate(
            ket=SimpleBlock(1), operator=QubitOperator("Z0"), grouping="commuting"
        )

    assert client.jobs == []


def test_large_finite_coefficients_are_sent_without_local_aggregation():
    estimator, _, client = make_estimator([success(1e200)])
    operator = QubitOperator("Z0", 1e200) + QubitOperator("Z1", 1e200)

    value = estimator.estimate(ket=SimpleBlock(2), operator=operator)

    assert value == pytest.approx(1e200)
    assert len(client.jobs) == 1
    assert [(term.pauli, term.coeff) for term in client.jobs[0].operator] == [
        ("Z 0", 1e200), ("Z 1", 1e200)
    ]


def test_native_estimation_obeys_ten_thousand_shot_limit():
    estimator, _, client = make_estimator([], n_shots=10_000)

    with pytest.raises(CapabilityError):
        estimator.estimate(
            ket=SimpleBlock(1), operator=QubitOperator("Z0"), n_shots=10_001
        )

    assert client.jobs == []


def test_noncommuting_terms_are_sent_together_in_one_estimation_job():
    estimator, _, client = make_estimator([success(0.25, 0.5)])
    operator = QubitOperator("Z0") + QubitOperator("X0", 0.5)

    value = estimator.estimate(ket=SimpleBlock(1), operator=operator, n_shots=4096)

    assert value == pytest.approx(0.25)
    assert len(client.jobs) == 1
    assert [(term.pauli, term.coeff) for term in client.jobs[0].operator] == [
        ("Z 0", 1.0),
        ("X 0", 0.5),
    ]
    assert client.jobs[0].shots == 4096
