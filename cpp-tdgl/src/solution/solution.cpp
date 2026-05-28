#include "solution/solution.h"
#include <highfive/H5File.hpp>
#include <highfive/H5Group.hpp>
#include <highfive/H5DataSet.hpp>
#include <stdexcept>
#include <vector>

namespace h5 = HighFive;

template<typename T>
void write_1d(h5::Group& g, const std::string& name,
              const T* data, size_t n) {
    h5::DataSpace space({n});
    g.createDataSet(name, space, h5::AtomicType<T>()).write_raw(data);
}

template<typename T>
void write_2d(h5::Group& g, const std::string& name,
              const T* data, size_t rows, size_t cols) {
    h5::DataSpace space({rows, cols});
    g.createDataSet(name, space, h5::AtomicType<T>()).write_raw(data);
}

SolutionWriter::SolutionWriter(const std::string& output_path, const Mesh& mesh,
                               const std::vector<int>& probe_indices)
    : output_path_(output_path), save_count_(0), probe_indices_(probe_indices) {
    file_ = std::make_unique<h5::File>(output_path, h5::File::Overwrite);

    auto mesh_grp = file_->createGroup("mesh");
    Eigen::Matrix<double, -1, 2, Eigen::RowMajor> sites_rm = mesh.sites;
    write_2d<double>(mesh_grp, "sites", sites_rm.data(), sites_rm.rows(), 2);
    Eigen::Matrix<int64_t, -1, 3, Eigen::RowMajor> elems_rm = mesh.elements;
    write_2d<int64_t>(mesh_grp, "elements", elems_rm.data(),
                       elems_rm.rows(), 3);
    std::vector<int64_t> bi(mesh.boundary_indices.data(),
                              mesh.boundary_indices.data() + mesh.boundary_indices.size());
    write_1d<int64_t>(mesh_grp, "boundary_indices", bi.data(), bi.size());
    write_1d<double>(mesh_grp, "areas", mesh.areas.data(), mesh.areas.rows());

    if (mesh.edge_mesh) {
        auto& em = *mesh.edge_mesh;
        auto eg = mesh_grp.createGroup("edge_mesh");
        Eigen::Matrix<double, -1, 2, Eigen::RowMajor> centers_rm = em.centers;
        write_2d<double>(eg, "centers", centers_rm.data(), centers_rm.rows(), 2);
        Eigen::Matrix<int64_t, -1, 2, Eigen::RowMajor> edges_rm = em.edges;
        write_2d<int64_t>(eg, "edges", edges_rm.data(), edges_rm.rows(), 2);
        write_1d<double>(eg, "edge_lengths", em.edge_lengths.data(), em.edge_lengths.rows());
        write_1d<double>(eg, "dual_edge_lengths", em.dual_edge_lengths.data(),
                         em.dual_edge_lengths.rows());
    }

    file_->createGroup("data");
}

SolutionWriter::~SolutionWriter() = default;

void SolutionWriter::save_step(int step, double time, double dt,
                                const Eigen::VectorXcd& psi,
                                const Eigen::VectorXd& mu,
                                const Eigen::VectorXd& supercurrent,
                                const Eigen::VectorXd& normal_current,
                                const Eigen::MatrixX2d& applied_A,
                                const Eigen::MatrixX2d& induced_A,
                                const Eigen::VectorXd& epsilon) {
    auto data_grp = file_->getGroup("data");
    std::string step_name = std::to_string(save_count_);
    auto grp = data_grp.createGroup(step_name);

    int n = psi.size();

    // Write psi as (N,2) float64 — interleaved [re0,im0,re1,im1,...]
    // This produces N*16 contiguous bytes matching py-tdgl format.
    std::vector<double> psi_interleaved(2 * n);
    for (int i = 0; i < n; ++i) {
        psi_interleaved[2 * i] = psi(i).real();
        psi_interleaved[2 * i + 1] = psi(i).imag();
    }
    h5::DataSpace psi_space({static_cast<unsigned long long>(n), 2ULL});
    grp.createDataSet<double>("psi", psi_space).write_raw(psi_interleaved.data());

    write_1d<double>(grp, "mu", mu.data(), n);
    write_1d<double>(grp, "supercurrent", supercurrent.data(), supercurrent.size());
    write_1d<double>(grp, "normal_current", normal_current.data(), normal_current.size());

    if (applied_A.size() > 0) {
        Eigen::Matrix<double, -1, 2, Eigen::RowMajor> A_rm = applied_A;
        write_2d<double>(grp, "applied_vector_potential",
                         A_rm.data(), A_rm.rows(), 2);
    }
    if (induced_A.size() > 0) {
        Eigen::Matrix<double, -1, 2, Eigen::RowMajor> Ai_rm = induced_A;
        write_2d<double>(grp, "induced_vector_potential",
                         Ai_rm.data(), Ai_rm.rows(), 2);
    }
    if (epsilon.size() > 0)
        write_1d<double>(grp, "epsilon", epsilon.data(), epsilon.size());

    // Attributes
    grp.createAttribute("step", step).write(step);
    grp.createAttribute("time", time).write(time);
    grp.createAttribute("dt", dt).write(dt);

    save_count_++;
}

