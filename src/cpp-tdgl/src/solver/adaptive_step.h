#pragma once

#include "solver/psi_update.h"
#include "options/options.h"
#include <complex>
#include <optional>

struct AdaptiveResult {
    Eigen::VectorXcd psi;
    Eigen::VectorXd abs_sq_psi;
    double dt;
};

AdaptiveResult adaptive_euler_step(
    int step, Eigen::VectorXcd& psi, const Eigen::VectorXd& abs_sq_psi,
    const Eigen::VectorXd& mu, const Eigen::VectorXd& epsilon,
    double dt, const Options& options, double gamma, double u,
    const Eigen::SparseMatrix<std::complex<double>>& psi_laplacian);
