"""Job metadata and validated sampling and estimation results."""

from dataclasses import dataclass
from numbers import Integral


@dataclass(frozen=True)
class JobRecord:
    """A submitted circuit; timeout does not imply remote cancellation."""

    job_id: str
    device_id: str
    n_shots: int
    n_qubits: int
    status: str


class OqtopusJobError(RuntimeError):
    """A submitted job failed, was cancelled, or could not be retrieved."""

    def __init__(self, message: str, *, job_id: str, status: str):
        self.job_id = job_id
        self.status = status
        super().__init__(f"{message} (job_id={job_id}, status={status})")


class OqtopusResultError(OqtopusJobError):
    """A successful job returned invalid sampling or estimation data."""


@dataclass(frozen=True)
class _SamplingResult:
    counts: dict[int, int]
    n_shots: int
    n_qubits: int


def sampling_result(result, *, n_shots: int, n_qubits: int, job_id: str):
    def invalid(message):
        raise OqtopusResultError(message, job_id=job_id, status="invalid_result")

    try:
        payload = result.counts_with_integer_keys()
        counts = payload["counts"]
        if payload.get("divided_counts"):
            invalid("Divided counts are not supported")
        if not isinstance(counts, dict) or not counts:
            invalid("Missing or empty counts")
        normalized = {}
        for outcome, count in counts.items():
            if isinstance(outcome, bool) or not isinstance(outcome, Integral):
                invalid("Outcome keys must be integers")
            if not 0 <= outcome < (1 << n_qubits):
                invalid("Outcome is outside the circuit register")
            if isinstance(count, bool) or not isinstance(count, Integral) or count < 0:
                invalid("Counts must be nonnegative integers")
            normalized[int(outcome)] = int(count)
        if sum(normalized.values()) != n_shots:
            invalid("Counts do not sum to the requested shots")
    except OqtopusResultError:
        raise
    except Exception as error:
        raise OqtopusResultError(
            "Unable to read sampling counts", job_id=job_id, status="invalid_result"
        ) from error
    return _SamplingResult(normalized, n_shots, n_qubits)


@dataclass(frozen=True)
class _EstimationResult:
    exp_value: float
    stds: float


def estimation_result(result, *, job_id: str):
    from math import isfinite
    from numbers import Real

    try:
        value, stds = result.exp_value, result.stds
        if (isinstance(value, bool) or not isinstance(value, Real) or not isfinite(value)
                or isinstance(stds, bool) or not isinstance(stds, Real)
                or not isfinite(stds) or stds < 0):
            raise ValueError("Invalid expectation value or standard deviation")
        return _EstimationResult(float(value), float(stds))
    except Exception as error:
        raise OqtopusResultError(
            "Unable to read estimation result", job_id=job_id, status="invalid_result"
        ) from error
