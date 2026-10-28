"""Qiskit V2 primitives using OQTOPUS Cloud and server-side transpilation.

Import this module after installing ``openqarp-oqtopus[qiskit]``.
Sampler bit arrays expand aggregate counts; temporal shot order is unavailable.
"""

from concurrent.futures import ThreadPoolExecutor
from math import ceil, hypot, isfinite, nextafter, sqrt
from numbers import Real
from threading import Lock
from uuid import uuid4

import numpy as np
from qiskit import qasm3
from qiskit.primitives import (
    BaseEstimatorV2, BasePrimitiveJob, BaseSamplerV2, BitArray, DataBin,
    PrimitiveResult, PubResult, SamplerPubResult,
)
from qiskit.primitives.containers import EstimatorPub, SamplerPub
from qiskit.providers import JobStatus
from qarp.errors import CapabilityError

from ._transport import _CloudTransport, _shots


class _CloudJob(BasePrimitiveJob):
    """Local asynchronous job; cancel() cannot stop an executing Cloud job."""

    def __init__(self, function, *args):
        super().__init__(str(uuid4()))
        pool = ThreadPoolExecutor(max_workers=1)
        self._future = pool.submit(function, *args)
        pool.shutdown(wait=False)

    def result(self, timeout=None):
        return self._future.result(timeout=timeout)

    def cancel(self):
        return self._future.cancel()

    def status(self):
        if self.cancelled():
            return JobStatus.CANCELLED
        if self.running():
            return JobStatus.RUNNING
        if self._future.done():
            return JobStatus.ERROR if self._future.exception() else JobStatus.DONE
        return JobStatus.INITIALIZING

    def done(self):
        return self.status() is JobStatus.DONE

    def running(self):
        return self._future.running()

    def cancelled(self):
        return self._future.cancelled()

    def in_final_state(self):
        return self.status() in (JobStatus.DONE, JobStatus.ERROR, JobStatus.CANCELLED)


def _qasm(circuit):
    if circuit.parameters:
        raise ValueError("Bind all circuit parameters before submission")
    for instruction in circuit.data:
        for parameter in instruction.operation.params:
            if isinstance(parameter, Real) and not isfinite(parameter):
                raise ValueError("Circuit parameters must be finite")
    if not isfinite(float(circuit.global_phase)):
        raise ValueError("Circuit global phase must be finite")
    # Serialization only: do not decompose, route, rebase or optimize the circuit.
    return qasm3.dumps(circuit)


def _registers(circuit):
    if not circuit.num_clbits:
        raise ValueError("Sampler circuits must have classical bits and measurements")
    registers = {
        register.name: [circuit.find_bit(bit).index for bit in register]
        for register in circuit.cregs
    }
    if {i for bits in registers.values() for i in bits} != set(range(circuit.num_clbits)):
        raise ValueError("Sampler classical bits must belong to classical registers")
    DataBin(**{name: None for name in registers})
    return registers


def _joint_samples(counts, n_bits, shots):
    """Expand joint counts once so register slices preserve their correlations."""
    width = (n_bits + 7) // 8
    packed = np.empty((shots, width), dtype=np.uint8)
    start = 0
    for value, count in counts.items():
        packed[start:start + count] = np.frombuffer(int(value).to_bytes(width, "big"), dtype=np.uint8)
        start += count
    return BitArray(packed, n_bits)


class _PrimitiveConnection:
    def __init__(self, *, device_id, client, n_shots=1000, timeout=300.0, transpiler_info=None):
        self._transport = _CloudTransport(
            device_id=device_id, client=client, n_shots=n_shots, timeout=timeout,
            transpiler_info=transpiler_info,
        )
        self._lock = Lock()

    @property
    def device_id(self):
        return self._transport.device_id

    @property
    def jobs(self):
        return self._transport.jobs


