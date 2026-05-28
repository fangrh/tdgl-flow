#include "solver/observables.h"

Observables solve_for_observables(
    const Eigen::VectorXcd& psi, const Eigen::VectorXd& dA_dt,
    const MeshOperators& operators, const Eigen::VectorXd& mu_boundary,
    const PoissonSolver& poisson_solver) {

    // Supercurrent: J_s = Im[conj(psi[edges[:,0]]) * (gradient @ psi)]
    Eigen::VectorXd supercurrent = operators.get_supercurrent(psi);

    // RHS of Poisson equation: div @ (J_s - dA/dt) - mu_boundary_laplacian @ mu_boundary
    Eigen::VectorXd rhs = operators.divergence_ * (supercurrent - dA_dt)
        - operators.mu_boundary_laplacian_ * mu_boundary;

    // Solve for mu (rhs[0] = 0 enforces mu[0] = 0 to match pinned system)
    rhs(0) = 0.0;
    Eigen::VectorXd mu = poisson_solver.solve(rhs);

    // Normal current: J_n = -grad @ mu - dA/dt
    Eigen::VectorXd normal_current = -(operators.mu_gradient_ * mu) - dA_dt;

    return {mu, supercurrent, normal_current};
}
