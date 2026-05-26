#include "device/device.h"
#include "mesh/io.h"
#include "options/options.h"
#include "operators/operators.h"
#include "operators/poisson_solver.h"
#include "solver/psi_update.h"
#include "solver/observables.h"
#include "solver/screening.h"
#include "solver/adaptive_step.h"
#include "solver/solver.h"
#include <benchmark/benchmark.h>
#include <iostream>

// Global state initialized once, shared across benchmarks
static Device g_device;
static Options g_options;
static MeshOperators g_operators;
static PoissonSolver g_poisson;
static Eigen::VectorXcd g_psi;
static Eigen::VectorXd g_mu;
static Eigen::VectorXd g_epsilon;
static Eigen::VectorXd g_mu_boundary;
static Eigen::VectorXd g_dA_dt;
static Eigen::MatrixX2d g_applied_A;
static Eigen::VectorXd g_screening_areas;
static Eigen::MatrixX2d g_screening_sites;
static Eigen::MatrixX2d g_screening_edge_centers;
static double g_gamma, g_u;
static bool g_initialized = false;

static void init_global_state() {
    if (g_initialized) return;
    g_initialized = true;

    auto device = read_device("data/reference.h5");
    auto options = read_options("data/reference.h5");
    g_device = std::move(device);
    g_options = std::move(options);

    int ns = g_device.mesh.num_sites;
    int ne = g_device.mesh.edge_mesh->num_edges;
    int nb = g_device.mesh.edge_mesh->boundary_edge_indices.size();

    g_gamma = g_device.layer.gamma;
    g_u = g_device.layer.u;
    g_epsilon = Eigen::VectorXd::Constant(ns, 1.0);
    g_applied_A = Eigen::MatrixX2d::Zero(ne, 2);
    g_dA_dt = Eigen::VectorXd::Zero(ne);
    g_mu_boundary = Eigen::VectorXd::Zero(nb);

    // Build operators with fixed sites from terminals
    Eigen::VectorXi fixed_sites(0);
    int total_fixed = 0;
    for (auto& t : g_device.terminals) {
        total_fixed += t.site_indices.size();
    }
    fixed_sites.resize(total_fixed);
    int idx = 0;
    for (auto& t : g_device.terminals) {
        for (int i = 0; i < t.site_indices.size(); ++i) {
            fixed_sites(idx++) = t.site_indices(i);
        }
    }
    g_operators.build_operators(g_device.mesh, fixed_sites, fixed_sites.size() > 0);
    g_poisson.factorize(g_operators.mu_laplacian_);
    g_operators.set_link_exponents(g_applied_A);

    // Initialize psi
    g_psi = Eigen::VectorXcd::Ones(ns);
    for (int i = 0; i < fixed_sites.size(); ++i) {
        g_psi(fixed_sites(i)) = std::complex<double>(g_options.terminal_psi, 0);
    }
    g_mu = Eigen::VectorXd::Zero(ns);

    // Screening coordinates
    double xi = g_device.layer.coherence_length;
    double Lambda = g_device.Lambda;
    double A_scale = 1.0 / (M_PI * Lambda) * 1e-6;
    g_screening_areas = A_scale * g_device.mesh.areas.array() * (xi * xi);
    g_screening_sites = xi * g_device.mesh.sites;
    g_screening_edge_centers = xi * g_device.mesh.edge_mesh->centers;

    std::cout << "Benchmark setup: " << ns << " sites, " << ne << " edges\n";
}

// --- Benchmarks ---

static void BM_PsiUpdate(benchmark::State& state) {
    init_global_state();
    double dt = g_options.dt_init;

    for (auto _ : state) {
        auto result = solve_for_psi_squared(
            g_psi, g_psi.array().abs().square(), g_mu, g_epsilon,
            g_gamma, g_u, dt, g_operators.psi_laplacian_);
        if (result.has_value()) {
            g_psi = result->psi;
        }
    }
}
BENCHMARK(BM_PsiUpdate);

static void BM_Observables(benchmark::State& state) {
    init_global_state();

    for (auto _ : state) {
        auto obs = solve_for_observables(
            g_psi, g_dA_dt, g_operators, g_mu_boundary, g_poisson);
        g_mu = obs.mu;
    }
}
BENCHMARK(BM_Observables);

static void BM_BiotSavart(benchmark::State& state) {
    init_global_state();
    int ns = g_device.mesh.num_sites;
    int ne = g_device.mesh.edge_mesh->num_edges;

    // Create a representative J_site (non-zero for meaningful timing)
    Eigen::MatrixX2d J_site = Eigen::MatrixX2d::Random(ns, 2) * 0.01;

    for (auto _ : state) {
        auto A = compute_induced_vector_potential(
            J_site, g_screening_areas, g_screening_sites, g_screening_edge_centers);
        benchmark::DoNotOptimize(A.data());
    }
}
BENCHMARK(BM_BiotSavart);

static void BM_Interpolation(benchmark::State& state) {
    init_global_state();
    int ns = g_device.mesh.num_sites;
    int ne = g_device.mesh.edge_mesh->num_edges;
    auto& edges = g_device.mesh.edge_mesh->edges;
    auto& norm_dirs = g_device.mesh.edge_mesh->normalized_directions;

    // Representative edge current vector
    Eigen::VectorXd edge_vals = Eigen::VectorXd::Random(ne) * 0.01;

    for (auto _ : state) {
        auto J_site = interpolate_vector_edges_to_sites(
            edge_vals, edges, norm_dirs, ns);
        benchmark::DoNotOptimize(J_site.data());
    }
}
BENCHMARK(BM_Interpolation);

static void BM_FullStep(benchmark::State& state) {
    init_global_state();
    double dt = g_options.dt_init;

    for (auto _ : state) {
        auto result = adaptive_euler_step(
            0, g_psi, g_psi.array().abs().square(), g_mu, g_epsilon,
            dt, g_options, g_gamma, g_u, g_operators.psi_laplacian_);

        g_psi = result.psi;

        auto obs = solve_for_observables(
            g_psi, g_dA_dt, g_operators, g_mu_boundary, g_poisson);
        g_mu = obs.mu;
    }
}
BENCHMARK(BM_FullStep);

static void BM_FullSolve(benchmark::State& state) {
    init_global_state();

    for (auto _ : state) {
        int ns = g_device.mesh.num_sites;
        g_psi = Eigen::VectorXcd::Ones(ns);
        g_mu = Eigen::VectorXd::Zero(ns);
        g_operators.set_link_exponents(g_applied_A);

        std::map<std::string, double> terminal_currents;
        for (auto& t : g_device.terminals) {
            if (t.name == "source") terminal_currents["source"] = 1.0;
            if (t.name == "drain") terminal_currents["drain"] = -1.0;
        }

        TdglSolver solver(g_device, g_options, g_applied_A,
                          terminal_currents, 1.0, "");
        solver.solve();
    }
}
BENCHMARK(BM_FullSolve)->Unit(benchmark::kMillisecond);

BENCHMARK_MAIN();
