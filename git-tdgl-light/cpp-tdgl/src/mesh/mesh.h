#pragma once

#include "mesh/edge_mesh.h"
#include <Eigen/Core>
#include <optional>

struct Mesh {
    int num_sites = 0;
    int num_elements = 0;
    Eigen::MatrixX2d sites;            // (num_sites, 2) dimensionless coords
    Eigen::Matrix<int64_t, -1, 3> elements; // (num_elements, 3) triangle connectivity
    Eigen::VectorXi boundary_indices;
    Eigen::VectorXd areas;              // (num_sites,) Voronoi cell areas
    std::optional<EdgeMesh> edge_mesh;
};
