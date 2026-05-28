#include "operators/operators.h"
#include <Eigen/SparseCore>
#include <cmath>
#include <complex>
#include <vector>

using std::complex;
using SpMat = Eigen::SparseMatrix<double>;
using SpCpx = Eigen::SparseMatrix<complex<double>>;
using Triplet = Eigen::Triplet<double>;
using CpxTriplet = Eigen::Triplet<complex<double>>;

// Build gradient matrix (n_edges x n_sites), CSR format
// G[i, j] = weights[i] for edge i from site edges[i,0] to edges[i,1]
// G[i, k] = -weights[i] for the reverse
static SpMat build_gradient_real(const Mesh& mesh) {
    auto& em = *mesh.edge_mesh;
    int ne = em.num_edges;
    Eigen::VectorXd weights = em.edge_lengths.cwiseInverse();

    std::vector<Triplet> triplets;
    triplets.reserve(2 * ne);
    for (int i = 0; i < ne; ++i) {
        int j0 = em.edges(i, 0);
        int j1 = em.edges(i, 1);
        triplets.emplace_back(i, j1, weights(i));
        triplets.emplace_back(i, j0, -weights(i));
    }
    SpMat grad(ne, mesh.num_sites);
    grad.setFromTriplets(triplets.begin(), triplets.end());
    return grad;
}

// Build divergence matrix (n_sites x n_edges), CSR format
// D[j0, i] = dual_edge_lengths[i] / areas[j0]
// D[j1, i] = -dual_edge_lengths[i] / areas[j1]
static SpMat build_divergence_real(const Mesh& mesh) {
    auto& em = *mesh.edge_mesh;
    int ne = em.num_edges;
    int ns = mesh.num_sites;

    std::vector<Triplet> triplets;
    triplets.reserve(2 * ne);
    for (int i = 0; i < ne; ++i) {
        int j0 = em.edges(i, 0);
        int j1 = em.edges(i, 1);
        triplets.emplace_back(j0, i, em.dual_edge_lengths(i) / mesh.areas(j0));
        triplets.emplace_back(j1, i, -em.dual_edge_lengths(i) / mesh.areas(j1));
    }
    SpMat div(ns, ne);
    div.setFromTriplets(triplets.begin(), triplets.end());
    return div;
}

// Build real Laplacian (n_sites x n_sites)
// With Dirichlet BC: fixed site rows are identity rows.
static SpMat build_laplacian_real(const Mesh& mesh,
                                  const Eigen::VectorXi& fixed_sites) {
    auto& em = *mesh.edge_mesh;
    int ns = mesh.num_sites;
    int ne = em.num_edges;
    Eigen::VectorXd weights = em.dual_edge_lengths.cwiseQuotient(em.edge_lengths);

    std::vector<bool> is_fixed(ns, false);
    for (int fi = 0; fi < fixed_sites.size(); ++fi)
        is_fixed[fixed_sites(fi)] = true;

    std::vector<Triplet> triplets;
    triplets.reserve(4 * ne);

    auto edges0 = em.edges.col(0);
    auto edges1 = em.edges.col(1);
    auto areas0 = mesh.areas(edges0);
    auto areas1 = mesh.areas(edges1);

    for (int i = 0; i < ne; ++i) {
        int j0 = edges0(i);
        int j1 = edges1(i);
        double w = weights(i);
        if (!is_fixed[j0]) {
            triplets.emplace_back(j0, j1, w / areas0(i));
            triplets.emplace_back(j0, j0, -w / areas0(i));
        }
        if (!is_fixed[j1]) {
            triplets.emplace_back(j1, j0, w / areas1(i));
            triplets.emplace_back(j1, j1, -w / areas1(i));
        }
    }

    // Add identity rows for fixed sites
    for (int fi = 0; fi < fixed_sites.size(); ++fi)
        triplets.emplace_back(fixed_sites(fi), fixed_sites(fi), 1.0);

    SpMat lap(ns, ns);
    lap.setFromTriplets(triplets.begin(), triplets.end());
    return lap;
}

