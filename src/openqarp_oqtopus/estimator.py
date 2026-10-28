"""Native OQTOPUS estimation for OpenQARP Pauli observables."""

from collections.abc import Mapping
from math import isfinite

import qarpx as qx
from oqtopus_client import OqtopusEstimationOperator
from qarp.blocks import AnyBlock
from qarp.errors import CapabilityError
from qarp.operators import QubitOperator

from .sampler import OpenQARPOqtopusSampler


class OpenQARPOqtopusEstimator:
    """Estimate a Hermitian Pauli sum in one native Cloud estimation job."""

    def __init__(self, *, sampler: OpenQARPOqtopusSampler):
        self._sampler = sampler

    @property
    def device_id(self):
        return self._sampler.device_id

    def estimate(
        self, *, ket: AnyBlock, operator: QubitOperator,
        params: Mapping | None = None, n_shots: int | None = None, grouping=None,
    ) -> float:
        """Submit the circuit and complete observable to the Cloud estimator."""
        if grouping is not None:
            raise CapabilityError("Grouping is handled by the Cloud estimator")
        if not isinstance(operator, QubitOperator):
            raise TypeError("operator must be an OpenQARP QubitOperator")
        operators = []
        for term, coefficient in operator.terms.items():
            value = complex(coefficient)
            if not isfinite(value.real) or value.imag != 0.0:
                raise CapabilityError("operator must have finite real Pauli coefficients")
            if any(q < 0 or q >= ket.n_qubits for q, _ in term):
                raise CapabilityError("operator acts outside the ket register")
            pauli = " ".join(f"{op} {q}" for q, op in term) if term else "I 0"
            operators.append(OqtopusEstimationOperator(pauli=pauli, coeff=value.real))
        if not operators:
            operators.append(OqtopusEstimationOperator(pauli="I 0", coeff=0.0))
        bindings = {str(key): float(value) for key, value in (params or {}).items()}
        if any(not isfinite(value) for value in bindings.values()):
            raise CapabilityError("Circuit parameters must be finite")
        ket.build()
        commands = qx.substitute_all(ket.flatten(), bindings)
        if any(command.is_parametric() for command in commands):
            raise CapabilityError("Bind all circuit parameters before submitting")
        for command in commands:
            if any(not isfinite(float(p.evaluate({}))) for p in command.params):
                raise CapabilityError("Circuit parameters must be finite")
        program = qx.QASM3Emitter().emit(commands, ket.n_qubits)
        transport = self._sampler.executor._transport
        prepared = transport.prepare_estimation(
            program, n_qubits=ket.n_qubits, operators=operators, n_shots=n_shots,
        )
        return transport.execute([prepared])[0].exp_value
