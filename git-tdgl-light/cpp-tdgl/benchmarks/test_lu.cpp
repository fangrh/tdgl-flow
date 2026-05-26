// Test SparseLU factorization on weak-link sized matrix
#include "mesh/io.h"
#include "device/device.h"
#include "options/options.h"
#include "operators/operators.h"
#include "operators/poisson_solver.h"
#include <iostream>
#include <chrono>

int main() {
    std::cout << "Reading mesh..." << std::endl;
    auto mesh = read_mesh("/mnt/c/Users/photo/Photonics_Group/Ruihuan/cpp-tdgl/data/weak_link_no_screen.h5");
    std::cout << "Mesh: " << mesh.num_sites << " sites, " << mesh.edge_mesh->num_edges << " edges" << std::endl;

    std::cout << "Building operators..." << std::endl;
    Eigen::VectorXi no_fixed;
    MeshOperators ops;
    ops.build_operators(mesh, no_fixed, false);
    std::cout << "Operators built" << std::endl;

    std::cout << "Factorizing mu_laplacian..." << std::endl;
    PoissonSolver ps;
    auto t0 = std::chrono::high_resolution_clock::now();
    ps.factorize(ops.mu_laplacian_);
    auto t1 = std::chrono::high_resolution_clock::now();
    double ms = std::chrono::duration<double, std::milli>(t1 - t0).count();
    std::cout << "Factorization: " << ms << " ms" << std::endl;

    Eigen::VectorXd rhs = Eigen::VectorXd::Random(mesh.num_sites);
    rhs -= Eigen::VectorXd::Constant(mesh.num_sites, rhs.mean());
    t0 = std::chrono::high_resolution_clock::now();
    auto x = ps.solve(rhs);
    t1 = std::chrono::high_resolution_clock::now();
    ms = std::chrono::duration<double, std::milli>(t1 - t0).count();
    std::cout << "Solve: " << ms << " ms" << std::endl;

    return 0;
}