void SolutionWriter::save_running_state(int frame_idx,
                                         const std::vector<double>& rsmu,
                                         const std::vector<double>& rsdt) {
    auto data_grp = file_->getGroup("data");
    std::string frame_name = std::to_string(frame_idx);
    auto grp = data_grp.getGroup(frame_name);
    auto rs_grp = grp.createGroup("running_state");
    write_1d<double>(rs_grp, "mu", rsmu.data(), rsmu.size());
    write_1d<double>(rs_grp, "dt", rsdt.data(), rsdt.size());
}

void SolutionWriter::flush() {
    if (file_) file_->flush();
}

// --- SplitSolutionWriter ---

#include <filesystem>
#include <fstream>
#include <sys/stat.h>

SplitSolutionWriter::SplitSolutionWriter(const std::string& output_dir, const Mesh& mesh, const Device& device)
    : output_dir_(output_dir), mesh_(mesh), device_(device) {
    // Create output directory if it doesn't exist
    struct stat st;
    if (stat(output_dir_.c_str(), &st) != 0) {
        std::filesystem::create_directories(output_dir_);
    }
}

SplitSolutionWriter::~SplitSolutionWriter() = default;

std::string SplitSolutionWriter::step_filename(int step_idx) const {
    char buf[64];
    std::snprintf(buf, sizeof(buf), "step_%04d.h5", step_idx);
    return output_dir_ + "/" + std::string(buf);
}

