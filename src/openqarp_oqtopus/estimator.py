"""Pauli expectation values using the shared Cloud sampling executor."""

from collections.abc import Mapping
from math import isfinite

from qarp.algorithms import PauliAveraging
from qarp.blocks import AnyBlock
from qarp.errors import CapabilityError
from qarp.operators import QubitOperator

from .sampler import OpenQARPOqtopusSampler


class OpenQARPOqtopusEstimator:
    """Estimate a Hermitian Pauli sum; shots are allocated per measurement group."""

    def __init__(self, *, sampler: OpenQARPOqtopusSampler):
        self._sampler = sampler

    @property
    def device_id(self):
        return self._sampler.device_id

    def estimate(
        self, *, ket: AnyBlock, operator: QubitOperator,
        params: Mapping | None = None, n_shots: int | None = None, grouping=None,
    ) -> float:
        """Estimate a real Pauli sum, with n_shots samples per measurement group."""
        if not isinstance(operator, QubitOperator):
            raise TypeError("operator must be an OpenQARP QubitOperator")
        magnitude = 0.0
        for term, coefficient in operator.terms.items():
            value = complex(coefficient)
            if not isfinite(value.real) or value.imag != 0.0:
                raise CapabilityError("operator must have finite real Pauli coefficients")
            magnitude += abs(value.real)
            if any(q >= ket.n_qubits for q, _ in term):
                raise CapabilityError("operator acts outside the ket register")
        if not isfinite(magnitude):
            raise CapabilityError("Sum of absolute Pauli coefficients exceeds float range")
        primitive = PauliAveraging(
            ket=ket, operator=operator, n_shots=n_shots, grouping=grouping
        )
        executor = self._sampler.executor
        executor.build([primitive])
        result = float(executor.run(params=params or {})[0])
        if not isfinite(result):
            raise ValueError("Estimated expectation is outside finite float range")
        return result
