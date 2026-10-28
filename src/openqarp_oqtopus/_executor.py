"""OpenQARP execution hooks backed by the synchronous Cloud client."""

from copy import deepcopy
from dataclasses import replace
from math import isfinite
from numbers import Integral

import qarpx as qx
from oqtopus_client import OqtopusJobSpec
from qarp.blocks import SimpleBlock
from qarp.engines import Engine as _QarpExecutorBase
from qarp.errors import CapabilityError

from ._results import JobRecord, OqtopusJobError, sampling_result


def _shots(value):
    # Bound from oqtopus-client's JobsSubmitJobRequest schema.
    if (isinstance(value, bool) or not isinstance(value, Integral)
            or not 1 <= value <= 10_000_000):
        raise CapabilityError("n_shots must be an integer between 1 and 10,000,000")
    return int(value)


class _OqtopusCloudExecutor(_QarpExecutorBase):
    supports_initial_state = False
    gradient_methods = frozenset()

    def __init__(self, *, device_id, client, n_shots, timeout, transpiler_info=None):
        if not isinstance(device_id, str) or not device_id.strip():
            raise ValueError("device_id must be a nonempty string")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError("timeout must be positive and finite")
        if not isfinite(timeout) or timeout <= 0:
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
        self._seed = None
        self._primitives = []
        self._jobs = []
        self._device = None
        gate_set = qx.GateSet()
        gate_set.allowed = {
            qx.GateType.H, qx.GateType.X, qx.GateType.Rx, qx.GateType.Ry,
            qx.GateType.Rz, qx.GateType.CX, qx.GateType.GPhase,
            qx.GateType.Measure, qx.GateType.Barrier,
        }
        self._transpiler = qx.Transpiler(gate_set)
        self._emitter = qx.QASM3Emitter()

    @property
    def device_id(self):
        return self._device_id

    @property
    def jobs(self):
        return tuple(self._jobs)

    @property
    def provides_amplitudes(self):
        return False

    def _compile_stamp(self):
        return (type(self), self.device_id, "qasm3-standard-measure-all-v1")

    def _validate_primitive(self, prim, shots_override=None):
        super()._validate_primitive(prim, shots_override)
        _shots(self._resolve_shots(prim, shots_override))
        if getattr(prim, "requires_noiseless", False):
            raise CapabilityError("Cloud execution does not guarantee noiseless measurement")

    def _commands(self, commands, n_qubits):
        if not isinstance(n_qubits, int) or not 1 <= n_qubits <= 63:
            raise CapabilityError("Circuit width must be between 1 and 63 qubits")
        self._reject_true_mcm(commands, primitive_name="Cloud sampling", reason="")
        measured = []
        seen_measurement = False
        for command in commands:
            if command.gate == qx.GateType.Measure:
                seen_measurement = True
                if len(command.qubits) != 1 or command.cbits != command.qubits:
                    raise CapabilityError("Measurements must map q[i] to c[i]")
                measured.extend(command.qubits)
            elif seen_measurement and command.gate != qx.GateType.Barrier:
                raise CapabilityError("Only a terminal measurement layer is supported")
        if measured and sorted(measured) != list(range(n_qubits)):
            raise CapabilityError("Measure every qubit exactly once, or omit measurements")
        compiled = self._transpiler.transpile(commands)
        # Only top-level global phase is removed, after controlled gates are lowered.
        # It cannot affect finite-shot measurement probabilities.
        compiled = [c for c in compiled if c.gate != qx.GateType.GPhase]
        if not measured:
            layer = SimpleBlock(n_qubits)
            layer.measure([(q, q) for q in range(n_qubits)])
            layer.build()
            compiled.extend(layer.flatten())
        return compiled

    def _compile_one(self, flat, block_n_qubits):
        return self._commands(flat, block_n_qubits), block_n_qubits, None

    def _pre_run(self):
        self._device = None
        # No network traffic is needed for constant-only estimators.
        if not any(p.compiled for p in self._primitives):
            return
        self._device = self._client.get_device(self.device_id)
        if self._device.status != "available":
            raise CapabilityError(f"Device {self.device_id!r} is not available")
        for primitive in self._primitives:
            self._validate_primitive(primitive)
            for circuit in primitive.compiled:
                self._check_capacity(circuit.n_qubits)

    def _check_capacity(self, n_qubits):
        if self._device is None:
            self._device = self._client.get_device(self.device_id)
        if self._device.status != "available":
            raise CapabilityError(f"Device {self.device_id!r} is not available")
        capacity = self._device.n_qubits
        if capacity is not None and n_qubits > capacity:
            raise CapabilityError(f"Circuit needs {n_qubits} qubits; device has {capacity}")

    def _prepare(self, primitive, compiled, params, shots_override=None):
        self._validate_primitive(primitive, shots_override)
        n_shots = _shots(self._resolve_shots(primitive, shots_override))
        if any(not isfinite(float(value)) for value in params.values()):
            raise CapabilityError("Circuit parameters must be finite")
        prepared = []
        for circuit in compiled:
            if circuit.stamp != self._compile_stamp() or circuit.program is not None:
                raise CapabilityError("Rebuild circuits with this executor before running")
            self._check_capacity(circuit.n_qubits)
            commands = qx.substitute_all(circuit.commands, params)
            if any(c.is_parametric() for c in commands):
                raise CapabilityError("Bind all circuit parameters before submitting")
            for command in commands:
                if any(not isfinite(float(p.evaluate({}))) for p in command.params):
                    raise CapabilityError("Circuit parameters must be finite")
            commands = self._commands(commands, circuit.n_qubits)
            program = self._emitter.emit(commands, circuit.n_qubits)
            spec = OqtopusJobSpec.sampling(
                device_id=self.device_id, program=program, shots=n_shots,
                transpiler_info=deepcopy(self._transpiler_info),
            )
            spec.to_model()
            prepared.append((spec, circuit.n_qubits))
        return prepared

    def _execute(self, prepared):
        results = []
        for spec, n_qubits in prepared:
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
                    result, n_shots=spec.shots, n_qubits=n_qubits, job_id=job_id
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

    def _dispatch_one(self, prim, compiled, ordinal, params):
        prepared = self._prepare(prim, compiled, params)
        return prim.run(self._execute(prepared)), ordinal + len(prepared)

    def _sweep(self, primitives, compiled_per_prim, param_sets, shots_override):
        # Validate every set before submitting any sweep job.
        prepared_sets = [
            [self._prepare(p, circuits, params, shots_override)
             for p, circuits in zip(primitives, compiled_per_prim, strict=True)]
            for params in param_sets
        ]
        return [
            [p.run(self._execute(prepared))
             for p, prepared in zip(primitives, by_primitive, strict=True)]
            for by_primitive in prepared_sets
        ]
