#include "solver/adaptive_step.h"
#include <stdexcept>
#include <iostream>

AdaptiveResult adaptive_euler_step(
    int step, Eigen::VectorXcd& psi, const Eigen::VectorXd& abs_sq_psi,
    const Eigen::VectorXd& mu, const Eigen::VectorXd& epsilon,
    double dt, const Options& options, double gamma, double u,
    const Eigen::SparseMatrix<std::complex<double>>& psi_laplacian) {

    int retries = 0;
    double current_dt = dt;

    while (retries <= options.max_solve_retries) {
        auto result = solve_for_psi_squared(
            psi, abs_sq_psi, mu, epsilon, gamma, u, current_dt, psi_laplacian);

        if (result.has_value()) {
            return {result->psi, result->abs_sq_psi, current_dt};
        }

        // Reduce time step and retry
        current_dt *= options.adaptive_time_step_multiplier;
        retries++;
    }

    throw std::runtime_error(
        "adaptive_euler_step: max retries (" + std::to_string(options.max_solve_retries)
        + ") exceeded at step " + std::to_string(step));
}
