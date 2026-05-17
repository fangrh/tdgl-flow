#pragma once

#include "mesh/mesh.h"
#include <Eigen/SparseCore>
#include <complex>

struct MeshOperators {
    int num_sites = 0;
    int num_edges = 0;

    // Precomputed constant weights
    Eigen::VectorXd gradient_weights_;
    Eigen::VectorXd laplacian_weights_;

    // Real operators (for mu, no link variables)
    Eigen::SparseMatrix<double> mu_gradient_;
    Eigen::SparseMatrix<double> divergence_;
    Eigen::SparseMatrix<double> mu_laplacian_;
    Eigen::SparseMatrix<double> mu_boundary_laplacian_;

    // Complex operators (for psi, with link variables)
    Eigen::SparseMatrix<std::complex<double>> psi_gradient_;
    Eigen::SparseMatrix<std::complex<double>> psi_laplacian_;

    // Internal references
    const Mesh* mesh_ = nullptr;
    const Eigen::Matrix<int64_t, -1, 2>* edges_ = nullptr;
    const Eigen::MatrixX2d* directions_ = nullptr;
    const Eigen::VectorXd* areas_ = nullptr;
    Eigen::VectorXi fixed_sites_;
    bool fix_psi_ = false;

    void build_operators(const Mesh& mesh, const Eigen::VectorXi& fixed_sites,
                         bool fix_psi);
    void set_link_exponents(const Eigen::MatrixX2d& A_on_edges);
    Eigen::VectorXd get_supercurrent(const Eigen::VectorXcd& psi) const;
};
