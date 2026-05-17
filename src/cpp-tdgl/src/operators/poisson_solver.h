#pragma once

#include <Eigen/SparseCore>
#include <Eigen/SparseLU>

class PoissonSolver {
public:
    PoissonSolver() = default;
    ~PoissonSolver() = default;

    void factorize(const Eigen::SparseMatrix<double>& laplacian);
    Eigen::VectorXd solve(const Eigen::VectorXd& rhs) const;

private:
    Eigen::SparseLU<Eigen::SparseMatrix<double>> lu_;
    int n_ = 0;
};