void SplitSolutionWriter::write_mesh() {
    h5::File file(mesh_path(), h5::File::Overwrite);

    // Write mesh group (same as SolutionWriter)
    auto mesh_grp = file.createGroup("mesh");
    Eigen::Matrix<double, -1, 2, Eigen::RowMajor> sites_rm = mesh_.sites;
    write_2d<double>(mesh_grp, "sites", sites_rm.data(), sites_rm.rows(), 2);
    Eigen::Matrix<int64_t, -1, 3, Eigen::RowMajor> elems_rm = mesh_.elements;
    write_2d<int64_t>(mesh_grp, "elements", elems_rm.data(),
                       elems_rm.rows(), 3);
    std::vector<int64_t> bi(mesh_.boundary_indices.data(),
                              mesh_.boundary_indices.data() + mesh_.boundary_indices.size());
    write_1d<int64_t>(mesh_grp, "boundary_indices", bi.data(), bi.size());
    write_1d<double>(mesh_grp, "areas", mesh_.areas.data(), mesh_.areas.rows());

    if (mesh_.edge_mesh) {
        auto& em = *mesh_.edge_mesh;
        auto eg = mesh_grp.createGroup("edge_mesh");
        Eigen::Matrix<double, -1, 2, Eigen::RowMajor> centers_rm = em.centers;
        write_2d<double>(eg, "centers", centers_rm.data(), centers_rm.rows(), 2);
        Eigen::Matrix<int64_t, -1, 2, Eigen::RowMajor> edges_rm = em.edges;
        write_2d<int64_t>(eg, "edges", edges_rm.data(), edges_rm.rows(), 2);
        write_1d<double>(eg, "edge_lengths", em.edge_lengths.data(), em.edge_lengths.rows());
        write_1d<double>(eg, "dual_edge_lengths", em.dual_edge_lengths.data(),
                         em.dual_edge_lengths.rows());
    }

    // Write device group
    auto dev_grp = file.createGroup("device");
    dev_grp.createAttribute("name", device_.name);
    dev_grp.createAttribute("length_units", device_.length_units);
    dev_grp.createAttribute("K0", device_.K0);
    dev_grp.createAttribute("A0", device_.A0);
    dev_grp.createAttribute("Bc2", device_.Bc2);
    dev_grp.createAttribute("Lambda", device_.Lambda);

    // Write layer
    auto layer_grp = dev_grp.createGroup("layer");
    layer_grp.createAttribute("london_lambda", device_.layer.london_lambda);
    layer_grp.createAttribute("coherence_length", device_.layer.coherence_length);
    layer_grp.createAttribute("thickness", device_.layer.thickness);
    layer_grp.createAttribute("u", device_.layer.u);
    layer_grp.createAttribute("gamma", device_.layer.gamma);
    if (device_.layer.z0 != 0.0)
        layer_grp.createAttribute("z0", device_.layer.z0);
    if (device_.layer.conductivity != 0.0)
        layer_grp.createAttribute("conductivity", device_.layer.conductivity);

    // Write terminals in "terminals" subgroup
    auto terms_grp = dev_grp.createGroup("terminals");
    for (size_t ti = 0; ti < device_.terminals.size(); ++ti) {
        auto& t = device_.terminals[ti];
        auto t_grp = terms_grp.createGroup(t.name);

        std::vector<int64_t> site_idx(t.site_indices.data(),
                                        t.site_indices.data() + t.site_indices.size());
        write_1d<int64_t>(t_grp, "site_indices", site_idx.data(), site_idx.size());

        std::vector<int64_t> edge_idx(t.edge_indices.data(),
                                        t.edge_indices.data() + t.edge_indices.size());
        write_1d<int64_t>(t_grp, "edge_indices", edge_idx.data(), edge_idx.size());

        std::vector<int64_t> boundary_edge_idx(t.boundary_edge_indices.data(),
                                                 t.boundary_edge_indices.data() + t.boundary_edge_indices.size());
        write_1d<int64_t>(t_grp, "boundary_edge_indices", boundary_edge_idx.data(), boundary_edge_idx.size());

        t_grp.createAttribute("length", t.length);
    }

    // Write probe point indices
    if (!device_.probe_point_indices.empty()) {
        std::vector<int64_t> ppi(device_.probe_point_indices.data(),
                                   device_.probe_point_indices.data() + device_.probe_point_indices.size());
        write_1d<int64_t>(dev_grp, "probe_point_indices", ppi.data(), ppi.size());
    }
}

void SplitSolutionWriter::begin_step(int step_idx, double je, double ramp_start, double stable_end) {
    current_step_idx_ = step_idx;
    current_frame_idx_ = 0;
    current_step_path_ = step_filename(step_idx);
    current_step_file_ = std::make_unique<h5::File>(current_step_path_, h5::File::Overwrite);

    // Write metadata
    auto meta_grp = current_step_file_->createGroup("metadata");
    meta_grp.createAttribute("step_idx", step_idx);
    meta_grp.createAttribute("je", je);
    meta_grp.createAttribute("ramp_start", ramp_start);
    meta_grp.createAttribute("stable_end", stable_end);
    current_step_file_->createGroup("data");
}

