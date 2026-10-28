"""Native-estimator workloads derived from upstream OpenQARP notebooks."""

import numpy as np

from qarp.algorithms import StateVector
from qarp.blocks import ComputationalBasisStateBlock, HEABlock
from qarp.engines import QarpEngine
from qarp.operators import QubitOperator
from qarp.optimizers import ScipyOptimizer


def estimator_cases():
    """Build notebook observables, including the locally optimized VQE state."""
    psi = HEABlock(3, 2, True, True, True, False).build()
    psi_01 = psi.set_symbols({s: 0.1 for s in psi.symbols}).build()
    hamiltonian = QubitOperator("Z0 Z1", -1.0) + QubitOperator("X2", 0.5)
    linear = psi.parameter_map(np.linspace(0.0, 1.0, len(psi.symbols)))

    basis = ComputationalBasisStateBlock([1, 1, 0, 1])
    basis_h = QubitOperator("Z0 Z1") - QubitOperator("Z2 Z3") + QubitOperator("Z0 Y1 X3")

    tfim = QubitOperator()
    for i in range(2):
        tfim += QubitOperator(f"Z{i} Z{i+1}", -1.0)
    for i in range(3):
        tfim += QubitOperator(f"X{i}", -0.5)
    ansatz = HEABlock(3, 2, True, True, True, False).build()
    primitive = StateVector(bra=ansatz, operator=tfim, ket=ansatz)
    engine = QarpEngine()
    engine.build([primitive])
    evaluations = []

    def energy(x):
        value = float(engine.run(ansatz.parameter_map(x))[0].real)
        evaluations.append(value)
        return value

    initial = np.random.default_rng(42).uniform(0, 2 * np.pi, len(ansatz.symbols))
    optimum = ScipyOptimizer("COBYLA", options={"maxiter": 400}).minimize(energy, initial)
    variational_params = ansatz.parameter_map(optimum.x)
    exact_ground = float(np.linalg.eigvalsh(tfim.sparse_matrix().toarray())[0])
    tutorial = "ref/openqarp/examples/tutorial_02_primitives.ipynb"
    return [
        {
            "id": "hea-linear", "title": "HEA expectation with symbolic parameters",
            "source": tutorial, "cells": [2, 4], "block": psi, "operator": hamiltonian,
            "params": linear,
            "settings": {"n_qubits": 3, "layers": 2, "bindings": "linspace(0,1,n_symbols) in canonical symbol order", "n_parameters": len(psi.symbols)},
            "execution_scope": "Notebook symbolic circuit and observable; native Cloud estimation replaces local exact statevector.",
            "expected_description": "Compare native estimation with a local exact statevector expectation.",
        },
        {
            "id": "hea-constant", "title": "HEA expectation at 0.1",
            "source": tutorial, "cells": [2, 9], "block": psi_01, "operator": hamiltonian,
            "params": {},
            "settings": {"n_qubits": 3, "layers": 2, "parameter_value": 0.1,
                         "notebook_shots": 20000},
            "execution_scope": "Notebook circuit and observable; native Cloud estimation replaces local termwise Hadamard sampling, with the runner's shot count.",
            "expected_description": "Compare native estimation with a local exact statevector expectation.",
        },
        {
            "id": "basis-pauli", "title": "Computational basis Pauli expectation",
            "source": "ref/openqarp/examples/algorithms/primitives/mwe_primitive_algorithms.ipynb",
            "cells": [15], "block": basis, "operator": basis_h, "params": {},
            "settings": {"n_qubits": 4, "basis_bits_lsb_first": [1, 1, 0, 1],
                         "analytic_expectation": 2.0},
            "execution_scope": "Notebook state and observable; one native Cloud estimation replaces local Pauli averaging.",
            "expected_description": "Z0 Z1 = +1, Z2 Z3 = -1, and Z0 Y1 X3 = 0; expectation is 2.",
        },
        {
            "id": "tfim-vqe", "title": "Three-qubit TFIM variational energy",
            "source": "ref/openqarp/examples/tutorial_04_variational_loop.ipynb",
            "cells": [1, 3, 5, 7], "block": ansatz, "operator": tfim,
            "params": variational_params,
            "settings": {"n_qubits": 3, "layers": 2, "field": 0.5, "seed": 42,
                         "optimizer": "COBYLA", "maxiter": 400,
                         "objective_evaluations": len(evaluations),
                         "local_variational_energy": float(optimum.fun),
                         "exact_ground_energy": exact_ground,
                         "optimized_parameters": [float(x) for x in optimum.x]},
            "execution_scope": "Notebook COBYLA optimization uses local exact statevector; only final bound circuit and full Hamiltonian are sent for native Cloud estimation.",
            "expected_description": "Compare native estimation to the locally optimized circuit energy; exact ground energy is a separate lower bound.",
        },
    ]
