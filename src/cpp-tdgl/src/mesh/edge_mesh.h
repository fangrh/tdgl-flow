#pragma once

#include <Eigen/Core>

struct EdgeMesh {
    int num_edges = 0;
    Eigen::MatrixX2d centers;           // (num_edges, 2)
    Eigen::Matrix<int64_t, -1, 2> edges; // (num_edges, 2) site index pairs
    Eigen::VectorXi boundary_edge_indices;
    Eigen::MatrixX2d directions;        // (num_edges, 2) unnormalized
    Eigen::MatrixX2d normalized_directions; // (num_edges, 2) unit vectors
    Eigen::VectorXd edge_lengths;       // (num_edges,)
    Eigen::VectorXd dual_edge_lengths;  // (num_edges,)
};
