"""Shared synchronous OQTOPUS job transport."""

from copy import deepcopy
from dataclasses import replace
from math import isfinite
from numbers import Integral

from oqtopus_client import OqtopusJobSpec
from qarp.errors import CapabilityError

from ._results import JobRecord, OqtopusJobError, sampling_result


def _shots(value):
    if (isinstance(value, bool) or not isinstance(value, Integral)
            or not 1 <= value <= 10_000):
        raise CapabilityError("n_shots must be an integer between 1 and 10,000")
    return int(value)


class _CloudTransport:
    def __init__(self, *, device_id, client, n_shots=1000, timeout=300.0,
                 transpiler_info=None):
        if not isinstance(device_id, str) or not device_id.strip():
            raise ValueError("device_id must be a nonempty string")
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not isfinite(timeout) or timeout <= 0):
            raise ValueError("timeout must be positive and finite")
        self._device_id = device_id
        self._client = client
        self._n_shots = _shots(n_shots)
        self._timeout = float(timeout)
        self._transpiler_info = deepcopy(
            dict(transpiler_info) if transpiler_info is not None else {
                "transpiler_lib": "qiskit",
                "transpiler_options": {"optimization_level": 2},
            }
        )
        self._jobs = []

    @property
    def device_id(self):
        return self._device_id

    @property
    def n_shots(self):
        return self._n_shots

    @property
    def jobs(self):
        return tuple(self._jobs)

    def prepare(self, program, *, n_qubits, n_shots=None, n_bits=None):
        if isinstance(n_qubits, bool) or not isinstance(n_qubits, Integral) or n_qubits < 1:
            raise CapabilityError("Circuit width must be a positive integer")
        if n_bits is None:
            n_bits = n_qubits
        if isinstance(n_bits, bool) or not isinstance(n_bits, Integral) or n_bits < 1:
            raise CapabilityError("Classical register width must be a positive integer")
        if not isinstance(program, str) or not program.strip():
            raise ValueError("program must be a nonempty string")
        shots = _shots(self.n_shots if n_shots is None else n_shots)
        spec = OqtopusJobSpec.sampling(
            device_id=self.device_id, program=program, shots=shots,
            transpiler_info=deepcopy(self._transpiler_info),
        )
        spec.to_model()
        return spec, int(n_qubits), int(n_bits)

    def execute(self, prepared):
        results = []
        for spec, n_qubits, n_bits in prepared:
            submission = self._client.submit_job(spec)
            job_id = submission.job_id
            record = JobRecord(job_id, self.device_id, spec.shots, n_qubits, "submitted")
            index = len(self._jobs)
            self._jobs.append(record)
            try:
                result = self._client.wait_for_job(job_id, timeout=self._timeout)
                status = getattr(result.status, "value", result.status)
                self._jobs[index] = replace(record, status=status)
                if status != "succeeded":
                    raise OqtopusJobError("Cloud job did not succeed", job_id=job_id, status=status)
                results.append(sampling_result(
                    result, n_shots=spec.shots, n_qubits=n_bits, job_id=job_id
                ))
            except OqtopusJobError as error:
                self._jobs[index] = replace(record, status=error.status)
                raise
            except Exception as error:
                status = "timeout" if isinstance(error, TimeoutError) else "retrieval_error"
                self._jobs[index] = replace(record, status=status)
                raise OqtopusJobError(
                    "Unable to wait for Cloud job", job_id=job_id, status=status
                ) from error
        return results