class OpenQARPOqtopusSamplerV2(_PrimitiveConnection, BaseSamplerV2):
    """Sample QuantumCircuits through Cloud without a local transpile call.

    PUB-specific shots override run(shots=...), then constructor n_shots.
    Results contain a BitArray for each classical register. BitArray order
    is an expansion of aggregate counts, not the original execution order.
    """

    def run(self, pubs, *, shots=None):
        default = _shots(self._transport.n_shots if shots is None else shots)
        plans = []
        for item in pubs:
            pub = SamplerPub.coerce(item, shots=default)
            pub_shots = _shots(pub.shots)
            registers = _registers(pub.circuit)
            circuits = pub.parameter_values.bind_all(pub.circuit)
            prepared = [
                self._transport.prepare(
                    _qasm(circuits[index]), n_qubits=pub.circuit.num_qubits,
                    n_bits=pub.circuit.num_clbits, n_shots=pub_shots,
                )
                for index in np.ndindex(pub.shape)
            ]
            plans.append((pub.shape, registers, pub.circuit.num_clbits, pub_shots, prepared))
        return _CloudJob(self._run, plans)

    def _run(self, plans):
        with self._lock:
            pub_results = []
            for shape, registers, n_bits, shots, prepared in plans:
                start = len(self.jobs)
                raw = self._transport.execute(prepared)
                packed = np.empty(shape + (shots, (n_bits + 7) // 8), dtype=np.uint8)
                for index, result in zip(np.ndindex(shape), raw, strict=True):
                    packed[index] = _joint_samples(result.counts, n_bits, shots).array
                joint = BitArray(packed, n_bits)
                fields = {name: joint.slice_bits(bits) for name, bits in registers.items()}
                pub_results.append(SamplerPubResult(
                    DataBin(shape=shape, **fields), metadata={
                        "shots": shots, "device_id": self.device_id,
                        "job_ids": [job.job_id for job in self.jobs[start:]],
                        "shot_order": "unavailable_counts_expanded",
                    },
                ))
            return PrimitiveResult(pub_results, metadata={"version": 2})


def _precision(value):
    if isinstance(value, bool) or not isinstance(value, Real) or not isfinite(value) or value <= 0:
        raise ValueError("precision must be finite and positive")
    return float(value)


def _term_shots(coefficients, precision):
    norm = hypot(*coefficients)
    ratio = norm / precision
    required = ratio * ratio
    if not isfinite(required) or required > 10_000 + 1e-10:
        raise CapabilityError("Requested precision requires more than 10,000 shots per Pauli term")
    return _shots(max(1, ceil(nextafter(required, -np.inf))))


def _measurement(circuit, label):
    measured = circuit.remove_final_measurements(inplace=False)
    mask = 0
    for qubit, pauli in enumerate(reversed(label)):
        if pauli == "X":
            measured.h(qubit)
        elif pauli == "Y":
            measured.sdg(qubit)
            measured.h(qubit)
        if pauli != "I":
            mask |= 1 << qubit
    # A fresh register allows original classical registers and mid-circuit records.
    from qiskit import ClassicalRegister
    names = {register.name for register in measured.cregs}
    name = "oqtopus_measure"
    while name in names:
        name += "_"
    offset = measured.num_clbits
    register = ClassicalRegister(measured.num_qubits, name)
    measured.add_register(register)
    measured.measure(range(measured.num_qubits), register)
    return measured, mask << offset


class OpenQARPOqtopusEstimatorV2(_PrimitiveConnection, BaseEstimatorV2):
    """Estimate Pauli sums, allocating independent shots to each Pauli term.

    precision is a statistical target. Per-term shots are chosen using the
    sum of squared coefficients; device noise and systematic bias are excluded.
    Returned stds use count-derived uncertainty, not a fabricated fixed error.
    """

    def __init__(
        self, *, device_id, client, n_shots=1000, timeout=300.0,
        transpiler_info=None, default_precision=None,
    ):
        super().__init__(
            device_id=device_id, client=client, n_shots=n_shots, timeout=timeout,
            transpiler_info=transpiler_info,
        )
        self.default_precision = _precision(
            1 / sqrt(n_shots) if default_precision is None else default_precision
        )

    def run(self, pubs, *, precision=None):
        default = _precision(self.default_precision if precision is None else precision)
        plans = []
        for item in pubs:
            pub = EstimatorPub.coerce(item, precision=default)
            target = _precision(pub.precision)
            bound = pub.parameter_values.bind_all(pub.circuit)
            observables = np.asarray(pub.observables.tolist(), dtype=object)
            bound, observables = np.broadcast_arrays(bound, observables)
            points = []
            for index in np.ndindex(pub.shape):
                terms, constant = [], 0.0
                for label, coefficient in observables[index].items():
                    coefficient = float(coefficient)
                    if not isfinite(coefficient):
                        raise ValueError("Pauli coefficients must be finite")
                    if set(label) <= {"I"}:
                        constant += coefficient
                    elif coefficient:
                        terms.append((label, coefficient))
                if not isfinite(constant):
                    raise ValueError("Identity coefficient exceeds float range")
                shots = _term_shots([c for _, c in terms], target) if terms else 0
                prepared, masks = [], []
                for label, coefficient in terms:
                    circuit, mask = _measurement(bound[index], label)
                    prepared.append(self._transport.prepare(
                        _qasm(circuit), n_qubits=circuit.num_qubits,
                        n_bits=circuit.num_clbits, n_shots=shots,
                    ))
                    masks.append((mask, coefficient))
                points.append((constant, shots, masks, prepared))
            plans.append((pub.shape, target, points))
        return _CloudJob(self._run, plans)

    def _run(self, plans):
        with self._lock:
            pub_results = []
            for shape, precision, points in plans:
                start = len(self.jobs)
                evs, stds = np.empty(shape), np.empty(shape)
                allocations = np.zeros(shape, dtype=int)
                for index, (constant, shots, masks, prepared) in zip(
                    np.ndindex(shape), points, strict=True
                ):
                    value, uncertainty = constant, 0.0
                    raw = self._transport.execute(prepared)
                    for (mask, coefficient), result in zip(masks, raw, strict=True):
                        mean = sum(
                            (1 if (outcome & mask).bit_count() % 2 == 0 else -1) * count
                            for outcome, count in result.counts.items()
                        ) / shots
                        value += coefficient * mean
                        variance_of_mean = (
                            max(0.0, 1 - mean ** 2) / (shots - 1) if shots > 1 else 1.0
                        )
                        uncertainty = hypot(uncertainty, coefficient * sqrt(variance_of_mean))
                    if not isfinite(value) or not isfinite(uncertainty):
                        raise ValueError("Estimated value or uncertainty exceeds float range")
                    evs[index], stds[index], allocations[index] = value, uncertainty, shots
                pub_results.append(PubResult(
                    DataBin(evs=evs, stds=stds, shape=shape), metadata={
                        "target_precision": precision, "device_id": self.device_id,
                        "shots_per_term": allocations.tolist(),
                        "job_ids": [job.job_id for job in self.jobs[start:]],
                    },
                ))
            return PrimitiveResult(pub_results, metadata={"version": 2})
