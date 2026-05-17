#pragma once

#include "device/device.h"
#include "options/options.h"
#include "operators/operators.h"
#include "operators/poisson_solver.h"
#include "solution/solution.h"
#include <Eigen/Core>
#include <memory>
#include <string>

class TdglSolver {
public:
    TdglSolver(const Device& device, const Options& options,
               const Eigen::MatrixX2d& applied_vector_potential,
               const std::map<std::string, double>& terminal_currents,
               double disorder_epsilon = 1.0,
               const std::string& output_path = "",
               const std::string& restart_path = "");

    void solve();
    const SolutionWriter* solution_writer() const { return solution_writer_.get(); }

private:
    void update_mu_boundary(double j_scale = 1.0);
    const Device& device_;
    Options options_;
    double u_, gamma_;
    Eigen::VectorXd epsilon_;
    Eigen::MatrixX2d applied_A_;
    std::map<std::string, double> terminal_currents_;

    Eigen::VectorXcd psi_;
    Eigen::VectorXd mu_;
    Eigen::VectorXd supercurrent_;
    Eigen::VectorXd normal_current_;

    MeshOperators operators_;
    PoissonSolver poisson_solver_;
    Eigen::VectorXd mu_boundary_;

    // Screening state
    Eigen::MatrixX2d A_induced_;
    Eigen::VectorXd screening_areas_;
    Eigen::MatrixX2d screening_sites_;
    Eigen::MatrixX2d screening_edge_centers_;
    bool include_screening_ = false;

    std::unique_ptr<SolutionWriter> solution_writer_;
    double time_ = 0.0;
    double dt_ = 0.0;
};
