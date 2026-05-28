#pragma once

#include "device/device.h"
#include "options/options.h"
#include "operators/operators.h"
#include "operators/poisson_solver.h"
#include "solution/solution.h"
#include "timing/timing.h"
#include <Eigen/Core>
#include <functional>
#include <memory>
#include <string>
#include <vector>

class TdglSolver {
public:
    TdglSolver(const Device& device, const Options& options,
               const Eigen::MatrixX2d& applied_vector_potential,
               const std::map<std::string, double>& terminal_currents,
               double disorder_epsilon = 1.0,
               const std::string& output_path = "",
               const std::string& restart_path = "");

    // Timing schedule constructor
    TdglSolver(const Device& device, const Options& options,
               const Eigen::MatrixX2d& applied_vector_potential,
               const TimingSchedule& timing,
               double disorder_epsilon = 1.0,
               const std::string& output_path = "",
               const std::string& restart_path = "");

    void solve();
    const SolutionWriter* solution_writer() const { return solution_writer_.get(); }
    SplitSolutionWriter* split_writer() const { return split_writer_; }
    double terminal_current_at(double t) const;

    // Callback invoked when a timing step boundary is crossed
    std::function<void(int step_idx)> on_step_complete;

private:
    void update_mu_boundary(double j_scale = 1.0);
    void record_running_state();

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

    // Timing schedule state
    TimingSchedule timing_;
    bool use_timing_ = false;
    std::vector<int> probe_indices_;

    // Running state tracking
    std::vector<double> rs_mu_buffer_;
    std::vector<double> rs_dt_buffer_;

    std::unique_ptr<SolutionWriter> solution_writer_;
    SplitSolutionWriter* split_writer_ = nullptr;
    int current_step_idx_ = 0;

    double time_ = 0.0;
    double dt_ = 0.0;
};
