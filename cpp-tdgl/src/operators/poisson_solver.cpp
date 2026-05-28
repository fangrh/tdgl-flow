#include "operators/poisson_solver.h"
#include <stdexcept>

void PoissonSolver::factorize(const Eigen::SparseMatrix<double>& laplacian) {
    n_ = laplacian.rows();
    lu_.compute(laplacian);
    if (lu_.info() != Eigen::Success) {
        throw std::runtime_error("PoissonSolver: LU factorization failed");
    }
}

Eigen::VectorXd PoissonSolver::solve(const Eigen::VectorXd& rhs) const {
    if (n_ == 0) {
        throw std::runtime_error("PoissonSolver: not factorized");
    }
    Eigen::VectorXd x = lu_.solve(rhs);
    if (lu_.info() != Eigen::Success) {
        throw std::runtime_error("PoissonSolver: solve failed");
    }
    return x;
}
