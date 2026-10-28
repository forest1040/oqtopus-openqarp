"""Notebook-derived OpenQARP circuits for the Cloud example runner."""

import networkx as nx
import numpy as np

from qarp import config

from qarp.algorithms import Grover, QAOA
from qarp.blocks import ComputationalBasisStateBlock, QPEBlock, SimpleBlock, TrotterBlock
from qarp.operators import FullyCommuting, JordanWigner
from qarp.operators.functions import eigenspectrum
from qarp.operators.models import fermi_hubbard


def algorithm_cases():
    """Build three notebook circuits; QAOA optimization runs locally."""
    oracle = SimpleBlock(2, name="O_good")
    oracle.x(0)
    oracle.mcz([0, 1])
    oracle.x(0)
    grover = Grover(oracle, n_marked=1, good_states=[2]).build()

    hamiltonian = JordanWigner().encode_operator(fermi_hubbard((2,), t=0.14, U=0.231))
    energies = eigenspectrum(hamiltonian)
    shift = float(abs(energies[0]) + np.finfo(float).eps) if energies[0] < 0 else 0.0
    hamiltonian = hamiltonian + shift
    shifted_energies = eigenspectrum(hamiltonian)
    unitary = TrotterBlock(
        operator=hamiltonian, n_qubits=4, steps=4, time=-2 * np.pi,
        order=2, grouping=FullyCommuting(),
    ).build()
    qpe = QPEBlock(ComputationalBasisStateBlock([1] * 4), unitary, 5, 4, measure=False)
    qpe.build()
    phase = float(2 * 0.231 + shift)
    if not np.isclose(phase, shifted_energies[-1], atol=1e-12, rtol=0):
        raise ValueError("Full-occupation energy does not match the highest eigenvalue")
    bins = np.arange(32)
    delta = phase - bins / 32
    probabilities = np.sinc(32 * delta) ** 2 / np.sinc(delta) ** 2

    config.seed = 1234
    graph = nx.gnm_random_graph(4, 2, seed=config.seed)
    for u, v in graph.edges:
        graph[u][v]["weight"] = 2
    cut_weights = {
        outcome: sum(data["weight"] for u, v, data in graph.edges(data=True)
                     if ((outcome >> u) & 1) != ((outcome >> v) & 1))
        for outcome in range(16)
    }
    best_cut_weight = max(cut_weights.values())
    optimal_outcomes = [outcome for outcome, weight in cut_weights.items()
                        if weight == best_cut_weight]
    qaoa = QAOA(problem=graph, n_layers=2, use_rzz=True,
                initial_parameters=[0] * 4).build()
    energy, optimized = qaoa.run()
    qaoa_block = qaoa.get_final_state_block()

    return [
        {
            "id": "grover", "title": "Grover search",
            "source": "ref/openqarp/examples/algorithms/composite/mwe_grover.ipynb",
            "cells": [3, 5], "block": grover.block, "params": {},
            "measured_qubits": None,
            "settings": {"n_qubits": 2, "n_marked": 1, "good_states": [2],
                         "iterations": 1},
            "execution_scope": "Notebook circuit sampled on Cloud; exact notebook sampling replaced by finite shots.",
            "expected_description": "Marked state 2 has probability 1 (LSB-first bits 0,1).",
            "expected_probabilities": {2: 1.0},
        },
        {
            "id": "qpe", "title": "QPE two-site Fermi-Hubbard",
            "source": "ref/openqarp/examples/algorithms/composite/mwe_qpe.ipynb",
            "cells": [3, 5, 10], "block": qpe, "params": {},
            "measured_qubits": list(range(5)),
            "settings": {"n_qubits": 9, "n_ancilla": 5, "t": 0.14, "U": 0.231,
                         "time": -float(2 * np.pi), "trotter_steps": 4,
                         "trotter_order": 2, "energy_shift": shift,
                         "expected_phase": phase, "full_occupation_energy": phase,
                         "highest_eigenvalue": float(shifted_energies[-1])},
            "execution_scope": "Notebook QPE circuit with equivalent measure-all and ancilla marginal; classical fitting omitted.",
            "expected_description": "Analytic QPE Dirichlet distribution for the |1111> eigenstate; peak near phase 0.6494.",
            "expected_probabilities": {int(k): float(p) for k, p in zip(bins, probabilities, strict=True)},
        },
        {
            "id": "qaoa", "title": "QAOA weighted MaxCut",
            "source": "ref/openqarp/examples/algorithms/composite/mwe_qaoa_max_cut.ipynb",
            "cells": [4, 5, 8, 12, 16], "block": qaoa_block, "params": {},
            "measured_qubits": None,
            "settings": {"n_qubits": 4, "edges": [list(e) for e in graph.edges],
                         "edge_weight": 2, "layers": 2, "seed": config.seed,
                         "best_cut_weight": best_cut_weight,
                         "optimal_outcomes": optimal_outcomes,
                         "initial_parameters": [0] * 4,
                         "optimizer": "SciPy CG (OpenQARP default)",
                         "local_energy": float(energy),
                         "optimized_parameters": [float(x) for x in optimized]},
            "execution_scope": "Notebook optimization runs locally with exact statevector; only its bound final circuit is sampled on Cloud.",
            "expected_description": "The locally optimized circuit favors cuts with minimum QAOA cost; compare against a local exact distribution.",
        },
    ]
