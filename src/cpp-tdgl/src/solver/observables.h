#pragma once

#include "mesh/mesh.h"
#include "operators/operators.h"
#include "operators/poisson_solver.h"
#include <Eigen/Core>

struct Observables {
    Eigen::VectorXd mu;
    Eigen::VectorXd supercurrent;
    Eigen::VectorXd normal_current;
};

Observables solve_for_observables(
    const Eigen::VectorXcd& psi, const Eigen::VectorXd& dA_dt,
    const MeshOperators& operators, const Eigen::VectorXd& mu_boundary,
    const PoissonSolver& poisson_solver);
