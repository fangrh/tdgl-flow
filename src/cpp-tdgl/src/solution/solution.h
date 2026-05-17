#pragma once

#include "mesh/mesh.h"
#include <Eigen/Core>
#include <highfive/H5File.hpp>
#include <memory>
#include <string>

class SolutionWriter {
public:
    SolutionWriter(const std::string& output_path, const Mesh& mesh);
    ~SolutionWriter();

    void save_step(int step, double time, double dt,
                   const Eigen::VectorXcd& psi,
                   const Eigen::VectorXd& mu,
                   const Eigen::VectorXd& supercurrent,
                   const Eigen::VectorXd& normal_current,
                   const Eigen::MatrixX2d& applied_A = {},
                   const Eigen::MatrixX2d& induced_A = {},
                   const Eigen::VectorXd& epsilon = {});

private:
    std::string output_path_;
    int save_count_ = 0;
    std::unique_ptr<HighFive::File> file_;
};