// Build complex gradient with link variables (n_edges x n_sites)
// G[i, j1] = weights[i] * link_vars[i]
// G[i, j0] = -weights[i]
static SpCpx build_gradient_complex(const Mesh& mesh,
                                     const Eigen::VectorXcd& link_vars,
                                     const Eigen::VectorXd& weights) {
    auto& em = *mesh.edge_mesh;
    int ne = em.num_edges;

    std::vector<CpxTriplet> triplets;
    triplets.reserve(2 * ne);
    for (int i = 0; i < ne; ++i) {
        int j0 = em.edges(i, 0);
        int j1 = em.edges(i, 1);
        triplets.emplace_back(i, j1, weights(i) * link_vars(i));
        triplets.emplace_back(i, j0, -weights(i));
    }
    SpCpx grad(ne, mesh.num_sites);
    grad.setFromTriplets(triplets.begin(), triplets.end());
    return grad;
}

// Build complex Laplacian with link variables (n_sites x n_sites)
// L[j0, j1] = w * link[i] / a[j0]
// L[j1, j0] = w * conj(link[i]) / a[j1]
// L[j0, j0] = -w / a[j0]
// L[j1, j1] = -w / a[j1]
// Fixed sites: row zeroed, diagonal = 1
static SpCpx build_laplacian_complex(const Mesh& mesh,
                                      const Eigen::VectorXcd& link_vars,
                                      const Eigen::VectorXd& weights,
                                      const Eigen::VectorXi& fixed_sites) {
    auto& em = *mesh.edge_mesh;
    int ns = mesh.num_sites;
    int ne = em.num_edges;

    std::vector<bool> is_fixed(ns, false);
    for (int fi = 0; fi < fixed_sites.size(); ++fi)
        is_fixed[fixed_sites(fi)] = true;

    std::vector<CpxTriplet> triplets;
    triplets.reserve(4 * ne);

    auto edges0 = em.edges.col(0);
    auto edges1 = em.edges.col(1);

    for (int i = 0; i < ne; ++i) {
        int j0 = edges0(i);
        int j1 = edges1(i);
        double w = weights(i);
        complex<double> lv = link_vars(i);
        if (!is_fixed[j0]) {
            triplets.emplace_back(j0, j1, w * lv / mesh.areas(j0));
            triplets.emplace_back(j0, j0, -w / mesh.areas(j0));
        }
        if (!is_fixed[j1]) {
            triplets.emplace_back(j1, j0, w * std::conj(lv) / mesh.areas(j1));
            triplets.emplace_back(j1, j1, -w / mesh.areas(j1));
        }
    }

    // Identity rows for fixed sites
    for (int fi = 0; fi < fixed_sites.size(); ++fi)
        triplets.emplace_back(fixed_sites(fi), fixed_sites(fi), 1.0);

    SpCpx lap(ns, ns);
    lap.setFromTriplets(triplets.begin(), triplets.end());
    return lap;
}

// Build Neumann boundary Laplacian (n_sites x n_boundary_edges)
// N[j0, k] = edge_lengths[k] / (2 * areas[j0])
// N[j1, k] = edge_lengths[k] / (2 * areas[j1])
// For mu: no fixed site exclusion (current injection at all boundary edges).
// For psi: fixed site rows are zeroed by the caller if needed.
static SpMat build_neumann_boundary(const Mesh& mesh,
                                     const Eigen::VectorXi& fixed_sites) {
    auto& em = *mesh.edge_mesh;
    int ns = mesh.num_sites;
    int nb = em.boundary_edge_indices.size();

    std::vector<Triplet> triplets;
    triplets.reserve(2 * nb);

    for (int k = 0; k < nb; ++k) {
        int ei = em.boundary_edge_indices(k);
        int j0 = em.edges(ei, 0);
        int j1 = em.edges(ei, 1);
        double el = em.edge_lengths(ei);
        triplets.emplace_back(j0, k, el / (2.0 * mesh.areas(j0)));
        triplets.emplace_back(j1, k, el / (2.0 * mesh.areas(j1)));
    }

    SpMat mat(ns, nb);
    mat.setFromTriplets(triplets.begin(), triplets.end());
    return mat;
}

