#include <gtest/gtest.h>
#include "device/device.h"
#include "solver/psi_update.h"
#include "mesh/io.h"
#include "operators/operators.h"
#include "operators/poisson_solver.h"
#include <Eigen/Core>
#include <cmath>
#include <iostream>

// Load the reference HDF5 once for all tests
class OperatorTest : public ::testing::Test {
protected:
    void SetUp() override {
        device_ = read_device("/mnt/c/Users/photo/Photonics_Group/Ruihuan/cpp-tdgl/data/reference.h5");
        options_ = read_options("/mnt/c/Users/photo/Photonics_Group/Ruihuan/cpp-tdgl/data/reference.h5");
    }
    Device device_;
    Options options_;
};

TEST_F(OperatorTest, MeshDimensions) {
    EXPECT_EQ(device_.mesh.num_sites, 295);
    EXPECT_EQ(device_.mesh.num_elements, 515);
    EXPECT_EQ(device_.mesh.edge_mesh->num_edges, 809);
    EXPECT_EQ(device_.terminals.size(), 2);
}

TEST_F(OperatorTest, LaplacianOfOnes) {
    // With Neumann BC, Laplacian of all-ones should be zero for interior sites
    Eigen::VectorXi no_fixed;
    MeshOperators ops;
    ops.build_operators(device_.mesh, no_fixed, false);

    Eigen::VectorXd ones = Eigen::VectorXd::Ones(device_.mesh.num_sites);
    Eigen::VectorXd result = ops.mu_laplacian_ * ones;

    // Sum should be approximately zero
    EXPECT_NEAR(result.sum(), 0.0, 1e-10);
}

TEST_F(OperatorTest, SupercurrentZeroForUniformPsi) {
    Eigen::VectorXi no_fixed;
    MeshOperators ops;
    ops.build_operators(device_.mesh, no_fixed, false);

    Eigen::VectorXcd psi = Eigen::VectorXcd::Ones(device_.mesh.num_sites);
    Eigen::VectorXd js = ops.get_supercurrent(psi);

    EXPECT_NEAR(js.norm(), 0.0, 1e-12);
}

TEST_F(OperatorTest, PoissonSolverIdentity) {
    Eigen::VectorXi no_fixed;
    MeshOperators ops;
    ops.build_operators(device_.mesh, no_fixed, false);

    PoissonSolver ps;
    ps.factorize(ops.mu_laplacian_);

    Eigen::VectorXd rhs = Eigen::VectorXd::Zero(device_.mesh.num_sites);
    Eigen::VectorXd x = ps.solve(rhs);
    EXPECT_NEAR(x.norm(), 0.0, 1e-10);
}

TEST_F(OperatorTest, PsiUpdateFirstStep) {
    // Run one step of psi update with known initial conditions
    Eigen::VectorXi no_fixed;
    MeshOperators ops;
    ops.build_operators(device_.mesh, no_fixed, false);

    int n = device_.mesh.num_sites;
    double gamma = device_.layer.gamma;
    double u = device_.layer.u;
    double dt = 1e-4;

    Eigen::VectorXcd psi = Eigen::VectorXcd::Ones(n);
    Eigen::VectorXd mu = Eigen::VectorXd::Zero(n);
    Eigen::VectorXd epsilon = Eigen::VectorXd::Ones(n);
    Eigen::VectorXd abs_sq_psi = Eigen::VectorXd::Ones(n);

    auto result = solve_for_psi_squared(psi, abs_sq_psi, mu, epsilon,
                                         gamma, u, dt, ops.psi_laplacian_);

    ASSERT_TRUE(result.has_value()) << "First step discriminant was negative!";

    // With psi=1, mu=0, epsilon=1, no A: psi should remain close to 1
    Eigen::VectorXd new_abs2 = result->psi.array().abs().square();
    EXPECT_NEAR(new_abs2.maxCoeff(), 1.0, 0.01);
    EXPECT_NEAR(new_abs2.minCoeff(), 1.0, 0.01);
}

TEST_F(OperatorTest, OperatorCounts) {
    Eigen::VectorXi no_fixed;
    MeshOperators ops;
    ops.build_operators(device_.mesh, no_fixed, false);

    // Check matrix dimensions
    EXPECT_EQ(ops.mu_gradient_.rows(), device_.mesh.edge_mesh->num_edges);
    EXPECT_EQ(ops.mu_gradient_.cols(), device_.mesh.num_sites);
    EXPECT_EQ(ops.divergence_.rows(), device_.mesh.num_sites);
    EXPECT_EQ(ops.divergence_.cols(), device_.mesh.edge_mesh->num_edges);
    EXPECT_EQ(ops.mu_laplacian_.rows(), device_.mesh.num_sites);
    EXPECT_EQ(ops.mu_laplacian_.cols(), device_.mesh.num_sites);
    EXPECT_EQ(ops.psi_gradient_.rows(), device_.mesh.edge_mesh->num_edges);
    EXPECT_EQ(ops.psi_gradient_.cols(), device_.mesh.num_sites);
    EXPECT_EQ(ops.psi_laplacian_.rows(), device_.mesh.num_sites);
    EXPECT_EQ(ops.psi_laplacian_.cols(), device_.mesh.num_sites);
}
