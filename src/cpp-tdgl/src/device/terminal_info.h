#pragma once

#include <Eigen/Core>
#include <string>

struct TerminalInfo {
    std::string name;
    Eigen::VectorXi site_indices;
    Eigen::VectorXi edge_indices;
    Eigen::VectorXi boundary_edge_indices;
    double length = 0.0;
};