// Compute link variables: exp(-i * dot(A, direction)) for each edge
static Eigen::VectorXcd compute_link_variables(
    const Eigen::MatrixX2d& A_on_edges, const Eigen::MatrixX2d& directions) {
    return (A_on_edges.array().col(0) * directions.array().col(0) +
            A_on_edges.array().col(1) * directions.array().col(1))
        .unaryExpr([](double x) { return std::exp(complex<double>(0, -x)); });
}

void MeshOperators::build_operators(const Mesh& mesh,
                                    const Eigen::VectorXi& fixed_sites,
                                    bool fix_psi) {
    auto& em = *mesh.edge_mesh;
    num_sites = mesh.num_sites;
    num_edges = em.num_edges;

    // Precompute constant weights
    gradient_weights_ = em.edge_lengths.cwiseInverse();
    laplacian_weights_ = em.dual_edge_lengths.cwiseQuotient(em.edge_lengths);

    // Build real operators (for mu)
    mu_gradient_ = build_gradient_real(mesh);
    divergence_ = build_divergence_real(mesh);

    // mu_laplacian: real, NO fixed sites (pure Neumann BC).
    // Matches py-tdgl: terminal current injection is via mu_boundary_laplacian
    // in the RHS. The system is singular (null space = constant vectors).
    // Different LU solvers return different particular solutions.
    {
        auto w = laplacian_weights_;
        auto e0 = em.edges.col(0), e1 = em.edges.col(1);
        std::vector<Triplet> triplets;
        triplets.reserve(4 * num_edges);
        for (int i = 0; i < num_edges; ++i) {
            int j0 = e0(i), j1 = e1(i);
            triplets.emplace_back(j0, j1, w(i) / mesh.areas(j0));
            triplets.emplace_back(j1, j0, w(i) / mesh.areas(j1));
            triplets.emplace_back(j0, j0, -w(i) / mesh.areas(j0));
            triplets.emplace_back(j1, j1, -w(i) / mesh.areas(j1));
        }
        mu_laplacian_.resize(num_sites, num_sites);
        mu_laplacian_.setFromTriplets(triplets.begin(), triplets.end());
    }

    mu_boundary_laplacian_ = build_neumann_boundary(mesh, fixed_sites);

    // Store references for link variable updates
    edges_ = &em.edges;
    directions_ = &em.directions;
    areas_ = &mesh.areas;
    fixed_sites_ = fixed_sites;
    fix_psi_ = fix_psi;
    mesh_ = &mesh;

    // Build complex operators with identity link variables
    set_link_exponents(Eigen::MatrixX2d::Zero(num_edges, 2));
}

void MeshOperators::set_link_exponents(const Eigen::MatrixX2d& A_on_edges) {
    auto link_vars = compute_link_variables(A_on_edges, *directions_);

    // Build complex gradient
    psi_gradient_ = build_gradient_complex(*mesh_, link_vars, gradient_weights_);

    // Build complex Laplacian
    if (fix_psi_) {
        psi_laplacian_ = build_laplacian_complex(*mesh_, link_vars,
                                                   laplacian_weights_,
                                                   fixed_sites_);
    } else {
        Eigen::VectorXi empty;
        psi_laplacian_ = build_laplacian_complex(*mesh_, link_vars,
                                                   laplacian_weights_, empty);
    }
}

Eigen::VectorXd MeshOperators::get_supercurrent(
    const Eigen::VectorXcd& psi) const {
    // J_s = Im[conj(psi[edges[:,0]]) * (gradient @ psi)]
    Eigen::VectorXcd grad_psi = psi_gradient_ * psi;
    Eigen::VectorXd j_s(num_edges);
    for (int i = 0; i < num_edges; ++i) {
        int j0 = (*edges_)(i, 0);
        j_s(i) = std::imag(std::conj(psi(j0)) * grad_psi(i));
    }
    return j_s;
}
