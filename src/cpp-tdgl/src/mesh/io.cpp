#include "mesh/io.h"
#include <highfive/H5File.hpp>
#include <highfive/H5Group.hpp>
#include <highfive/H5DataSet.hpp>
#include <vector>

namespace h5 = HighFive;

template<typename T>
std::vector<T> read_1d(const h5::Group& g, const std::string& name) {
    auto ds = g.getDataSet(name);
    auto dims = ds.getDimensions();
    size_t n = dims[0];
    for (size_t i = 1; i < dims.size(); ++i) n *= dims[i];
    std::vector<T> buf(n);
    ds.read_raw(buf.data());
    return buf;
}

template<typename T>
std::vector<T> read_flat(const h5::Group& g, const std::string& name) {
    auto ds = g.getDataSet(name);
    auto dims = ds.getDimensions();
    size_t total = dims[0];
    for (size_t i = 1; i < dims.size(); ++i) total *= dims[i];
    std::vector<T> buf(total);
    ds.read_raw(buf.data());
    return buf;
}

template<typename T>
void write_flat(h5::Group& g, const std::string& name,
                const T* data, size_t rows, size_t cols) {
    h5::DataSpace space({rows, cols});
    g.createDataSet(name, space, h5::AtomicType<T>()).write_raw(data);
}

template<typename T>
void write_1d(h5::Group& g, const std::string& name,
              const T* data, size_t n) {
    h5::DataSpace space({n});
    g.createDataSet(name, space, h5::AtomicType<T>()).write_raw(data);
}

EdgeMesh read_edge_mesh(const h5::Group& eg) {
    EdgeMesh em;
    auto centers = read_flat<double>(eg, "centers");
    em.centers = Eigen::Map<Eigen::Matrix<double, -1, 2, Eigen::RowMajor>>(
        centers.data(), centers.size() / 2, 2);
    em.num_edges = em.centers.rows();

    auto edges = read_flat<int64_t>(eg, "edges");
    em.edges = Eigen::Map<Eigen::Matrix<int64_t, -1, 2, Eigen::RowMajor>>(
        edges.data(), edges.size() / 2, 2);

    auto bei = read_1d<int64_t>(eg, "boundary_edge_indices");
    em.boundary_edge_indices.resize(bei.size());
    for (size_t i = 0; i < bei.size(); ++i)
        em.boundary_edge_indices(i) = static_cast<int>(bei[i]);

    auto dirs = read_flat<double>(eg, "directions");
    em.directions = Eigen::Map<Eigen::Matrix<double, -1, 2, Eigen::RowMajor>>(
        dirs.data(), dirs.size() / 2, 2);

    auto el = read_1d<double>(eg, "edge_lengths");
    em.edge_lengths = Eigen::Map<Eigen::VectorXd>(el.data(), el.size());

    // normalized_directions = directions / edge_lengths
    em.normalized_directions = em.directions.array().colwise() / em.edge_lengths.array();

    auto del = read_1d<double>(eg, "dual_edge_lengths");
    em.dual_edge_lengths = Eigen::Map<Eigen::VectorXd>(del.data(), del.size());

    return em;
}

Mesh read_mesh(const std::string& h5_path) {
    h5::File file(h5_path, h5::File::ReadOnly);
    return read_mesh(file);
}

Mesh read_mesh(h5::File& file) {
    h5::Group mg;
    if (file.exist("mesh"))
        mg = file.getGroup("mesh");
    else if (file.exist("solution/device/mesh"))
        mg = file.getGroup("solution/device/mesh");
    else
        throw std::runtime_error("No mesh group found");

    Mesh m;
    auto sites = read_flat<double>(mg, "sites");
    m.sites = Eigen::Map<Eigen::Matrix<double, -1, 2, Eigen::RowMajor>>(
        sites.data(), sites.size() / 2, 2);
    m.num_sites = m.sites.rows();

    auto elems = read_flat<int64_t>(mg, "elements");
    m.elements = Eigen::Map<Eigen::Matrix<int64_t, -1, 3, Eigen::RowMajor>>(
        elems.data(), elems.size() / 3, 3);
    m.num_elements = m.elements.rows();

    auto bi = read_1d<int64_t>(mg, "boundary_indices");
    m.boundary_indices.resize(bi.size());
    for (size_t i = 0; i < bi.size(); ++i)
        m.boundary_indices(i) = static_cast<int>(bi[i]);

    auto areas = read_1d<double>(mg, "areas");
    m.areas = Eigen::Map<Eigen::VectorXd>(areas.data(), areas.size());

    m.edge_mesh = read_edge_mesh(mg.getGroup("edge_mesh"));
    return m;
}

void write_mesh(const Mesh& mesh, const std::string& h5_path) {
    h5::File file(h5_path, h5::File::Overwrite);
    write_mesh(mesh, file);
}

void write_mesh(const Mesh& mesh, h5::File& file) {
    auto mg = file.createGroup("mesh");

    // Write 2D data in row-major order (matching HDF5 convention)
    // Copy to row-major temporary for correct memory layout
    Eigen::Matrix<double, -1, 2, Eigen::RowMajor> sites_rm = mesh.sites;
    write_flat<double>(mg, "sites", sites_rm.data(), mesh.sites.rows(), 2);

    Eigen::Matrix<int64_t, -1, 3, Eigen::RowMajor> elems_rm = mesh.elements;
    write_flat<int64_t>(mg, "elements", elems_rm.data(),
                         mesh.elements.rows(), 3);

    std::vector<int64_t> bi(mesh.boundary_indices.data(),
                              mesh.boundary_indices.data() + mesh.boundary_indices.size());
    write_1d<int64_t>(mg, "boundary_indices", bi.data(), bi.size());
    write_1d<double>(mg, "areas", mesh.areas.data(), mesh.areas.rows());

    if (mesh.edge_mesh) {
        auto& em = *mesh.edge_mesh;
        auto eg = mg.createGroup("edge_mesh");

        Eigen::Matrix<double, -1, 2, Eigen::RowMajor> centers_rm = em.centers;
        write_flat<double>(eg, "centers", centers_rm.data(), em.centers.rows(), 2);

        Eigen::Matrix<int64_t, -1, 2, Eigen::RowMajor> edges_rm = em.edges;
        write_flat<int64_t>(eg, "edges", edges_rm.data(), em.edges.rows(), 2);

        std::vector<int64_t> bei(em.boundary_edge_indices.data(),
                                   em.boundary_edge_indices.data() + em.boundary_edge_indices.size());
        write_1d<int64_t>(eg, "boundary_edge_indices", bei.data(), bei.size());

        Eigen::Matrix<double, -1, 2, Eigen::RowMajor> dirs_rm = em.directions;
        write_flat<double>(eg, "directions", dirs_rm.data(), em.directions.rows(), 2);

        write_1d<double>(eg, "edge_lengths", em.edge_lengths.data(), em.edge_lengths.rows());
        write_1d<double>(eg, "dual_edge_lengths", em.dual_edge_lengths.data(),
                         em.dual_edge_lengths.rows());
    }
}
