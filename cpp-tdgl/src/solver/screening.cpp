#include "solver/screening.h"
#include <Eigen/Core>
#include <vector>
#include <cmath>

#ifdef _OPENMP
#include <omp.h>
#endif

Eigen::MatrixX2d compute_induced_vector_potential(
    const Eigen::MatrixX2d& J_site,
    const Eigen::VectorXd& site_areas,
    const Eigen::MatrixX2d& sites,
    const Eigen::MatrixX2d& edge_centers) {

    int ne = edge_centers.rows();
    int ns = sites.rows();
    Eigen::MatrixX2d A_induced = Eigen::MatrixX2d::Zero(ne, 2);

    #pragma omp parallel for schedule(dynamic)
    for (int i = 0; i < ne; ++i) {
        double tmp_x = 0.0, tmp_y = 0.0;
        double cx = edge_centers(i, 0);
        double cy = edge_centers(i, 1);
        for (int j = 0; j < ns; ++j) {
            double dx = cx - sites(j, 0);
            double dy = cy - sites(j, 1);
            double dr = std::sqrt(dx * dx + dy * dy);
            double scale = site_areas(j) / dr;
            tmp_x += J_site(j, 0) * scale;
            tmp_y += J_site(j, 1) * scale;
        }
        A_induced(i, 0) = tmp_x;
        A_induced(i, 1) = tmp_y;
    }

    return A_induced;
}

Eigen::VectorXd interpolate_scalar_edges_to_sites(
    const Eigen::VectorXd& edge_values,
    const Eigen::Matrix<int64_t, -1, 2>& edges,
    const Eigen::MatrixX2d& normalized_directions,
    int ns) {

    int ne = edges.rows();
    Eigen::VectorXd result = Eigen::VectorXd::Zero(ns);
    Eigen::VectorXi counts = Eigen::VectorXi::Zero(ns);

    for (int i = 0; i < ne; ++i) {
        int j0 = static_cast<int>(edges(i, 0));
        int j1 = static_cast<int>(edges(i, 1));
        double val = edge_values(i);
        result(j0) += val;
        result(j1) += val;
        counts(j0)++;
        counts(j1)++;
    }

    for (int k = 0; k < ns; ++k) {
        result(k) /= (2.0 * counts(k));
    }

    return result;
}

Eigen::MatrixX2d interpolate_vector_edges_to_sites(
    const Eigen::VectorXd& edge_values,
    const Eigen::Matrix<int64_t, -1, 2>& edges,
    const Eigen::MatrixX2d& normalized_directions,
    int ns) {

    int ne = edges.rows();
    Eigen::MatrixX2d result = Eigen::MatrixX2d::Zero(ns, 2);
    Eigen::VectorXi counts = Eigen::VectorXi::Zero(ns);

    for (int i = 0; i < ne; ++i) {
        int j0 = static_cast<int>(edges(i, 0));
        int j1 = static_cast<int>(edges(i, 1));
        double val = edge_values(i);
        double fx = val * normalized_directions(i, 0);
        double fy = val * normalized_directions(i, 1);
        result(j0, 0) += fx;
        result(j0, 1) += fy;
        result(j1, 0) += fx;
        result(j1, 1) += fy;
        counts(j0)++;
        counts(j1)++;
    }

    for (int k = 0; k < ns; ++k) {
        result(k, 0) /= (2.0 * counts(k));
        result(k, 1) /= (2.0 * counts(k));
    }

    return result;
}
