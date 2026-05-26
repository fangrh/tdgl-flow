#pragma once

#include <Eigen/Core>
#include <Eigen/SparseCore>
#include <complex>
#include <optional>

struct PsiResult {
    Eigen::VectorXcd psi;
    Eigen::VectorXd abs_sq_psi;
};

std::optional<PsiResult> solve_for_psi_squared(
    const Eigen::VectorXcd& psi, const Eigen::VectorXd& abs_sq_psi,
    const Eigen::VectorXd& mu, const Eigen::VectorXd& epsilon,
    double gamma, double u, double dt,
    const Eigen::SparseMatrix<std::complex<double>>& psi_laplacian);
