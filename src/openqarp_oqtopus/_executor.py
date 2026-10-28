"""OpenQARP execution hooks backed by the synchronous Cloud client."""

from math import isfinite

import qarpx as qx
from qarp.blocks import SimpleBlock
from qarp.engines import Engine as _QarpExecutorBase
from qarp.errors import CapabilityError

from ._transport import _CloudTransport, _shots


class _OqtopusCloudExecutor(_QarpExecutorBase):
    supports_initial_state = False
    gradient_methods = frozenset()

    def __init__(self, *, device_id, client, n_shots, timeout, transpiler_info=None):
        self._transport = _CloudTransport(
            device_id=device_id, client=client, n_shots=n_shots, timeout=timeout,
            transpiler_info=transpiler_info,
        )
        self._n_shots = self._transport.n_shots
        self._seed = None
        self._primitives = []
        self._emitter = qx.QASM3Emitter()

    @property
    def device_id(self):
        return self._transport.device_id

    @property
    def jobs(self):
        return self._transport.jobs

    @property
    def provides_amplitudes(self):
        return False

    def _compile_stamp(self):
        return (type(self), self.device_id, "qasm3-pass-through-measure-all-v2")

    def _validate_primitive(self, prim, shots_override=None):
        super()._validate_primitive(prim, shots_override)
        _shots(self._resolve_shots(prim, shots_override))
        if getattr(prim, "requires_noiseless", False):
            raise CapabilityError("Cloud execution does not guarantee noiseless measurement")

    def _commands(self, commands, n_qubits):
        if isinstance(n_qubits, bool) or not isinstance(n_qubits, int) or n_qubits < 1:
            raise CapabilityError("Circuit width must be a positive integer")
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
        compiled = list(commands)
        if not measured:
            layer = SimpleBlock(n_qubits)
            layer.measure([(q, q) for q in range(n_qubits)])
            layer.build()
            compiled.extend(layer.flatten())
        return compiled

    def _compile_one(self, flat, block_n_qubits):
        return self._commands(flat, block_n_qubits), block_n_qubits, None

    def _pre_run(self):
        for primitive in self._primitives:
            self._validate_primitive(primitive)

    def _prepare(self, primitive, compiled, params, shots_override=None):
        self._validate_primitive(primitive, shots_override)
        n_shots = _shots(self._resolve_shots(primitive, shots_override))
        if any(not isfinite(float(value)) for value in params.values()):
            raise CapabilityError("Circuit parameters must be finite")
        prepared = []
        for circuit in compiled:
            if circuit.stamp != self._compile_stamp() or circuit.program is not None:
                raise CapabilityError("Rebuild circuits with this executor before running")
            commands = qx.substitute_all(circuit.commands, params)
            if any(c.is_parametric() for c in commands):
                raise CapabilityError("Bind all circuit parameters before submitting")
            for command in commands:
                if any(not isfinite(float(p.evaluate({}))) for p in command.params):
                    raise CapabilityError("Circuit parameters must be finite")
            commands = self._commands(commands, circuit.n_qubits)
            program = self._emitter.emit(commands, circuit.n_qubits)
            prepared.append(self._transport.prepare(
                program, n_qubits=circuit.n_qubits, n_shots=n_shots,
            ))
        return prepared

    def _execute(self, prepared):
        return self._transport.execute(prepared)

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
