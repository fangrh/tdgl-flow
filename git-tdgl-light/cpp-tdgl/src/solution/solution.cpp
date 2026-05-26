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
