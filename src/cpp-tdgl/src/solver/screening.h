#pragma once

#include <Eigen/Core>

// O(n^2) Biot-Savart: A_induced[i] = sum_j J_site[j] * area[j] / |r_i - r_j|
Eigen::MatrixX2d compute_induced_vector_potential(
    const Eigen::MatrixX2d& J_site,
    const Eigen::VectorXd& site_areas,
    const Eigen::MatrixX2d& sites,
    const Eigen::MatrixX2d& edge_centers);

// Average a scalar edge quantity to sites (py-tdgl's get_quantity_on_site with vector=false)
// J_site[k] = (1/(2*count[k])) * sum_{edges incident to k} edge_values[i] * dir[i]
Eigen::VectorXd interpolate_scalar_edges_to_sites(
    const Eigen::VectorXd& edge_values,
    const Eigen::Matrix<int64_t, -1, 2>& edges,
    const Eigen::MatrixX2d& normalized_directions,
    int ns);

// Average a vector edge quantity to sites (py-tdgl's get_quantity_on_site with vector=true)
// Returns (ns, 2) matrix: site_current[k] = (1/(2*count[k])) * sum_{edges} edge_vals[i] * dir[i]
Eigen::MatrixX2d interpolate_vector_edges_to_sites(
    const Eigen::VectorXd& edge_values,
    const Eigen::Matrix<int64_t, -1, 2>& edges,
    const Eigen::MatrixX2d& normalized_directions,
    int ns);
