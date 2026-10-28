"""Finite-shot sampling through OQTOPUS Cloud."""

from collections.abc import Mapping, Sequence

from oqtopus_client import OqtopusClient
from qarp import SamplingDistribution
from qarp.algorithms import Sampler
from qarp.blocks import AnyBlock
from qarp.errors import CapabilityError

from ._executor import _OqtopusCloudExecutor


class OpenQARPOqtopusSampler:
    """Synchronous sampling; the caller owns the injected SDK client.

    One instance is intended for sequential use. Device selection is fixed
    at construction. Jobs remain accessible after failure through ``jobs``.
    """

    def __init__(
        self, *, device_id: str, client: OqtopusClient, n_shots: int = 1000,
        timeout: float = 300.0, transpiler_info: Mapping | None = None,
    ):
        self._executor = _OqtopusCloudExecutor(
            device_id=device_id, client=client, n_shots=n_shots, timeout=timeout,
            transpiler_info=transpiler_info,
        )

    @property
    def device_id(self):
        return self._executor.device_id

    @property
    def executor(self):
        return self._executor

    @property
    def jobs(self):
        return self._executor.jobs

    def sample(
        self, *, ket: AnyBlock, params: Mapping | None = None,
        n_shots: int | None = None, measured_qubits: Sequence[int] | None = None,
    ) -> SamplingDistribution:
        """Return LSB-first bit tuples mapped to probabilities, blocking until done."""
        if measured_qubits is not None:
            measured_qubits = list(measured_qubits)
            if not measured_qubits or len(set(measured_qubits)) != len(measured_qubits):
                raise CapabilityError("measured_qubits must be nonempty and unique")
            if any(isinstance(q, bool) or not isinstance(q, int) or not 0 <= q < ket.n_qubits
                   for q in measured_qubits):
                raise CapabilityError("measured_qubits must contain valid qubit indices")
        primitive = Sampler(ket=ket, n_shots=n_shots, measured_qubits=measured_qubits)
        self._executor.build([primitive])
        return self._executor.run(params=params or {})[0]
