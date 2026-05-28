#pragma once

#include "device/device.h"
#include "mesh/mesh.h"
#include <Eigen/Core>
#include <highfive/H5File.hpp>
#include <memory>
#include <nlohmann/json.hpp>
#include <string>
#include <vector>

class SolutionWriter {
public:
    SolutionWriter(const std::string& output_path, const Mesh& mesh,
                   const std::vector<int>& probe_indices = {});
    ~SolutionWriter();

    void save_step(int step, double time, double dt,
                   const Eigen::VectorXcd& psi,
                   const Eigen::VectorXd& mu,
                   const Eigen::VectorXd& supercurrent,
                   const Eigen::VectorXd& normal_current,
                   const Eigen::MatrixX2d& applied_A = {},
                   const Eigen::MatrixX2d& induced_A = {},
                   const Eigen::VectorXd& epsilon = {});

    void save_running_state(int frame_idx,
                            const std::vector<double>& rsmu,
                            const std::vector<double>& rsdt);

    void flush();

    int frame_count() const { return save_count_; }

private:
    std::string output_path_;
    int save_count_ = 0;
    std::vector<int> probe_indices_;
    std::unique_ptr<HighFive::File> file_;
};

class SplitSolutionWriter {
public:
    SplitSolutionWriter(const std::string& output_dir, const Mesh& mesh, const Device& device);
    ~SplitSolutionWriter();

    void write_mesh();
    void begin_step(int step_idx, double je, double ramp_start, double stable_end);
    void write_frame(int frame_idx, double time, double dt,
                     const Eigen::VectorXcd& psi,
                     const Eigen::VectorXd& mu,
                     const Eigen::VectorXd& supercurrent,
                     const Eigen::VectorXd& normal_current,
                     const Eigen::MatrixX2d& applied_A,
                     const Eigen::MatrixX2d& induced_A,
                     const Eigen::VectorXd& epsilon);
    void end_step();
    void write_manifest(const std::string& run_id, double solve_time);

    int current_step() const { return current_step_idx_; }
    int current_frame() const { return current_frame_idx_; }

private:
    std::string output_dir_;
    const Mesh& mesh_;
    const Device& device_;

    int current_step_idx_ = -1;
    int current_frame_idx_ = 0;
    std::string current_step_path_;
    std::unique_ptr<HighFive::File> current_step_file_;

    // Per-dataset byte offsets for the current step (populated in end_step)
    std::map<std::string, uint64_t> current_step_offsets_;

    // Cumulative discrete index: array of step index entries
    std::vector<nlohmann::json> discrete_index_steps_;

    std::string step_filename(int step_idx) const;
    std::string mesh_path() const { return output_dir_ + "/mesh.h5"; }
    std::string manifest_path() const { return output_dir_ + "/manifest.json"; }
    std::string discrete_index_path() const { return output_dir_ + "/discrete_index.json"; }
};
