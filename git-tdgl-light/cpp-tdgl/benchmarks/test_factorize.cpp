// Quick test: read mesh and factorize
#include "mesh/io.h"
#include "device/device.h"
#include "options/options.h"
#include "operators/operators.h"
#include "operators/poisson_solver.h"
#include <iostream>

int main() {
    try {
        std::cout << "Reading device..." << std::endl;
        auto device = read_device("data/weak_link_no_screen.h5");
        std::cout << "Device: " << device.name
                  << ", sites=" << device.mesh.num_sites
                  << ", edges=" << device.mesh.edge_mesh->num_edges << std::endl;

        auto options = read_options("data/weak_link_no_screen.h5");
        std::cout << "Options loaded" << std::endl;

        Eigen::VectorXi no_fixed;
        MeshOperators ops;
        std::cout << "Building operators..." << std::endl;
        ops.build_operators(device.mesh, no_fixed, false);
        std::cout << "Operators built" << std::endl;

        PoissonSolver ps;
        std::cout << "Factorizing..." << std::endl;
        ps.factorize(ops.mu_laplacian_);
        std::cout << "Factorization done!" << std::endl;

        Eigen::VectorXd rhs = Eigen::VectorXd::Random(device.mesh.num_sites);
        rhs -= Eigen::VectorXd::Constant(device.mesh.num_sites, rhs.mean());
        auto x = ps.solve(rhs);
        std::cout << "Solve done! |x|=" << x.norm() << std::endl;
    } catch (const std::exception& e) {
        std::cerr << "Error: " << e.what() << std::endl;
        return 1;
    }
    return 0;
}
