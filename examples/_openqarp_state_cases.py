"""Notebook-derived state-preparation and Trotter circuits for Cloud checks."""

import numpy as np

from qarp.blocks import (
    CompositeBlock,
    ComputationalBasisStateBlock,
    QROMBlock,
    ReadoutBlock,
    SimpleBlock,
    SparseStateBlock,
    TrotterBlock,
    UniformSuperpositionBlock,
)
from qarp.operators import FullyCommuting, JordanWigner
from qarp.operators.functions import count_qubits
from qarp.operators.models import fermi_hubbard


def _basis_index(bits):
    """Pack the LSB-first basis tuple as an integer outcome key."""
    return sum(int(bit) << qubit for qubit, bit in enumerate(bits))


def state_cases():
    """Build small notebook examples and their independent Born probabilities."""
    rng = np.random.default_rng(1)
    amplitudes = {}
    while len(amplitudes) < 2:
        address = tuple(int(bit) for bit in rng.integers(0, 2, size=8))
        amplitudes[address] = complex(rng.normal(), rng.normal())
    norm_squared = sum(abs(amplitude) ** 2 for amplitude in amplitudes.values())
    sparse = SparseStateBlock(n_qubits=8, amplitudes=amplitudes).build()
    sparse_probabilities = {
        _basis_index(bits): float(abs(amplitude) ** 2 / norm_squared)
        for bits, amplitude in amplitudes.items()
    }

    sparse_settings_amplitudes = [
        {"bits_lsb_first": list(bits), "real": float(amplitude.real),
         "imag": float(amplitude.imag)}
        for bits, amplitude in amplitudes.items()
    ]

    uniform_size = 11
    uniform = UniformSuperpositionBlock(uniform_size).build()

    fermionic_hamiltonian = fermi_hubbard((2,), t=0.14, U=0.231)
    qubit_hamiltonian = JordanWigner().encode_operator(fermionic_hamiltonian)
    n_trotter_qubits = count_qubits(qubit_hamiltonian)
    assert n_trotter_qubits == 4
    evolution = TrotterBlock(
        operator=qubit_hamiltonian,
        n_qubits=n_trotter_qubits,
        steps=1,
        time=1.0,
        order=2,
        grouping=FullyCommuting(),
    ).build()
    initial_occupation = [1, 0, 0, 0]
    trotter = CompositeBlock(
        [
            ComputationalBasisStateBlock(initial_occupation),
            evolution,
            ReadoutBlock(n_qubits=n_trotter_qubits),
        ]
    ).build()

    qrom_table = {
        0: (1, 0, 1),
        1: (0, 1, 1),
        2: (1, 1, 0),
        3: (0, 0, 1),
    }
    qrom = QROMBlock(index_qubits=2, data=qrom_table)
    index_superposition = SimpleBlock(2, name="H_index")
    index_superposition.h([0, 1])
    lookup = CompositeBlock([index_superposition, qrom]).build()
    qrom_probabilities = {}
    for index, data_bits in qrom_table.items():
        full_bits = (
            (index & 1),
            ((index >> 1) & 1),
            *data_bits,
        )
        qrom_probabilities[_basis_index(full_bits)] = 0.25

    t = 0.14
    return [
        {
            "id": "sparse-state-8q",
            "title": "Sparse complex state preparation (8 qubits)",
            "source": "ref/openqarp/examples/blocks/mwe_state_preparation.ipynb",
            "cells": [25],
            "block": sparse,
            "params": {},
            "measured_qubits": None,
            "settings": {
                "seed": 1,
                "n_qubits": 8,
                "support_size": 2,
                "amplitudes": sparse_settings_amplitudes,
            },
            "execution_scope": (
                "Build the notebook's seed-1 two-amplitude SparseStateBlock and sample it; "
                "the dense synthesis cost comparison is omitted."
            ),
            "expected_description": (
                "The two packed outcomes have probabilities |amplitude|² / norm² "
                f"({sparse_probabilities}); every other outcome has probability 0. "
                "Sampling verifies magnitudes only; the relative complex phase is not observed."
            ),
            "expected_probabilities": sparse_probabilities,
        },
        {
            "id": "uniform-superposition-11-of-16",
            "title": "Uniform superposition over 11 states",
            "source": "ref/openqarp/examples/blocks/mwe_state_preparation.ipynb",
            "cells": [19],
            "block": uniform,
            "params": {},
            "measured_qubits": None,
            "settings": {"M": uniform_size, "n_qubits": 4},
            "execution_scope": (
                "Sample the notebook's UniformSuperpositionBlock(M=11); "
                "statevector conformance diagnostics are omitted."
            ),
            "expected_description": (
                "Packed outcomes 0 through 10 each have probability 1/11; "
                "outcomes 11 through 15 have probability 0."
            ),
            "expected_probabilities": {outcome: 1 / uniform_size for outcome in range(uniform_size)},
        },
        {
            "id": "fermi-hubbard-trotter-one-particle",
            "title": "One-particle two-site Fermi-Hubbard Trotter evolution",
            "source": "ref/openqarp/examples/blocks/mwe_trotter.ipynb",
            "cells": [1, 3],
            "block": trotter,
            "params": {},
            "measured_qubits": None,
            "settings": {
                "dims": [2],
                "hopping_t": t,
                "onsite_U": 0.231,
                "initial_occupation_lsb_first": initial_occupation,
                "time": 1.0,
                "steps": 1,
                "order": 2,
                "grouping": "FullyCommuting",
                "n_qubits": n_trotter_qubits,
            },
            "execution_scope": (
                "Use the notebook's two-site Fermi-Hubbard Hamiltonian and Trotter settings, "
                "with a one-particle initial state plus terminal readout."
            ),
            "expected_description": (
                "In the one-particle sector, only the alpha-spin site-0/site-1 hopping acts; "
                "the interaction term vanishes and this commuting subspace evolves exactly. "
                f"Expected P(q0 occupied) = cos²({t}) = {float(np.cos(t) ** 2):.12f}, "
                f"P(q2 occupied) = sin²({t}) = {float(np.sin(t) ** 2):.12f}; other outcomes 0."
            ),
            "expected_probabilities": {
                _basis_index((1, 0, 0, 0)): float(np.cos(t) ** 2),
                _basis_index((0, 0, 1, 0)): float(np.sin(t) ** 2),
            },
        },
        {
            "id": "qrom-superposed-lookup",
            "title": "QROM lookup over a two-qubit index superposition",
            "source": "ref/openqarp/examples/blocks/mwe_state_preparation.ipynb",
            "cells": [23],
            "block": lookup,
            "params": {},
            "measured_qubits": None,
            "settings": {
                "index_qubits": 2,
                "data_width": 3,
                "n_qubits": 5,
                "table": {str(index): list(bits) for index, bits in qrom_table.items()},
            },
            "execution_scope": (
                "Prepare the notebook's uniform index superposition and apply its QROM table; "
                "the cell's local statevector pretty-print is omitted."
            ),
            "expected_description": (
                "Each table index and its loaded three-bit value occur together with probability "
                "1/4; the four outcomes are checked as packed LSB-first integers."
            ),
            "expected_probabilities": qrom_probabilities,
        },
    ]