void SplitSolutionWriter::write_frame(int frame_idx, double time, double dt,
                                      const Eigen::VectorXcd& psi,
                                      const Eigen::VectorXd& mu,
                                      const Eigen::VectorXd& supercurrent,
                                      const Eigen::VectorXd& normal_current,
                                      const Eigen::MatrixX2d& applied_A,
                                      const Eigen::MatrixX2d& induced_A,
                                      const Eigen::VectorXd& epsilon) {
    auto data_grp = current_step_file_->getGroup("data");
    std::string frame_name = "step_" + std::to_string(frame_idx);
    auto grp = data_grp.createGroup(frame_name);

    int n = psi.size();

    // Write psi as (N, 2) interleaved [re0, im0, re1, im1, ...]
    std::vector<double> psi_interleaved(2 * n);
    for (int i = 0; i < n; ++i) {
        psi_interleaved[2 * i] = psi(i).real();
        psi_interleaved[2 * i + 1] = psi(i).imag();
    }
    h5::DataSpace psi_space({static_cast<unsigned long long>(n), 2ULL});
    grp.createDataSet<double>("psi", psi_space).write_raw(psi_interleaved.data());

    write_1d<double>(grp, "mu", mu.data(), n);
    write_1d<double>(grp, "supercurrent", supercurrent.data(), supercurrent.size());
    write_1d<double>(grp, "normal_current", normal_current.data(), normal_current.size());

    if (applied_A.size() > 0) {
        Eigen::Matrix<double, -1, 2, Eigen::RowMajor> A_rm = applied_A;
        write_2d<double>(grp, "applied_vector_potential", A_rm.data(), A_rm.rows(), 2);
    }
    if (induced_A.size() > 0) {
        Eigen::Matrix<double, -1, 2, Eigen::RowMajor> Ai_rm = induced_A;
        write_2d<double>(grp, "induced_vector_potential", Ai_rm.data(), Ai_rm.rows(), 2);
    }
    if (epsilon.size() > 0)
        write_1d<double>(grp, "epsilon", epsilon.data(), epsilon.size());

    grp.createAttribute("time", time);
    grp.createAttribute("dt", dt);

    current_frame_idx_++;
}

void SplitSolutionWriter::end_step() {
    if (!current_step_file_) return;

    // Re-open step file read-only to get dataset offsets
    h5::File ro_file(current_step_path_, h5::File::ReadOnly);
    current_step_offsets_.clear();

    std::vector<std::string> dataset_names = {
        "psi", "mu", "supercurrent", "normal_current",
        "applied_vector_potential", "induced_vector_potential", "epsilon"
    };

    for (const auto& name : dataset_names) {
        try {
            auto ds = ro_file.getDataSet("data/step_" + std::to_string(current_frame_idx_ - 1) + "/" + name);
            current_step_offsets_[name] = ds.getOffset();
        } catch (...) {
            // Dataset not present, skip
        }
    }

    // Build step entry for discrete_index
    nlohmann::json step_entry;
    step_entry["step_idx"] = current_step_idx_;
    step_entry["file"] = current_step_path_;
    step_entry["offsets"] = current_step_offsets_;
    step_entry["total_frames"] = current_frame_idx_;
    step_entry["je"] = current_step_idx_;  // Will be set properly via metadata if needed
    step_entry["ramp_start"] = current_step_idx_;
    step_entry["stable_end"] = current_step_idx_;

    // Read actual values from metadata
    {
        h5::File meta_file(current_step_path_, h5::File::ReadOnly);
        auto meta_grp = meta_file.getGroup("metadata");
        try { step_entry["je"] = meta_grp.getAttribute("je").read<double>(); } catch (...) {}
        try { step_entry["ramp_start"] = meta_grp.getAttribute("ramp_start").read<double>(); } catch (...) {}
        try { step_entry["stable_end"] = meta_grp.getAttribute("stable_end").read<double>(); } catch (...) {}
    }

    discrete_index_steps_.push_back(step_entry);

    // Write/update discrete_index.json
    nlohmann::json idx;
    idx["steps"] = discrete_index_steps_;
    std::ofstream ofs(discrete_index_path());
    ofs << idx.dump(2);
    ofs.close();

    current_step_file_.reset();
}

void SplitSolutionWriter::write_manifest(const std::string& run_id, double solve_time) {
    nlohmann::json manifest;
    manifest["run_id"] = run_id;
    manifest["solve_time"] = solve_time;
    manifest["num_steps"] = static_cast<int>(discrete_index_steps_.size());
    manifest["mesh_file"] = mesh_path();
    manifest["discrete_index_file"] = discrete_index_path();

    std::ofstream ofs(manifest_path());
    ofs << manifest.dump(2);
    ofs.close();
}
